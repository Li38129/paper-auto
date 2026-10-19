"""把下载任务与用户已有 DOI 清单去重，并把逐篇结果回写该清单。

用途：AutoPaper 文献工作流在启动下载前与用户维护的 DOI 清单（如
`E:\\Dataset\\整理清单.csv`）核对，避免重复下载；下载结束后把结果 upsert 回清单。
清单路径一律由参数传入，脚本不硬编码任何用户路径。

清单列（沿用现有格式）：Sequence, DOI, Article, SupplementFolder,
SupplementFileCount, OutputFolder, Status。

命令：
  python scripts/dataset-list-sync.py check --candidates 本次清单.csv --list 整理清单.csv
  python scripts/dataset-list-sync.py apply --report batch-report.json --list 整理清单.csv --dry-run
  python scripts/dataset-list-sync.py apply --report batch-report.json --list 整理清单.csv

恢复：check 只读；apply 先写临时文件再原子替换，失败时原清单保持不变。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import runpy
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
normalize_doi = runpy.run_path(str(PROJECT / "src/doi_harvester/doi.py"))["normalize_doi"]

HEADERS = [
    "Sequence",
    "DOI",
    "Article",
    "SupplementFolder",
    "SupplementFileCount",
    "OutputFolder",
    "Status",
]

# 规则排除的出版社前缀，与 AGENTS.md 第 8 条一致
DEFAULT_EXCLUDES = ("10.3390/", "10.1149/")
SUCCESS_STATUSES = {"downloaded", "cached"}


def read_candidates(path: Path) -> list[str]:
    """读取候选 DOI：CSV 取 DOI 列，其他格式按行读取。"""
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        rows = list(csv.DictReader(text.splitlines()))
        field = next(
            (name for name in (rows[0] if rows else {}) if name.strip().lower() == "doi"),
            None,
        )
        if field is None:
            raise ValueError(f"候选 CSV 缺少 DOI 列：{path}")
        return [row[field].strip() for row in rows if row.get(field, "").strip()]
    return [line.strip() for line in text.splitlines() if line.strip()]


def read_list(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """读取已有清单，返回列顺序与行数据。"""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        return fieldnames, list(reader)


def is_excluded(doi: str, prefixes: tuple[str, ...]) -> bool:
    return any(doi.startswith(prefix) for prefix in prefixes)


def status_for(result: dict) -> str:
    """按既有取值把报告结果映射为清单 Status。"""
    status = str(result.get("status") or "").lower()
    if status not in SUCCESS_STATUSES:
        return "failed"
    supplement = str(result.get("supplement_status") or "not_requested").lower()
    if supplement in {"downloaded", "cached"}:
        return "copied"
    return "body_only_copied"


def next_sequence(rows: list[dict[str, str]]) -> tuple[int, int]:
    """返回下一个序号与既有补零宽度（至少 4 位）。"""
    values = [int(row["Sequence"]) for row in rows if str(row.get("Sequence", "")).isdigit()]
    width = max((len(str(row["Sequence"]).strip()) for row in rows), default=4)
    return (max(values) + 1 if values else 1), max(width, 4)


def supplement_count(result: dict) -> int:
    items = result.get("supplements")
    if isinstance(items, list):
        return len(items)
    return 0


def write_list(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    """原子写回清单，保留原编码与列顺序。"""
    handle, temporary = tempfile.mkstemp(
        prefix=path.stem + "-", suffix=".csv", dir=path.parent
    )
    os.close(handle)
    try:
        with Path(temporary).open("w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({name: row.get(name, "") for name in fieldnames})
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def command_check(args: argparse.Namespace) -> int:
    prefixes = tuple(args.exclude_prefix or DEFAULT_EXCLUDES)
    _, rows = read_list(args.list)
    existing = {
        normalize_doi(str(row.get("DOI", "")))
        for row in rows
        if str(row.get("DOI", "")).strip()
    }
    candidates = []
    for raw in read_candidates(args.candidates):
        try:
            candidates.append(normalize_doi(raw))
        except Exception:  # noqa: BLE001
            continue
    already = [doi for doi in candidates if doi in existing]
    excluded = [doi for doi in candidates if is_excluded(doi, prefixes) and doi not in existing]
    fresh = [
        doi for doi in candidates if doi not in existing and not is_excluded(doi, prefixes)
    ]
    payload = {
        "list": str(args.list),
        "candidates": len(candidates),
        "already_listed": already,
        "excluded_by_rule": excluded,
        "new": fresh,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"清单：{args.list}")
    print(
        f"候选 {len(candidates)} 条｜已存在 {len(already)}"
        f"｜按规则排除 {len(excluded)}｜新增 {len(fresh)}"
    )
    for title, items in (("已存在", already), ("按规则排除", excluded)):
        for doi in items[:20]:
            print(f"  {title}：{doi}")
    return 0


def command_apply(args: argparse.Namespace) -> int:
    fieldnames, rows = read_list(args.list)
    if not fieldnames:
        raise ValueError(f"清单为空或缺少表头：{args.list}")
    missing = [name for name in ("Sequence", "DOI", "Status") if name not in fieldnames]
    if missing:
        raise ValueError(f"清单缺少必需列：{'、'.join(missing)}")

    payload = json.loads(Path(args.report).read_text(encoding="utf-8-sig"))
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("报告缺少 results 数组")

    by_doi = {
        normalize_doi(str(row.get("DOI", ""))): row
        for row in rows
        if str(row.get("DOI", "")).strip()
    }
    sequence, width = next_sequence(rows)
    added = updated = kept = 0
    changes: list[str] = []
    for result in results:
        raw_doi = str(result.get("doi") or "").strip()
        if not raw_doi:
            continue
        doi = normalize_doi(raw_doi)
        status = status_for(result)
        folder = str(result.get("article_dir") or result.get("requested_folder_path") or "")
        row = by_doi.get(doi)
        if row is None:
            row = {name: "" for name in fieldnames}
            row["Sequence"] = str(sequence).zfill(width)
            row["DOI"] = doi
            row["Article"] = ""
            row["SupplementFolder"] = ""
            row["SupplementFileCount"] = str(supplement_count(result))
            row["OutputFolder"] = folder
            row["Status"] = status
            rows.append(row)
            by_doi[doi] = row
            sequence += 1
            added += 1
            changes.append(f"  新增 {row['Sequence']} {doi} -> {status}")
            continue
        before = str(row.get("Status", "")).strip()
        folder_changed = bool(folder) and str(row.get("OutputFolder", "")).strip() != folder
        if folder_changed:
            row["OutputFolder"] = folder
        if before and not args.overwrite_status:
            # 保留用户已确认的状态，但仍允许补齐输出目录
            kept += 1
            if folder_changed:
                updated += 1
                changes.append(f"  目录 {row.get('Sequence', '')} {doi}：{folder}")
            continue
        if before != status:
            row["Status"] = status
            updated += 1
            changes.append(f"  更新 {row.get('Sequence', '')} {doi}：{before or '空'} -> {status}")

    for line in changes[: args.show]:
        print(line)
    if len(changes) > args.show:
        print(f"  …… 另有 {len(changes) - args.show} 条变更")

    summary = {
        "list": str(args.list),
        "results": len(results),
        "added": added,
        "updated": updated,
        "kept_existing_status": kept,
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    if added or updated:
        write_list(args.list, fieldnames, rows)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="已有 DOI 清单去重与结果回写")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="比对候选 DOI 与已有清单")
    check.add_argument("--candidates", type=Path, required=True)
    check.add_argument("--list", type=Path, required=True)
    check.add_argument("--exclude-prefix", action="append", default=None)
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=command_check)

    apply_parser = subparsers.add_parser("apply", help="把下载报告结果回写清单")
    apply_parser.add_argument("--report", type=Path, required=True)
    apply_parser.add_argument("--list", type=Path, required=True)
    apply_parser.add_argument("--dry-run", action="store_true")
    apply_parser.add_argument(
        "--overwrite-status",
        action="store_true",
        help="覆盖已有非空 Status；默认保留。",
    )
    apply_parser.add_argument("--show", type=int, default=10)
    apply_parser.set_defaults(func=command_apply)

    args = parser.parse_args()
    if not args.list.is_file():
        print(f"清单不存在：{args.list}", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
