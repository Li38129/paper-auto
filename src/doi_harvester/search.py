"""供 Agent 与 MCP 使用的 OpenAlex/Crossref 文献检索。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Protocol

import requests

from .doi import InvalidDoiError, normalize_doi


class SearchSession(Protocol):
    def get(self, url: str, **kwargs: Any) -> Any: ...


def _normalized_title(value: str) -> str:
    return re.sub(r"[^\w]+", "", value.casefold(), flags=re.UNICODE)


def _doi(value: object) -> str:
    try:
        return normalize_doi(str(value or ""))
    except InvalidDoiError:
        return ""


class LiteratureSearchClient:
    """以 OpenAlex 为主、Crossref 为补充生成标准文献记录。"""

    def __init__(
        self,
        *,
        session: SearchSession | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def search(
        self,
        query: str,
        *,
        year_from: int | None = None,
        year_to: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        if not query.strip():
            raise ValueError("query 不能为空。")
        if not 1 <= limit <= 200:
            raise ValueError("limit 必须在 1 到 200 之间。")
        if year_from and year_to and year_from > year_to:
            raise ValueError("year_from 不能大于 year_to。")

        records = self._openalex(query, year_from, year_to, limit)
        if len(records) < limit:
            records.extend(self._crossref(query, year_from, year_to, limit))
        return self._deduplicate(records)[:limit]

    def _openalex(
        self,
        query: str,
        year_from: int | None,
        year_to: int | None,
        limit: int,
    ) -> list[dict[str, object]]:
        filters: list[str] = []
        if year_from:
            filters.append(f"from_publication_date:{year_from}-01-01")
        if year_to:
            filters.append(f"to_publication_date:{year_to}-12-31")
        params: dict[str, object] = {"search": query, "per-page": limit}
        if filters:
            params["filter"] = ",".join(filters)
        try:
            response = self.session.get(
                "https://api.openalex.org/works",
                params=params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            results = response.json().get("results", [])
        except (requests.RequestException, AttributeError, TypeError, ValueError):
            return []
        records: list[dict[str, object]] = []
        for item in results if isinstance(results, list) else []:
            if not isinstance(item, Mapping):
                continue
            primary = item.get("primary_location")
            source = primary.get("source") if isinstance(primary, Mapping) else None
            records.append(
                {
                    "title": str(item.get("display_name") or "").strip(),
                    "doi": _doi(item.get("doi")),
                    "year": item.get("publication_year"),
                    "journal": str(source.get("display_name") or "")
                    if isinstance(source, Mapping)
                    else "",
                    "type": str(item.get("type") or ""),
                    "landing_url": str(item.get("doi") or item.get("id") or ""),
                    "source": "openalex",
                }
            )
        return records

    def _crossref(
        self,
        query: str,
        year_from: int | None,
        year_to: int | None,
        limit: int,
    ) -> list[dict[str, object]]:
        filters: list[str] = []
        if year_from:
            filters.append(f"from-pub-date:{year_from}-01-01")
        if year_to:
            filters.append(f"until-pub-date:{year_to}-12-31")
        params: dict[str, object] = {"query": query, "rows": limit}
        if filters:
            params["filter"] = ",".join(filters)
        try:
            response = self.session.get(
                "https://api.crossref.org/works",
                params=params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            items = response.json().get("message", {}).get("items", [])
        except (requests.RequestException, AttributeError, TypeError, ValueError):
            return []
        records: list[dict[str, object]] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, Mapping):
                continue
            titles = item.get("title")
            containers = item.get("container-title")
            published = item.get("published")
            parts = published.get("date-parts") if isinstance(published, Mapping) else None
            year = parts[0][0] if isinstance(parts, list) and parts and parts[0] else None
            records.append(
                {
                    "title": str(titles[0] if isinstance(titles, list) and titles else ""),
                    "doi": _doi(item.get("DOI")),
                    "year": year,
                    "journal": str(
                        containers[0] if isinstance(containers, list) and containers else ""
                    ),
                    "type": str(item.get("type") or ""),
                    "landing_url": str(item.get("URL") or ""),
                    "source": "crossref",
                }
            )
        return records

    @staticmethod
    def _deduplicate(records: list[dict[str, object]]) -> list[dict[str, object]]:
        output: list[dict[str, object]] = []
        seen: set[str] = set()
        for record in records:
            title = str(record.get("title") or "").strip()
            doi = str(record.get("doi") or "")
            key = f"doi:{doi}" if doi else f"title:{_normalized_title(title)}"
            if not title or key in seen:
                continue
            seen.add(key)
            output.append(record)
        return output
