"""把 RIS 解析为标准化记录清单（通用版，路径全部走参数）。

用途：为 RIS 批量下载流程生成 records.json、DOI 清单与期刊分布，
      过滤 MDPI/IOP/ECS（AGENTS.md 第 8 条），并报告缺 DOI 与重复 DOI。

用法：
  python scripts/ris-batch/extract-ris.py --ris E:\\path\\input.ris --work E:\\path\\work

输出（写入 --work 目录）：
  records.json     结构化记录（含 excluded_records）
  DOI清单.csv      rank,doi,title,journal,issn,year
  期刊分布.csv     journal,count
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

EXCLUDED_DOI_PREFIXES = ("10.3390/", "10.1149/")
EXCLUDED_JOURNAL_HINTS = ("mdpi", "iopscience", "ecsdl")


def normalize_doi(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"^doi:\s*", "", text)
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    return re.sub(r"[?#].*$", "", text).strip()


def parse_ris(text: str) -> list[dict[str, list[str]]]:
    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] | None = None
    last = ""
    for raw in re.split(r"\r\n|\n|\r", text.lstrip("\ufeff")):
        match = re.match(r"^([A-Z0-9]{2})  - ?(.*)$", raw)
        if match:
            tag, value = match.group(1), match.group(2)
            if tag == "TY":
                if current is not None:
                    records.append(current)
                current = {"TY": [value]}
                last = "TY"
                continue
            if current is None:
                continue
            current.setdefault(tag, []).append(value)
            if tag == "ER":
                records.append(current)
                current = None
                last = ""
            else:
                last = tag
            continue
        if current is not None and last and raw.strip():
            target = current.setdefault(last, [""])
            target[-1] = (target[-1] + " " + raw.strip()).strip()
    if current is not None:
        records.append(current)
    return records


def first(record: dict[str, list[str]], *tags: str) -> str:
    for tag in tags:
        values = [value.strip() for value in record.get(tag, []) if value.strip()]
        if values:
            return values[0]
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="解析 RIS 并生成记录清单")
    parser.add_argument("--ris", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()

    args.work.mkdir(parents=True, exist_ok=True)
    text = args.ris.read_text(encoding="utf-8", errors="replace")
    raw = parse_ris(text)

    seen: set[str] = set()
    rows: list[dict] = []
    excluded: list[dict] = []
    missing_doi: list[int] = []
    duplicates: list[str] = []
    for index, record in enumerate(raw, start=1):
        doi = normalize_doi(first(record, "DO", "DI"))
        title = re.sub(r"\s+", " ", first(record, "TI", "T1")).strip()
        journal = re.sub(r"\s+", " ", first(record, "T2", "JO", "JF")).strip()
        year_match = re.search(r"\d{4}", first(record, "PY", "Y1"))
        issns = sorted({re.sub(r"[^0-9Xx]", "", value).upper() for value in record.get("SN", []) if value.strip()})
        row = {
            "rank": index,
            "doi": doi,
            "title": title,
            "journal": journal,
            "journal_abbreviation": first(record, "J9") or first(record, "JI"),
            "issn": issns,
            "year": int(year_match.group(0)) if year_match else None,
            "type": first(record, "TY"),
            "publisher": first(record, "PU"),
            "volume": first(record, "VL"),
            "issue": first(record, "IS"),
            "pages": first(record, "SP"),
            "keywords": [value.strip() for value in record.get("KW", []) if value.strip()],
            "abstract": first(record, "AB"),
            "author_count": len(record.get("AU", [])),
            "wos": first(record, "AN"),
        }
        if not doi:
            missing_doi.append(index)
        elif doi in seen:
            duplicates.append(doi)
        else:
            seen.add(doi)
        low_journal = journal.lower()
        if doi.startswith(EXCLUDED_DOI_PREFIXES) or any(hint in low_journal for hint in EXCLUDED_JOURNAL_HINTS):
            row["excluded_rule"] = "按规则跳过（IOP/ECS）" if doi.startswith("10.1149/") else "按规则跳过（MDPI）"
            excluded.append(row)
            continue
        rows.append(row)

    payload = {
        "schema_version": 1,
        "source_ris": str(args.ris),
        "total_records": len(raw),
        "included": len(rows),
        "excluded": len(excluded),
        "missing_doi": missing_doi,
        "duplicate_dois": duplicates,
        "records": rows,
        "excluded_records": excluded,
    }
    (args.work / "records.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with (args.work / "DOI清单.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rank", "doi", "title", "journal", "issn", "year"])
        for row in rows:
            writer.writerow([row["rank"], row["doi"], row["title"], row["journal"], "|".join(row["issn"]), row["year"] or ""])

    counter = Counter(str(row["journal"]).lower() for row in rows)
    with (args.work / "期刊分布.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["journal", "count"])
        writer.writerows(counter.most_common())

    print(json.dumps({
        "total_records": len(raw),
        "included": len(rows),
        "excluded": len(excluded),
        "missing_doi": len(missing_doi),
        "duplicate_dois": len(duplicates),
        "journals": len(counter),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
