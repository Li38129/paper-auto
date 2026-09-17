"""从公开学术元数据服务生成 PDF 候选入口。"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import quote

import requests

from .models import ArticleMetadata, DownloadCandidate
from .publisher_profiles import infer_publisher_profile

LOGGER = logging.getLogger(__name__)


class MetadataError(RuntimeError):
    """元数据服务不可用或返回异常。"""


class SupportsGet(Protocol):
    """便于测试注入的最小 HTTP 会话协议。"""

    def get(self, url: str, **kwargs: Any) -> Any: ...


def _publisher_candidates(doi: str) -> list[DownloadCandidate]:
    """为已知出版社补充稳定正文入口。"""
    profile = infer_publisher_profile(doi)
    if profile is None:
        return []
    return [
        DownloadCandidate(
            url=template.format(doi=doi),
            source=f"publisher:{profile.key}",
            open_access=None,
        )
        for template in profile.pdf_templates
    ]


def _deduplicate(candidates: list[DownloadCandidate]) -> list[DownloadCandidate]:
    result: list[DownloadCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.url.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


class CrossrefMetadataClient:
    """读取 Crossref Works API，并提取正文 PDF 候选。"""

    base_url = "https://api.crossref.org/works"

    def __init__(
        self,
        *,
        session: SupportsGet | None = None,
        email: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.session = session or requests.Session()
        self.email = email or os.getenv("CROSSREF_MAILTO", "")
        self.timeout_seconds = timeout_seconds
        headers = getattr(self.session, "headers", None)
        if headers is not None:
            contact = f"; mailto:{self.email}" if self.email else ""
            headers.update({"User-Agent": f"doi-harvester/0.1 ({contact})"})

    def fetch(self, doi: str) -> ArticleMetadata:
        params = {"mailto": self.email} if self.email else None
        try:
            response = self.session.get(
                f"{self.base_url}/{quote(doi, safe='')}",
                params=params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise MetadataError(f"Crossref 查询失败：{exc}") from exc

        message = payload.get("message")
        if not isinstance(message, Mapping):
            raise MetadataError("Crossref 响应缺少 message 对象")

        titles = message.get("title") or []
        title = str(titles[0]) if isinstance(titles, list) and titles else ""
        publisher = str(message.get("publisher") or "")
        landing_url = str(message.get("URL") or f"https://doi.org/{doi}")
        candidates: list[DownloadCandidate] = []

        links = message.get("link") or []
        if isinstance(links, list):
            for link in links:
                if not isinstance(link, Mapping):
                    continue
                url = str(link.get("URL") or "").strip()
                content_type = str(link.get("content-type") or "").lower()
                if url and ("pdf" in content_type or "/pdf" in url.lower()):
                    candidates.append(
                        DownloadCandidate(url=url, source="crossref", open_access=None)
                    )

        candidates.extend(_publisher_candidates(doi))
        return ArticleMetadata(
            doi=doi,
            title=title,
            publisher=publisher,
            landing_url=landing_url,
            candidates=_deduplicate(candidates),
        )


class OpenAlexMetadataClient:
    """从 OpenAlex 补充开放获取 PDF 入口。"""

    base_url = "https://api.openalex.org/works"

    def __init__(
        self,
        *,
        session: SupportsGet | None = None,
        email: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.session = session or requests.Session()
        self.email = email or os.getenv("OPENALEX_MAILTO", "")
        self.timeout_seconds = timeout_seconds

    def fetch_candidates(self, doi: str) -> list[DownloadCandidate]:
        params = {"mailto": self.email} if self.email else None
        url = f"{self.base_url}/https://doi.org/{doi}"
        try:
            response = self.session.get(url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError, TypeError) as exc:
            LOGGER.info("OpenAlex 未提供 %s 的入口：%s", doi, exc)
            return []

        candidates: list[DownloadCandidate] = []
        locations = payload.get("locations") or []
        if isinstance(locations, list):
            for location in locations:
                if not isinstance(location, Mapping):
                    continue
                pdf_url = str(location.get("pdf_url") or "").strip()
                if pdf_url:
                    candidates.append(
                        DownloadCandidate(url=pdf_url, source="openalex", open_access=True)
                    )
        return _deduplicate(candidates)


def merge_candidates(
    preferred: list[DownloadCandidate], fallback: list[DownloadCandidate]
) -> list[DownloadCandidate]:
    """按优先级合并候选并保持顺序。"""
    return _deduplicate([*preferred, *fallback])
