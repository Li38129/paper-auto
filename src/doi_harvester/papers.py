"""读取 literature-search-organizer 与下载器之间的任务清单。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .doi import InvalidDoiError, normalize_doi


class PapersFileError(ValueError):
    """papers.json 不符合交换格式。"""


@dataclass(frozen=True, slots=True)
class PaperJob:
    """一篇已排序论文及其目标目录。"""

    rank: int
    doi: str
    title: str
    folder_path: Path
    journal: str = ""
    issn: str = ""
    year: int | None = None


def load_paper_jobs(path: Path) -> list[PaperJob]:
    """读取并验证 papers.json，返回已规范化的下载任务。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise PapersFileError(f"papers.json 不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise PapersFileError(f"papers.json 不是有效 JSON：{exc}") from exc

    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise PapersFileError("papers.json 必须使用 schema_version=1。")
    raw_papers = payload.get("papers")
    if not isinstance(raw_papers, list) or not raw_papers:
        raise PapersFileError("papers.json 的 papers 必须是非空数组。")

    jobs: list[PaperJob] = []
    seen_dois: set[str] = set()
    seen_ranks: set[int] = set()
    for index, raw_paper in enumerate(raw_papers, start=1):
        if not isinstance(raw_paper, Mapping):
            raise PapersFileError(f"第 {index} 条论文记录必须是对象。")
        rank = raw_paper.get("rank")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            raise PapersFileError(f"第 {index} 条记录的 rank 必须是正整数。")
        if rank in seen_ranks:
            raise PapersFileError(f"发现重复 rank：{rank}")
        seen_ranks.add(rank)

        try:
            doi = normalize_doi(str(raw_paper.get("doi") or ""))
        except InvalidDoiError as exc:
            raise PapersFileError(f"第 {index} 条记录 DOI 无效：{exc}") from exc
        if doi in seen_dois:
            raise PapersFileError(f"发现重复 DOI：{doi}")
        seen_dois.add(doi)

        title = str(raw_paper.get("title") or "").strip()
        if not title:
            raise PapersFileError(f"第 {index} 条记录缺少 title。")
        folder_path = Path(str(raw_paper.get("folder_path") or ""))
        if not folder_path.is_absolute():
            raise PapersFileError(f"第 {index} 条记录的 folder_path 必须是绝对路径。")
        raw_year = raw_paper.get("year")
        if raw_year in {None, ""}:
            year = None
        elif isinstance(raw_year, int) and not isinstance(raw_year, bool) and raw_year > 0:
            year = raw_year
        else:
            raise PapersFileError(f"第 {index} 条记录的 year 必须是正整数或空值。")
        jobs.append(
            PaperJob(
                rank=rank,
                doi=doi,
                title=title,
                folder_path=folder_path,
                journal=str(raw_paper.get("journal") or "").strip(),
                issn=str(raw_paper.get("issn") or "").strip(),
                year=year,
            )
        )

    return sorted(jobs, key=lambda item: item.rank)
