"""使用用户本人已有订阅会话的浏览器下载兜底。"""

from __future__ import annotations

import base64
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from .models import Attempt
from .transport import is_valid_pdf

LOGGER = logging.getLogger(__name__)

CHALLENGE_MARKERS = (
    "just a moment",
    "captcha",
    "cloudflare",
    "ray id",
    "正在进行安全验证",
    "安全验证",
)
LOGIN_URL_MARKERS = ("login", "signin", "sso", "shibboleth", "authorize")
SUBSCRIPTION_MARKERS = (
    "available to purchase",
    "pay-per-view",
    "buy this article",
    "you do not currently have access",
)
PDF_SELECTORS = (
    'a[href*="/doi/pdf/"]',
    'a[href*="/doi/pdfdirect/"]',
    'iframe[src*="/doi/pdfdirect/"]',
    'a[href*="/content/pdf/"]',
    'a[href*="/content/articlepdf/"]',
    'a[href*="/article-pdf/"]',
    'a[aria-label*="PDF" i]',
    'a[title*="PDF" i]',
)


def browser_proxy_from_environment() -> dict[str, str] | None:
    """把常见代理环境变量转换为 Playwright 浏览器代理参数。"""
    server = os.getenv("HTTPS_PROXY") or os.getenv("ALL_PROXY") or os.getenv("HTTP_PROXY")
    if not server:
        return None
    proxy = {"server": server}
    bypass = os.getenv("NO_PROXY")
    if bypass:
        proxy["bypass"] = bypass
    return proxy


def _launch_options(*, headless: bool, channel: str | None) -> dict[str, object]:
    options: dict[str, object] = {
        "headless": headless,
        "accept_downloads": True,
        "args": ["--disable-extensions"],
    }
    if channel:
        options["channel"] = channel
    proxy = browser_proxy_from_environment()
    if proxy:
        options["proxy"] = proxy
    return options


class ProfileInUseError(RuntimeError):
    """浏览器配置目录已被另一个下载器进程占用。"""


class ProfileLock:
    """用独占锁文件避免并发打开同一浏览器配置目录。"""

    def __init__(self, profile_dir: Path) -> None:
        self.profile_dir = Path(profile_dir)
        self.lock_path = self.profile_dir / ".doi-harvester.lock"
        self._acquired = False

    def __enter__(self) -> ProfileLock:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            descriptor = os.open(self.lock_path, flags)
        except FileExistsError as exc:
            raise ProfileInUseError(
                f"浏览器配置目录正在使用：{self.profile_dir}；请关闭另一个任务后重试。"
            ) from exc
        try:
            payload = f"pid={os.getpid()}\ncreated={datetime.now(UTC).isoformat()}\n"
            os.write(descriptor, payload.encode("utf-8"))
        finally:
            os.close(descriptor)
        self._acquired = True
        return self

    def __exit__(self, *_args: object) -> None:
        if self._acquired:
            self.lock_path.unlink(missing_ok=True)
            self._acquired = False


