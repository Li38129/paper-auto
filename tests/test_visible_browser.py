import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from doi_harvester import cli, mcp_server
from doi_harvester.job_store import _item_status
from doi_harvester.models import DownloadResult
from doi_harvester.pipeline import Harvester
from doi_harvester.visible_browser import DisplayResult


@pytest.fixture(autouse=True)
def external_edge_session(monkeypatch):
    monkeypatch.setattr("doi_harvester.visible_browser.require_edge_browser", lambda _: None)


def test_arxiv_doi_matches_its_abs_page_even_when_metadata_points_to_version_of_record() -> None:
    from doi_harvester.visible_browser import _page_matches_doi

    page = SimpleNamespace(
        url="https://arxiv.org/abs/1412.6027",
        evaluate=lambda _script: ["10.1039/C4CP03677H"],
    )
    assert _page_matches_doi(page, "10.48550/arxiv.1412.6027")


def test_arxiv_doi_does_not_match_a_different_abs_page() -> None:
    from doi_harvester.visible_browser import _page_matches_doi

    page = SimpleNamespace(
        url="https://arxiv.org/abs/1412.9999",
        evaluate=lambda _script: ["10.1039/C4CP03677H"],
    )
    assert not _page_matches_doi(page, "10.48550/arxiv.1412.6027")


def test_stale_cdp_restarts_after_old_playwright_closes(monkeypatch, tmp_path: Path) -> None:
    from doi_harvester.visible_browser import VisibleBrowser

    state = {"endpoint": "http://127.0.0.1:11111", "inside_playwright": False}

    class Playwright:
        def __enter__(self):
            assert not state["inside_playwright"]
            state["inside_playwright"] = True
            self.chromium = SimpleNamespace(connect_over_cdp=self.connect)
            return self

        def __exit__(self, *_args):
            state["inside_playwright"] = False

        def connect(self, endpoint: str, **_kwargs):
            if endpoint.endswith("11111"):
                raise ConnectionError("旧端口已关闭")
            context = SimpleNamespace(new_cdp_session=lambda page: Session())
            return SimpleNamespace(contexts=[context])

    class Session:
        def send(self, command: str, *_args):
            return {"targetInfo": {"targetId": "work"}}

        def detach(self):
            pass

    class Page:
        def is_closed(self):
            return False

        url = "https://publisher.test/article/10.1000/example"

        def goto(self, *_args, **_kwargs):
            return None

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

        def wait_for_timeout(self, *_args):
            return None

        def bring_to_front(self):
            return None

        def evaluate(self, *_args):
            return ["10.1000/example"]

    class Authorizer:
        def __init__(self, **_kwargs):
            pass

        def authorize(self, **_kwargs):
            assert not state["inside_playwright"]
            state["endpoint"] = "http://127.0.0.1:22222"
            return SimpleNamespace(cdp_endpoint=state["endpoint"], final_url=Page.url)

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        SimpleNamespace(sync_playwright=Playwright, TimeoutError=TimeoutError),
    )
    monkeypatch.setattr(
        "doi_harvester.visible_browser._read_cdp_endpoint", lambda *_: state["endpoint"]
    )
    monkeypatch.setattr("doi_harvester.visible_browser.BrowserAuthorizer", Authorizer)
    monkeypatch.setattr("doi_harvester.visible_browser.work_page", lambda *_: Page())
    monkeypatch.setattr("doi_harvester.visible_browser.classify_page", lambda *_: "ready")
    monkeypatch.setattr(VisibleBrowser, "update", lambda *_args, **_kwargs: True)

    result = VisibleBrowser(profile_dir=tmp_path).show(
        doi="10.1000/example", rank=1, mode="仅补充材料"
    )
    assert result.status == "visible"
    assert "browser_reconnected" in result.event


