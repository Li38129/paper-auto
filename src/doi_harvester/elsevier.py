"""Elsevier Article Retrieval 与 Content Object API 下载实现。"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import requests
from pypdf import PdfReader

from .models import Attempt

LOGGER = logging.getLogger(__name__)

ARTICLE_API = "https://api.elsevier.com/content/article/doi"
OBJECT_API = "https://api.elsevier.com/content/object/eid"
MINIMUM_PDF_BYTES = 10_000
_EID_PATTERN = re.compile(
    r"\b1-s2\.0-[A-Za-z0-9]+(?:-[A-Za-z0-9_.]+)?(?:\.pdf)?\b",
    re.IGNORECASE,
)
_SUPPLEMENT_HINTS = (
    "supplement",
    "supplementary",
    "mmc",
    "appendix",
    "graphical",
    "thumbnail",
    "image",
    "figure",
)
_MAIN_HINTS = ("main", "web-pdf", "full-text", "fulltext", "attachment", "pdf")


class SupportsSession(Protocol):
    trust_env: bool
    proxies: dict[str, str]

    def get(self, url: str, **kwargs: Any) -> Any: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _Route:
    name: str
    proxy_url: str = ""


@dataclass(slots=True)
class ElsevierDownload:
    success: bool
    reason: str
    source: str = ""
    attempts: list[Attempt] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def extract_main_pdf_eids(xml_text: str) -> list[str]:
    """从 FULL XML 中提取正文 PDF EID，并排除补充材料。"""
    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, TypeError):
        return []
    parent_map = {child: parent for parent in root.iter() for child in parent}
    scored: list[tuple[int, str]] = []
    for element in root.iter():
        context_parts: list[str] = []
        current: ET.Element | None = element
        for _ in range(3):
            if current is None:
                break
            context_parts.append(_local_name(current.tag))
            context_parts.extend(_local_name(key) for key in current.attrib)
            context_parts.extend(str(value) for value in current.attrib.values())
            if current.text:
                context_parts.append(current.text)
            current = parent_map.get(current)
        context = " ".join(context_parts).lower()
        values = [*element.attrib.values()]
        if element.text:
            values.append(element.text)
        for value in values:
            for raw_eid in _EID_PATTERN.findall(str(value)):
                eid = _normalize_eid(raw_eid)
                haystack = f"{eid} {context}".lower()
                if any(hint in haystack for hint in _SUPPLEMENT_HINTS):
                    continue
                score = 100 if eid.lower().endswith("-main.pdf") else 20
                score += sum(10 for hint in _MAIN_HINTS if hint in context)
                scored.append((score, eid))
    scored.sort(key=lambda item: item[0], reverse=True)
    return _deduplicate([eid for _, eid in scored])


def visible_xml_characters(xml_text: str) -> int:
    """计算 FULL XML 的本地可见字符数，不调用模型。"""
    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, TypeError):
        return 0
    return len("".join("".join(root.itertext()).split()))


class ElsevierApiClient:
    """使用 direct-first 路由下载 Elsevier 正式正文 PDF。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], SupportsSession] | None = None,
        xml_timeout_seconds: float = 30.0,
        pdf_timeout_seconds: float = 60.0,
    ) -> None:
        self.session_factory = session_factory or requests.Session
        self.xml_timeout_seconds = xml_timeout_seconds
        self.pdf_timeout_seconds = pdf_timeout_seconds

    def download(
        self,
        *,
        doi: str,
        destination: Path,
        api_key: str,
        inst_token: str = "",
        proxy_url: str = "",
    ) -> ElsevierDownload:
        if not api_key:
            return ElsevierDownload(False, "api_key_missing")
        attempts: list[Attempt] = []
        warnings: list[str] = []
        routes = [_Route("direct")]
        if proxy_url:
            routes.append(_Route("configured_proxy", proxy_url))
        last_reason = "elsevier_api_failed"
        for route in routes:
            xml_response, xml_attempt = self._get_xml(
                doi=doi,
                api_key=api_key,
                inst_token=inst_token,
                route=route,
            )
            attempts.append(xml_attempt)
            last_reason = xml_attempt.reason
            if xml_response is None:
                if not _allows_route_fallback(xml_attempt):
                    break
                continue
            eids = extract_main_pdf_eids(xml_response.text)
            if visible_xml_characters(xml_response.text) < 1500:
                warnings.append("short_fulltext_under_1500")
            _close_response(xml_response)
            if not eids:
                attempts.append(
                    Attempt(
                        source="elsevier_api",
                        url=str(getattr(xml_response, "url", "")),
                        success=False,
                        reason="main_pdf_eid_missing",
                        stage="publisher_api_xml",
                        provider="elsevier",
                        route=route.name,
                    )
                )
                last_reason = "main_pdf_eid_missing"
                break
            result = self._download_object(
                doi=doi,
                eids=eids,
                destination=destination,
                api_key=api_key,
                inst_token=inst_token,
                route=route,
            )
            attempts.extend(result.attempts)
            if result.success:
                result.attempts = attempts
                result.warnings = list(dict.fromkeys(warnings))
                return result
            last_reason = result.reason
            if not any(_allows_route_fallback(item) for item in result.attempts[-len(eids) :]):
                break
        return ElsevierDownload(
            False,
            last_reason,
            attempts=attempts,
            warnings=list(dict.fromkeys(warnings)),
        )

    def _get_xml(
        self,
        *,
        doi: str,
        api_key: str,
        inst_token: str,
        route: _Route,
    ) -> tuple[Any | None, Attempt]:
        url = f"{ARTICLE_API}/{quote(doi, safe='')}"
        headers = {"X-ELS-APIKey": api_key, "Accept": "application/xml"}
        if inst_token:
            headers["X-ELS-Insttoken"] = inst_token
        response, error, duration_ms = self._request(
            route,
            url,
            headers=headers,
            params={"view": "FULL"},
            timeout=self.xml_timeout_seconds,
        )
        if response is None:
            return None, Attempt(
                source="elsevier_api",
                url=url,
                success=False,
                reason=error,
                stage="publisher_api_xml",
                provider="elsevier",
                route=route.name,
                duration_ms=duration_ms,
            )
        status_code = int(response.status_code)
        els_status = str(response.headers.get("X-ELS-Status", ""))
        reason = _http_reason(status_code, els_status)
        attempt = Attempt(
            source="elsevier_api",
            url=url,
            success=status_code == 200,
            reason="xml_downloaded" if status_code == 200 else reason,
            status_code=status_code,
            content_type=str(response.headers.get("Content-Type", "")),
            final_url=str(getattr(response, "url", url)),
            bytes_written=len(str(response.text).encode("utf-8")),
            stage="publisher_api_xml",
            provider="elsevier",
            route=route.name,
            duration_ms=duration_ms,
            response_status=els_status,
        )
        return (response if status_code == 200 else None), attempt

    def _download_object(
        self,
        *,
        doi: str,
        eids: list[str],
        destination: Path,
        api_key: str,
        inst_token: str,
        route: _Route,
    ) -> ElsevierDownload:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(f"{destination.suffix}.part")
        attempts: list[Attempt] = []
        headers = {"X-ELS-APIKey": api_key, "Accept": "application/pdf"}
        if inst_token:
            headers["X-ELS-Insttoken"] = inst_token
        last_reason = "object_download_failed"
        try:
            for eid in eids:
                temporary.unlink(missing_ok=True)
                url = f"{OBJECT_API}/{quote(eid, safe='')}"
                response, error, duration_ms = self._request(
                    route,
                    url,
                    headers=headers,
                    timeout=self.pdf_timeout_seconds,
                    stream=True,
                )
                if response is None:
                    last_reason = error
                    attempts.append(
                        Attempt(
                            source="elsevier_api",
                            url=url,
                            success=False,
                            reason=error,
                            stage="publisher_api_object",
                            provider="elsevier",
                            route=route.name,
                            duration_ms=duration_ms,
                        )
                    )
                    continue
                status_code = int(response.status_code)
                els_status = str(response.headers.get("X-ELS-Status", ""))
                if status_code != 200:
                    last_reason = _http_reason(status_code, els_status)
                    attempts.append(
                        Attempt(
                            source="elsevier_api",
                            url=url,
                            success=False,
                            reason=last_reason,
                            status_code=status_code,
                            content_type=str(response.headers.get("Content-Type", "")),
                            final_url=str(getattr(response, "url", url)),
                            stage="publisher_api_object",
                            provider="elsevier",
                            route=route.name,
                            duration_ms=duration_ms,
                            response_status=els_status,
                        )
                    )
                    _close_response(response)
                    continue
                bytes_written = 0
                with temporary.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            handle.write(chunk)
                            bytes_written += len(chunk)
                _close_response(response)
                reason = _validate_pdf(temporary)
                success = not reason
                attempts.append(
                    Attempt(
                        source="elsevier_api",
                        url=url,
                        success=success,
                        reason="downloaded" if success else reason,
                        status_code=status_code,
                        content_type=str(response.headers.get("Content-Type", "")),
                        final_url=str(getattr(response, "url", url)),
                        bytes_written=bytes_written,
                        stage="publisher_api_object",
                        provider="elsevier",
                        route=route.name,
                        duration_ms=duration_ms,
                        response_status=els_status,
                    )
                )
                if success:
                    temporary.replace(destination)
                    return ElsevierDownload(
                        True,
                        "downloaded",
                        source=f"elsevier_api:object_eid:{route.name}",
                        attempts=attempts,
                    )
                last_reason = reason
            return ElsevierDownload(False, last_reason, attempts=attempts)
        finally:
            temporary.unlink(missing_ok=True)

    def _request(
        self,
        route: _Route,
        url: str,
        **kwargs: Any,
    ) -> tuple[Any | None, str, int]:
        start = time.perf_counter()
        session = self.session_factory()
        session.trust_env = False
        if route.proxy_url:
            session.proxies = {"http": route.proxy_url, "https": route.proxy_url}
        try:
            for attempt_index in range(2):
                try:
                    response = session.get(url, allow_redirects=True, **kwargs)
                except requests.exceptions.SSLError:
                    session.close()
                    return None, "tls_error", int((time.perf_counter() - start) * 1000)
                except requests.Timeout:
                    if attempt_index == 0:
                        continue
                    session.close()
                    return None, "timeout", int((time.perf_counter() - start) * 1000)
                except requests.RequestException as exc:
                    LOGGER.info("Elsevier API route=%s 请求失败：%s", route.name, exc)
                    session.close()
                    return (
                        None,
                        type(exc).__name__,
                        int((time.perf_counter() - start) * 1000),
                    )
                if int(response.status_code) >= 500 and attempt_index == 0:
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()
                    continue
                response._autopaper_session = session
                return response, "", int((time.perf_counter() - start) * 1000)
        except Exception:
            session.close()
            raise
        session.close()
        return None, "request_failed", int((time.perf_counter() - start) * 1000)


