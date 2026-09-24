"""把固定编号的论文 CSV 转为可恢复的补充材料任务。"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path

import requests


def _metadata(doi: str) -> tuple[str, int | None, str]:
    for attempt in range(3):
        try:
            response = requests.get(
                f"https://api.crossref.org/works/{doi}",
                timeout=20,
                headers={"User-Agent": "AutoPaper/0.1 (literature metadata)"},
            )
            response.raise_for_status()
            record = response.json()["message"]
            raw_title = (record.get("title") or [""])[0]
            title = " ".join(re.sub(r"<[^>]+>", "", unescape(raw_title)).split())
            dates = (record.get("published") or {}).get("date-parts") or []
            year = int(dates[0][0]) if dates and dates[0] else None
            journal = (record.get("container-title") or [""])[0]
            return title, year, journal
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
            if attempt < 2:
                time.sleep(attempt + 1)
    return "", None, ""


def prepare(args: argparse.Namespace) -> None:
    source = Path(args.csv).resolve()
    output = Path(args.output_dir).resolve()
    job_dir = Path(args.job_dir).resolve()
    previous_path = job_dir / "literature-records.json"
    previous = {}
    if previous_path.exists():
        previous = {
            item["doi"]: item
            for item in json.loads(previous_path.read_text(encoding="utf-8"))["records"]
        }
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"序号", "DOI", "目标文件名"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("CSV 缺少序号、DOI 或目标文件名列。")
        selected = [row for row in reader if args.start <= int(row["序号"]) <= args.end]
    sequences = [int(row["序号"]) for row in selected]
    dois = [row["DOI"].strip().lower() for row in selected]
    if sequences != list(range(args.start, args.end + 1)) or len(set(dois)) != len(dois):
        raise ValueError("选定序号不连续，或 DOI 存在重复。")
    valid_indexes = [index for index, doi in enumerate(dois) if doi.startswith("10.") and "/" in doi]
    metadata_by_index = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {}
        for index in valid_indexes:
            cached = previous.get(dois[index], {})
            if cached.get("title"):
                metadata_by_index[index] = (
                    str(cached.get("title") or ""),
                    cached.get("year"),
                    str(cached.get("journal") or ""),
                )
            else:
                futures[index] = pool.submit(_metadata, dois[index])
        for index, future in futures.items():
            metadata_by_index[index] = future.result()
    records = []
    targets = []
    papers = []
    for index, (row, doi) in enumerate(zip(selected, dois, strict=True)):
        title, year, journal = metadata_by_index.get(index, ("", None, ""))
        prior = previous.get(doi, {})
        title = title or str(prior.get("title") or "")
        year = year or prior.get("year")
        journal = journal or str(prior.get("journal") or "")
        sequence = int(row["序号"])
        valid_doi = doi.startswith("10.") and "/" in doi
        if not valid_doi and not title:
            title = f"DOI 待核验（原序号 {sequence}）"
        folder_name = f"{sequence:04d}_{doi.replace('/', '_')}" if valid_doi else ""
        folder_path = output / folder_name
        page = (
            (f"https://advanced.onlinelibrary.wiley.com/doi/{doi}"
             if doi.startswith("10.1002/") else f"https://doi.org/{doi}")
            if valid_doi else ""
        )
        records.append(
            {
                "rank": sequence,
                "sequence": sequence,
                "folder_name": folder_name,
                "folder_label": doi.replace("/", "_") if valid_doi else f"DOI待核验_{sequence}",
                "title": title,
                "doi": doi if valid_doi else "",
                "year": year,
                "journal": journal,
                "paper_type": "",
                "system": "",
                "metric": "",
                "source_url": page,
            }
        )
        if valid_doi:
            papers.append(
                {"rank": sequence, "doi": doi, "title": title, "folder_path": str(folder_path)}
            )
        targets.append(
            {
                "序号": sequence,
                "原始DOI": doi,
                "DOI": doi if valid_doi else "",
                "论文题名": title,
                "原目标文件名": row["目标文件名"],
                "出版社入口": page,
                "SI目标目录": str(folder_path / "supplements") if valid_doi else "",
                "计划状态": "待下载" if valid_doi else "DOI待核验",
            }
        )
    job_dir.mkdir(parents=True, exist_ok=True)
    records_path = job_dir / "literature-records.json"
    resolved_path = job_dir / "resolved-records.json"
    papers_path = job_dir / "papers.json"
    records_path.write_text(
        json.dumps(
            {"schema_version": 1, "topic": "固定清单补充材料", "records": records},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    script = (
        Path(__file__).resolve().parents[1]
        / ".agents/skills/autopaper-literature/scripts/literature-workbook.mjs"
    )
    workbook = output / "文献检索汇总.xlsx"
    command = [
        args.node,
        str(script),
        "--node-modules",
        args.node_modules,
        "--workbook",
        str(workbook),
        "--records",
        str(records_path),
        "--resolved",
        str(resolved_path),
    ]
    subprocess.run(command, check=True)
    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))["records"]
    actual_papers = [item for item in resolved if item.get("doi")]
    for actual, planned in zip(actual_papers, papers, strict=True):
        if actual["sequence"] != planned["rank"] or Path(actual["folder_path"]) != Path(
            planned["folder_path"]
        ):
            raise ValueError("Excel 返回的编号或目录与 CSV 不一致。")
    papers_path.write_text(
        json.dumps({"schema_version": 1, "papers": papers}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    target_path = output / f"SI下载目标_{args.start:04d}-{args.end:04d}.csv"
    with target_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(targets[0]))
        writer.writeheader()
        writer.writerows(targets)
    print(
        json.dumps(
            {
                "count": len(targets),
                "workbook": str(workbook),
                "target_csv": str(target_path),
                "papers_file": str(papers_path),
                "missing_titles": sum(not item["论文题名"] for item in targets),
                "missing_doi": sum(item["计划状态"] == "DOI待核验" for item in targets),
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--node-modules", required=True)
    prepare(parser.parse_args())


if __name__ == "__main__":
    main()