def test_supplements_only_shows_page_before_creating_directory(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []

    def show(self, *, doi: str, rank: int | None, mode: str) -> DisplayResult:
        events.append("show")
        assert mode == "仅补充材料"
        assert not (tmp_path / "article").exists()
        return DisplayResult("visible", f"https://publisher.test/{doi}", "foreground_confirmed")

    def supplements(self, result: DownloadResult) -> None:
        events.append("supplements")
        result.supplement_status = "not_found"

    monkeypatch.setattr("doi_harvester.visible_browser.VisibleBrowser.show", show)
    monkeypatch.setattr(
        "doi_harvester.visible_browser.VisibleBrowser.update", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(Harvester, "_add_supplements", supplements)
    harvester = Harvester(output_dir=tmp_path, supplements_only=True, browser_display="foreground")
    result = harvester.download("10.1000/example", article_dir=tmp_path / "article")
    assert result.success
    assert events == ["show", "supplements"]
    assert not (tmp_path / "article" / "article.pdf").exists()


def test_wrong_article_page_stops_before_cache_or_network(monkeypatch, tmp_path: Path) -> None:
    def show(self, **kwargs) -> DisplayResult:
        return DisplayResult(
            "browser_publisher_unavailable", "https://publisher.test/", "doi_not_confirmed"
        )

    monkeypatch.setattr("doi_harvester.visible_browser.VisibleBrowser.show", show)
    harvester = Harvester(output_dir=tmp_path, browser_display="foreground")
    result = harvester.download("10.1000/example", article_dir=tmp_path / "article")
    assert not result.success
    assert result.reason.startswith("browser_publisher_unavailable")
    assert not (tmp_path / "article").exists()


def test_article_page_opens_when_window_focus_and_status_panel_fail(
    monkeypatch, tmp_path: Path
) -> None:
    from doi_harvester.visible_browser import VisibleBrowser

    class Page:
        def is_closed(self):
            return False

        url = "https://publisher.test/article/10.1000/example"

        def goto(self, *_args, **_kwargs):
            return None

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

        def wait_for_timeout(self, *_args):
            return None

        def evaluate(self, *_args):
            return ["10.1000/example"]

        def bring_to_front(self):
            raise RuntimeError("Windows 拒绝置前")

    page = Page()
    context = SimpleNamespace(
        pages=[page],
        new_cdp_session=lambda _: SimpleNamespace(
            send=lambda _: {"targetInfo": {"targetId": "work"}}, detach=lambda: None
        ),
    )
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(
            connect_over_cdp=lambda *_args, **_kwargs: SimpleNamespace(contexts=[context])
        )
    )

    class PlaywrightContext:
        def __enter__(self):
            return playwright

        def __exit__(self, *_args):
            return None

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        SimpleNamespace(sync_playwright=PlaywrightContext, TimeoutError=TimeoutError),
    )
    monkeypatch.setattr(
        "doi_harvester.visible_browser._read_cdp_endpoint", lambda *_: "http://127.0.0.1:11111"
    )
    monkeypatch.setattr("doi_harvester.visible_browser.work_page", lambda *_: page)
    monkeypatch.setattr("doi_harvester.visible_browser.classify_page", lambda *_: "ready")
    monkeypatch.setattr(VisibleBrowser, "update", lambda *_args, **_kwargs: False)

    result = VisibleBrowser(profile_dir=tmp_path).show(
        doi="10.1000/example", rank=1, mode="仅补充材料"
    )
    assert result.status == "visible"
    assert "article_page_opened" in result.event
    assert "status_panel_unavailable" in result.event


