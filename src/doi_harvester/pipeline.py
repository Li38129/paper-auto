"""单篇与批量 DOI 下载编排。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .doi import doi_slug, normalize_doi
from .metadata import (
    CrossrefMetadataClient,
    MetadataError,
    OpenAlexMetadataClient,
    merge_candidates,
)
from .models import ArticleMetadata, DownloadResult
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
    ) -> None:
        self.output_dir = Path(output_dir)
        self.browser_fallback = browser_fallback
        self.crossref = crossref or CrossrefMetadataClient(email=email)
        self.openalex = openalex or OpenAlexMetadataClient(email=email)
        self.transport = transport or HttpPdfTransport()
        self.browser_options = browser_options or {}
        self.download_supplements = download_supplements

    def download(
        self,
        raw_doi: str,
        *,
        overwrite: bool = False,
        article_dir: Path | None = None,
    ) -> DownloadResult:
        doi = normalize_doi(raw_doi)
        article_dir = (
            Path(article_dir)
            if article_dir is not None
            else self.output_dir / doi_slug(doi)
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
            )
            self._add_supplements(result)
            return result

        metadata = self._metadata(doi)
        open_candidates = self.openalex.fetch_candidates(doi)
        metadata.candidates = merge_candidates(open_candidates, metadata.candidates)
        result = DownloadResult(
            doi=doi,
            success=False,
            status="failed",
            article_dir=article_dir,
            title=metadata.title,
            publisher=metadata.publisher,
        )

        referer = metadata.landing_url or f"https://doi.org/{doi}"
        for candidate in metadata.candidates:
            attempt = self.transport.download(
                url=candidate.url,
                destination=pdf_path,
                referer=referer,
            )
            attempt.source = candidate.source
            result.attempts.append(attempt)
            if attempt.success:
                result.success = True
                result.status = "downloaded"
                result.pdf_path = pdf_path
                result.source = candidate.source
                break

        if not result.success and self.browser_fallback:
            from .browser import BrowserPdfDownloader

            browser = BrowserPdfDownloader(**self.browser_options)
            attempt = browser.download(
                doi=doi,
                destination=pdf_path,
                candidate_urls=[item.url for item in metadata.candidates],
            )
            result.attempts.append(attempt)
            if attempt.success:
                result.success = True
                result.status = "downloaded"
                result.pdf_path = pdf_path
                result.source = "browser"

        if not result.success and result.attempts:
            result.status = result.attempts[-1].reason

        self._add_supplements(result)
        return result

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
