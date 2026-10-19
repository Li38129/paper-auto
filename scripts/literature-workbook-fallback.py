"""缺少 Artifact Tool 时使用的通用单页工作簿接口，保持稳定编号。"""

from __future__ import annotations

import argparse
import copy
import json
import os
import runpy
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment

PROJECT = Path(__file__).resolve().parents[1]
# 单独加载现有纯函数，避免工作簿入口初始化网络下载依赖。
normalize_doi = runpy.run_path(str(PROJECT / "src/doi_harvester/doi.py"))["normalize_doi"]
literature_name = runpy.run_path(str(PROJECT / "src/doi_harvester/naming.py"))["literature_name"]
native_file_path = runpy.run_path(str(PROJECT / "src/doi_harvester/filesystem.py"))[
    "native_file_path"
]

HEADERS = [
    "序号",
    "目录名",
    "论文题名",
    "DOI",
    "年份",
    "期刊",
    "文献类型",
    "体系/最佳组成",
    "关注指标",
    "检索主题",
    "文献入口",
    "下载成功",
    "SI是否下载成功",
    "下载状态",
    "失败原因",
    "PDF路径",
    "下载来源",
    "首次收录时间",
    "最后更新时间",
    "中科院分区",
    "影响因子（IF）",
    "分区/IF依据",
]
FIELDS = {
    "title": "论文题名",
    "doi": "DOI",
    "year": "年份",
    "journal": "期刊",
    "paper_type": "文献类型",
    "system": "体系/最佳组成",
    "metric": "关注指标",
    "source_url": "文献入口",
    "cas_quartile": "中科院分区",
    "impact_factor": "影响因子（IF）",
    "journal_metrics_source": "分区/IF依据",
}