def test_acs_direct_fallback_when_doi_resolver_stays_on_doi_org(
    monkeypatch, tmp_path: Path
) -> None:
    from doi_harvester.visible_browser import VisibleBrowser

    class Page:
        def is_closed(self):
            return False

        url = "about:blank"

        def goto(self, url: str, **_kwargs):
            if url.startswith("https://pubs.acs.org/doi/"):
                self.url = url
            else:
                self.url = "https://doi.org/10.1021/acsaem.6c00640"

        def wait_for_url(self, *_args, **_kwargs):
            raise TimeoutError("DOI 跳转未完成")

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

        def wait_for_timeout(self, *_args):
            return None

        def bring_to_front(self):
            return None

        def evaluate(self, *_args):
            return ["10.1000/example"]

    class Session:
        def send(self, command: str, *_args):
            return {"targetInfo": {"targetId": "work"}}

        def detach(self):
            pass

    page = Page()
    context = SimpleNamespace(pages=[page], new_cdp_session=lambda _page: Session())
    browser = SimpleNamespace(contexts=[context])
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(connect_over_cdp=lambda *_args, **_kwargs: browser)
    )

    class PlaywrightContext:
        def __enter__(self):
            return playwright

        def __exit__(self, *_args):
            return None

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        SimpleNamespace(sync_playwright=PlaywrightContext, TimeoutError=TimeoutError),
    )
    monkeypatch.setattr(
        "doi_harvester.visible_browser._read_cdp_endpoint", lambda *_: "http://127.0.0.1:11111"
    )
    monkeypatch.setattr("doi_harvester.visible_browser.work_page", lambda *_: page)
    monkeypatch.setattr("doi_harvester.visible_browser.classify_page", lambda *_: "ready")
    monkeypatch.setattr(
        "doi_harvester.visible_browser._page_matches_doi",
        lambda _page, doi: doi == "10.1021/acsaem.6c00640" and "/doi/" in page.url,
    )
    monkeypatch.setattr(VisibleBrowser, "update", lambda *_args, **_kwargs: True)

    result = VisibleBrowser(profile_dir=tmp_path).show(
        doi="10.1021/acsaem.6c00640", rank=1549, mode="仅补充材料"
    )

    assert result.status == "visible"
    assert result.url == "https://pubs.acs.org/doi/10.1021/acsaem.6c00640"
    assert "acs_direct_fallback" in result.event


def test_page_doi_matches_sciencedirect_metadata() -> None:
    from doi_harvester.visible_browser import _page_matches_doi

    page = SimpleNamespace(
        url="https://www.sciencedirect.com/science/article/pii/S123456789",
        evaluate=lambda *_args: ["10.1000/example"],
    )
    assert _page_matches_doi(page, "10.1000/example")
    assert not _page_matches_doi(page, "10.1000/other")


def test_work_page_reuses_saved_target_across_origins(tmp_path: Path) -> None:
    import json

    from doi_harvester.visible_browser import work_page

    class Page:
        def is_closed(self):
            return False

        def __init__(self, target: str) -> None:
            self.target = target

        def evaluate(self, script: str):
            return ""

    class Context:
        pages = [Page("other"), Page("saved")]

        def new_cdp_session(self, page):
            return SimpleNamespace(send=lambda command: {"targetInfo": {"targetId": page.target}})

        def new_page(self):
            raise AssertionError("工作标签应复用")

    (tmp_path / "work-target.json").write_text(json.dumps({"target_id": "saved"}), encoding="utf-8")
    assert work_page(Context(), tmp_path).target == "saved"


def test_display_options_and_retryable_failure(tmp_path: Path) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        ["download", "--doi", "10.1000/example", "--browser-display", "foreground"]
    )
    assert args.browser_display == "foreground"
    import inspect

    assert (
        inspect.signature(mcp_server.download).parameters["browser_display"].default == "foreground"
    )
    with __import__("pytest").raises(SystemExit, match="不能同时使用"):
        cli.main(
            [
                "download",
                "--doi",
                "10.1000/example",
                "--output-dir",
                str(tmp_path),
                "--headless",
                "--browser-display",
                "foreground",
            ]
        )
    result = DownloadResult(
        doi="10.1000/example",
        success=False,
        status="browser_not_foreground",
        article_dir=tmp_path,
        reason="browser_not_foreground",
    )
    assert _item_status(result) == "retryable"


def test_doi_matching_requires_complete_consistent_evidence():
    from doi_harvester.visible_browser import _page_matches_doi

    cases = [
        ("https://pubs.acs.org/doi/10.1000/Example-long", [], "10.1000/example", False),
        ("https://pubs.acs.org/doi/10.1000%2FEXAMPLE", [], "doi:10.1000/example", True),
        (
            "https://publisher.test/article",
            ["https://doi.org/10.1000/EXAMPLE"],
            "10.1000/example",
            True,
        ),
        ("https://pubs.acs.org/doi/10.1000/example", ["10.1000/other"], "10.1000/example", False),
        ("https://www.sciencedirect.com/science/article/pii/S123", [], "10.1000/example", False),
    ]
    for url, metadata, doi, expected in cases:
        page = SimpleNamespace(url=url, evaluate=lambda *_, metadata=metadata: metadata)
        assert _page_matches_doi(page, doi) is expected