@dataclass(frozen=True, slots=True)
class AuthorizationResult:
    """一次浏览器授权初始化的结果。"""

    success: bool
    status: str
    final_url: str
    profile_dir: Path
    reason: str = ""
    cdp_endpoint: str = ""
    browser_pid: int | None = None
    publisher: str = ""
    probe_doi: str = ""
    connection_mode: str = ""
    browser_restarted: bool = False
    challenge_status: str = "unknown"
    institution_status: str = "unknown"
    article_status: str = "unknown"
    wait_seconds: float = 0.0
    state_transitions: tuple[str, ...] = ()
    cookie_metadata: tuple[dict[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["profile_dir"] = str(self.profile_dir)
        return payload


def _page_signals(page: object) -> tuple[str, str, str]:
    title = page.title().lower()
    url = str(page.url).lower()
    try:
        body_text = page.locator("body").inner_text(timeout=3000).lower()
    except Exception:  # noqa: BLE001
        body_text = ""
    return title, body_text, url


def _pdf_link_is_visible(page: object) -> bool:
    for selector in PDF_SELECTORS:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def classify_page(page: object) -> str:
    """把当前页面归类为挑战、登录、订阅不足或可下载状态。"""
    title, body_text, url = _page_signals(page)
    if any(marker in title or marker in body_text for marker in CHALLENGE_MARKERS):
        return "challenge_required"
    if any(marker in url for marker in LOGIN_URL_MARKERS):
        return "authentication_required"
    if any(marker in body_text for marker in SUBSCRIPTION_MARKERS):
        return "subscription_required"
    if _pdf_link_is_visible(page):
        return "ready"
    return "authenticated"


def split_access_state(status: str) -> tuple[str, str, str]:
    """把页面结果拆分为验证、机构登录和单篇访问状态。"""
    if status == "challenge_required":
        return "required", "unknown", "unknown"
    if status == "authentication_required":
        return "clear", "required", "unknown"
    if status == "subscription_required":
        return "clear", "active", "subscription_required"
    if status == "ready":
        return "clear", "active", "ready"
    if status == "authenticated":
        return "clear", "active", "unknown"
    return "unknown", "unknown", "unknown"


def _safe_page_url(value: str) -> str:
    """移除可能携带授权令牌的查询参数和片段。"""
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _cookie_metadata(context: object) -> tuple[dict[str, object], ...]:
    """只记录会话相关 Cookie 的名称、域和到期时间。"""
    try:
        cookies = context.cookies()
    except Exception:  # noqa: BLE001
        return ()
    markers = ("cf_", "clearance", "auth", "session", "shib", "sso")
    metadata = []
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        if not any(marker in name.casefold() for marker in markers):
            continue
        metadata.append(
            {
                "name": name,
                "domain": str(cookie.get("domain") or ""),
                "expires": cookie.get("expires"),
            }
        )
    return tuple(metadata)


def _prior_ready_probe(profile_dir: Path, publisher: str) -> str:
    """优先复用同一出版社最近验证成功的 DOI。"""
    try:
        payload = json.loads((profile_dir / "auth-state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if payload.get("publisher") != publisher or payload.get("article_status") != "ready":
        return ""
    return str(payload.get("probe_doi") or "")


def wait_for_authorization(
    page: object,
    *,
    timeout_seconds: float,
    stable_checks: int = 3,
    poll_ms: int = 1000,
) -> str:
    """等待用户完成安全验证/SSO，直到 PDF 入口连续稳定出现。"""
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    ready_count = 0
    last_status = classify_page(page)
    previous_status = ""

    while True:
        status = classify_page(page)
        last_status = status
        if status != previous_status:
            LOGGER.info("浏览器授权状态：%s", status)
            previous_status = status

        if status == "ready":
            ready_count += 1
            if ready_count >= max(stable_checks, 1):
                return "ready"
        else:
            ready_count = 0
        if status == "subscription_required":
            return status

        if time.monotonic() >= deadline:
            return last_status
        page.wait_for_timeout(max(poll_ms, 1))


def browser_executable_path(channel: str | None) -> Path | None:
    """根据浏览器通道定位本机 Chrome/Edge 可执行文件。"""
    resolved_channel = channel or BrowserPdfDownloader._default_channel()
    if platform.system() == "Windows":
        candidates: dict[str, tuple[Path, ...]] = {
            "chrome": (
                Path(os.getenv("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
                Path(os.getenv("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
            ),
            "msedge": (
                Path(os.getenv("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
                Path(os.getenv("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
                Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
            ),
        }
        for path in candidates.get(resolved_channel or "", ()):
            if path.is_file():
                return path
        return None
    executable = shutil.which(resolved_channel or "chromium")
    return Path(executable) if executable else None


def _find_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _read_cdp_endpoint(profile_dir: Path) -> str:
    state_path = profile_dir / "auth-state.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("cdp_endpoint") or "")


def _read_auth_state(profile_dir: Path) -> dict[str, object]:
    try:
        payload = json.loads((profile_dir / "auth-state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_session_state(
    profile_dir: Path,
    *,
    context: object,
    connection_mode: str,
    browser_restarted: bool,
    result: Attempt,
) -> None:
    """记录下载会话诊断，不保存 Cookie 值或授权查询参数。"""
    path = profile_dir / "session-state.json"
    payload = {
        "schema_version": 1,
        "checked_at": datetime.now(UTC).isoformat(),
        "connection_mode": connection_mode,
        "browser_restarted": browser_restarted,
        "result": result.reason,
        "final_url": _safe_page_url(result.final_url),
        "cookie_metadata": _cookie_metadata(context),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _content_pages(context: object) -> list[object]:
    """过滤浏览器扩展、开发者工具等非网页标签。"""
    return [
        page
        for page in context.pages
        if str(getattr(page, "url", "")).lower().startswith(("http://", "https://"))
    ]


def _content_page_or_new(context: object) -> object:
    """优先复用最新网页标签；不存在时创建空白网页标签。"""
    pages = _content_pages(context)
    return pages[-1] if pages else context.new_page()


def _content_surfaces(context: object) -> list[object]:
    """返回网页标签及其子框架，供内嵌 PDF 查看器检测。"""
    surfaces: list[object] = []
    for page in _content_pages(context):
        surfaces.append(page)
        surfaces.extend(getattr(page, "frames", []))
    return surfaces


def _looks_like_article_pdf_url(url: str) -> bool:
    """只接受出版社正文路径，排除参考文献或广告中的任意 PDF。"""
    lowered = url.lower()
    markers = (
        "/doi/pdf/",
        "/doi/epdf/",
        "/doi/pdfdirect/",
        "/content/pdf/",
        "/content/articlepdf/",
        "/article-pdf/",
    )
    return any(marker in lowered for marker in markers)


def _matches_expected_article_url(url: str, expected_urls: set[str]) -> bool:
    """允许同一 DOI 在出版社不同子域和 PDF 路径间跳转。"""
    if url in expected_urls:
        return True

    def doi_tokens(value: str) -> set[str]:
        decoded = unquote(value).lower()
        return set(re.findall(r"10\.\d{4,9}/[^?#\s]+", decoded))

    actual_tokens = doi_tokens(url)
    expected_tokens = set().union(*(doi_tokens(item) for item in expected_urls))
    return bool(actual_tokens & expected_tokens)


class BrowserAuthorizer:
    """在专用持久化浏览器配置中初始化合法出版社会话。"""

    publisher_probe_dois = {
        "acs": "10.1021/acs.chemmater.9b01639",
        "elsevier": "10.1016/j.watres.2024.121507",
        "rsc": "10.1039/c9ta10964a",
    }

    def __init__(
        self,
        *,
        profile_dir: Path | None = None,
        channel: str | None = None,
        navigation_timeout_seconds: float = 60.0,
        cdp: bool = False,
    ) -> None:
        self.profile_dir = profile_dir or BrowserPdfDownloader._default_profile_dir()
        self.channel = channel if channel is not None else BrowserPdfDownloader._default_channel()
        self.navigation_timeout_ms = int(navigation_timeout_seconds * 1000)
        self.cdp = cdp

    def authorize(
        self,
        *,
        publisher: str,
        doi: str | None,
        timeout_seconds: float,
    ) -> AuthorizationResult:
        """打开可见浏览器并等待用户本人完成站点验证与机构登录。"""
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return AuthorizationResult(
                success=False,
                status="playwright_not_installed",
                final_url="",
                profile_dir=self.profile_dir,
                reason="请安装 browser 可选依赖。",
            )

        probe_doi = (
            doi
            or _prior_ready_probe(self.profile_dir, publisher)
            or self.publisher_probe_dois.get(publisher)
        )
        if not probe_doi:
            return AuthorizationResult(
                success=False,
                status="unsupported_publisher",
                final_url="",
                profile_dir=self.profile_dir,
            )
        target_url = (
            probe_doi
            if probe_doi.lower().startswith(("http://", "https://"))
            else f"https://doi.org/{probe_doi}"
        )

        if self.cdp:
            return self._authorize_with_cdp(
                sync_playwright=sync_playwright,
                target_url=target_url,
                timeout_seconds=timeout_seconds,
                publisher=publisher,
                probe_doi=probe_doi,
            )

        try:
            with ProfileLock(self.profile_dir), sync_playwright() as playwright:
                launch_options = _launch_options(headless=False, channel=self.channel)
                context = playwright.chromium.launch_persistent_context(
                    str(self.profile_dir), **launch_options
                )
                try:
                    page = _content_page_or_new(context)
                    page.set_default_timeout(self.navigation_timeout_ms)
                    page.goto(
                        target_url,
                        wait_until="domcontentloaded",
                        timeout=self.navigation_timeout_ms,
                    )
                    LOGGER.warning(
                        "请在打开的浏览器中完成站点安全验证和机构登录；程序会自动检测结果。"
                    )
                    initial_status = classify_page(page)
                    started = time.monotonic()
                    status = wait_for_authorization(
                        page,
                        timeout_seconds=timeout_seconds,
                    )
                    challenge_status, institution_status, article_status = split_access_state(
                        status
                    )
                    result = AuthorizationResult(
                        success=status == "ready",
                        status=status,
                        final_url=page.url,
                        profile_dir=self.profile_dir,
                        publisher=publisher,
                        probe_doi=probe_doi,
                        connection_mode="persistent_context",
                        browser_restarted=True,
                        challenge_status=challenge_status,
                        institution_status=institution_status,
                        article_status=article_status,
                        wait_seconds=round(time.monotonic() - started, 3),
                        state_transitions=tuple(dict.fromkeys((initial_status, status))),
                        cookie_metadata=_cookie_metadata(context),
                    )
                    self._write_state(result)
                    return result
                finally:
                    context.close()
        except PlaywrightTimeoutError:
            result = AuthorizationResult(
                success=False,
                status="authorization_timeout",
                final_url=target_url,
                profile_dir=self.profile_dir,
            )
            self._write_state(result)
            return result
        except ProfileInUseError:
            raise
        except Exception as exc:  # noqa: BLE001
            result = AuthorizationResult(
                success=False,
                status=f"browser_error:{type(exc).__name__}",
                final_url=target_url,
                profile_dir=self.profile_dir,
                reason=str(exc),
            )
            self._write_state(result)
            return result

    def _authorize_with_cdp(
        self,
        *,
        sync_playwright: object,
        target_url: str,
        timeout_seconds: float,
        publisher: str,
        probe_doi: str,
    ) -> AuthorizationResult:
        existing_state = _read_auth_state(self.profile_dir)
        existing_endpoint = str(existing_state.get("cdp_endpoint") or "")
        if existing_endpoint:
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.connect_over_cdp(
                        existing_endpoint,
                        timeout=self.navigation_timeout_ms,
                    )
                    if browser.contexts:
                        context = browser.contexts[0]
                        page = _content_page_or_new(context)
                        page.set_default_timeout(self.navigation_timeout_ms)
                        page.goto(
                            target_url,
                            wait_until="domcontentloaded",
                            timeout=self.navigation_timeout_ms,
                        )
                        initial_status = classify_page(page)
                        started = time.monotonic()
                        status = wait_for_authorization(page, timeout_seconds=timeout_seconds)
                        challenge_status, institution_status, article_status = split_access_state(
                            status
                        )
                        result = AuthorizationResult(
                            success=status == "ready",
                            status=status,
                            final_url=page.url,
                            profile_dir=self.profile_dir,
                            cdp_endpoint=existing_endpoint,
                            browser_pid=int(existing_state.get("browser_pid") or 0) or None,
                            publisher=publisher,
                            probe_doi=probe_doi,
                            connection_mode="cdp_reused",
                            browser_restarted=False,
                            challenge_status=challenge_status,
                            institution_status=institution_status,
                            article_status=article_status,
                            wait_seconds=round(time.monotonic() - started, 3),
                            state_transitions=tuple(dict.fromkeys((initial_status, status))),
                            cookie_metadata=_cookie_metadata(context),
                        )
                        self._write_state(result)
                        return result
            except Exception as exc:  # noqa: BLE001
                LOGGER.info("现有 CDP 会话不可用，将使用原配置目录重启浏览器：%s", exc)

        executable = browser_executable_path(self.channel)
        if executable is None:
            result = AuthorizationResult(
                success=False,
                status="browser_executable_not_found",
                final_url=target_url,
                profile_dir=self.profile_dir,
            )
            self._write_state(result)
            return result

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        port = _find_free_local_port()
        endpoint = f"http://127.0.0.1:{port}"
        command = [
            str(executable),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={self.profile_dir.resolve()}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
        ]
        proxy = browser_proxy_from_environment()
        if proxy:
            command.append(f"--proxy-server={proxy['server']}")
            if proxy.get("bypass"):
                command.append(f"--proxy-bypass-list={proxy['bypass'].replace(',', ';')}")
        command.append(target_url)

        process = subprocess.Popen(  # noqa: S603
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            with sync_playwright() as playwright:
                deadline = time.monotonic() + 20
                browser = None
                while browser is None and time.monotonic() < deadline:
                    try:
                        browser = playwright.chromium.connect_over_cdp(
                            endpoint,
                            timeout=1000,
                        )
                    except Exception:  # noqa: BLE001
                        time.sleep(0.25)
                if browser is None:
                    raise RuntimeError("无法连接普通 Chrome 的本地调试端口")
                if not browser.contexts:
                    raise RuntimeError("普通 Chrome 未提供默认浏览器上下文")
                context = browser.contexts[0]
                page = _content_page_or_new(context)
                page.set_default_timeout(self.navigation_timeout_ms)
                if page.url in {"", "about:blank", "chrome://newtab/"}:
                    page.goto(
                        target_url,
                        wait_until="domcontentloaded",
                        timeout=self.navigation_timeout_ms,
                    )
                # 先保存可复用端点；即使授权等待被中止，后续任务仍可连接同一浏览器。
                self._write_state(
                    AuthorizationResult(
                        success=False,
                        status="authorization_pending",
                        final_url=page.url,
                        profile_dir=self.profile_dir,
                        cdp_endpoint=endpoint,
                        browser_pid=process.pid,
                        publisher=publisher,
                        probe_doi=probe_doi,
                        connection_mode="cdp",
                        browser_restarted=True,
                    )
                )
                LOGGER.warning(
                    "普通 Chrome 已保持打开；请完成站点验证和机构登录，程序会自动检测结果。"
                )
                initial_status = classify_page(page)
                started = time.monotonic()
                status = wait_for_authorization(
                    page,
                    timeout_seconds=timeout_seconds,
                )
                challenge_status, institution_status, article_status = split_access_state(status)
                result = AuthorizationResult(
                    success=status == "ready",
                    status=status,
                    final_url=page.url,
                    profile_dir=self.profile_dir,
                    cdp_endpoint=endpoint,
                    browser_pid=process.pid,
                    publisher=publisher,
                    probe_doi=probe_doi,
                    connection_mode="cdp",
                    browser_restarted=True,
                    challenge_status=challenge_status,
                    institution_status=institution_status,
                    article_status=article_status,
                    wait_seconds=round(time.monotonic() - started, 3),
                    state_transitions=tuple(dict.fromkeys((initial_status, status))),
                    cookie_metadata=_cookie_metadata(context),
                )
                self._write_state(result)
                return result
        except Exception as exc:  # noqa: BLE001
            result = AuthorizationResult(
                success=False,
                status=f"cdp_error:{type(exc).__name__}",
                final_url=target_url,
                profile_dir=self.profile_dir,
                reason=str(exc),
                cdp_endpoint=endpoint,
                browser_pid=process.pid,
            )
            self._write_state(result)
            return result

    def _write_state(self, result: AuthorizationResult) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        path = self.profile_dir / "auth-state.json"
        temporary = path.with_suffix(".json.part")
        payload = {
            "schema_version": 1,
            "checked_at": datetime.now(UTC).isoformat(),
            **result.to_dict(),
        }
        payload["final_url"] = _safe_page_url(str(payload.get("final_url") or ""))
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


class BrowserPdfDownloader:
    """通过 Playwright 持久化浏览器配置获取正文 PDF。"""

    selectors = PDF_SELECTORS

    def __init__(
        self,
        *,
        profile_dir: Path | None = None,
        channel: str | None = None,
        headless: bool = False,
        timeout_seconds: float = 60.0,
        interactive_wait_seconds: float = 0.0,
        challenge_policy: str = "skip",
        challenge_timeout_seconds: float = 600.0,
    ) -> None:
        self.profile_dir = profile_dir or self._default_profile_dir()
        self.channel = channel if channel is not None else self._default_channel()
        self.headless = headless
        self.timeout_ms = int(timeout_seconds * 1000)
        self.interactive_wait_seconds = max(interactive_wait_seconds, 0.0)
        if challenge_policy not in {"pause", "skip", "fail-fast"}:
            raise ValueError(f"未知验证页处理策略：{challenge_policy}")
        self.challenge_policy = challenge_policy
        self.challenge_timeout_seconds = max(challenge_timeout_seconds, 0.0)
        if self.interactive_wait_seconds > 0:
            self.challenge_policy = "pause"
            self.challenge_timeout_seconds = self.interactive_wait_seconds

    def download(
        self,
        *,
        doi: str,
        destination: Path,
        candidate_urls: list[str],
    ) -> Attempt:
        """打开 DOI 页面并复用同一浏览器上下文中的 Cookie 下载正文。"""
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return Attempt(
                source="browser",
                url=f"https://doi.org/{doi}",
                success=False,
                reason="playwright_not_installed",
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(f"{destination.suffix}.part")
        temporary.unlink(missing_ok=True)
        doi_url = f"https://doi.org/{doi}"

        try:
            with sync_playwright() as playwright:
                cdp_endpoint = _read_cdp_endpoint(self.profile_dir)
                if cdp_endpoint:
                    try:
                        browser = playwright.chromium.connect_over_cdp(
                            cdp_endpoint,
                            timeout=self.timeout_ms,
                        )
                        if browser.contexts:
                            context = browser.contexts[0]
                            result = self._download_in_context(
                                context=context,
                                doi=doi,
                                destination=destination,
                                temporary=temporary,
                                candidate_urls=candidate_urls,
                            )
                            _write_session_state(
                                self.profile_dir,
                                context=context,
                                connection_mode="cdp_reused",
                                browser_restarted=False,
                                result=result,
                            )
                            return result
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.warning("现有 CDP 浏览器不可用，改用持久化启动：%s", exc)

                profile_lock = ProfileLock(self.profile_dir)
                profile_lock.__enter__()
                launch_options = _launch_options(
                    headless=self.headless,
                    channel=self.channel,
                )
                try:
                    context = playwright.chromium.launch_persistent_context(
                        str(self.profile_dir), **launch_options
                    )
                    try:
                        result = self._download_in_context(
                            context=context,
                            doi=doi,
                            destination=destination,
                            temporary=temporary,
                            candidate_urls=candidate_urls,
                        )
                        _write_session_state(
                            self.profile_dir,
                            context=context,
                            connection_mode="persistent_context",
                            browser_restarted=True,
                            result=result,
                        )
                        return result
                    finally:
                        context.close()
                finally:
                    profile_lock.__exit__(None, None, None)
        except PlaywrightTimeoutError:
            return Attempt(
                source="browser",
                url=doi_url,
                success=False,
                reason="browser_timeout",
            )
        except ProfileInUseError:
            raise
        except Exception as exc:  # noqa: BLE001
            return Attempt(
                source="browser",
                url=doi_url,
                success=False,
                reason=f"browser_error:{type(exc).__name__}",
            )
        finally:
            temporary.unlink(missing_ok=True)

    def _download_in_context(
        self,
        *,
        context: object,
        doi: str,
        destination: Path,
        temporary: Path,
        candidate_urls: list[str],
    ) -> Attempt:
        doi_url = f"https://doi.org/{doi}"
        # 复用同一网页标签，避免批量下载时不断抢占前台并积累验证页。
        page = _content_page_or_new(context)
        page.set_default_timeout(self.timeout_ms)
        page.goto(doi_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        page.wait_for_timeout(3000)

        page_status = classify_page(page)
        if (
            page_status in {"challenge_required", "authentication_required"}
            and not self.headless
            and self.challenge_policy == "pause"
            and self.challenge_timeout_seconds
        ):
            LOGGER.warning(
                "浏览器正在等待用户完成站点验证/登录（最多 %.0f 秒）。",
                self.challenge_timeout_seconds,
            )
            page_status = wait_for_authorization(
                page,
                timeout_seconds=self.challenge_timeout_seconds,
            )
        if page_status in {"challenge_required", "authentication_required"}:
            return Attempt(
                source="browser",
                url=doi_url,
                success=False,
                reason=page_status,
                final_url=page.url,
            )

        urls = self._collect_urls(page=page, candidate_urls=candidate_urls)
        for url in urls:
            response = context.request.get(
                url,
                headers={
                    "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
                    "Referer": page.url,
                },
                timeout=self.timeout_ms,
                fail_on_status_code=False,
            )
            body = response.body()
            if response.ok and b"%PDF-" in body[:1024] and len(body) >= 1024:
                temporary.write_bytes(body)
                temporary.replace(destination)
                return Attempt(
                    source="browser",
                    url=url,
                    success=True,
                    reason="downloaded",
                    status_code=response.status,
                    content_type=response.headers.get("content-type", ""),
                    final_url=response.url,
                    bytes_written=len(body),
                )

        surface_result = self._download_from_surfaces(
            context=context,
            current_page=page,
            expected_urls=set(urls),
            destination=destination,
            temporary=temporary,
        )
        if surface_result is not None:
            return surface_result

        click_result = self._download_by_click(page=page, temporary=temporary)
        if click_result and is_valid_pdf(temporary):
            size = temporary.stat().st_size
            temporary.replace(destination)
            return Attempt(
                source="browser",
                url=page.url,
                success=True,
                reason="downloaded",
                final_url=page.url,
                bytes_written=size,
            )

        surface_result = self._download_from_surfaces(
            context=context,
            current_page=page,
            expected_urls=set(urls),
            destination=destination,
            temporary=temporary,
        )
        if surface_result is not None:
            return surface_result

        final_status = classify_page(page)
        reason = (
            final_status
            if final_status
            in {
                "challenge_required",
                "authentication_required",
                "subscription_required",
            }
            else "no_pdf_after_browser"
        )
        return Attempt(
            source="browser",
            url=doi_url,
            success=False,
            reason=reason,
            final_url=page.url,
        )

    def _download_from_surfaces(
        self,
        *,
        context: object,
        current_page: object,
        expected_urls: set[str],
        destination: Path,
        temporary: Path,
    ) -> Attempt | None:
        """从当前标签、弹出的 PDF 标签或内嵌 PDF 框架保存正文。"""
        pdf_pages = [current_page, *reversed(_content_surfaces(context))]
        seen_pages: set[int] = set()
        for pdf_page in pdf_pages:
            page_identity = id(pdf_page)
            if page_identity in seen_pages:
                continue
            seen_pages.add(page_identity)
            page_url = str(pdf_page.url)
            if pdf_page is not current_page and not _matches_expected_article_url(
                page_url, expected_urls
            ):
                continue
            page_pdf = self._fetch_pdf_from_page(page=pdf_page)
            if page_pdf is None:
                continue
            body, status_code, content_type, final_url = page_pdf
            temporary.write_bytes(body)
            temporary.replace(destination)
            return Attempt(
                source="browser",
                url=final_url,
                success=True,
                reason="downloaded",
                status_code=status_code,
                content_type=content_type,
                final_url=final_url,
                bytes_written=len(body),
            )
        return None

    def _collect_urls(self, *, page: object, candidate_urls: list[str]) -> list[str]:
        urls = list(candidate_urls)
        for selector in self.selectors:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 10)):
                item = locator.nth(index)
                href = item.get_attribute("href") or item.get_attribute("src")
                if href:
                    resolved = urljoin(page.url, href)
                    if _looks_like_article_pdf_url(resolved):
                        urls.append(resolved)
        return list(dict.fromkeys(urls))

    def _download_by_click(self, *, page: object, temporary: Path) -> bool:
        for selector in self.selectors:
            if selector.startswith("iframe"):
                continue
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            try:
                with page.expect_download(timeout=min(self.timeout_ms, 20_000)) as download_info:
                    locator.click()
                download_info.value.save_as(str(temporary))
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _fetch_pdf_from_page(
        self,
        *,
        page: object,
    ) -> tuple[bytes, int, str, str] | None:
        """处理出版社把当前标签导航到内嵌 PDF 查看器的情况。"""
        current_url = str(page.url)
        if ".pdf" not in current_url.lower() and not _looks_like_article_pdf_url(current_url):
            return None
        try:
            payload = page.evaluate(
                """
                async () => {
                    const response = await fetch(window.location.href, {credentials: "include"});
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
                        finalUrl: response.url,
                        body: btoa(binary),
                    };
                }
                """
            )
            body = base64.b64decode(payload["body"], validate=True)
            status_code = int(payload["status"])
            content_type = str(payload["contentType"])
            final_url = str(payload["finalUrl"])
        except Exception:  # noqa: BLE001
            return None
        if status_code >= 400 or b"%PDF-" not in body[:1024] or len(body) < 1024:
            return None
        return body, status_code, content_type, final_url

    @staticmethod
    def _default_profile_dir() -> Path:
        runtime_dir = os.getenv("DOI_HARVESTER_RUNTIME_DIR")
        if runtime_dir:
            return Path(runtime_dir).expanduser() / "profiles" / "default"
        if platform.system() == "Windows":
            local_app_data = os.getenv("LOCALAPPDATA")
            if local_app_data:
                return Path(local_app_data) / "doi-harvester" / "browser-profile"
        return Path.home() / ".cache" / "doi-harvester" / "browser-profile"

    @staticmethod
    def _default_channel() -> str | None:
        if platform.system() != "Windows":
            return None
        edge_paths = (
            Path(os.getenv("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.getenv("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
        )
        if any(path.is_file() for path in edge_paths):
            return "msedge"
        chrome_paths = (
            Path(os.getenv("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.getenv("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        )
        return "chrome" if any(path.is_file() for path in chrome_paths) else None
