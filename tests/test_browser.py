import base64
import builtins
import sys
import types
from pathlib import Path

import pytest

from doi_harvester.browser import (
    BrowserAuthorizer,
    BrowserPdfDownloader,
    ProfileInUseError,
    ProfileLock,
    _launch_options,
    _matches_expected_article_url,
    browser_proxy_from_environment,
    classify_page,
    wait_for_authorization,
)


class FakeLocator:
    @property
    def first(self) -> "FakeLocator":
        return self

    def count(self) -> int:
        return 0

    def nth(self, _index: int) -> "FakeLocator":
        return self

    def get_attribute(self, _name: str) -> None:
        return None

    def inner_text(self, **_kwargs: object) -> str:
        return ""


class FakePage:
    url = "https://publisher.test/article"

    def __init__(self, *, title: str = "Article") -> None:
        self._title = title

    def set_default_timeout(self, _timeout: int) -> None:
        return None

    def goto(self, *_args: object, **_kwargs: object) -> None:
        return None

    def wait_for_timeout(self, _timeout: int) -> None:
        return None

    def title(self) -> str:
        return self._title

    def locator(self, _selector: str) -> FakeLocator:
        return FakeLocator()


class SignalLocator(FakeLocator):
    def __init__(self, *, body: str = "", count: int = 0) -> None:
        self.body = body
        self._count = count

    def count(self) -> int:
        return self._count

    def inner_text(self, **_kwargs: object) -> str:
        return self.body


class SignalPage(FakePage):
    def __init__(
        self,
        *,
        title: str = "Article",
        body: str = "",
        url: str = "https://pubs.acs.org/doi/10.1021/example",
        pdf_count: int = 0,
    ) -> None:
        super().__init__(title=title)
        self.body = body
        self.url = url
        self.pdf_count = pdf_count
        self.wait_calls = 0

    def locator(self, selector: str) -> SignalLocator:
        if selector == "body":
            return SignalLocator(body=self.body)
        return SignalLocator(count=self.pdf_count)

    def wait_for_timeout(self, _timeout: int) -> None:
        self.wait_calls += 1


class FakeApiResponse:
    ok = True
    status = 200
    headers = {"content-type": "application/pdf"}
    url = "https://publisher.test/article.pdf"

    def __init__(self, body: bytes) -> None:
        self._body = body

    def body(self) -> bytes:
        return self._body


class FakeRequest:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def get(self, *_args: object, **_kwargs: object) -> FakeApiResponse:
        return FakeApiResponse(self.body)


class FakeContext:
    def __init__(self, *, body: bytes, title: str = "Article") -> None:
        self.title = title
        self.pages = [FakePage(title=title)]
        self.request = FakeRequest(body)
        self.new_page_calls = 0

    def new_page(self) -> FakePage:
        self.new_page_calls += 1
        return FakePage(title=self.title)

    def close(self) -> None:
        return None


class FakeChromium:
    def __init__(self, context: FakeContext) -> None:
        self.context = context

    def launch_persistent_context(self, *_args: object, **_kwargs: object) -> FakeContext:
        return self.context

    def connect_over_cdp(self, *_args: object, **_kwargs: object) -> object:
        return types.SimpleNamespace(contexts=[self.context])


class FakePlaywrightManager:
    def __init__(self, context: FakeContext) -> None:
        self.playwright = types.SimpleNamespace(chromium=FakeChromium(context))

    def __enter__(self) -> object:
        return self.playwright

    def __exit__(self, *_args: object) -> None:
        return None


def install_fake_playwright(
    monkeypatch: pytest.MonkeyPatch,
    *,
    body: bytes,
    title: str = "Article",
    page: FakePage | None = None,
) -> None:
    module = types.ModuleType("playwright.sync_api")
    context = FakeContext(body=body, title=title)
    if page is not None:
        context.pages = [page]
    module.TimeoutError = TimeoutError
    module.sync_playwright = lambda: FakePlaywrightManager(context)
    package = types.ModuleType("playwright")
    package.sync_api = module
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)


def test_browser_reports_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original_import = builtins.__import__

    def rejecting_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("playwright"):
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", rejecting_import)
    downloader = BrowserPdfDownloader(profile_dir=tmp_path / "profile")

    result = downloader.download(
        doi="10.1000/example",
        destination=tmp_path / "article.pdf",
        candidate_urls=[],
    )

    assert result.success is False
    assert result.reason == "playwright_not_installed"


def test_browser_detects_installed_chrome_channel() -> None:
    channel = BrowserPdfDownloader._default_channel()
    assert channel in {"chrome", "msedge", None}


def test_browser_saves_pdf_from_authenticated_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = b"%PDF-1.7\n" + b"x" * 2048
    install_fake_playwright(monkeypatch, body=body)
    destination = tmp_path / "article.pdf"
    downloader = BrowserPdfDownloader(profile_dir=tmp_path / "profile", channel="chrome")

    result = downloader.download(
        doi="10.1000/example",
        destination=destination,
        candidate_urls=["https://publisher.test/article.pdf"],
    )

    assert result.success is True
    assert result.bytes_written == len(body)
    assert destination.read_bytes() == body


