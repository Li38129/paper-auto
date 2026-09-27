import shutil
import subprocess
from pathlib import Path

import pytest


def test_excel_si_report_uses_final_attachment_outcome():
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
const start = source.indexOf('function updateSupplementSheet(');
const end = source.indexOf('function seedSupplementSheet(', start);
const context = {SI_HEADERS: Array(11).fill(''),
  normalizeDoi: x => x.toLowerCase(), cleanText: x => String(x || '')};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
let rows;
const sheet = {
  getUsedRange: () => ({values: [[], [200, '10.1000/retained']]}),
  getRange: () => ({format: {font: {}}}),
  getRangeByIndexes: (r, c) => r === 1 && c === 0 ? {set values(v) {rows = v;}} : {format: {}},
  freezePanes: {freezeRows: () => {}}
};
const workbook = {worksheets: {getItem: name => {
  assert.strictEqual(name, '补充材料'); return sheet;}}};
context.updateSupplementSheet(workbook, [{download_mode: 'supplements_only',
  rank: 301, doi: '10.1000/test', supplement_status: 'partial',
  supplements: [{url: 'https://cdn/good', name: 'si.docx'}], supplement_attempts: [
  {url: 'https://cdn/good', success: false, reason: 'http_500'},
  {url: 'https://cdn/good', success: true, reason: 'downloaded'},
  {url: 'https://cdn/bad', success: false, reason: 'http_500'},
  {url: 'https://cdn/bad', success: false, reason: 'Failed to fetch'}
]}]);
assert.strictEqual(rows.length, 3);
assert.strictEqual(rows.filter(r => r[9] === 'failed').length, 1);
assert.strictEqual(rows.find(r => r[3] === 'si.docx')[9], 'downloaded');
assert.strictEqual(rows.find(r => r[9] === 'failed')[10], 'Failed to fetch');
assert.strictEqual(rows[0][1], '10.1000/retained');
"""
    subprocess.run([node, "-e", code, str(script)], check=True, capture_output=True, text=True)
