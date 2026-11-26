"""人机验证的桌面辅助。

浏览器取文遇到 Cloudflare / Turnstile 挑战时，页面里的复选框必须在真实输入
桌面上点击才可能通过。这里用 Windows-MCP（stdio MCP 服务）驱动真实鼠标，
把页面内坐标换算成屏幕坐标后点击，并按页面状态判定是否已通过。

设计要点：

* 只依赖标准库，缺少 MCP 服务时静默降级，不影响原下载流程。
* 通过环境变量配置，默认 ``auto``：找得到服务就用，找不到就跳过。
* Windows-MCP 把控制权判给用户时会持续拒动（``USER_CONTROL``）；这时改用
  进程内 Win32 注入（``SetCursorPos`` + ``mouse_event``）完成点击，既不需要
  逐次确认，也不受服务端输入仲裁阻挡。
* 坐标换算与挑战 frame 选择都是纯函数，方便单元测试。
"""

from __future__ import annotations

import atexit
import contextlib
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
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

LOGGER = logging.getLogger(__name__)


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
    显式配置支持带参数的整行命令，便于指向 Medium 完整性的解释器；一旦给出就
    独占候选列表，运维明确指定解释器时不再去猜别处的 exe（实测某些 shim 起来的
    服务能应答 ControlStatus，却让 ``SetCursorPos`` 失败）。
    """
    explicit = (env.get("DOI_HARVESTER_DESKTOP_COMMAND") or "").strip()
    if explicit:
        extra = shlex_split(env.get("DOI_HARVESTER_DESKTOP_ARGS") or "")
        return [shlex_split(explicit) + extra]

    candidates: list[list[str]] = []
    on_path = shutil.which("windows-mcp")
    if on_path:
        candidates.append([on_path, "serve"])
    candidates.extend([[item, "serve"] for item in managed_executables(env)])
    return candidates


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
    """按命令行规则切分，兼容 Windows 反斜杠路径与外层引号。"""
    import shlex

    if not text.strip():
        return []
    # posix=False 才把反斜杠当普通字符，否则 `C:\py\python.exe` 会被吃掉反斜杠；
    # 代价是它保留外层引号，这里统一剥掉。
    tokens = shlex.split(text, posix=False)
    cleaned: list[str] = []
    for token in tokens:
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
            token = token[1:-1]
        if token:
            cleaned.append(token)
    return cleaned


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
    left = (
        float(screen.get("screenX", 0.0))
        + float(screen.get("outerWidth", 0.0))
        - float(screen.get("innerWidth", 0.0))
        - max(float(screen.get("borderX", 0.0)), 0.0)
    )
    top = (
        float(screen.get("screenY", 0.0))
        + float(screen.get("outerHeight", 0.0))
        - float(screen.get("innerHeight", 0.0))
        - max(float(screen.get("borderY", 0.0)), 0.0)
    )
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
            # 子命令由候选命令自己带全：显式命令可能已经是 `-m windows_mcp serve`，
            # 这里再补一次 `serve` 会让服务端多出位置参数并立即退出。
            self._process = subprocess.Popen(
                self.command,
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


def current_cursor() -> tuple[int, int] | None:
    """读取当前光标位置；非 Windows 或读取失败时返回 ``None``。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
        return int(point.x), int(point.y)
    except Exception:  # noqa: BLE001 - 读取光标只为冒烟校验，失败即视为不可用
        return None


def native_move(x: int, y: int) -> str:
    """把光标移到屏幕绝对坐标（进程内 Win32 调用，不经过任何审批）。"""
    if os.name != "nt":
        raise DesktopAssistError("原生指针注入只在 Windows 上可用")
    try:
        import ctypes

        user32 = ctypes.windll.user32
        if not user32.SetCursorPos(int(x), int(y)):
            raise DesktopAssistError(f"SetCursorPos({x}, {y}) 失败")
    except DesktopAssistError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一包装成可读错误
        raise DesktopAssistError(f"SetCursorPos({x}, {y}) 异常：{exc}") from exc
    return f"Moved the mouse pointer to ({int(x)},{int(y)})."


