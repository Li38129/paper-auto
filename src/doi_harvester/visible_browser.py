"""为下载任务打开并核验出版社工作页面。"""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .browser import BrowserAuthorizer, _read_cdp_endpoint, classify_page
from .doi import InvalidDoiError, normalize_doi


@dataclass(frozen=True)
class DisplayResult:
    status: str
    url: str
    event: str


class _CDPConnectionError(Exception):
    """标记调试连接失败，供外层退出 Playwright 后重连。"""


def _page_matches_doi(page: object, doi: str) -> bool:
    """核对工作标签 URL 或论文元数据中的 DOI。"""
    if doi in unquote(urlsplit(str(page.url)).path).casefold():
        return True
    values = page.evaluate(
        """() => Array.from(document.querySelectorAll(
          'meta[name="citation_doi"], meta[name="dc.identifier"], '
          + 'meta[name="DC.Identifier"], meta[name="prism.doi"], '
          + 'meta[name="doi"], meta[property="og:doi"], '
          + 'meta[name="citation_id"], link[rel="canonical"], [itemprop="doi"]'
        )).map(node => node.content || node.href || node.textContent || '')"""
    )
    for value in values or []:
        try:
            if normalize_doi(str(value)) == doi:
                return True
        except InvalidDoiError:
            if doi in unquote(str(value)).casefold():
                return True
    return False


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

    def show(
        self, *, doi: str, rank: int | None, mode: str, _reconnect_attempted: bool = False
    ) -> DisplayResult:
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
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.connect_over_cdp(endpoint, timeout=10000)
                except Exception as exc:
                    raise _CDPConnectionError(str(exc)) from exc
                context = browser.contexts[0]
                page = work_page(context, self.profile_dir)
                if not opened_url or page.url != opened_url:
                    try:
                        page.goto(target, wait_until="domcontentloaded", timeout=60000)
                    except PlaywrightTimeoutError:
                        events.append("publisher_navigation_timeout")
                if page.url.startswith("https://doi.org/"):
                    with suppress(PlaywrightTimeoutError):
                        page.wait_for_url(
                            lambda url: not url.startswith("https://doi.org/"),
                            timeout=20000,
                        )
                with suppress(PlaywrightTimeoutError):
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                page.wait_for_timeout(1500)
                events.append("publisher_navigated")
                if page.url.startswith("https://doi.org/"):
                    return DisplayResult(
                        "browser_publisher_unavailable", page.url, "publisher_navigation_failed"
                    )
                status = classify_page(page)
                if status in {"challenge_required", "authentication_required"}:
                    return DisplayResult(status, page.url, "authorization_required")
                if not _page_matches_doi(page, doi):
                    return DisplayResult(
                        "browser_publisher_unavailable", page.url, "doi_not_confirmed"
                    )
                # 工作标签尽量显示给用户，但系统焦点和窗口状态不阻断已核对的页面。
                with suppress(Exception):
                    page.bring_to_front()
                    session = context.new_cdp_session(page)
                    window = session.send("Browser.getWindowForTarget")
                    session.send(
                        "Browser.setWindowBounds",
                        {"windowId": window["windowId"], "bounds": {"windowState": "normal"}},
                    )
                if not self.update(
                    doi=doi, rank=rank, mode=mode, stage="页面已打开", page=page
                ):
                    events.append("status_panel_unavailable")
                events.append("article_page_opened")
                return DisplayResult("visible", page.url, ";".join(events))
        except _CDPConnectionError as exc:
            if _reconnect_attempted:
                return DisplayResult(
                    "browser_display_unavailable", target, f"cdp_reconnect_failed:{exc}"
                )
            # 退出旧 Playwright 上下文后再启动授权器，避免同步 API 嵌套。
            restarted = BrowserAuthorizer(
                profile_dir=self.profile_dir, channel=self.channel, cdp=True
            ).authorize(publisher="download", doi=doi, timeout_seconds=0)
            if not restarted.cdp_endpoint:
                return DisplayResult(
                    "browser_display_unavailable", restarted.final_url or target, restarted.status
                )
            result = self.show(
                doi=doi, rank=rank, mode=mode, _reconnect_attempted=True
            )
            if result.status == "visible":
                return DisplayResult(
                    result.status, result.url, f"browser_reconnected;{result.event}"
                )
            return result
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