def _normalize_eid(value: str) -> str:
    value = value.strip()
    if value.lower().endswith(".pdf"):
        return value
    if value.lower().endswith("-main"):
        return f"{value}.pdf"
    return f"{value}-main.pdf"


def _deduplicate(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _local_name(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _http_reason(status_code: int, els_status: str) -> str:
    normalized_status = els_status.upper()
    if (
        "AUTHENTICATION_ERROR" in normalized_status
        or "INVALID_API_KEY" in normalized_status
        or "REQUESTOR CONFIGURATION SETTINGS INSUFFICIENT" in normalized_status
    ):
        return "api_configuration_error"
    if "NOT_ENTITLED" in normalized_status:
        return "not_entitled"
    if status_code in {401, 403}:
        return "not_entitled"
    if status_code == 404:
        return "not_found"
    if status_code == 429:
        return "rate_limited"
    return f"http_{status_code}"


def _allows_route_fallback(attempt: Attempt) -> bool:
    return attempt.reason in {
        "timeout",
        "ConnectionError",
        "ProxyError",
        "api_configuration_error",
        "not_entitled",
        "http_400",
    }


def _validate_pdf(path: Path) -> str:
    try:
        if path.stat().st_size < MINIMUM_PDF_BYTES:
            return "pdf_too_small"
        with path.open("rb") as handle:
            if b"%PDF-" not in handle.read(1024):
                return "not_pdf"
        reader = PdfReader(path)
        if len(reader.pages) <= 1:
            return "preview_pdf"
    except (OSError, ValueError):
        return "invalid_pdf"
    return ""


def _close_response(response: Any) -> None:
    """在流式内容消费完毕后关闭响应及其专用会话。"""
    close = getattr(response, "close", None)
    if callable(close):
        close()
    session = getattr(response, "_autopaper_session", None)
    if session is not None:
        session.close()
