"""为下载任务显示并核验可见的出版社工作页面。"""

from __future__ import annotations

import ctypes
import json
import platform
import subprocess
from contextlib import suppress
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from .browser import BrowserAuthorizer, _read_cdp_endpoint, classify_page


@dataclass(frozen=True)
class DisplayResult:
    status: str
    url: str
    event: str


def _foreground_window(pid: int) -> bool:
    """恢复浏览器窗口并验证当前前台窗口属于该进程。"""
    if platform.system() != "Windows" or not pid:
        return False
    user32 = ctypes.windll.user32
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    handles: list[int] = []
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def collect(hwnd: int, _data: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            handles.append(hwnd)
        return True

    callback = enum_proc(collect)
    user32.EnumWindows(callback, 0)
    for hwnd in handles:
        user32.ShowWindow(hwnd, 9)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if user32.GetForegroundWindow() == hwnd:
            return True
        foreground = user32.GetForegroundWindow()
        foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
        own_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        if foreground_thread and user32.AttachThreadInput(own_thread, foreground_thread, True):
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                user32.AttachThreadInput(own_thread, foreground_thread, False)
        if user32.GetForegroundWindow() == hwnd:
            return True
    return False


def _browser_pid(profile_dir: Path) -> int:
    try:
        state = json.loads((profile_dir / "auth-state.json").read_text(encoding="utf-8"))
        return int(state.get("browser_pid") or 0)
    except (OSError, ValueError, TypeError):
        return 0


def _pid_for_cdp_port(endpoint: str) -> int:
    """Edge 首次启动会重建主进程，按调试端口寻找实际窗口进程。"""
    if platform.system() != "Windows":
        return 0
    try:
        port = int(endpoint.rsplit(":", 1)[1])
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" "
            f"| Where-Object {{ $_.CommandLine -like '*--remote-debugging-port={port}*' "
            "-and $_.CommandLine -notlike '*--type=*' } "
            "| Select-Object -ExpandProperty ProcessId -First 1"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return int(result.stdout.strip() or 0)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return 0


def work_page(context: object, profile_dir: Path | None = None) -> object:
    """复用专用工作标签，避免覆盖用户打开的其他网页。"""
    if context.pages and not hasattr(context.pages[0], "evaluate"):
        return context.pages[-1]
    marker = Path(profile_dir) / "work-target.json" if profile_dir else None
    target_id = ""
    if marker:
        with suppress(OSError, ValueError, TypeError):
            target_id = str(json.loads(marker.read_text(encoding="utf-8")).get("target_id") or "")
    if target_id:
        for page in context.pages:
            try:
                session = context.new_cdp_session(page)
                info = session.send("Target.getTargetInfo")
                if info["targetInfo"]["targetId"] == target_id:
                    return page
            except Exception:
                continue
    for page in context.pages:
        try:
            if page.evaluate("() => window.name") == "autopaper-work":
                return page
        except Exception:
            continue
    page = context.new_page()
    if hasattr(page, "evaluate"):
        page.evaluate("() => { window.name = 'autopaper-work'; }")
    if marker and hasattr(context, "new_cdp_session"):
        try:
            session = context.new_cdp_session(page)
            info = session.send("Target.getTargetInfo")
            marker.write_text(
                json.dumps({"target_id": info["targetInfo"]["targetId"]}), encoding="utf-8"
            )
        except Exception:
            pass
    return page


class VisibleBrowser:
    def __init__(self, *, profile_dir: Path, channel: str | None = None) -> None:
        self.profile_dir = Path(profile_dir)
        self.channel = channel

    def show(self, *, doi: str, rank: int | None, mode: str) -> DisplayResult:
        """每篇先打开页面；失败时调用方必须暂停队列。"""
        target = f"https://doi.org/{doi}"
        endpoint = _read_cdp_endpoint(self.profile_dir)
        events = ["browser_connected"] if endpoint else ["browser_started"]
        opened_url = ""
        if not endpoint:
            started = BrowserAuthorizer(
                profile_dir=self.profile_dir, channel=self.channel, cdp=True
            ).authorize(publisher="download", doi=doi, timeout_seconds=0)
            endpoint = started.cdp_endpoint or _read_cdp_endpoint(self.profile_dir)
            opened_url = started.final_url
            if not endpoint:
                return DisplayResult(
                    "browser_display_unavailable", started.final_url or target, started.status
                )
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.connect_over_cdp(endpoint, timeout=10000)
                except Exception:
                    restarted = BrowserAuthorizer(
                        profile_dir=self.profile_dir, channel=self.channel, cdp=True
                    ).authorize(publisher="download", doi=doi, timeout_seconds=0)
                    endpoint = restarted.cdp_endpoint
                    opened_url = restarted.final_url
                    browser = playwright.chromium.connect_over_cdp(endpoint, timeout=10000)
                    events.append("browser_reconnected")
                context = browser.contexts[0]
                page = work_page(context, self.profile_dir)
                if not opened_url or page.url != opened_url:
                    page.goto(target, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1500)
                events.append("publisher_navigated")
                if page.url.startswith("https://doi.org/"):
                    return DisplayResult(
                        "browser_publisher_unavailable", page.url, "publisher_navigation_failed"
                    )
                page.bring_to_front()
                session = context.new_cdp_session(page)
                window = session.send("Browser.getWindowForTarget")
                session.send(
                    "Browser.setWindowBounds",
                    {"windowId": window["windowId"], "bounds": {"windowState": "normal"}},
                )
                if not (
                    _foreground_window(_browser_pid(self.profile_dir))
                    or _foreground_window(_pid_for_cdp_port(endpoint))
                ):
                    return DisplayResult("browser_not_foreground", page.url, "foreground_failed")
                if not page.evaluate("() => document.visibilityState === 'visible'"):
                    return DisplayResult("browser_not_visible", page.url, "visibility_failed")
                status = classify_page(page)
                if status in {"challenge_required", "authentication_required"}:
                    return DisplayResult(status, page.url, "authorization_required")
                if not self.update(
                    doi=doi, rank=rank, mode=mode, stage="页面已显示", page=page
                ):
                    return DisplayResult(
                        "browser_status_unavailable", page.url, "status_panel_failed"
                    )
                events.append("foreground_confirmed")
                return DisplayResult("visible", page.url, ";".join(events))
        except Exception as exc:
            return DisplayResult(
                "browser_display_unavailable", target, f"{type(exc).__name__}:{exc}"
            )

    def update(
        self,
        *,
        doi: str,
        rank: int | None,
        mode: str,
        stage: str,
        attachments: int = 0,
        page: object | None = None,
    ) -> bool:
        """普通页面显示可收起状态；验证页保持原貌。"""

        def apply(target_page: object) -> None:
            if classify_page(target_page) in {"challenge_required", "authentication_required"}:
                return
            target_page.evaluate(
                """data => {
              let panel = document.getElementById('autopaper-status');
              if (!panel) {
                panel = document.createElement('details');
                panel.id = 'autopaper-status'; panel.open = true;
                panel.style.cssText = 'position:fixed;right:12px;bottom:12px;' +
                  'z-index:2147483647;background:#fff;color:#111;' +
                  'border:1px solid #777;padding:8px;max-width:340px;font:12px sans-serif';
                panel.appendChild(document.createElement('summary'));
                panel.appendChild(document.createElement('div'));
                document.body.appendChild(panel);
              }
              panel.firstChild.textContent = 'AutoPaper 下载状态';
              panel.lastChild.textContent = `${data.rank || ''} ${data.doi} · ` +
                `${data.mode} · ${data.stage} · 附件 ${data.attachments}`;
            }""",
                {
                    "doi": doi,
                    "rank": rank,
                    "mode": mode,
                    "stage": stage,
                    "attachments": attachments,
                },
            )

        try:
            if page is not None:
                apply(page)
            else:
                from playwright.sync_api import sync_playwright

                with sync_playwright() as playwright:
                    browser = playwright.chromium.connect_over_cdp(
                        _read_cdp_endpoint(self.profile_dir)
                    )
                    apply(work_page(browser.contexts[0], self.profile_dir))
            return True
        except Exception:
            return False
