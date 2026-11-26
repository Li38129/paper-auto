"""匹配期刊的中科院分区与 JCR 影响因子（通用版）。

用途：把 records.json 中的期刊按 ISSN（优先）或规范化刊名，匹配到本地
      ShowJCR 开放数据（FQBJCR2025 中科院分区表、JCR 影响因子表），
      输出带来源说明的期刊指标表，并列出未匹配项。

用法：
  python scripts/ris-batch/journal-metrics.py --records E:\\work\\records.json --metrics-dir E:\\showjcr --out E:\\work

--metrics-dir 需包含：分区表 CSV（含 Journal/ISSN-EISSN/大类分区/Top 列）与
JCR CSV（含 Journal/ISSN/EISSN/IF 列）。默认按文件名关键字自动识别，也可用
--partition-file / --jcr-file 显式指定。

输出：
  期刊指标.csv    journal,abbreviation,issn,count,cas_quartile,impact_factor,...
  metrics-report.json  未匹配与缺简称清单
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

SOURCE_NOTE = "分区：中科院分区表（ShowJCR 开放数据）；IF：JCR（ShowJCR 开放数据）"


def norm_issn(value: str) -> str:
    return re.sub(r"[^0-9X]", "", (value or "").upper())


def name_key(value: str) -> str:
    text = re.sub(r"&amp;", "&", value or "")
    text = re.sub(r"[^0-9a-zA-Z& ]+", " ", text)
    return " ".join(text.lower().split())


def load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def header_of(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle), [])


def pick_file(directory: Path, kind: str, explicit: Path | None) -> Path:
    """按表头特征识别分区表与 JCR 表，避免 FQBJCR/JCR 文件名互相误选。"""
    if explicit:
        return explicit
    for path in sorted(directory.glob("*.csv")):
        columns = [column.strip() for column in header_of(path)]
        if kind == "partition" and "大类分区" in columns:
            return path
        if kind == "jcr" and any(column.upper().startswith("IF") for column in columns):
            return path
    raise SystemExit(f"在 {directory} 找不到{kind}对应的 CSV，请用参数显式指定")


def main() -> int:
    parser = argparse.ArgumentParser(description="匹配期刊分区与影响因子")
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--metrics-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--partition-file", type=Path)
    parser.add_argument("--jcr-file", type=Path)
    parser.add_argument("--source-note", default=SOURCE_NOTE)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    records = json.loads(args.records.read_text(encoding="utf-8"))["records"]
    partition_rows = load(pick_file(args.metrics_dir, "partition", args.partition_file))
    jcr_rows = load(pick_file(args.metrics_dir, "jcr", args.jcr_file))

    partition_by_issn: dict[str, dict] = {}
    partition_by_name: dict[str, dict] = {}
    for row in partition_rows:
        for token in str(row.get("ISSN/EISSN", "")).split("/"):
            key = norm_issn(token)
            if key:
                partition_by_issn.setdefault(key, row)
        partition_by_name.setdefault(name_key(row.get("Journal", "")), row)

    jcr_by_issn: dict[str, dict] = {}
    jcr_by_name: dict[str, dict] = {}
    for row in jcr_rows:
        for field in ("ISSN", "EISSN"):
            key = norm_issn(row.get(field, ""))
            if key:
                jcr_by_issn.setdefault(key, row)
        jcr_by_name.setdefault(name_key(row.get("Journal", "")), row)

    journals: dict[str, dict] = {}
    for record in records:
        key = "|".join(record.get("issn", [])) or name_key(record["journal"])
        entry = journals.setdefault(
            key,
            {
                "journal": record["journal"],
                "abbreviation": record.get("journal_abbreviation") or "",
                "issn": record.get("issn", []),
                "count": 0,
            },
        )
        entry["count"] += 1

    out_rows: list[dict] = []
    unmatched: list[dict] = []
    for entry in journals.values():
        partition_row = jcr_row = None
        for issn in entry["issn"]:
            token = norm_issn(issn)
            partition_row = partition_row or partition_by_issn.get(token)
            jcr_row = jcr_row or jcr_by_issn.get(token)
        key = name_key(entry["journal"])
        partition_row = partition_row or partition_by_name.get(key)
        jcr_row = jcr_row or jcr_by_name.get(key)

        quartile = ""
        if partition_row:
            big = str(partition_row.get("大类分区", "")).strip()
            match = re.match(r"\d", big)
            if match:
                quartile = f"{match.group(0)}区"
            if str(partition_row.get("Top", "")).strip() == "是":
                quartile = f"{quartile}Top"
        impact = ""
        if jcr_row:
            for field in ("IF(2025)", "IF(2024)", "IF"):
                value = str(jcr_row.get(field, "")).strip()
                if value:
                    impact = value
                    break
        row = {
            "journal": entry["journal"],
            "abbreviation": entry["abbreviation"],
            "issn": "|".join(entry["issn"]),
            "count": entry["count"],
            "cas_quartile": quartile or "未核实",
            "impact_factor": impact or "未核实",
            "metrics_source": args.source_note if (partition_row or jcr_row) else "未核实",
        }
        out_rows.append(row)
        if not quartile or not impact:
            unmatched.append(row)

    out_rows.sort(key=lambda item: (-item["count"], item["journal"].lower()))
    with (args.out / "期刊指标.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(out_rows[0].keys()))
        writer.writeheader()
        writer.writerows(out_rows)

    report = {
        "journals": len(out_rows),
        "fully_matched": len(out_rows) - len(unmatched),
        "unmatched": unmatched,
        "missing_abbreviation": [row["journal"] for row in out_rows if not row["abbreviation"]],
    }
    (args.out / "metrics-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in report.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