def test_browser_surfaces_cloudflare_challenge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_fake_playwright(monkeypatch, body=b"", title="Just a moment...")
    downloader = BrowserPdfDownloader(profile_dir=tmp_path / "profile")

    result = downloader.download(
        doi="10.1000/example",
        destination=tmp_path / "article.pdf",
        candidate_urls=[],
    )

    assert result.success is False
    assert result.reason == "challenge_required"


def test_browser_pause_policy_waits_on_challenge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from doi_harvester import browser as browser_module

    body = b"%PDF-1.7\n" + b"x" * 2048
    page = SignalPage(title="Just a moment...")
    install_fake_playwright(monkeypatch, body=body, page=page)
    waits: list[float] = []

    def fake_wait(_page: object, *, timeout_seconds: float) -> str:
        waits.append(timeout_seconds)
        return "ready"

    monkeypatch.setattr(browser_module, "wait_for_authorization", fake_wait)
    destination = tmp_path / "article.pdf"
    downloader = BrowserPdfDownloader(
        profile_dir=tmp_path / "profile",
        challenge_policy="pause",
        challenge_timeout_seconds=42,
    )

    result = downloader.download(
        doi="10.1000/example",
        destination=destination,
        candidate_urls=["https://publisher.test/article.pdf"],
    )

    assert waits == [42]
    assert result.success is True
    assert destination.read_bytes() == body


def test_browser_reuses_existing_work_page(tmp_path: Path) -> None:
    body = b"%PDF-1.7\n" + b"x" * 2048
    context = FakeContext(body=body)
    destination = tmp_path / "article.pdf"
    downloader = BrowserPdfDownloader(profile_dir=tmp_path / "profile")

    result = downloader._download_in_context(
        context=context,
        doi="10.1000/example",
        destination=destination,
        temporary=tmp_path / "article.pdf.part",
        candidate_urls=["https://publisher.test/article.pdf"],
    )

    assert result.success is True
    assert context.new_page_calls == 0


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        (SignalPage(title="Just a moment..."), "challenge_required"),
        (
            SignalPage(url="https://login.university.edu/shibboleth"),
            "authentication_required",
        ),
        (SignalPage(body="Available to Purchase Pay-Per-View"), "subscription_required"),
        (SignalPage(pdf_count=1), "ready"),
        (SignalPage(), "authenticated"),
    ],
)
def test_classify_page_distinguishes_authorization_states(page: SignalPage, expected: str) -> None:
    assert classify_page(page) == expected


def test_wait_for_authorization_requires_stable_ready_state() -> None:
    page = SignalPage(pdf_count=1)

    status = wait_for_authorization(page, timeout_seconds=1, stable_checks=3, poll_ms=1)

    assert status == "ready"
    assert page.wait_calls == 2


def test_wait_for_authorization_returns_subscription_without_waiting() -> None:
    page = SignalPage(body="Available to Purchase Pay-Per-View")

    status = wait_for_authorization(page, timeout_seconds=600, poll_ms=1)

    assert status == "subscription_required"
    assert page.wait_calls == 0


def test_profile_lock_prevents_concurrent_profile_use(tmp_path: Path) -> None:
    profile_dir = tmp_path / "profile"

    with ProfileLock(profile_dir):
        assert (profile_dir / ".doi-harvester.lock").exists()
        with pytest.raises(ProfileInUseError), ProfileLock(profile_dir):
            pass

    assert not (profile_dir / ".doi-harvester.lock").exists()


def test_default_profile_uses_runtime_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("DOI_HARVESTER_RUNTIME_DIR", str(runtime_dir))

    assert BrowserPdfDownloader._default_profile_dir() == (runtime_dir / "profiles" / "default")


def test_browser_proxy_inherits_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")

    proxy = browser_proxy_from_environment()

    assert proxy == {
        "server": "http://127.0.0.1:7897",
        "bypass": "localhost,127.0.0.1",
    }


def test_launch_options_disable_browser_extensions() -> None:
    options = _launch_options(headless=False, channel="chrome")

    assert "--disable-extensions" in options["args"]


def test_authorizer_waits_for_ready_and_writes_safe_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page = SignalPage(pdf_count=1)
    install_fake_playwright(monkeypatch, body=b"", page=page)
    profile_dir = tmp_path / "profile"
    authorizer = BrowserAuthorizer(profile_dir=profile_dir, channel="chrome")

    result = authorizer.authorize(
        publisher="acs",
        doi="10.1021/example",
        timeout_seconds=1,
    )

    assert result.success is True
    assert result.status == "ready"
    state = (profile_dir / "auth-state.json").read_text(encoding="utf-8")
    assert '"status": "ready"' in state
    assert '"cookie_metadata"' in state
    assert '"value"' not in state.lower()
    assert '"challenge_status": "clear"' in state
    assert '"institution_status": "active"' in state
    assert not (profile_dir / ".doi-harvester.lock").exists()


