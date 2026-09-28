"""为下载任务打开并核验出版社工作页面。"""

from __future__ import annotations

import json
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .browser import (
    BrowserAuthorizer,
    _read_cdp_endpoint,
    classify_page,
    require_edge_browser,
    wait_for_verification,
)
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
    try:
        target = normalize_doi(doi)
    except InvalidDoiError:
        return False
    page_url = urlsplit(str(page.url))
    page_path = unquote(page_url.path).rstrip("/")
    arxiv_prefix = "10.48550/arxiv."
    if target.startswith(arxiv_prefix) and (
        (page_url.hostname or "").casefold() in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
    ):
        return page_path.casefold() == f"/abs/{target[len(arxiv_prefix) :]}"

    def candidate(raw: str) -> str | None:
        parsed = urlsplit(raw)
        if parsed.scheme in {"http", "https"}:
            path = unquote(parsed.path).rstrip("/")
            if (parsed.hostname or "").casefold().endswith("frontiersin.org"):
                path = re.sub(r"/full$", "", path, flags=re.IGNORECASE)
            if (parsed.hostname or "").casefold() in {"doi.org", "dx.doi.org"}:
                raw = path.lstrip("/")
            else:
                match = re.search(r"(?:^|/)10\.\d{4,9}/", path, re.IGNORECASE)
                if not match:
                    return None
                raw = path[match.start() :].lstrip("/")
        try:
            return normalize_doi(raw)
        except InvalidDoiError:
            return None

    values = page.evaluate(
        """() => Array.from(document.querySelectorAll(
          'meta[name="citation_doi"], meta[name="dc.identifier"], '
          + 'meta[name="DC.Identifier"], meta[name="prism.doi"], '
          + 'meta[name="doi"], meta[property="og:doi"], '
          + 'meta[name="citation_id"], link[rel="canonical"], [itemprop="doi"]'
        )).map(node => node.content || node.href || node.textContent || '')"""
    )
    metadata = {value for raw in values or [] if (value := candidate(str(raw)))}
    url_doi = candidate(str(page.url))
    known = metadata | ({url_doi} if url_doi else set())
    return known == {target}


def wait_for_sciencedirect_article(page: object) -> bool:
    """等待 Elsevier DOI 中间页跳转到带完整正文内容的 ScienceDirect 页面。"""
    host = (urlsplit(str(getattr(page, "url", ""))).hostname or "").casefold()
    if host == "linkinghub.elsevier.com":
        try:
            page.wait_for_url("https://www.sciencedirect.com/science/article/**", timeout=20000)
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            host = (urlsplit(str(getattr(page, "url", ""))).hostname or "").casefold()
        except Exception:
            return False
    if host != "www.sciencedirect.com":
        return False
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        if page.evaluate("() => document.documentElement.dataset.autopaperSdReady === 'true'"):
            return True
        attached = page.locator(
            "a[href*='-mmc'], a[href*='supplementary'], a[href*='supplemental']"
        ).count()
        if attached == 0:
            # ScienceDirect 的 SI 下载链接由页面脚本延迟加入。
            with suppress(Exception):
                page.wait_for_function(
                    "() => Array.from(document.querySelectorAll('a[href]'))"
                    + ".some(a => /-mmc/i.test(a.href))",
                    timeout=5000,
                )
        page.evaluate("() => { document.documentElement.dataset.autopaperSdReady = 'true'; }")
        return True
    except Exception:
        return False


def work_page(context: object, profile_dir: Path | None = None) -> object:
    """复用专用工作标签，避免覆盖用户打开的其他网页。"""
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
        self.channel = channel or "msedge"

    def show(
        self, *, doi: str, rank: int | None, mode: str, _reconnect_attempted: bool = False
    ) -> DisplayResult:
        """每篇先打开页面；失败时调用方必须暂停队列。"""
        target = f"https://doi.org/{doi}"
        if self.channel != "msedge":
            return DisplayResult(
                "browser_display_unavailable",
                target,
                "explicit_non_edge_channel:请确认改用外部 Edge",
            )
        endpoint = _read_cdp_endpoint(self.profile_dir)
        events = ["browser_connected"] if endpoint else ["browser_started"]
        if not endpoint:
            started = BrowserAuthorizer(
                profile_dir=self.profile_dir, channel="msedge", cdp=True
            ).authorize(publisher="download", doi=doi, timeout_seconds=0)
            endpoint = started.cdp_endpoint or _read_cdp_endpoint(self.profile_dir)
            if not endpoint:
                return DisplayResult(
                    "browser_display_unavailable", started.final_url or target, started.status
                )
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.connect_over_cdp(endpoint, timeout=10000)
                except Exception as exc:
                    raise _CDPConnectionError(str(exc)) from exc
                require_edge_browser(browser)
                context = browser.contexts[0]
                page = work_page(context, self.profile_dir)
                session = context.new_cdp_session(page)
                try:
                    target_id = session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
                finally:
                    session.detach()
                # 本次请求与真实标签绑定；加载错误不阻断缓存和独立传输路径。
                record = {
                    "target_id": target_id,
                    "endpoint": endpoint,
                    "doi": doi,
                    "target_url": target,
                    "navigation_result": "requested",
                }
                self.profile_dir.mkdir(parents=True, exist_ok=True)
                marker = self.profile_dir / "work-target.json"
                marker.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
                try:
                    page.goto(target, wait_until="commit", timeout=10000)
                    record["navigation_result"] = "committed"
                except Exception as exc:
                    record["navigation_result"] = f"navigation_error:{type(exc).__name__}:{exc}"
                    events.append("publisher_navigation_failed")
                if page.is_closed():
                    return DisplayResult("browser_display_unavailable", target, "work_tab_closed")
                if page.url.startswith("https://doi.org/") and doi.startswith("10.1021/"):
                    with suppress(Exception):
                        page.goto(
                            f"https://pubs.acs.org/doi/{doi}", wait_until="commit", timeout=10000
                        )
                        events.append("acs_direct_fallback")
                record["current_url"] = page.url
                marker.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
                with suppress(Exception):
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                wait_for_sciencedirect_article(page)
                status = wait_for_verification(page, initial_status=classify_page(page))
                if page.is_closed():
                    return DisplayResult("browser_display_unavailable", target, "work_tab_closed")
                if status in {"challenge_required", "authentication_required"}:
                    return DisplayResult(status, page.url, "authorization_required_after_10s")
                verified = False
                if not page.url.startswith("https://doi.org/"):
                    with suppress(Exception):
                        verified = _page_matches_doi(page, doi)
                events.append(
                    "article_content_verified" if verified else "article_content_unconfirmed"
                )
                if not self.update(
                    doi=doi, rank=rank, mode=mode, stage="Edge 标签已打开", page=page
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
            result = self.show(doi=doi, rank=rank, mode=mode, _reconnect_attempted=True)
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
