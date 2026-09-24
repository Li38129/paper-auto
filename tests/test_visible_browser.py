from pathlib import Path
from types import SimpleNamespace

from doi_harvester import cli, mcp_server
from doi_harvester.job_store import _item_status
from doi_harvester.models import DownloadResult
from doi_harvester.pipeline import Harvester
from doi_harvester.visible_browser import DisplayResult


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


def test_invisible_page_stops_before_cache_or_network(monkeypatch, tmp_path: Path) -> None:
    def show(self, **kwargs) -> DisplayResult:
        return DisplayResult(
            "browser_not_foreground", "https://publisher.test/", "foreground_failed"
        )

    monkeypatch.setattr("doi_harvester.visible_browser.VisibleBrowser.show", show)
    harvester = Harvester(output_dir=tmp_path, browser_display="foreground")
    result = harvester.download("10.1000/example", article_dir=tmp_path / "article")
    assert not result.success
    assert result.reason == "browser_not_foreground"
    assert not (tmp_path / "article").exists()


def test_work_page_reuses_saved_target_across_origins(tmp_path: Path) -> None:
    import json

    from doi_harvester.visible_browser import work_page

    class Page:
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