def test_authorizer_has_rsc_probe_doi() -> None:
    assert BrowserAuthorizer.publisher_probe_dois["rsc"].startswith("10.1039/")


def test_cdp_authorizer_keeps_external_browser_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from doi_harvester import browser as browser_module

    page = SignalPage(pdf_count=1)
    install_fake_playwright(monkeypatch, body=b"", page=page)
    monkeypatch.setattr(
        browser_module,
        "browser_executable_path",
        lambda _channel: Path("chrome.exe"),
    )
    monkeypatch.setattr(browser_module, "_find_free_local_port", lambda: 9222)
    commands: list[list[str]] = []

    def fake_popen(command: list[str], **_kwargs: object) -> object:
        commands.append(command)
        return types.SimpleNamespace(pid=1234)

    monkeypatch.setattr(browser_module.subprocess, "Popen", fake_popen)
    profile_dir = tmp_path / "profile"
    authorizer = BrowserAuthorizer(profile_dir=profile_dir, channel="chrome", cdp=True)

    result = authorizer.authorize(
        publisher="acs",
        doi="10.1021/example",
        timeout_seconds=1,
    )

    assert result.success is True
    assert result.cdp_endpoint == "http://127.0.0.1:9222"
    assert result.browser_pid == 1234
    assert "--disable-extensions" in commands[0]


def test_downloader_reuses_active_cdp_browser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = b"%PDF-1.7\n" + b"x" * 2048
    install_fake_playwright(monkeypatch, body=body)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / "auth-state.json").write_text(
        '{"cdp_endpoint": "http://127.0.0.1:9222"}',
        encoding="utf-8",
    )
    destination = tmp_path / "article.pdf"
    downloader = BrowserPdfDownloader(profile_dir=profile_dir, channel="chrome")

    result = downloader.download(
        doi="10.1000/example",
        destination=destination,
        candidate_urls=["https://publisher.test/article.pdf"],
    )

    assert result.success is True
    assert destination.read_bytes() == body


def test_browser_fetches_pdf_from_embedded_viewer(tmp_path: Path) -> None:
    body = b"%PDF-1.7\n" + b"x" * 2048
    page = FakePage()
    page.url = "https://publisher.test/doi/pdfdirect/10.1000/example"
    page.evaluate = lambda _script: {
        "status": 200,
        "contentType": "application/pdf",
        "finalUrl": page.url,
        "body": base64.b64encode(body).decode("ascii"),
    }
    downloader = BrowserPdfDownloader(profile_dir=tmp_path / "profile")

    result = downloader._fetch_pdf_from_page(page=page)

    assert result == (body, 200, "application/pdf", page.url)


def test_browser_ignores_non_pdf_page(tmp_path: Path) -> None:
    downloader = BrowserPdfDownloader(profile_dir=tmp_path / "profile")

    result = downloader._fetch_pdf_from_page(page=FakePage())

    assert result is None


def test_browser_matches_same_doi_across_wiley_subdomains() -> None:
    expected = {"https://onlinelibrary.wiley.com/doi/pdf/10.1002/aenm.202506351"}

    matched = _matches_expected_article_url(
        "https://advanced.onlinelibrary.wiley.com/doi/pdfdirect/10.1002/aenm.202506351",
        expected,
    )

    assert matched is True


def test_browser_rejects_invalid_embedded_pdf(tmp_path: Path) -> None:
    page = FakePage()
    page.url = "https://publisher.test/article.pdf"
    page.evaluate = lambda _script: {
        "status": 403,
        "contentType": "text/html",
        "finalUrl": page.url,
        "body": base64.b64encode(b"blocked").decode("ascii"),
    }
    downloader = BrowserPdfDownloader(profile_dir=tmp_path / "profile")

    result = downloader._fetch_pdf_from_page(page=page)

    assert result is None


def test_browser_saves_pdf_from_new_viewer_tab(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = b"%PDF-1.7\n" + b"x" * 2048
    pdf_url = "https://publisher.test/article-pdf/example.pdf"
    pdf_page = FakePage()
    pdf_page.url = pdf_url
    pdf_page.evaluate = lambda _script: {
        "status": 200,
        "contentType": "application/pdf",
        "finalUrl": pdf_url,
        "body": base64.b64encode(body).decode("ascii"),
    }
    context = FakeContext(body=b"blocked")
    context.pages = [pdf_page]
    downloader = BrowserPdfDownloader(profile_dir=tmp_path / "profile")
    monkeypatch.setattr(downloader, "_download_by_click", lambda **_kwargs: False)
    destination = tmp_path / "article.pdf"

    result = downloader._download_in_context(
        context=context,
        doi="10.1000/example",
        destination=destination,
        temporary=tmp_path / "article.pdf.part",
        candidate_urls=[pdf_url],
    )

    assert result.success is True
    assert destination.read_bytes() == body
