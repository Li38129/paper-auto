"""在用户合法浏览器会话中发现并保存补充材料。"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .models import Attempt, SupplementArtifact

MAX_SUPPLEMENT_BYTES = 100 * 1024 * 1024
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _read_cdp_endpoint(profile_dir: Path) -> str:
    try:
        payload = json.loads(
            (profile_dir / "auth-state.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("cdp_endpoint") or "")


def _supplement_filename(
    *,
    url: str,
    disposition: str,
    content_type: str,
    index: int,
) -> str:
    """从响应头、查询参数和 URL 生成 Windows 安全文件名。"""
    encoded_name = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
    plain_name = re.search(r'filename="?([^";]+)', disposition, re.I)
    if encoded_name:
        name = unquote(encoded_name.group(1))
    elif plain_name:
        name = plain_name.group(1).strip()
    else:
        parsed = urlparse(url)
        query_name = parse_qs(parsed.query).get("file", [""])[0]
        name = unquote(query_name or Path(parsed.path).name)

    if not Path(name).suffix:
        extensions = {
            "application/pdf": ".pdf",
            "application/zip": ".zip",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
            "text/csv": ".csv",
        }
        name = f"{name or f'supplement-{index:02d}'}{extensions.get(content_type, '.bin')}"
    safe = INVALID_FILENAME.sub("_", name).strip(" .")
    return safe or f"supplement-{index:02d}.bin"


class BrowserSupplementDownloader:
    """通过已连接的 CDP 浏览器下载出版社补充材料。"""

    def __init__(self, *, profile_dir: Path, timeout_seconds: float = 60.0) -> None:
        self.profile_dir = Path(profile_dir)
        self.timeout_ms = int(timeout_seconds * 1000)

    def download(
        self,
        *,
        doi: str,
        article_dir: Path,
    ) -> tuple[str, list[SupplementArtifact], list[Attempt]]:
        """发现补充材料并返回状态、文件和逐链接诊断。"""
        endpoint = _read_cdp_endpoint(self.profile_dir)
        if not endpoint:
            return "browser_session_required", [], []
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return "playwright_not_installed", [], []

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(
                    endpoint, timeout=self.timeout_ms
                )
                if not browser.contexts:
                    return "browser_session_required", [], []
                context = browser.contexts[0]
                page = context.new_page()
                try:
                    return self._download_from_page(
                        page=page,
                        doi=doi,
                        article_dir=article_dir,
                    )
                finally:
                    page.close()
        except Exception as exc:  # noqa: BLE001
            attempt = Attempt(
                source="supplement:browser",
                url=f"https://doi.org/{doi}",
                success=False,
                reason=f"browser_error:{type(exc).__name__}",
            )
            return "browser_error", [], [attempt]

    def _download_from_page(
        self,
        *,
        page: object,
        doi: str,
        article_dir: Path,
    ) -> tuple[str, list[SupplementArtifact], list[Attempt]]:
        page.goto(
            f"https://doi.org/{doi}",
            wait_until="domcontentloaded",
            timeout=self.timeout_ms,
        )
        page.wait_for_timeout(3000)
        urls = page.evaluate(
            """
            () => [...document.querySelectorAll("a[href]")]
                .map(link => link.href)
                .filter(href => {
                    const value = href.toLowerCase();
                    return value.includes("downloadsupplement")
                        || value.includes("article-supplement")
                        || value.includes("suppl_file")
                        || value.includes("/suppdata/")
                        || value.includes("/esm/")
                        || value.includes("mmc");
                })
            """
        )
        urls = list(dict.fromkeys(str(url) for url in urls))[:20]
        if not urls:
            return "not_found", [], []

        supplement_dir = article_dir / "supplements"
        artifacts: list[SupplementArtifact] = []
        attempts: list[Attempt] = []
        used_names: set[str] = set()
        for index, url in enumerate(urls, start=1):
            payload = self._fetch(page=page, url=url)
            if payload is None:
                attempts.append(
                    Attempt(
                        source="supplement:browser",
                        url=url,
                        success=False,
                        reason="not_downloadable",
                    )
                )
                continue
            body, status_code, content_type, disposition, final_url = payload
            name = _supplement_filename(
                url=final_url,
                disposition=disposition,
                content_type=content_type,
                index=index,
            )
            if name in used_names:
                name = f"{index:02d}-{name}"
            used_names.add(name)
            supplement_dir.mkdir(parents=True, exist_ok=True)
            destination = supplement_dir / name
            temporary = destination.with_suffix(f"{destination.suffix}.part")
            temporary.write_bytes(body)
            temporary.replace(destination)
            digest = hashlib.sha256(body).hexdigest().upper()
            artifacts.append(
                SupplementArtifact(
                    name=name,
                    path=str(destination),
                    url=final_url,
                    content_type=content_type,
                    bytes_written=len(body),
                    sha256=digest,
                )
            )
            attempts.append(
                Attempt(
                    source="supplement:browser",
                    url=url,
                    success=True,
                    reason="downloaded",
                    status_code=status_code,
                    content_type=content_type,
                    final_url=final_url,
                    bytes_written=len(body),
                )
            )
        return ("downloaded" if artifacts else "not_downloadable"), artifacts, attempts

    def _fetch(
        self,
        *,
        page: object,
        url: str,
    ) -> tuple[bytes, int, str, str, str] | None:
        try:
            payload = page.evaluate(
                """
                async (url) => {
                    const response = await fetch(url, {credentials: "include"});
                    const bytes = new Uint8Array(await response.arrayBuffer());
                    let binary = "";
                    const chunkSize = 32768;
                    for (let offset = 0; offset < bytes.length; offset += chunkSize) {
                        const chunk = bytes.subarray(offset, offset + chunkSize);
                        binary += String.fromCharCode(...chunk);
                    }
                    return {
                        status: response.status,
                        contentType: response.headers.get("content-type") || "",
                        disposition: response.headers.get("content-disposition") || "",
                        finalUrl: response.url,
                        body: btoa(binary),
                    };
                }
                """,
                url,
            )
            body = base64.b64decode(payload["body"], validate=True)
            status_code = int(payload["status"])
            content_type = str(payload["contentType"]).split(";", maxsplit=1)[0].lower()
            disposition = str(payload["disposition"])
            final_url = str(payload["finalUrl"])
        except Exception:  # noqa: BLE001
            return None
        if (
            status_code >= 400
            or not body
            or len(body) > MAX_SUPPLEMENT_BYTES
            or content_type in {"text/html", "application/xhtml+xml"}
        ):
            return None
        return body, status_code, content_type, disposition, final_url
