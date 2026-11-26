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
import hashlib
import json
import os
import re
import runpy
import sys
import tempfile
from contextlib import contextmanager, suppress
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


def candidate_key(raw: str) -> str:
    try:
        return normalize_doi(raw)
    except ValueError:
        return ""


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
    except OSError as exc:
        raise OSError(f"清单未替换，临时文件：{temporary}") from exc


def failure_list(args: argparse.Namespace) -> Path | None:
    path = getattr(args, "failed_list", None)
    if path is None and args.list.name == "整理清单_成功.csv":
        path = args.list.with_name("整理清单_已尝试但未下载成功.xlsx")
    if path is not None and not path.is_file():
        raise FileNotFoundError(f"失败清单不存在：{path}")
    return path


def success_keys(rows):
    keys = {}
    for row in rows:
        for raw in [row["DOI"], *(row.get("DOI别名") or "").split("|")]:
            if raw.strip():
                keys[normalize_doi(raw)] = row
    return keys


def failed_sheet(book):
    sheet = book.active
    fields = {c.value: c.column for c in sheet[1] if c.value}
    if "DOI" not in fields or "Sequence" not in fields:
        raise ValueError("失败清单首行须含 DOI、Sequence")
    return sheet, fields


def command_check(args: argparse.Namespace) -> int:
    prefixes = tuple(args.exclude_prefix or DEFAULT_EXCLUDES)
    _, rows = read_list(args.list)
    existing = set(success_keys(rows))
    failed = set()
    path = failure_list(args)
    if path:
        from openpyxl import load_workbook

        book = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet, fields = failed_sheet(book)
            col = fields["DOI"]
            failed = {
                normalize_doi(row[0])
                for row in sheet.iter_rows(min_row=2, min_col=col, max_col=col, values_only=True)
                if row[0]
            }
        finally:
            book.close()
        existing.update(failed)
    keys = map(candidate_key, read_candidates(args.candidates))
    candidates = list(dict.fromkeys(filter(None, keys)))
    already = [doi for doi in candidates if doi in existing]
    excluded = [doi for doi in candidates if is_excluded(doi, prefixes) and doi not in existing]
    fresh = [doi for doi in candidates if doi not in existing and not is_excluded(doi, prefixes)]
    payload = {
        "list": str(args.list),
        "candidates": len(candidates),
        "already_listed": already,
        "failed_list": str(path) if path else None,
        "already_attempted_failed": [doi for doi in already if doi in failed],
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


@contextmanager
def pair_lock(path: Path):
    """将同一组清单的并发回写串行化，进程退出后系统释放锁。"""
    key = hashlib.sha256(os.path.normcase(str(path.resolve())).encode()).hexdigest()
    with (Path(tempfile.gettempdir()) / f"autopaper-{key}.lock").open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def report_body(result, folder, fs):
    """在报告正文路径及下载器标准文件名中核对正文，排除附件封面。"""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    candidates = []
    if result.get("pdf_path"):
        candidates.append(Path(fs["display_path"](result["pdf_path"])))
    if folder and Path(folder).is_absolute():
        directory = Path(folder)
        named = directory / (directory.name + ".pdf")
        candidates.extend([directory / "article.pdf", named])
        with suppress(ValueError):
            candidates.append(fs["shell_safe_file_path"](named))
    for candidate in dict.fromkeys(candidates):
        local = fs["native_file_path"](candidate)
        if not local.is_file() or local.suffix.lower() != ".pdf":
            continue
        with local.open("rb") as handle:
            if b"%PDF-" not in handle.read(1024):
                continue
            handle.seek(0)
            try:
                pdf = PdfReader(handle, strict=False)
                if not len(pdf.pages):
                    continue
                text = " ".join((pdf.pages[0].extract_text() or "").split())
            except (PyPdfError, ValueError, IndexError, KeyError):
                continue
        if re.match(
            r"^(?:(?:S[- ]?)?\d+\s+)?(?:Electronic\s+)?"
            r"(?:Supporting Information|Supplement(?:ary|al) (?:Material|Information))\b",
            text, re.IGNORECASE,
        ):
            continue
        return str(candidate)
    return ""


def apply_pair(args: argparse.Namespace, path: Path) -> int:
    from openpyxl import load_workbook

    fs = runpy.run_path(str(PROJECT / "src/doi_harvester/filesystem.py"))
    fields, rows = read_list(args.list)
    if not set(HEADERS) <= set(fields):
        raise ValueError("成功清单缺少必需列：" + "、".join(sorted(set(HEADERS) - set(fields))))
    results = json.loads(args.report.read_text(encoding="utf-8-sig"))["results"]
    known = success_keys(rows)
    book = load_workbook(path)
    try:
        sheet, cols = failed_sheet(book)
        failures = {}
        sequences = list(rows)
        for idx, values in enumerate(sheet.values):
            if not idx:
                continue
            doi = values[cols["DOI"] - 1]
            if doi:
                failures[normalize_doi(doi)] = idx + 1
            sequences.append({"Sequence": str(values[cols["Sequence"] - 1] or "")})
        seq, width = next_sequence(sequences)
        remove = set()
        success_changed = failed_changed = False
        for result in results:
            if not result.get("doi"):
                continue
            doi = normalize_doi(result["doi"])
            status = result.get("status")
            if status in {None, "", "pending", "running", "policy_skipped", "already_listed"}:
                continue
            index = failures.get(doi)
            if doi in known:
                if index:
                    remove.add(index)
                continue
            folder = fs["display_path"](
                result.get("article_dir") or result.get("requested_folder_path") or ""
            )
            pdf_path = report_body(result, folder, fs)
            if not pdf_path and result.get("download_mode") == "supplements_only":
                continue
            if index:
                number = sheet.cell(index, cols["Sequence"]).value
            else:
                number = str(seq).zfill(width)
                seq += 1
            if pdf_path:
                items = [
                    item
                    for item in result.get("supplements", [])
                    if item.get("path") and fs["native_file_path"](Path(item["path"])).is_file()
                ]
                row = dict(
                    Sequence=number,
                    DOI=doi,
                    Article=pdf_path,
                    SupplementFolder=fs["display_path"](Path(items[0]["path"]).parent)
                    if items
                    else "",
                    SupplementFileCount=str(len(items)),
                    OutputFolder=folder or str(Path(pdf_path).parent),
                    Status="copied" if items else "body_only_copied",
                )
                rows.append(row)
                known[doi] = row
                success_changed = True
                if index:
                    remove.add(index)
            else:
                if not index:
                    index = sheet.max_row + 1
                    failures[doi] = index
                record = {
                    "Sequence": number,
                    "DOI": doi,
                    "论文题名": result.get("title"),
                    "失败原因": result.get("failure_reason") or "未发现正文 PDF",
                    "历史状态": status,
                    "OutputFolder": folder,
                    "来源": str(args.report.resolve()),
                    "Status": "failed",
                }
                for name, value in record.items():
                    if name in cols and value and sheet.cell(index, cols[name]).value != value:
                        sheet.cell(index, cols[name], value)
                        failed_changed = True
        for index in sorted(remove, reverse=True):
            sheet.delete_rows(index)
        if not args.dry_run:
            # 先写成功清单，断电时两表依然能够拦截该 DOI，重放报告可消除重复。
            if success_changed:
                write_list(args.list, fields, rows)
            if failed_changed or remove:
                sheet.auto_filter.ref = sheet.dimensions
                fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".xlsx", prefix=path.stem + "-")
                os.close(fd)
                try:
                    book.save(tmp)
                    os.replace(tmp, path)
                except OSError as exc:
                    raise OSError(f"失败清单未替换，临时文件：{tmp}。请重放同一报告") from exc
        print(
            json.dumps(
                {
                    "success": len(rows),
                    "failed_with_doi": len(failures) - len(remove),
                    "dry_run": args.dry_run,
                },
                ensure_ascii=False,
            )
        )
    finally:
        book.close()
    return 0


def command_apply(args: argparse.Namespace) -> int:
    path = failure_list(args)
    if path:
        with pair_lock(args.list):
            return apply_pair(args, path)
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
    check.add_argument("--failed-list", type=Path, help="同时用于去重的失败工作簿")
    check.add_argument("--exclude-prefix", action="append", default=None)
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=command_check)

    apply_parser = subparsers.add_parser("apply", help="把下载报告结果回写清单")
    apply_parser.add_argument("--report", type=Path, required=True)
    apply_parser.add_argument("--list", type=Path, required=True)
    apply_parser.add_argument("--failed-list", type=Path, help="按正文结果分别回写成功和失败清单")
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
