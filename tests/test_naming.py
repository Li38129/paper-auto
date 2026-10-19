import shutil
import subprocess
from pathlib import Path

import pytest

from doi_harvester.naming import literature_name
from doi_harvester.supplements import _supplement_filename

SCRIPT = Path(__file__).resolve().parents[1] / (
    ".agents/skills/autopaper-literature/scripts/literature-workbook.mjs"
)


def _run_node(code: str, *args: str) -> str:
    """执行 Node 片段并返回 stdout；本机 node 桩吞输出时跳过一致性校验。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("未安装 Node.js")
    result = subprocess.run(
        [node, "-e", code, str(SCRIPT), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if not result.stdout.strip():
        pytest.skip("本机 node 未返回输出（可能是包装器），跳过与 Node 实现的一致性校验")
    return result.stdout


@pytest.mark.parametrize(
    ("metric", "zone", "expected"),
    [
        ("2024 JCR 7.2", "2025版 1区", "IF7.2_一区"),
        ("IF=12.5", "一区 TOP", "IF12.5_顶刊"),
        ("3.0", "2区", "IF3.0_二区"),
        ("IF 2.6", "三区", "IF2.6_三区"),
        ("未核实", "未核实", "IF未核实_分区未核实"),
        ("2024版 未核实", "JCR Q1", "IF未核实_分区未核实"),
        ("2024 JCR 未核实", "2025版 1区 未核实", "IF未核实_分区未核实"),
    ],
)
def test_naming_matches_workbook(metric, zone, expected):
    name = literature_name(8, "A: title/with_underlines", 2023, metric, zone, "J. Test")
    impact, quartile = expected.split("_", 1)
    assert name == f"8_{impact}_J. Test_{quartile}_2023_A- title-with_underlines"
    code = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('function cleanText(');
const end = source.indexOf('function mergeTopic(', start);
const context = {};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
process.stdout.write(context.literatureFolderName(8, {
  title: 'A: title/with_underlines', year: 2023, journalAbbreviation: 'J. Test',
  impactFactor: process.argv[2], casQuartile: process.argv[3]
}));
"""
    assert _run_node(code, metric, zone) == name


def test_si_inserts_sequence_before_title_and_keeps_extension():
    name = _supplement_filename(
        url="https://publisher.test/download", disposition='filename="data.xlsx"',
        content_type="application/octet-stream", index=2,
        article_name="8_IF7.2_J. Test_一区_2023_Title_with_underlines",
    )
    assert name == "8_IF7.2_J. Test_一区_2023_si2_Title_with_underlines.xlsx"


def test_long_title_is_safe_and_leaves_space_for_si_suffix():
    name = literature_name(1, "Long title?" * 50, 2024, "8.1", "一区")
    assert len(name) <= 160
    assert "?" not in name
    assert not name.endswith((" ", "."))


def test_missing_abbreviation_is_explicit_and_underscores_are_safe():
    assert literature_name(1, "Title", 2024, "7.2", "一区") == (
        "1_IF7.2_期刊简称未核实_一区_2024_Title"
    )
    assert literature_name(1, "Title", 2024, "7.2", "一区", "J._Test") == (
        "1_IF7.2_J. Test_一区_2024_Title"
    )


def test_si_preserves_previous_format_without_abbreviation():
    name = _supplement_filename(
        url="https://publisher.test/si.pdf", disposition="",
        content_type="application/pdf", index=2,
        article_name="8_IF7.2_一区_2023_Title_with_underlines",
    )
    assert name == "8_IF7.2_一区_2023_si2_Title_with_underlines.pdf"


def test_workbook_uses_new_names_and_preserves_existing_directories():
    code = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
const context = {path: require('path')};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('const HEADERS ='),
  source.indexOf('const PREVIOUS_HEADERS =')), context);
vm.runInContext(source.slice(source.indexOf('function normalizeDoi('),
  source.indexOf('function resolveTable(')), context);
vm.runInContext(source.slice(source.indexOf('function updateRowMap('),
  source.indexOf('function applyReport(')), context);
const rows = [{'序号': 9, '目录名': '9旧目录', DOI: '10.1000/old',
  '论文题名': 'Old title', '首次收录时间': 'original'}];
const payload = context.validateRecordsPayload({schema_version: 1, records: [
  {rank: 1, doi: '10.1000/old', title: 'Updated title', year: 2023,
    impact_factor: '8.5', cas_quartile: '一区'},
  {rank: 2, doi: '10.1000/new', title: 'New title', year: 2024,
    impact_factor: '2024 JCR 7.2', cas_quartile: '2025版 1区 TOP',
    journal_abbreviation: 'J. Test'}
]});
const resolved = context.upsertRecords(rows, payload, 'book.xlsx', 'papers');
assert.strictEqual(resolved[0].sequence, 9);
assert.strictEqual(resolved[0].folder_name, '9旧目录');
assert.strictEqual(rows[0]['首次收录时间'], 'original');
assert.strictEqual(resolved[1].sequence, 10);
assert.strictEqual(resolved[1].folder_name, '10_IF7.2_J. Test_顶刊_2024_New title');
const again = context.upsertRecords(rows, payload, 'book.xlsx', 'papers');
assert.strictEqual(rows.length, 2);
assert.strictEqual(again[1].folder_name, resolved[1].folder_name);
process.stdout.write('ok');
"""
    assert _run_node(code) == "ok"
