"""流式 PDF 下载与内容校验。"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Protocol

import requests

from .models import Attempt

LOGGER = logging.getLogger(__name__)


class SupportsGet(Protocol):
    """便于测试注入的最小 HTTP 会话协议。"""

    headers: Any

    def get(self, url: str, **kwargs: Any) -> Any: ...


def is_valid_pdf(path: Path, *, minimum_bytes: int = 1024) -> bool:
    """用魔数和最小尺寸验证下载结果。"""
    try:
        if path.stat().st_size < minimum_bytes:
            return False
        with path.open("rb") as handle:
            return b"%PDF-" in handle.read(1024)
    except OSError:
        return False


class HttpPdfTransport:
    """通过 HTTP 下载正文，成功前只写入临时文件。"""

    def __init__(
        self,
        *,
        session: SupportsGet | None = None,
        timeout_seconds: float = 120.0,
        minimum_bytes: int = 1024,
        max_rate_limit_retries: int = 2,
        sleeper: Any = time.sleep,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.minimum_bytes = minimum_bytes
        self.max_rate_limit_retries = max(max_rate_limit_retries, 0)
        self.sleeper = sleeper
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/140.0.0.0 Safari/537.36"
                )
            }
        )

    def download(self, *, url: str, destination: Path, referer: str) -> Attempt:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(f"{destination.suffix}.part")
        temporary.unlink(missing_ok=True)
        headers = {
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
            "Referer": referer,
        }

        try:
            for retry_index in range(self.max_rate_limit_retries + 1):
                response = self.session.get(
                    url,
                    headers=headers,
                    timeout=self.timeout_seconds,
                    stream=True,
                    allow_redirects=True,
                )
                with response:
                    status_code = int(response.status_code)
                    if status_code == 429 and retry_index < self.max_rate_limit_retries:
                        raw_retry_after = str(response.headers.get("Retry-After", "")).strip()
                        try:
                            retry_after = float(raw_retry_after)
                        except ValueError:
                            retry_after = float(2 ** retry_index)
                        self.sleeper(min(max(retry_after, 0.0), 60.0))
                        continue
                    return self._save_response(
                        response=response,
                        url=url,
                        destination=destination,
                        temporary=temporary,
                    )
            return Attempt(source="http", url=url, success=False, reason="rate_limited")
        except requests.RequestException as exc:
            temporary.unlink(missing_ok=True)
            LOGGER.info("下载 %s 失败：%s", url, exc)
            return Attempt(source="http", url=url, success=False, reason=type(exc).__name__)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            return Attempt(source="http", url=url, success=False, reason=f"io_error:{exc}")

    def _save_response(
        self,
        *,
        response: Any,
        url: str,
        destination: Path,
        temporary: Path,
    ) -> Attempt:
        """校验一次 HTTP 响应并原子保存 PDF。"""
        status_code = int(response.status_code)
        content_type = str(response.headers.get("Content-Type", ""))
        final_url = str(getattr(response, "url", url))
        if status_code != 200:
            return Attempt(
                source="http",
                url=url,
                success=False,
                reason=f"http_{status_code}",
                status_code=status_code,
                content_type=content_type,
                final_url=final_url,
            )

        bytes_written = 0
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                bytes_written += len(chunk)

        if not is_valid_pdf(temporary, minimum_bytes=self.minimum_bytes):
            temporary.unlink(missing_ok=True)
            return Attempt(
                source="http",
                url=url,
                success=False,
                reason="not_pdf",
                status_code=status_code,
                content_type=content_type,
                final_url=final_url,
                bytes_written=bytes_written,
            )

        temporary.replace(destination)
        return Attempt(
            source="http",
            url=url,
            success=True,
            reason="downloaded",
            status_code=status_code,
            content_type=content_type,
            final_url=final_url,
            bytes_written=bytes_written,
        )
