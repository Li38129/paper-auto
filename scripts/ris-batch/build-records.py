"""合成工作簿写入记录（通用版）。

用途：把 RIS 记录、期刊指标与整理清单去重结果合并为 literature-records.json，
      供 scripts/literature-workbook-fallback.py --records 写入工作簿；
      同时输出已存在条目的回写报告和待下载清单。

用法：
  python scripts/ris-batch/build-records.py --records E:\\work\\records.json --metrics E:\\work\\期刊指标.csv \\
      --dedup E:\\work\\dedup-check.json --topic "主题" --out E:\\work [--duplicate-note "来源说明"]

--dedup 由 scripts/dataset-list-sync.py check --json 生成；缺省时不做去重标记。

输出：
  literature-records.json   工作簿输入
  dedup-report.json         已存在条目（already_listed）
  pending.csv               待下载 DOI
"""

from __future__ import annotations

import argparse
import csv
import json
import runpy
from pathlib import Path

candidate_key = runpy.run_path(str(Path(__file__).resolve().parents[1] / "dataset-list-sync.py"))[
    "candidate_key"
]


def main() -> int:
    parser = argparse.ArgumentParser(description="合成工作簿记录")
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--dedup", type=Path)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--duplicate-note", default="已存在于用户 DOI 清单，按去重规则跳过下载")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    records = json.loads(args.records.read_text(encoding="utf-8"))["records"]
    already: set[str] = set()
    failed: set[str] = set()
    failed_list = ""
    if args.dedup:
        payload = json.loads(args.dedup.read_text(encoding="utf-8-sig"))
        already = {str(doi).strip().lower() for doi in payload.get("already_listed", [])}
        failed = set(payload.get("already_attempted_failed", []))
        failed_list = payload.get("failed_list", "")

    with args.metrics.open(encoding="utf-8-sig", newline="") as handle:
        metrics = {row["journal"].lower(): row for row in csv.DictReader(handle)}

    out: list[dict] = []
    pending: list[dict] = []
    missing_metrics: list[str] = []
    for index, record in enumerate(records, start=1):
        metric = metrics.get(record["journal"].lower())
        if metric is None:
            missing_metrics.append(record["journal"])
        out.append(
            {
                "rank": index,
                "title": record["title"],
                "doi": record["doi"],
                "year": record["year"],
                "journal": record["journal"],
                "journal_abbreviation": record.get("journal_abbreviation") or "",
                "cas_quartile": (metric or {}).get("cas_quartile", "未核实"),
                "impact_factor": (metric or {}).get("impact_factor", "未核实"),
                "journal_metrics_source": (metric or {}).get("metrics_source", "未核实"),
                "paper_type": "期刊论文",
                "system": "",
                "metric": "",
                "source_url": f"https://doi.org/{record['doi']}" if record["doi"] else "",
            }
        )
        if candidate_key(record["doi"]) not in already:
            pending.append(
                {
                    "rank": index,
                    "doi": record["doi"],
                    "title": record["title"],
                    "journal": record["journal"],
                    "year": record["year"],
                }
            )

    (args.out / "literature-records.json").write_text(
        json.dumps({"schema_version": 1, "topic": args.topic, "records": out}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    dedup_report = {
        "schema_version": 1,
        "results": [
            {
                "doi": doi,
                "title": next((r["title"] for r in records if candidate_key(r["doi"]) == doi), ""),
                "success": False,
                "status": "already_listed",
                "download_mode": "article",
                "failure_reason": (
                    f"已尝试且无正文，来源：{failed_list}" if doi in failed else args.duplicate_note
                ),
                "pdf_path": "",
                "source": "已有 DOI 清单",
            }
            for doi in sorted(already)
        ],
    }
    (args.out / "dedup-report.json").write_text(
        json.dumps(dedup_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    with (args.out / "pending.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["rank", "doi", "title", "journal", "year"])
        writer.writeheader()
        writer.writerows(pending)

    print(json.dumps({
        "records": len(out),
        "pending": len(pending),
        "already_listed": len(already),
        "missing_metrics": len(missing_metrics),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
