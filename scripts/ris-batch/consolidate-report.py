"""把多个任务的逐篇状态合并成一份统一回写报告（通用版）。

用途：同一批 DOI 可能分布在多个 DOI Harvester 任务（取消、重试、分段）里。
      逐份报告回写会让状态倒退；本脚本按 DOI 取“最终状态”，并把磁盘
      <目录>/supplements 下已存在的附件补登记为 SI 成功。

用法：
  python scripts/ris-batch/consolidate-report.py --db temp\\doi-harvester\\jobs\\jobs.sqlite3 \\
      --job 20260101-000000-aaaaaaaa --job 20260102-000000-bbbbbbbb --out temp\\job\\final

输出：<out>\\batch-report.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

SUCCESS = {"downloaded", "cached"}


def main() -> int:
    parser = argparse.ArgumentParser(description="合并多任务状态为一份回写报告")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--job", action="append", required=True, help="可重复传入任务 ID")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    final: dict[str, dict] = {}
    for job in args.job:
        for row in con.execute("select * from job_items where job_id=?", (job,)):
            doi = row["doi"].lower()
            entry = final.setdefault(doi, {
                "doi": row["doi"],
                "title": row["title"],
                "requested_folder_path": row["folder_path"],
                "article_dir": row["folder_path"],
                "success": False,
                "status": "pending",
                "pdf_path": None,
                "source": "",
                "failure_reason": "",
                "supplement_status": "not_requested",
                "supplements": [],
                "download_mode": "article_and_supplements",
            })
            status = row["status"] or "pending"
            if status in SUCCESS:
                entry.update(
                    success=True, status=status, pdf_path=row["pdf_path"],
                    source=row["source"] or "", failure_reason="",
                )
                supplements = json.loads(row["supplements_json"] or "[]")
                if supplements:
                    entry["supplements"] = supplements
                if row["supplement_status"]:
                    entry["supplement_status"] = row["supplement_status"]
            elif entry["status"] not in SUCCESS:
                entry.update(
                    success=False, status=status, pdf_path=row["pdf_path"],
                    source=row["source"] or "",
                    failure_reason=row["failure_reason"] or status,
                )
                if row["supplement_status"]:
                    entry["supplement_status"] = row["supplement_status"]

    # 磁盘 supplements 附件补登记：正文失败不代表 SI 未落盘
    for entry in final.values():
        supplement_dir = Path(entry["requested_folder_path"]) / "supplements"
        if not supplement_dir.is_dir():
            continue
        files = sorted(path for path in supplement_dir.rglob("*") if path.is_file())
        if not files:
            continue
        known = {str(item.get("path") or item.get("filename") or "") for item in entry["supplements"]}
        merged = list(entry["supplements"])
        for path in files:
            if str(path) not in known:
                merged.append({"path": str(path), "filename": path.name, "bytes": path.stat().st_size})
        entry["supplements"] = merged
        if entry["supplement_status"] not in SUCCESS:
            entry["supplement_status"] = "downloaded"

    results = sorted(final.values(), key=lambda item: item["requested_folder_path"] or "")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "batch-report.json").write_text(
        json.dumps({"schema_version": 1, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ok = sum(1 for item in results if item["success"])
    print(json.dumps({"results": len(results), "success": ok, "failed": len(results) - ok}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