def native_click(x: int, y: int, *, button: str = "left", hold_ms: int = 40) -> str:
    """在屏幕绝对坐标完成一次真实鼠标点击。

    windows-mcp 在 ``state: "user"`` 时会拒绝一切指针动作（``USER_CONTROL``），
    而验证页点击需要立即生效，因此提供进程内直接注入的通道：同样是真实输入，
    只是绕开了服务端的输入仲裁，也不需要任何确认弹窗。
    """
    if os.name != "nt":
        raise DesktopAssistError("原生指针注入只在 Windows 上可用")
    flags = {
        "left": (0x0002, 0x0004),
        "right": (0x0008, 0x0010),
        "middle": (0x0020, 0x0040),
    }
    if button not in flags:
        raise DesktopAssistError(f"未知鼠标键：{button}")
    down, up = flags[button]
    try:
        import ctypes

        user32 = ctypes.windll.user32
        native_move(x, y)
        user32.mouse_event(down, 0, 0, 0, 0)
        time.sleep(max(hold_ms, 0) / 1000)
        user32.mouse_event(up, 0, 0, 0, 0)
    except DesktopAssistError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一包装成可读错误
        raise DesktopAssistError(f"原生点击 ({x}, {y}) 异常：{exc}") from exc
    return f"{button} clicked at ({int(x)},{int(y)})."


class NativePointer:
    """与 :class:`WindowsMcpDesktop` 同形的进程内指针控制器。"""

    name = "native"

    def status(self) -> str:
        point = current_cursor()
        return f'{{"state": "native", "cursor": {list(point) if point else None}}}'

    def move(self, x: int, y: int) -> str:
        return native_move(x, y)

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> str:
        detail = ""
        for _ in range(max(int(clicks), 1)):
            detail = native_click(x, y, button=button)
            time.sleep(0.05)
        return detail

    def close(self) -> None:
        """原生控制器没有子进程，无需清理。"""


def _desktop_input_held(message: str) -> bool:
    """判断错误是否是「用户正在操作鼠标/键盘」导致的拒动。

    服务端对同一情形的错误码并不稳定：实测出现过 ``PHYSICAL_INPUT_HELD``（带
    ``status.state == "user"``）与 ``USER_CONTROL`` 两种写法，所以按语义判。
    """
    lowered = message.lower()
    if "physical_input_held" in lowered or "user_control" in lowered:
        return True
    return '"state": "user"' in lowered or '"state":"user"' in lowered


def _smoke_check(controller: WindowsMcpDesktop) -> None:
    """启动后立刻验证状态与光标控制。

    实测存在「能应答 ControlStatus，但 SetCursorPos 一律失败」的服务（例如某些
    shim 包装的 exe），只 start() 会把它当可用，到验证页那一步才暴露。这里读完
    状态再让光标原地挪一次：目标就是当前位置，没有可见副作用。

    但用户正在用鼠标时，服务会按输入仲裁拒绝挪动光标（返回
    ``PHYSICAL_INPUT_HELD`` / ``state: "user"``）。这是服务健康的表现，不是候选
    不可用，所以单独放行。
    """
    controller.status()
    point = current_cursor()
    if point is None:
        return
    try:
        controller.move(*point)
    except DesktopAssistError as exc:
        if not _desktop_input_held(str(exc)):
            raise
        LOGGER.info("桌面辅助已就绪，但当前用户正在操作鼠标：%s", exc)


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
            _smoke_check(controller)
        except DesktopAssistError as exc:
            # 候选逐个试，失败原因要落日志：静默 continue 会让「能应答但点不动」的服务
            # 变成一句「未找到可用的 windows-mcp」，排查时无从下手。
            LOGGER.warning("windows-mcp 候选不可用：%s（%s）", command, exc)
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
        # 没有可用的 windows-mcp 也不放弃：进程内注入同样能点，且无需确认。
        controller = NativePointer()
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
            if not _desktop_input_held(str(exc)):
                return AssistOutcome(attempted=True, passed=False, detail=str(exc))
            # 服务端把控制权判给用户时会持续拒动，直接改用进程内注入完成这次点击。
            try:
                detail = native_click(*point)
            except DesktopAssistError as native_exc:
                return AssistOutcome(
                    attempted=True,
                    passed=False,
                    detail=f"{exc}｜原生注入失败：{native_exc}",
                )
        with_suppress(lambda: page.wait_for_timeout(settle_ms))
        status = str(classify(page))
        if status not in {"challenge_required", "authentication_required"}:
            return AssistOutcome(attempted=True, passed=True, detail=detail)
        detail = f"{detail}｜第 {index + 1} 次点击后仍为 {status}"
    return AssistOutcome(attempted=True, passed=False, detail=detail)
