"""验证模板表头解析、字段别名与缺列报错。"""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from doi_harvester.workbook_schema import (
    HEADER_ALIASES,
    REQUIRED_RECORD_FIELDS,
    REQUIRED_REPORT_FIELDS,
    build_column_index,
    detect_header_row,
    load_template,
    missing_fields,
    read_headers,
)

TEMPLATE = Path(__file__).resolve().parents[1] / "reference/文献检索汇总模板.xlsx"


def make_sheet(path: Path, headers: list[str], header_row: int = 4) -> Path:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "文献清单"
    sheet["A1"] = "文献检索汇总"
    for column, header in enumerate(headers, start=1):
        sheet.cell(header_row, column, header)
    book.save(path)
    return path


def test_reference_template_is_readable_and_has_doi():
    schema = load_template(TEMPLATE)

    assert schema.sheet_name == "文献清单"
    assert schema.header_row == 4
    assert schema.headers[0] == "序号"
    assert "DOI" in schema.headers
    assert "下载状态" in schema.headers


def test_reference_template_covers_all_required_fields():
    schema = load_template(TEMPLATE)

    assert missing_fields(schema.headers, REQUIRED_RECORD_FIELDS) == []
    assert missing_fields(schema.headers, REQUIRED_REPORT_FIELDS) == []


def test_alias_mapping_is_position_independent(tmp_path: Path):
    path = make_sheet(tmp_path / "t.xlsx", ["目录名", "DOI", "序号", "影响因子(IF)"])
    schema = load_template(path)
    columns = schema.columns

    assert columns["rank"] == 2
    assert columns["doi"] == 1
    assert columns["folder_name"] == 0
    # 半角括号写法也能识别为影响因子
    assert columns["impact_factor"] == 3


def test_missing_fields_reports_recommended_names(tmp_path: Path):
    path = make_sheet(tmp_path / "t.xlsx", ["序号", "论文题名"])
    schema = load_template(path)

    assert missing_fields(schema.headers, REQUIRED_RECORD_FIELDS) == ["目录名"]
    assert "PDF路径" in missing_fields(schema.headers, REQUIRED_REPORT_FIELDS)


def test_header_row_can_move(tmp_path: Path):
    path = make_sheet(tmp_path / "t.xlsx", ["序号", "目录名"], header_row=6)
    book = openpyxl.load_workbook(path)
    sheet = book["文献清单"]
    sheet["A5"] = "说明行"

    assert detect_header_row(sheet) == 6


def test_headers_without_sequence_are_rejected(tmp_path: Path):
    path = make_sheet(tmp_path / "t.xlsx", ["题名"])

    with pytest.raises(ValueError):
        load_template(path)


def test_read_headers_drops_trailing_empty_columns(tmp_path: Path):
    path = make_sheet(tmp_path / "t.xlsx", ["序号", "目录名", "论文题名"])
    book = openpyxl.load_workbook(path)
    sheet = book["文献清单"]

    assert read_headers(sheet, 4) == ["序号", "目录名", "论文题名"]


def test_every_alias_group_is_non_empty():
    for field, aliases in HEADER_ALIASES.items():
        assert aliases and all(alias.strip() for alias in aliases), field


def test_column_index_ignores_whitespace_and_case():
    index = build_column_index([" 序号 ", "doi ", "论 文 题名"])

    assert index["rank"] == 0
    assert index["doi"] == 1
    # 表头内空格会被归一化，但仍按原文本匹配不到带空格的题名
    assert "title" not in index