def read_json(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if payload.get("schema_version") != 1:
        raise ValueError("交换数据必须使用 schema_version=1")
    return payload


def key_doi(value):
    return normalize_doi(str(value).strip()) if value else ""


def load(workbook_path, template_path):
    book = openpyxl.load_workbook(workbook_path if workbook_path.exists() else template_path)
    if "文献清单" not in book.sheetnames:
        raise ValueError("工作簿缺少文献清单页，原文件未修改")
    sheet = book["文献清单"]
    if (
        "LiteratureTable" not in sheet.tables
        or [sheet.cell(4, c).value for c in range(1, 23)] != HEADERS
    ):
        raise ValueError("后备入口仅支持22列单页模板，其他结构须使用主入口迁移")
    rows = []
    for values in sheet.iter_rows(min_row=5, max_col=22, values_only=True):
        if any(v is not None and str(v).strip() for v in values):
            rows.append(dict(zip(HEADERS, values, strict=True)))
    return book, sheet, rows


def validate_rows(rows):
    sequences, folders, dois = set(), set(), set()
    for row in rows:
        seq, folder, doi = int(row["序号"]), str(row["目录名"]).casefold(), key_doi(row["DOI"])
        if seq < 1 or seq in sequences or folder in folders or (doi and doi in dois):
            raise ValueError("已有记录的序号、目录或DOI冲突，原文件未修改")
        sequences.add(seq)
        folders.add(folder)
        if doi:
            dois.add(doi)


def upsert(rows, payload, folder_root):
    validate_rows(rows)
    records = payload.get("records", [])
    if not records:
        raise ValueError("records不能为空")
    ranks, seen_dois, seen_titles = set(), set(), set()
    for raw in records:
        rank, doi = raw.get("rank"), key_doi(raw.get("doi"))
        title = str(raw.get("title", "")).strip().casefold()
        if (
            not isinstance(rank, int)
            or isinstance(rank, bool)
            or rank < 1
            or rank in ranks
            or (doi and doi in seen_dois)
            or (not doi and title in seen_titles)
        ):
            raise ValueError("输入记录的rank、DOI或题名重复或无效")
        if not title and not doi:
            raise ValueError("输入记录缺少题名和DOI")
        ranks.add(rank)
        seen_dois.add(doi)
        seen_titles.add(title)
    stamp = datetime.now(UTC).isoformat()
    max_seq = max((int(r["序号"]) for r in rows), default=0)
    resolved = []
    for raw in sorted(records, key=lambda r: r["rank"]):
        doi = key_doi(raw.get("doi"))
        matches = (
            [r for r in rows if key_doi(r["DOI"]) == doi]
            if doi
            else [
                r
                for r in rows
                if str(r["论文题名"]).strip().casefold()
                == str(raw.get("title", "")).strip().casefold()
            ]
        )
        if len(matches) > 1:
            raise ValueError("题名无法唯一匹配")
        if matches:
            row = matches[0]
            if (raw.get("sequence") is not None and int(raw["sequence"]) != row["序号"]) or (
                raw.get("folder_name") and raw["folder_name"] != row["目录名"]
            ):
                raise ValueError("导入记录与原编号或目录不一致")
        else:
            seq = raw.get("sequence", max_seq + 1)
            if (
                not isinstance(seq, int)
                or isinstance(seq, bool)
                or seq < 1
                or any(r["序号"] == seq for r in rows)
            ):
                raise ValueError("导入序号冲突或无效")
            folder = raw.get("folder_name") or literature_name(
                seq,
                raw.get("title", ""),
                raw.get("year"),
                raw.get("impact_factor", ""),
                raw.get("cas_quartile", ""),
                raw.get("journal_abbreviation", ""),
            )
            if (
                Path(folder).name != folder
                or folder in {".", ".."}
                or any(str(r["目录名"]).casefold() == str(folder).casefold() for r in rows)
            ):
                raise ValueError("目录名不安全或冲突")
            row = dict.fromkeys(HEADERS, "")
            row.update(
                {
                    "序号": seq,
                    "目录名": folder,
                    "首次收录时间": stamp,
                    "下载成功": "未尝试" if doi else "不适用",
                    "SI是否下载成功": "未尝试" if doi else "不适用",
                    "下载状态": "pending" if doi else "missing_doi",
                }
            )
            rows.append(row)
            max_seq = max(max_seq, seq)
        for field, header in FIELDS.items():
            if raw.get(field) not in (None, ""):
                row[header] = key_doi(raw[field]) if field == "doi" else raw[field]
        topics = list(
            dict.fromkeys(
                x
                for x in (
                    str(row.get("检索主题") or "") + "；" + str(payload.get("topic", ""))
                ).split("；")
                if x
            )
        )
        row.update({"检索主题": "；".join(topics), "最后更新时间": stamp})
        resolved.append(
            {
                "rank": raw["rank"],
                "sequence": row["序号"],
                "folder_name": row["目录名"],
                "folder_path": str((folder_root / row["目录名"]).resolve()),
                "title": row["论文题名"],
                "doi": row["DOI"],
            }
        )
    validate_rows(rows)
    return resolved


def apply_report(rows, payload):
    validate_rows(rows)
    mapping = {key_doi(r["DOI"]): r for r in rows if r["DOI"]}
    results = payload.get("results", [])
    if any(key_doi(item.get("doi")) not in mapping for item in results):
        raise ValueError("报告包含未汇总的DOI，原文件未修改")
    for result in results:
        row = mapping[key_doi(result["doi"])]
        mode = result.get("download_mode", "article")
        if mode != "article":
            si = result.get("supplement_status", "not_requested")
            row["SI是否下载成功"] = (
                "是"
                if si in {"downloaded", "cached"}
                else "未尝试"
                if si in {"not_requested", "pending"}
                else "否"
            )
            if result.get("status") == "auth_skipped":
                row["SI是否下载成功"] = "否"
        if mode != "supplements_only":
            status = result.get("status", "failed")
            success = result.get("success") is True and status in {"downloaded", "cached"}
            # 组合任务的SI失败不应抹去已成功保存的正文。
            validation = result.get("body_validation")
            if isinstance(validation, dict):
                success = validation.get("valid") is True
            elif result.get("pdf_path"):
                try:
                    path = native_file_path(Path(result["pdf_path"]))
                    with path.open("rb") as stream:
                        success = path.stat().st_size >= 1024 and stream.read(5) == b"%PDF-"
                except OSError:
                    success = False
            if success and status not in {"downloaded", "cached"}:
                status = "downloaded"
            row.update(
                {
                    "下载成功": "是" if success else "否",
                    "下载状态": status,
                    "失败原因": "" if success else str(result.get("failure_reason") or status),
                    "PDF路径": result.get("pdf_path") or "",
                    "下载来源": result.get("source") or "",
                }
            )
        row["最后更新时间"] = datetime.now(UTC).isoformat()


def save(book, sheet, rows, destination):
    if not rows:
        raise ValueError("不导出空文献清单")
    # 延续模板色彩和列顺序，补足新扩展指标列的可读宽度。
    for letter, width in {"C": 60, "O": 45, "P": 48, "T": 22, "U": 24, "V": 100}.items():
        sheet.column_dimensions[letter].width = width
    for idx, row in enumerate(rows, 5):
        for col, header in enumerate(HEADERS, 1):
            cell = sheet.cell(idx, col)
            cell._style = copy.copy(sheet.cell(5, col)._style)
            cell.value = row.get(header)
            cell.alignment = Alignment(
                horizontal="left" if col not in {1, 5, 12, 13} else "center",
                vertical="top",
                wrap_text=True,
            )
        sheet.row_dimensions[idx].height = 120
    sheet.tables["LiteratureTable"].ref = f"A4:V{len(rows) + 4}"
    sheet.auto_filter.ref = sheet.tables["LiteratureTable"].ref
    sheet.freeze_panes = "D5"
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=destination.stem + "-", suffix=".tmp.xlsx", dir=destination.parent
    )
    os.close(handle)
    try:
        book.save(temporary)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument(
        "--template", type=Path, default=PROJECT / "reference/文献检索汇总模板.xlsx"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--records", type=Path)
    source.add_argument("--report", type=Path)
    parser.add_argument("--resolved", type=Path)
    args = parser.parse_args()
    book, sheet, rows = load(args.workbook, args.template)
    resolved = []
    if args.records:
        if not args.resolved:
            parser.error("写入清单时必须指定--resolved")
        resolved = upsert(rows, read_json(args.records), args.workbook.parent.resolve())
    else:
        apply_report(rows, read_json(args.report))
    save(book, sheet, rows, args.workbook)
    if args.resolved:
        args.resolved.parent.mkdir(parents=True, exist_ok=True)
        args.resolved.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "workbook": str(args.workbook.resolve()),
                    "records": resolved,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "workbook": str(args.workbook.resolve()),
                "records": len(rows),
                "resolved": len(resolved),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
