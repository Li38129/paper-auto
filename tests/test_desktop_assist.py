"""桌面辅助（windows-mcp 过验证）的单元测试。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from doi_harvester import desktop_assist
from doi_harvester.desktop_assist import (
    AssistOutcome,
    DesktopAssistError,
    NativePointer,
    WindowsMcpDesktop,
    _desktop_input_held,
    challenge_screen_point,
    click_point,
    local_environment,
    managed_command_candidates,
    pick_challenge_frame,
    resolve_challenge,
    resolve_mode,
    viewport_origin,
)


class FakeElement:
    def __init__(self, box: dict[str, float] | None) -> None:
        self._box = box

    def bounding_box(self) -> dict[str, float] | None:
        return self._box


class FakeFrame:
    def __init__(self, url: str, box: dict[str, float] | None = None) -> None:
        self.url = url
        self._box = box

    def frame_element(self) -> FakeElement:
        return FakeElement(self._box)


class FakePage:
    def __init__(self, frames: list[FakeFrame], geometry: dict[str, float] | None = None) -> None:
        self.frames = frames
        self.geometry = geometry or {
            "screenX": 40.0,
            "screenY": 30.0,
            "outerWidth": 1200.0,
            "outerHeight": 900.0,
            "innerWidth": 1184.0,
            "innerHeight": 836.0,
            "dpr": 1.0,
        }
        self.waits = 0

    def evaluate(self, _script: str) -> dict[str, float]:
        return dict(self.geometry)

    def wait_for_timeout(self, _ms: int) -> None:
        self.waits += 1


class FakeController:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []

    def click(self, x: int, y: int) -> str:
        self.clicks.append((x, y))
        return f"clicked {x},{y}"


def test_resolve_mode_accepts_known_values_and_rejects_unknown() -> None:
    assert resolve_mode(None) == "auto"
    assert resolve_mode("OFF") == "off"
    assert resolve_mode("windows-mcp") == "windows-mcp"
    with pytest.raises(ValueError, match="桌面辅助模式"):
        resolve_mode("maybe")


def test_click_point_scales_css_offset_into_physical_pixels() -> None:
    point = click_point(
        origin=(40.0, 30.0),
        box={"x": 100.0, "y": 200.0, "width": 300.0, "height": 65.0},
        ratio=(0.10, 0.50),
        scale=1.3333,
    )
    assert point == (round((140.0 + 30.0) * 1.3333), round((230.0 + 32.5) * 1.3333))


def test_viewport_origin_subtracts_window_chrome() -> None:
    origin = viewport_origin(
        {
            "screenX": 40.0,
            "screenY": 30.0,
            "outerWidth": 1200.0,
            "outerHeight": 900.0,
            "innerWidth": 1184.0,
            "innerHeight": 836.0,
        }
    )
    assert origin == (56.0, 94.0)


def test_pick_challenge_frame_matches_turnstile_iframe() -> None:
    article = FakeFrame("https://onlinelibrary.wiley.com/doi/10.1002/x")
    widget = FakeFrame("https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b")
    assert pick_challenge_frame([article, widget]) is widget
    assert pick_challenge_frame([article]) is None


def test_challenge_screen_point_requires_visible_box() -> None:
    page = FakePage([FakeFrame("https://challenges.cloudflare.com/x")])
    assert challenge_screen_point(page, page.frames[0]) is None
    page = FakePage(
        [
            FakeFrame(
                "https://challenges.cloudflare.com/x",
                {"x": 100.0, "y": 200.0, "width": 300.0, "height": 65.0},
            )
        ]
    )
    assert challenge_screen_point(page, page.frames[0]) == (186, 326)


def test_resolve_challenge_passes_after_desktop_click() -> None:
    page = FakePage(
        [
            FakeFrame(
                "https://challenges.cloudflare.com/x",
                {"x": 100.0, "y": 200.0, "width": 300.0, "height": 65.0},
            )
        ]
    )
    controller = FakeController()
    statuses = iter(["ready"])
    outcome = resolve_challenge(page, classify=lambda _page: next(statuses), controller=controller)
    assert outcome.attempted is True
    assert outcome.passed is True
    assert controller.clicks == [(186, 326)]
    assert page.waits == 1


def test_resolve_challenge_reports_failure_after_attempts() -> None:
    page = FakePage(
        [
            FakeFrame(
                "https://challenges.cloudflare.com/x",
                {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0},
            )
        ]
    )
    controller = FakeController()
    outcome = resolve_challenge(
        page,
        classify=lambda _page: "challenge_required",
        controller=controller,
        attempts=2,
        settle_ms=0,
    )
    assert isinstance(outcome, AssistOutcome)
    assert outcome.attempted is True
    assert outcome.passed is False
    assert "仍为 challenge_required" in outcome.detail
    assert len(controller.clicks) == 2


def test_resolve_challenge_without_controller_falls_back_to_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clicks: list[tuple[int, int]] = []

    def fake_native_click(x: int, y: int, *, button: str = "left", hold_ms: int = 40) -> str:
        clicks.append((x, y))
        return f"native clicked {x},{y}"

    monkeypatch.setattr(desktop_assist, "native_click", fake_native_click)
    page = FakePage(
        [
            FakeFrame(
                "https://challenges.cloudflare.com/x",
                {"x": 100.0, "y": 200.0, "width": 300.0, "height": 65.0},
            )
        ]
    )
    outcome = resolve_challenge(page, classify=lambda _page: "ready", controller=None)
    assert outcome.attempted is True
    assert outcome.passed is True
    assert clicks == [(186, 326)]


def test_resolve_challenge_switches_to_native_when_input_is_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """windows-mcp 把控制权判给用户时，直接用进程内注入完成点击。"""

    class HoldingController:
        def __init__(self) -> None:
            self.attempts = 0

        def click(self, x: int, y: int) -> str:
            self.attempts += 1
            raise DesktopAssistError(
                'click 返回错误{"code": "USER_CONTROL", "status": {"state": "user"}}'
            )

    native: list[tuple[int, int]] = []
    monkeypatch.setattr(
        desktop_assist,
        "native_click",
        lambda x, y, **_: native.append((x, y)) or f"native clicked {x},{y}",
    )
    page = FakePage(
        [
            FakeFrame(
                "https://challenges.cloudflare.com/x",
                {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0},
            )
        ]
    )
    controller = HoldingController()
    outcome = resolve_challenge(page, classify=lambda _page: "ready", controller=controller)
    assert outcome.attempted is True
    assert outcome.passed is True
    assert controller.attempts == 1
    assert native == [(66, 144)]


def test_desktop_input_held_recognizes_both_error_codes() -> None:
    assert _desktop_input_held('{"code": "USER_CONTROL"}') is True
    assert _desktop_input_held('{"code": "PHYSICAL_INPUT_HELD"}') is True
    assert _desktop_input_held('{"state": "user"}') is True
    assert _desktop_input_held("SetCursorPos(1, 2) 失败") is False


def test_native_pointer_delegates_to_native_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    moves: list[tuple[int, int]] = []
    monkeypatch.setattr(
        desktop_assist, "native_move", lambda x, y: moves.append((x, y)) or f"moved {x},{y}"
    )
    pointer = NativePointer()
    assert pointer.name == "native"
    assert pointer.move(5, 6) == "moved 5,6"
    assert moves == [(5, 6)]
    pointer.close()


def test_managed_command_candidates_prefers_explicit_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOI_HARVESTER_DESKTOP_COMMAND", '"C:\\py\\python.exe" -m windows_mcp')
    monkeypatch.setenv("DOI_HARVESTER_DESKTOP_ARGS", "--verbose")
    monkeypatch.setenv("WINDOWS_MCP_HOME", str(Path.home() / "no-such-windows-mcp"))
    commands = managed_command_candidates(dict(os.environ))
    assert commands[0][0] == "C:\\py\\python.exe"
    assert commands[0][-1] == "--verbose"


def test_local_environment_creates_writable_temp() -> None:
    env = local_environment(
        {"DOI_HARVESTER_DESKTOP_TEMP": r"C:\Users\18229\AppData\Local\Temp\wmcp"}
    )
    assert env["TEMP"].endswith("wmcp")
    assert env["TMP"] == env["TEMP"]
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_windows_mcp_desktop_speaks_json_rpc(tmp_path: Path) -> None:
    stub = tmp_path / "stub_windows_mcp.py"
    stub.write_text(
        "\n".join(
            [
                "import json, sys",
                "for line in sys.stdin:",
                "    line = line.strip()",
                "    if not line:",
                "        continue",
                "    message = json.loads(line)",
                "    if 'id' not in message:",
                "        continue",
                "    if message.get('method') == 'initialize':",
                "        result = {'protocolVersion': '2024-11-05', 'capabilities': {}}",
                "    else:",
                "        arguments = message['params']['arguments']",
                "        result = {'content': [{'type': 'text',",
                "                  'text': 'clicked:%s' % (arguments['loc'],)}], 'isError': False}",
                "    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}),",
                "          flush=True)",
            ]
        ),
        encoding="utf-8",
    )
    with WindowsMcpDesktop([sys.executable, str(stub)], timeout=20.0) as controller:
        assert controller.click(11, 22) == "clicked:[11, 22]"
