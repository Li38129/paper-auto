"""在用户合法浏览器会话中发现并保存补充材料。"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests

from .models import Attempt, SupplementArtifact

MAX_SUPPLEMENT_BYTES = 100 * 1024 * 1024
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SUPPLEMENT_HINTS = (
    "downloadsupplement",
    "article-supplement",
    "suppl_file",
    "/suppdata/",
    "/esm/",
    "-sup-",
    "mmc",
    "supplementary",
    "supporting-information",
    "supplemental",
    "suppdata",
    "additional_file",
    "additional-file",
    "ndownloader/files/",
    "figshare.com",
    "media.springernature.com",
    "/doi/suppl/",
)
SUPPLEMENT_TEXT_HINTS = (
    "supporting information",
    "supplementary material",
    "supplementary information",
    "electronic supplementary",
    "supplemental information",
)
SUPPLEMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".mp4", ".mov", ".avi", ".mpg", ".mpeg", ".gif",
}


class _SupplementLinks(HTMLParser):
    """从出版社页面收集实际附件入口。"""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.urls: list[str] = []
        self._anchor_href = ""
        self._anchor_context = ""
        self.confirmed_no_supplements = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if (
            tag == "li"
            and values.get("data-content-filter") == "supplementary-data"
            and "hide" in values.get("class", "").split()
            and "has-content" not in values.get("class", "").split()
        ):
            self.confirmed_no_supplements = True
        if tag == "a":
            self._anchor_href = values.get("href", "")
            self._anchor_context = " ".join(values.values())

    def handle_data(self, data: str) -> None:
        if self._anchor_href:
            self._anchor_context += f" {data}"

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._anchor_href:
            return
        href = self._anchor_href.casefold()
        if href.startswith("#"):
            self._anchor_href = ""
            self._anchor_context = ""
            return
        parsed_href = urlparse(urljoin(self.base_url, self._anchor_href))
        parsed_base = urlparse(self.base_url)
        path = parsed_href.path.casefold()
        if (
            parsed_href.fragment
            and parsed_href.netloc == parsed_base.netloc
            and parsed_href.path == parsed_base.path
        ):
            self._anchor_href = ""
            self._anchor_context = ""
            return
        is_hint_url = any(hint in href for hint in SUPPLEMENT_HINTS)
        has_supplement_label = any(
            hint in self._anchor_context.casefold() for hint in SUPPLEMENT_TEXT_HINTS
        )
        is_file_link = Path(path).suffix in SUPPLEMENT_EXTENSIONS
        if is_hint_url or (has_supplement_label and is_file_link):
            self.urls.append(urljoin(self.base_url, self._anchor_href))
        self._anchor_href = ""
        self._anchor_context = ""


def _valid_payload(path: Path, content_type: str, expected_name: str = "") -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    with path.open("rb") as source:
        head = source.read(512)
    if head.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        return False
    suffix = Path(expected_name).suffix.lower() if expected_name else path.suffix.lower()
    if suffix == ".pdf" or content_type == "application/pdf":
        return head.startswith(b"%PDF-")
    if suffix in {".zip", ".docx", ".xlsx", ".pptx"} or content_type in {
        "application/zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }:
        return zipfile.is_zipfile(path)
    return True


def _page_confirms_no_supplements(html: str) -> bool:
    """仅在页面明确说明没有补充材料时确认空结果。"""
    text = re.sub(r"<[^>]+>", " ", html).casefold()
    return any(
        phrase in text
        for phrase in (
            "no supplementary information",
            "no supplementary material",
            "no supporting information",
            "supporting information is not available",
            "supplementary material is not available",
        )
    )


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest().upper()


class HttpSupplementDownloader:
    """先使用普通 HTTP，再复用已有浏览器会话获取附件。"""

    def __init__(self, *, profile_dir: Path | None = None, timeout_seconds: float = 45.0) -> None:
        self.profile_dir = Path(profile_dir) if profile_dir else None
        self.timeout_seconds = timeout_seconds

    def download(
        self, *, doi: str, article_dir: Path
    ) -> tuple[str, list[SupplementArtifact], list[Attempt]]:
        page_url = (
            f"https://advanced.onlinelibrary.wiley.com/doi/{doi}"
            if doi.startswith("10.1002/")
            else f"https://www.nature.com/articles/{doi.split('/', 1)[1]}"
            if doi.startswith("10.1038/")
            else f"https://doi.org/{doi}"
        )
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; AutoPaper/0.1)"})
        try:
            response = session.get(page_url, timeout=self.timeout_seconds)
            html = response.text
            if response.status_code in {401, 403, 429} or "just a moment" in html[:600].casefold():
                return self._browser_or_auth(
                    doi=doi, article_dir=article_dir, session=session, page_url=page_url
                )
            response.raise_for_status()
        except requests.RequestException as exc:
            if self.profile_dir and _read_cdp_endpoint(self.profile_dir):
                return self._browser_or_auth(
                    doi=doi, article_dir=article_dir, session=session, page_url=page_url
                )
            return (
                "page_unavailable",
                [],
                [
                    Attempt(
                        source="supplement:http",
                        url=page_url,
                        success=False,
                        reason=f"page_error:{type(exc).__name__}",
                    )
                ],
            )
        parser = _SupplementLinks(response.url)
        parser.feed(html)
        urls = list(dict.fromkeys(parser.urls))
        if not urls:
            confirmed_none = parser.confirmed_no_supplements or _page_confirms_no_supplements(html)
            if not confirmed_none and self.profile_dir and _read_cdp_endpoint(self.profile_dir):
                return self._browser_or_auth(
                    doi=doi, article_dir=article_dir, session=session, page_url=page_url
                )
            return (
                "not_found" if confirmed_none else "unconfirmed",
                [],
                [],
            )
        return self._download_urls(session, urls, article_dir)

    def _browser_or_auth(
        self, *, doi: str, article_dir: Path, session: requests.Session, page_url: str
    ) -> tuple[str, list[SupplementArtifact], list[Attempt]]:
        endpoint = _read_cdp_endpoint(self.profile_dir) if self.profile_dir else ""
        if not endpoint:
            return (
                "challenge_required",
                [],
                [
                    Attempt(
                        source="supplement:browser",
                        url=page_url,
                        success=False,
                        reason="challenge_required",
                    )
                ],
            )
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(endpoint, timeout=10000)
                if not browser.contexts:
                    raise RuntimeError("浏览器没有可用会话")
                context = browser.contexts[0]
                from .visible_browser import work_page
                page = work_page(context, self.profile_dir)
                current_url = page.url.casefold()
                article_page = doi.casefold() in current_url and not any(
                    marker in current_url
                    for marker in ("/doi/pdf/", "/doi/epdf/", "/articlepdf/", "/pdf/")
                )
                if page.url != page_url and not article_page:
                    page.goto(
                        page_url,
                        wait_until="domcontentloaded",
                        timeout=int(self.timeout_seconds * 1000),
                    )
                html = page.content()
                from .browser import classify_page
                page_status = classify_page(page)
                if page_status in {"challenge_required", "authentication_required"}:
                    return (
                        page_status,
                        [],
                        [Attempt(
                            source="supplement:browser", url=page.url,
                            success=False, reason=page_status, final_url=page.url,
                        )],
                    )
                parser = _SupplementLinks(page.url)
                parser.feed(html)
                urls = list(dict.fromkeys(parser.urls))
                if not urls:
                    confirmed_none = (
                        parser.confirmed_no_supplements or _page_confirms_no_supplements(html)
                    )
                    return (
                        "not_found" if confirmed_none else "unconfirmed",
                        [],
                        [],
                    )
                return self._download_browser_urls(page, urls, article_dir)
        except Exception as exc:  # noqa: BLE001
            return (
                "browser_error",
                [],
                [
                    Attempt(
                        source="supplement:browser",
                        url=page_url,
                        success=False,
                        reason=f"browser_error:{type(exc).__name__}",
                        response_status=type(exc).__name__,
                    )
                ],
            )

    def _download_browser_urls(
        self, page: object, urls: list[str], article_dir: Path
    ) -> tuple[str, list[SupplementArtifact], list[Attempt]]:
        """让已授权的浏览器发起下载，并把浏览器缓存文件复制到目标目录。"""
        artifacts: list[SupplementArtifact] = []
        attempts: list[Attempt] = []
        supplement_dir = article_dir / "supplements"
        for index, url in enumerate(urls, start=1):
            temporary: Path | None = None
            try:
                response = page.evaluate(
                    """async url => {
                      const reply = await fetch(url, {credentials: 'include'});
                      if (!reply.ok || !reply.body) return {status: reply.status};
                      window.__autopaper_si_reader = reply.body.getReader();
                      return {
                        status: reply.status,
                        contentType: reply.headers.get('content-type') || '',
                        disposition: reply.headers.get('content-disposition') || '',
                        finalUrl: reply.url
                      };
                    }""",
                    url,
                )
                if int(response["status"]) >= 400:
                    raise ValueError(f"http_{response['status']}")
                content_type = str(response.get("contentType") or "").split(";", 1)[0].lower()
                name = _supplement_filename(
                    url=str(response.get("finalUrl") or url),
                    disposition=str(response.get("disposition") or ""),
                    content_type=content_type,
                    index=index,
                )
                supplement_dir.mkdir(parents=True, exist_ok=True)
                temporary = supplement_dir / f".{name}.{index}.part"
                with temporary.open("wb") as output:
                    while True:
                        chunk = page.evaluate(
                            """async () => {
                              const item = await window.__autopaper_si_reader.read();
                              if (item.done) return null;
                              const bytes = item.value;
                              let text = '';
                              for (let i = 0; i < bytes.length; i += 32768) {
                                text += String.fromCharCode(...bytes.subarray(i, i + 32768));
                              }
                              return btoa(text);
                            }"""
                        )
                        if chunk is None:
                            break
                        output.write(base64.b64decode(chunk, validate=True))
                page.evaluate("() => { delete window.__autopaper_si_reader; }")
                if not _valid_payload(temporary, content_type, name):
                    raise ValueError("invalid_attachment")
                digest = _digest(temporary)
                destination = supplement_dir / name
                if destination.exists():
                    if (
                        _valid_payload(destination, content_type, name)
                        and _digest(destination) == digest
                    ):
                        status = "cached"
                        temporary.unlink()
                    else:
                        destination = (
                            supplement_dir / f"{Path(name).stem}-{digest[:12]}{Path(name).suffix}"
                        )
                        if destination.exists():
                            if (
                                not _valid_payload(destination, content_type, name)
                                or _digest(destination) != digest
                            ):
                                raise ValueError("filename_collision")
                            status = "cached"
                            temporary.unlink()
                        else:
                            temporary.replace(destination)
                            status = "downloaded"
                else:
                    temporary.replace(destination)
                    status = "downloaded"
                artifacts.append(
                    SupplementArtifact(
                        name=destination.name,
                        path=str(destination),
                        url=url,
                        content_type=content_type or "application/octet-stream",
                        bytes_written=destination.stat().st_size,
                        sha256=digest,
                    )
                )
                attempts.append(
                    Attempt(
                        source="supplement:browser",
                        url=url,
                        success=True,
                        reason=status,
                        final_url=url,
                        bytes_written=destination.stat().st_size,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                attempts.append(
                    Attempt(
                        source="supplement:browser",
                        url=url,
                        success=False,
                        reason=f"browser_download_error:{type(exc).__name__}:{exc}",
                    )
                )
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        if any(not item.success for item in attempts):
            return "partial" if artifacts else "not_downloadable", artifacts, attempts
        return (
            (
                "cached"
                if artifacts and all(item.reason == "cached" for item in attempts)
                else "downloaded"
            ),
            artifacts,
            attempts,
        )

    def _download_urls(
        self, session: requests.Session, urls: list[str], article_dir: Path
    ) -> tuple[str, list[SupplementArtifact], list[Attempt]]:
        artifacts: list[SupplementArtifact] = []
        attempts: list[Attempt] = []
        supplement_dir = article_dir / "supplements"
        for index, url in enumerate(urls, start=1):
            try:
                with session.get(url, timeout=self.timeout_seconds, stream=True) as response:
                    if response.status_code in {401, 403, 429}:
                        attempts.append(
                            Attempt(
                                source="supplement:http",
                                url=url,
                                success=False,
                                reason="authentication_required"
                                if response.status_code == 401
                                else "challenge_required",
                                status_code=response.status_code,
                            )
                        )
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    name = _supplement_filename(
                        url=response.url,
                        disposition=response.headers.get("content-disposition", ""),
                        content_type=content_type,
                        index=index,
                    )
                    supplement_dir.mkdir(parents=True, exist_ok=True)
                    destination = supplement_dir / name
                    temporary = supplement_dir / f".{name}.{index}.part"
                    try:
                        with temporary.open("wb") as output:
                            for chunk in response.iter_content(chunk_size=1024 * 1024):
                                if chunk:
                                    output.write(chunk)
                        if not _valid_payload(temporary, content_type, name):
                            raise ValueError("invalid_attachment")
                        incoming_digest = _digest(temporary)
                        if destination.exists():
                            if (
                                _valid_payload(destination, content_type)
                                and _digest(destination) == incoming_digest
                            ):
                                status = "cached"
                                temporary.unlink()
                            else:
                                destination = (
                                    supplement_dir
                                    / f"{Path(name).stem}-{incoming_digest[:12]}{Path(name).suffix}"
                                )
                                if (
                                    destination.exists()
                                    and _valid_payload(destination, content_type)
                                    and _digest(destination) == incoming_digest
                                ):
                                    status = "cached"
                                    temporary.unlink()
                                elif destination.exists():
                                    raise ValueError("filename_collision")
                                else:
                                    temporary.replace(destination)
                                    status = "downloaded"
                        else:
                            temporary.replace(destination)
                            status = "downloaded"
                    finally:
                        temporary.unlink(missing_ok=True)
                    artifacts.append(
                        SupplementArtifact(
                            name=destination.name,
                            path=str(destination),
                            url=response.url,
                            content_type=content_type,
                            bytes_written=destination.stat().st_size,
                            sha256=incoming_digest,
                        )
                    )
                    attempts.append(
                        Attempt(
                            source="supplement:http",
                            url=url,
                            success=True,
                            reason=status,
                            status_code=response.status_code,
                            content_type=content_type,
                            final_url=response.url,
                            bytes_written=destination.stat().st_size,
                        )
                    )
            except (requests.RequestException, OSError, ValueError) as exc:
                attempts.append(
                    Attempt(
                        source="supplement:http",
                        url=url,
                        success=False,
                        reason=f"download_error:{type(exc).__name__}",
                    )
                )
        if any(not item.success for item in attempts):
            if any(
                item.reason in {"challenge_required", "authentication_required"}
                for item in attempts
            ):
                return "challenge_required", artifacts, attempts
            return "partial" if artifacts else "not_downloadable", artifacts, attempts
        return (
            (
                "cached"
                if artifacts and all(item.reason == "cached" for item in attempts)
                else "downloaded"
            ),
            artifacts,
            attempts,
        )


def _read_cdp_endpoint(profile_dir: Path) -> str:
    try:
        payload = json.loads((profile_dir / "auth-state.json").read_text(encoding="utf-8"))
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
                browser = playwright.chromium.connect_over_cdp(endpoint, timeout=self.timeout_ms)
                if not browser.contexts:
                    return "browser_session_required", [], []
                context = browser.contexts[0]
                from .visible_browser import work_page
                page = work_page(context, self.profile_dir)
                return self._download_from_page(
                    page=page,
                    doi=doi,
                    article_dir=article_dir,
                )
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
        if doi.casefold() not in str(getattr(page, "url", "")).casefold():
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
        urls = list(dict.fromkeys(str(url) for url in urls))
        if not urls:
            return "unconfirmed", [], []

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
