"""单篇与批量 DOI 下载编排。"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .access_policy import AccessPolicyStore
from .config import ConfigError, ElsevierCredentials, load_elsevier_credentials
from .doi import doi_slug, normalize_doi
from .elsevier import ElsevierApiClient
from .metadata import (
    CrossrefMetadataClient,
    MetadataError,
    OpenAlexMetadataClient,
)
from .models import ArticleMetadata, Attempt, DownloadCandidate, DownloadResult, NextAction
from .publisher_profiles import infer_publisher_profile
from .transport import HttpPdfTransport, is_valid_pdf

LOGGER = logging.getLogger(__name__)


class Harvester:
    """按优先级尝试开放入口与出版社入口。"""

    def __init__(
        self,
        *,
        output_dir: Path,
        browser_fallback: bool = False,
        email: str | None = None,
        crossref: CrossrefMetadataClient | None = None,
        openalex: OpenAlexMetadataClient | None = None,
        transport: HttpPdfTransport | None = None,
        browser_options: dict[str, Any] | None = None,
        download_supplements: bool = False,
        supplements_only: bool = False,
        browser_display: str = "off",
        elsevier: ElsevierApiClient | None = None,
        elsevier_credentials: ElsevierCredentials | None = None,
        access_store: AccessPolicyStore | None = None,
        ignore_access_policy: bool = False,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.browser_fallback = browser_fallback
        self.crossref = crossref or CrossrefMetadataClient(email=email)
        self.openalex = openalex or OpenAlexMetadataClient(email=email)
        self.transport = transport or HttpPdfTransport()
        self.browser_options = browser_options or {}
        self.download_supplements = download_supplements
        self.supplements_only = supplements_only
        self.browser_display = browser_display
        self.visible_browser = None
        self.display_rank: int | None = None
        self._last_display = None
        self.elsevier = elsevier or ElsevierApiClient()
        self.elsevier_credentials = elsevier_credentials
        self.access_store = access_store or AccessPolicyStore()
        self.ignore_access_policy = ignore_access_policy

    def download(
        self,
        raw_doi: str,
        *,
        overwrite: bool = False,
        article_dir: Path | None = None,
    ) -> DownloadResult:
        doi = normalize_doi(raw_doi)
        article_dir = (
            Path(article_dir) if article_dir is not None else self.output_dir / doi_slug(doi)
        )
        if self.browser_display == "foreground":
            from .visible_browser import VisibleBrowser

            if self.visible_browser is None:
                from .browser import BrowserPdfDownloader

                self.visible_browser = VisibleBrowser(
                    profile_dir=Path(
                        self.browser_options.get("profile_dir")
                        or BrowserPdfDownloader._default_profile_dir()
                    ),
                    channel=self.browser_options.get("channel"),
                )
            mode = (
                "仅补充材料" if self.supplements_only
                else "正文与补充材料" if self.download_supplements else "仅正文"
            )
            display = self.visible_browser.show(doi=doi, rank=self.display_rank, mode=mode)
            self._last_display = display
            if display.status != "visible":
                return DownloadResult(
                    doi=doi, success=False, status=display.status,
                    article_dir=article_dir,
                    reason=f"{display.status}:{display.event}",
                    outcome=("auth_required" if display.status in {
                        "challenge_required", "authentication_required"
                    } else "blocked"),
                    attempts=[Attempt(
                        source="browser_display", url=display.url, success=False,
                        reason=f"{display.status}:{display.event}", stage="browser_display",
                    )],
                )
        pdf_path = article_dir / "article.pdf"
        article_dir.mkdir(parents=True, exist_ok=True)

        if self.supplements_only:
            result = DownloadResult(
                doi=doi,
                success=False,
                status="supplement_pending",
                article_dir=article_dir,
            )
            self._add_supplements(result)
            result.success = result.supplement_status in {"downloaded", "cached", "not_found"}
            result.status = result.supplement_status
            result.reason = "" if result.success else result.supplement_status
            self._display_result(result)
            return result

        if pdf_path.exists() and not overwrite and is_valid_pdf(pdf_path):
            result = DownloadResult(
                doi=doi,
                success=True,
                status="cached",
                article_dir=article_dir,
                pdf_path=pdf_path,
                source="cache",
                outcome="success",
                quality="pdf",
            )
            self._add_supplements(result)
            self._display_result(result)
            return result

        if self.visible_browser is not None:
            self.visible_browser.update(
                doi=doi, rank=self.display_rank,
                mode="正文与补充材料" if self.download_supplements else "仅正文",
                stage="获取正文",
            )
        metadata = self._metadata(doi)
        open_candidates = self.openalex.fetch_candidates(doi)
        result = DownloadResult(
            doi=doi,
            success=False,
            status="failed",
            article_dir=article_dir,
            title=metadata.title,
            publisher=metadata.publisher,
            journal=metadata.journal,
            issns=metadata.issns,
            year=metadata.year,
        )

        referer = metadata.landing_url or f"https://doi.org/{doi}"
        self._download_candidates(
            result,
            candidates=open_candidates,
            destination=pdf_path,
            referer=referer,
            stage="open_access",
        )

        access_rule = None if self.ignore_access_policy else self.access_store.match(metadata)
        if not result.success and access_rule is not None:
            result.status = "policy_skipped"
            result.reason = "access_policy_skip_paid"
            result.outcome = "blocked"
            result.quality = "metadata"
            result.attempts.append(
                Attempt(
                    source="access_policy",
                    url=referer,
                    success=False,
                    reason="access_policy_skip_paid",
                    stage="access_policy",
                    provider=access_rule.journal,
                    route=access_rule.policy,
                )
            )
            self._add_supplements(result)
            self._display_result(result)
            return result

        profile = infer_publisher_profile(
            doi,
            publisher=metadata.publisher,
            landing_url=metadata.landing_url,
        )
        if not result.success and profile and profile.key == "elsevier":
            self._download_elsevier(result, destination=pdf_path)

        if not result.success:
            self._download_candidates(
                result,
                candidates=metadata.candidates,
                destination=pdf_path,
                referer=referer,
                stage="publisher_candidate",
            )

        if not result.success and self.browser_fallback:
            from .browser import BrowserPdfDownloader

            browser = BrowserPdfDownloader(**self.browser_options)
            started = time.perf_counter()
            attempt = browser.download(
                doi=doi,
                destination=pdf_path,
                candidate_urls=[item.url for item in metadata.candidates],
            )
            attempt.stage = "browser_fallback"
            attempt.provider = profile.key if profile else "browser"
            attempt.route = "persistent_profile"
            attempt.duration_ms = int((time.perf_counter() - started) * 1000)
            result.attempts.append(attempt)
            if attempt.success:
                result.success = True
                result.status = "downloaded"
                result.pdf_path = pdf_path
                result.source = "browser"

        if not result.success and result.attempts:
            result.status = result.attempts[-1].reason

        self._classify_result(result)

        self._add_supplements(result)
        self._display_result(result)
        return result

    def _display_result(self, result: DownloadResult) -> None:
        if self.visible_browser is not None:
            if self._last_display is not None:
                result.attempts.insert(0, Attempt(
                    source="browser_display", url=self._last_display.url,
                    success=True, reason=self._last_display.event,
                    stage="browser_display", final_url=self._last_display.url,
                ))
            mode = (
                "仅补充材料" if self.supplements_only
                else "正文与补充材料" if self.download_supplements else "仅正文"
            )
            self.visible_browser.update(
                doi=result.doi, rank=self.display_rank, mode=mode,
                stage=(f"正文:{result.status}；SI:{result.supplement_status}"
                       if self.download_supplements and not self.supplements_only
                       else result.supplement_status if self.supplements_only else result.status),
                attachments=len(result.supplements),
            )

    def _download_candidates(
        self,
        result: DownloadResult,
        *,
        candidates: list[DownloadCandidate],
        destination: Path,
        referer: str,
        stage: str,
    ) -> None:
        for candidate in candidates:
            started = time.perf_counter()
            attempt = self.transport.download(
                url=candidate.url,
                destination=destination,
                referer=referer,
            )
            attempt.source = candidate.source
            attempt.stage = stage
            attempt.provider = candidate.source.split(":", 1)[-1]
            attempt.route = "direct"
            attempt.duration_ms = int((time.perf_counter() - started) * 1000)
            result.attempts.append(attempt)
            if attempt.success:
                result.success = True
                result.status = "downloaded"
                result.pdf_path = destination
                result.source = candidate.source
                return

    def _download_elsevier(self, result: DownloadResult, *, destination: Path) -> None:
        try:
            credentials = self.elsevier_credentials or load_elsevier_credentials()
        except ConfigError:
            result.attempts.append(
                Attempt(
                    source="elsevier_api",
                    url=f"https://doi.org/{result.doi}",
                    success=False,
                    reason="config_error",
                    stage="publisher_api",
                    provider="elsevier",
                )
            )
            return
        download = self.elsevier.download(
            doi=result.doi,
            destination=destination,
            api_key=credentials.api_key,
            inst_token=credentials.inst_token,
            proxy_url=credentials.proxy_url,
        )
        result.attempts.extend(download.attempts)
        result.warnings.extend(download.warnings)
        if download.success:
            result.success = True
            result.status = "downloaded"
            result.pdf_path = destination
            result.source = download.source
        elif not download.attempts:
            result.attempts.append(
                Attempt(
                    source="elsevier_api",
                    url=f"https://doi.org/{result.doi}",
                    success=False,
                    reason=download.reason,
                    stage="publisher_api",
                    provider="elsevier",
                )
            )

    @staticmethod
    def _classify_result(result: DownloadResult) -> None:
        if result.success:
            result.outcome = "success"
            result.quality = "pdf"
            result.reason = ""
            result.next_action = None
            return
        reason = result.status or "failed"
        result.reason = reason
        result.quality = "none"
        if reason in {
            "api_key_missing",
            "api_configuration_error",
            "config_error",
            "playwright_not_installed",
        }:
            result.outcome = "config_needed"
            command = (
                "doi-harvester elsevier-setup --set-key --validate"
                if reason in {"api_key_missing", "api_configuration_error", "config_error"}
                else "uv sync --extra browser"
            )
            message = (
                "Elsevier 拒绝了当前开发者应用配置，请检查 API Key 的 Article Retrieval 权限。"
                if reason == "api_configuration_error"
                else "完成所需本地配置后重试。"
            )
            result.next_action = NextAction(
                kind="configure",
                message=message,
                command=command,
            )
        elif reason in {"challenge_required", "authentication_required"}:
            profile = infer_publisher_profile(result.doi, publisher=result.publisher)
            publisher_key = (
                profile.key if profile and profile.key in {"acs", "elsevier", "rsc"} else "acs"
            )
            result.outcome = "auth_required"
            result.next_action = NextAction(
                kind="authenticate",
                message="在可见浏览器中完成站点验证或机构登录后重试。",
                command=f"doi-harvester auth --publisher {publisher_key} --cdp",
            )
        elif reason in {"subscription_required", "not_entitled"}:
            result.outcome = "subscription_required"
            result.next_action = NextAction(
                kind="check_subscription",
                message="确认当前校园网或机构订阅覆盖该论文。",
            )
        else:
            result.outcome = "blocked"
            result.next_action = NextAction(
                kind="inspect_attempts",
                message="检查下载尝试记录并按最终失败原因处理。",
            )

    def _add_supplements(self, result: DownloadResult) -> None:
        if not self.download_supplements:
            return
        if self.visible_browser is not None:
            self.visible_browser.update(
                doi=result.doi, rank=self.display_rank,
                mode="仅补充材料" if self.supplements_only else "正文与补充材料",
                stage="发现及下载 SI",
            )
        from .supplements import HttpSupplementDownloader

        profile_dir = self.browser_options.get("profile_dir")
        downloader = HttpSupplementDownloader(
            profile_dir=Path(profile_dir) if profile_dir else None
        )
        status, artifacts, attempts = downloader.download(
            doi=result.doi,
            article_dir=result.article_dir,
        )
        result.supplement_status = status
        result.supplements = artifacts
        result.supplement_attempts = attempts
        if status not in {"downloaded", "cached", "not_found"}:
            result.success = False
            result.reason = status

    def _metadata(self, doi: str) -> ArticleMetadata:
        try:
            return self.crossref.fetch(doi)
        except MetadataError as exc:
            LOGGER.warning("%s；继续使用出版社规则。", exc)
            from .metadata import _publisher_candidates

            return ArticleMetadata(
                doi=doi,
                landing_url=f"https://doi.org/{doi}",
                candidates=_publisher_candidates(doi),
            )
