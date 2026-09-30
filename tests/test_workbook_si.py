import shutil
import subprocess
from pathlib import Path

import pytest


def test_excel_si_report_updates_single_status_column():
    node = shutil.which("node")
    if not node:
        pytest.skip("未安装 Node.js")
    script = (
        Path(__file__).resolve().parents[1]
        / ".agents/skills/autopaper-literature/scripts/literature-workbook.mjs"
    )
    code = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('function applyReport(');
const end = source.indexOf('function writeRowsToTable(', start);
const context = {
  validateExistingRows: () => {},
  updateRowMap: () => ({byDoi: new Map([['10.1000/test', 0]])}),
  normalizeDoi: x => String(x || '').toLowerCase(),
  cleanText: x => String(x || '').trim(),
  nowText: () => '2026-09-30',
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
const rows = [{DOI: '10.1000/test', '下载成功': '是', '下载状态': 'downloaded',
  'PDF路径': 'article.pdf', 'SI是否下载成功': '未尝试'}];
context.applyReport(rows, [{download_mode: 'supplements_only', doi: '10.1000/test',
  supplement_status: 'downloaded'}]);
assert.strictEqual(rows[0]['SI是否下载成功'], '是');
assert.strictEqual(rows[0]['下载成功'], '是');
assert.strictEqual(rows[0]['PDF路径'], 'article.pdf');
context.applyReport(rows, [{download_mode: 'supplements_only', doi: '10.1000/test',
  supplement_status: 'partial'}]);
assert.strictEqual(rows[0]['SI是否下载成功'], '否');
"""
    subprocess.run([node, "-e", code, str(script)], check=True, capture_output=True, text=True)
