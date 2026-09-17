"""单篇与批量 DOI 下载编排。"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

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
        elsevier: ElsevierApiClient | None = None,
        elsevier_credentials: ElsevierCredentials | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.browser_fallback = browser_fallback
        self.crossref = crossref or CrossrefMetadataClient(email=email)
        self.openalex = openalex or OpenAlexMetadataClient(email=email)
        self.transport = transport or HttpPdfTransport()
        self.browser_options = browser_options or {}
        self.download_supplements = download_supplements
        self.elsevier = elsevier or ElsevierApiClient()
        self.elsevier_credentials = elsevier_credentials

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
        pdf_path = article_dir / "article.pdf"
        article_dir.mkdir(parents=True, exist_ok=True)

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
            return result

        metadata = self._metadata(doi)
        open_candidates = self.openalex.fetch_candidates(doi)
        result = DownloadResult(
            doi=doi,
            success=False,
            status="failed",
            article_dir=article_dir,
            title=metadata.title,
            publisher=metadata.publisher,
        )

        referer = metadata.landing_url or f"https://doi.org/{doi}"
        self._download_candidates(
            result,
            candidates=open_candidates,
            destination=pdf_path,
            referer=referer,
            stage="open_access",
        )

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
        return result

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
        if reason in {"api_key_missing", "config_error", "playwright_not_installed"}:
            result.outcome = "config_needed"
            command = (
                "doi-harvester elsevier-setup --set-key --validate"
                if reason in {"api_key_missing", "config_error"}
                else "uv sync --extra browser"
            )
            result.next_action = NextAction(
                kind="configure",
                message="完成所需本地配置后重试。",
                command=command,
            )
        elif reason in {"challenge_required", "authentication_required"}:
            result.outcome = "auth_required"
            result.next_action = NextAction(
                kind="authenticate",
                message="在可见浏览器中完成站点验证或机构登录后重试。",
                command="doi-harvester auth --publisher acs --cdp",
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
        profile_dir = self.browser_options.get("profile_dir")
        if profile_dir is None:
            result.supplement_status = "browser_session_required"
            return
        from .supplements import BrowserSupplementDownloader

        downloader = BrowserSupplementDownloader(profile_dir=Path(profile_dir))
        status, artifacts, attempts = downloader.download(
            doi=result.doi,
            article_dir=result.article_dir,
        )
        result.supplement_status = status
        result.supplements = artifacts
        result.supplement_attempts = attempts

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
