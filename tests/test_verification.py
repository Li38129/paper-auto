from pathlib import Path
from types import SimpleNamespace

import pytest

from doi_harvester import browser
from doi_harvester.visible_browser import VisibleBrowser


@pytest.mark.parametrize("gate", ["challenge_required", "authentication_required"])
@pytest.mark.parametrize("clear_at", [3, None])
def test_verification_grace_has_ten_second_deadline(monkeypatch, gate, clear_at):
    clock = {"seconds": 0}
    waits = []
    monkeypatch.setattr(browser, "time", SimpleNamespace(monotonic=lambda: clock["seconds"]))
    monkeypatch.setattr(
        browser,
        "classify_page",
        lambda _: (
            "authenticated" if clear_at is not None and clock["seconds"] >= clear_at else gate
        ),
    )

    class Page:
        def wait_for_timeout(self, milliseconds):
            waits.append(milliseconds)
            clock["seconds"] += milliseconds / 1000

    page = Page()
    assert browser.wait_for_verification(page) == ("authenticated" if clear_at else gate)
    assert clock["seconds"] == (clear_at or 10)
    assert all(value == 1000 for value in waits)
    clock["seconds"] = 0
    waits.clear()
    browser.wait_for_verification(page)
    assert clock["seconds"] == (clear_at or 10)


@pytest.mark.parametrize("user_agent,valid", [("Mozilla Edg/130.0", True), ("Chrome/130.0", False)])
def test_cdp_browser_identity_requires_edge(user_agent, valid):
    detached = []
    session = SimpleNamespace(
        send=lambda _: {"userAgent": user_agent}, detach=lambda: detached.append(True)
    )
    connection = SimpleNamespace(new_browser_cdp_session=lambda: session)
    if valid:
        browser.require_edge_browser(connection)
    else:
        with pytest.raises(RuntimeError, match="Edge"):
            browser.require_edge_browser(connection)
    assert detached == [True]


@pytest.mark.parametrize(
    "failure,metadata,closed",
    [(False, [], False), (True, [], False), (False, ["10.1000/other"], False), (False, [], True)],
)
def test_edge_tab_binding_allows_failed_content_but_not_closed_tab(
    monkeypatch, tmp_path, failure, metadata, closed
):
    import json
    import sys

    from doi_harvester import visible_browser

    class Page:
        url = "https://publisher.test/loading"

        def goto(self, target, **kwargs):
            assert kwargs["wait_until"] == "commit"
            if failure:
                raise TimeoutError("publisher load failed")

        def is_closed(self):
            return closed

        def evaluate(self, *_):
            return metadata

        def bring_to_front(self):
            raise AssertionError("不得请求焦点")

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

    page = Page()
    target_session = SimpleNamespace(
        send=lambda _: {"targetInfo": {"targetId": "target-1"}}, detach=lambda: None
    )
    context = SimpleNamespace(new_cdp_session=lambda _: target_session)
    identity_session = SimpleNamespace(
        send=lambda _: {"userAgent": "Edg/130.0"}, detach=lambda: None
    )
    connection = SimpleNamespace(
        contexts=[context], new_browser_cdp_session=lambda: identity_session
    )

    class Playwright:
        def __enter__(self):
            return SimpleNamespace(
                chromium=SimpleNamespace(connect_over_cdp=lambda *_args, **_kwargs: connection)
            )

        def __exit__(self, *_):
            pass

    monkeypatch.setitem(
        sys.modules, "playwright.sync_api", SimpleNamespace(sync_playwright=Playwright)
    )
    monkeypatch.setattr(visible_browser, "_read_cdp_endpoint", lambda _: "http://localhost:9222")
    monkeypatch.setattr(visible_browser, "work_page", lambda *_: page)
    monkeypatch.setattr(visible_browser, "classify_page", lambda _: "authenticated")
    monkeypatch.setattr(
        visible_browser, "wait_for_verification", lambda *_args, **_kwargs: "authenticated"
    )
    monkeypatch.setattr(VisibleBrowser, "update", lambda *_args, **_kwargs: False)
    result = VisibleBrowser(profile_dir=tmp_path).show(doi="10.1000/example", rank=1, mode="正文")
    assert result.status == ("browser_display_unavailable" if closed else "visible")
    record = json.loads((tmp_path / "work-target.json").read_text(encoding="utf-8"))
    assert record["doi"] == "10.1000/example" and record["target_id"] == "target-1"
    assert record["target_url"] == "https://doi.org/10.1000/example"
    if not closed:
        assert "article_content_unconfirmed" in result.event


def test_explicit_non_edge_channel_needs_attention(tmp_path):
    result = VisibleBrowser(profile_dir=tmp_path, channel="chrome").show(
        doi="10.1000/example", rank=1, mode="正文"
    )
    assert result.status == "browser_display_unavailable"
    assert "explicit_non_edge_channel" in result.event
    assert not (tmp_path / "work-target.json").exists()


def test_temporary_scripts_directory_is_ignored():
    import subprocess

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "check-ignore", "temp/doi-harvester/task-scripts/example/script.py"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_missing_edge_cannot_silently_fall_back(monkeypatch, tmp_path):
    from doi_harvester import visible_browser

    channels = []

    class Authorizer:
        def __init__(self, **kwargs):
            channels.append(kwargs["channel"])

        def authorize(self, **kwargs):
            return SimpleNamespace(
                cdp_endpoint="",
                final_url="https://doi.org/10.1000/example",
                status="browser_executable_not_found",
            )

    monkeypatch.setattr(visible_browser, "_read_cdp_endpoint", lambda _: "")
    monkeypatch.setattr(visible_browser, "BrowserAuthorizer", Authorizer)
    result = VisibleBrowser(profile_dir=tmp_path).show(doi="10.1000/example", rank=1, mode="正文")
    assert result.status == "browser_display_unavailable"
    assert channels == ["msedge"]
    assert result.event == "browser_executable_not_found"
