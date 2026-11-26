"""人机验证的桌面辅助。

浏览器取文遇到 Cloudflare / Turnstile 挑战时，页面里的复选框必须在真实输入
桌面上点击才可能通过。这里用 Windows-MCP（stdio MCP 服务）驱动真实鼠标，
把页面内坐标换算成屏幕坐标后点击，并按页面状态判定是否已通过。

设计要点：

* 只依赖标准库，缺少 MCP 服务时静默降级，不影响原下载流程。
* 通过环境变量配置，默认 ``auto``：找得到服务就用，找不到就跳过。
* 坐标换算与挑战 frame 选择都是纯函数，方便单元测试。
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import queue
import shutil
import subprocess
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PROTOCOL_VERSION = "2024-11-05"
DEFAULT_REQUEST_TIMEOUT = 20.0
# Turnstile 复选框在组件内的相对位置（按组件宽高取比例）
DEFAULT_CLICK_RATIO = (0.10, 0.50)
CHALLENGE_FRAME_MARKERS = ("challenges.cloudflare.com", "turnstile")
# 允许的配置值：关闭、自动探测、强制使用
ASSIST_MODES = ("off", "auto", "windows-mcp")


class DesktopAssistError(RuntimeError):
    """桌面辅助不可用或调用失败。"""


def resolve_mode(value: str | None) -> str:
    """把配置值规范化为 ``off`` / ``auto`` / ``windows-mcp``。"""
    text = (value or "auto").strip().lower()
    if text in ASSIST_MODES:
        return text
    raise ValueError(f"未知的桌面辅助模式：{value}")


def managed_command_candidates(env: Mapping[str, str]) -> list[list[str]]:
    """列出可尝试的 Windows-MCP 启动命令。

    顺序：显式配置 -> PATH 里的 ``windows-mcp`` -> DSH 托管安装里的可执行文件。
    显式配置支持带参数的整行命令，便于指向 Medium 完整性的解释器。
    """
    candidates: list[list[str]] = []
    explicit = (env.get("DOI_HARVESTER_DESKTOP_COMMAND") or "").strip()
    if explicit:
        candidates.append(shlex_split(explicit))
    on_path = shutil.which("windows-mcp")
    if on_path:
        candidates.append([on_path])
    candidates.extend([[item] for item in managed_executables(env)])
    extra = shlex_split(env.get("DOI_HARVESTER_DESKTOP_ARGS") or "")
    return [item + extra for item in candidates if item]


def managed_executables(env: Mapping[str, str]) -> list[str]:
    """探测 DSH 托管安装里的 Windows-MCP 可执行文件。"""
    root = (env.get("WINDOWS_MCP_HOME") or "").strip()
    if root:
        found = sorted(str(item) for item in Path(root).glob("**/windows-mcp.exe"))
        return found
    harness = Path(env.get("APPDATA") or Path.home() / "AppData/Roaming")
    base = harness / "dsh-desktop/harness/dsh-windows-mcp/toolchain"
    if not base.is_dir():
        return []
    return sorted(str(item) for item in base.glob("*/windows-mcp/Scripts/windows-mcp.exe"))


def shlex_split(text: str) -> list[str]:
    """按 shell 规则切分命令行，保留引号里的空格。"""
    import shlex

    # posix=True 才会剥掉路径外层的引号，Windows 反斜杠不受影响
    return shlex.split(text, posix=True)


def pick_challenge_frame(frames: Iterable[Any]) -> Any | None:
    """从页面 frame 列表里挑出挑战组件所在的 frame。"""
    for frame in frames:
        url = str(getattr(frame, "url", "") or "").lower()
        if any(marker in url for marker in CHALLENGE_FRAME_MARKERS):
            return frame
    return None


def click_point(
    *,
    origin: tuple[float, float],
    box: Mapping[str, float],
    ratio: tuple[float, float] = DEFAULT_CLICK_RATIO,
    scale: float = 1.0,
) -> tuple[int, int]:
    """把 frame 内的相对坐标换算成屏幕物理像素坐标。

    ``origin`` 为页面视口原点在屏幕上的 CSS 坐标，``box`` 是 frame 在视口内的
    位置与尺寸，``scale`` 是设备像素比（物理像素 = CSS 像素 × scale）。
    """
    offset_x = box["x"] + box["width"] * ratio[0]
    offset_y = box["y"] + box["height"] * ratio[1]
    return int(round((origin[0] + offset_x) * scale)), int(round((origin[1] + offset_y) * scale))


def viewport_origin(screen: Mapping[str, float]) -> tuple[float, float]:
    """由页面报告的窗口几何推算视口原点（CSS 坐标）。"""
    left = float(screen.get("screenX", 0.0)) + float(screen.get("outerWidth", 0.0)) - float(
        screen.get("innerWidth", 0.0)
    ) - max(float(screen.get("borderX", 0.0)), 0.0)
    top = float(screen.get("screenY", 0.0)) + float(screen.get("outerHeight", 0.0)) - float(
        screen.get("innerHeight", 0.0)
    ) - max(float(screen.get("borderY", 0.0)), 0.0)
    return left, top


@dataclass(frozen=True)
class AssistOutcome:
    """一次验证辅助的结果。"""

    attempted: bool
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"attempted": self.attempted, "passed": self.passed, "detail": self.detail}


class WindowsMcpDesktop:
    """Windows-MCP 的极简 stdio 客户端，只暴露点击与移动。"""

    def __init__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self.command = list(command)
        self.timeout = timeout
        self._env = dict(local_environment(env))
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._next_id = 0
        self._reader: threading.Thread | None = None

    # ---- 生命周期 ----
    def __enter__(self) -> WindowsMcpDesktop:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def start(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = subprocess.Popen(
                self.command + ["serve"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._env,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:  # 找不到可执行文件等
            raise DesktopAssistError(f"无法启动 Windows-MCP：{exc}") from exc
        self._reader = threading.Thread(target=self._pump, name="windows-mcp-reader", daemon=True)
        self._reader.start()
        self._initialize()

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:  # noqa: BLE001
            with_suppress(process.kill)

    # ---- 协议 ----
    def _pump(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._messages.put(None)
            return
        try:
            for line in process.stdout:
                text = line.strip()
                if not text:
                    continue
                try:
                    self._messages.put(json.loads(text))
                except json.JSONDecodeError:
                    continue
        except Exception:  # noqa: BLE001 关闭流时读取线程会抛错，忽略即可
            pass
        finally:
            self._messages.put(None)

    def _write(self, payload: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise DesktopAssistError("Windows-MCP 未启动")
        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except OSError as exc:
            raise DesktopAssistError(f"写入 Windows-MCP 失败：{exc}") from exc

    def _request(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)
        while True:
            try:
                message = self._messages.get(timeout=self.timeout)
            except queue.Empty as exc:
                raise DesktopAssistError(f"等待 {method} 响应超时") from exc
            if message is None:
                raise DesktopAssistError("Windows-MCP 已退出")
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise DesktopAssistError(f"{method} 失败：{message['error']}")
            return message.get("result") or {}

    def _initialize(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": DEFAULT_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "doi-harvester", "version": "1.0"},
            },
        )
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> str:
        result = self._request("tools/call", {"name": name, "arguments": dict(arguments or {})})
        if result.get("isError"):
            raise DesktopAssistError(f"{name} 返回错误：{_content_text(result)}")
        return _content_text(result)

    # ---- 便捷动作 ----
    def click(self, x: int, y: int) -> str:
        return self.call_tool("Click", {"loc": [x, y], "button": "left", "clicks": 1})

    def move(self, x: int, y: int) -> str:
        return self.call_tool("Move", {"loc": [x, y]})

    def status(self) -> str:
        return self.call_tool("ControlStatus")


def _content_text(result: Mapping[str, Any]) -> str:
    parts = [str(item.get("text", "")) for item in result.get("content") or []]
    return "\n".join(part for part in parts if part)


def local_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """构造子进程环境：补上 Windows-MCP 需要的可写临时目录。"""
    merged = dict(os.environ if env is None else env)
    temp = merged.get("DOI_HARVESTER_DESKTOP_TEMP")
    if temp:
        merged.setdefault("TEMP", temp)
        merged.setdefault("TMP", temp)
    merged.setdefault("PYTHONUNBUFFERED", "1")
    merged.setdefault("PYTHONIOENCODING", "utf-8")
    return merged


def with_suppress(action: Any) -> None:
    with contextlib.suppress(Exception):
        action()


def open_controller(env: Mapping[str, str] | None = None) -> WindowsMcpDesktop | None:
    """按配置打开一个可用的桌面控制器，失败时返回 ``None``。"""
    environment = dict(os.environ if env is None else env)
    mode = resolve_mode(environment.get("DOI_HARVESTER_DESKTOP_ASSIST"))
    if mode == "off":
        return None
    candidates = managed_command_candidates(environment)
    if not candidates:
        return None
    for command in candidates:
        controller = WindowsMcpDesktop(command, env=environment)
        try:
            controller.start()
        except DesktopAssistError:
            controller.close()
            continue
        # 进程退出时兜底回收，避免留下常驻的 MCP 服务
        atexit.register(controller.close)
        return controller
    return None


def page_screen_geometry(page: Any) -> dict[str, float]:
    """读取页面窗口在屏幕上的几何与缩放比例。"""
    script = """() => ({
        screenX: window.screenX,
        screenY: window.screenY,
        outerWidth: window.outerWidth,
        outerHeight: window.outerHeight,
        innerWidth: window.innerWidth,
        innerHeight: window.innerHeight,
        dpr: window.devicePixelRatio || 1,
    })"""
    geometry = page.evaluate(script) or {}
    return {key: float(value or 0.0) for key, value in geometry.items()}


def challenge_screen_point(
    page: Any,
    frame: Any,
    *,
    ratio: tuple[float, float] = DEFAULT_CLICK_RATIO,
) -> tuple[int, int] | None:
    """计算挑战复选框的屏幕物理像素坐标。"""
    try:
        element = frame.frame_element()
        box = element.bounding_box()
    except Exception:  # noqa: BLE001
        return None
    if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
        return None
    geometry = page_screen_geometry(page)
    scale = geometry.get("dpr") or 1.0
    origin = viewport_origin(geometry)
    return click_point(origin=origin, box=box, ratio=ratio, scale=scale)


def resolve_challenge(
    page: Any,
    *,
    classify: Any,
    controller: WindowsMcpDesktop | None = None,
    attempts: int = 2,
    settle_ms: int = 1500,
    ratio: tuple[float, float] = DEFAULT_CLICK_RATIO,
) -> AssistOutcome:
    """尝试用桌面点击通过人机验证。

    ``classify`` 是页面状态判定函数（返回 ``challenge_required`` 等状态字符串）。
    只点 ``attempts`` 次，宁可快速失败也不与挑战长时间纠缠。
    """
    if controller is None:
        return AssistOutcome(attempted=False, passed=False, detail="未配置桌面辅助")
    frames = list(getattr(page, "frames", []) or [])
    frame = pick_challenge_frame(frames)
    if frame is None:
        return AssistOutcome(attempted=False, passed=False, detail="页面上没有挑战组件")
    point = challenge_screen_point(page, frame, ratio=ratio)
    if point is None:
        return AssistOutcome(attempted=False, passed=False, detail="挑战组件没有可见区域")
    detail = ""
    for index in range(max(attempts, 1)):
        try:
            detail = controller.click(*point)
        except DesktopAssistError as exc:
            return AssistOutcome(attempted=True, passed=False, detail=str(exc))
        with_suppress(lambda: page.wait_for_timeout(settle_ms))
        status = str(classify(page))
        if status not in {"challenge_required", "authentication_required"}:
            return AssistOutcome(attempted=True, passed=True, detail=detail)
        detail = f"{detail}｜第 {index + 1} 次点击后仍为 {status}"
    return AssistOutcome(attempted=True, passed=False, detail=detail)