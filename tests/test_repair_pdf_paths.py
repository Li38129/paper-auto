"""验证离线修复不覆盖原件，拒绝冲突并保留失败记录。"""

import importlib.util
from pathlib import Path

from pypdf import PdfWriter

from doi_harvester.filesystem import native_file_path, shell_safe_file_path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/repair-pdf-paths.py"
spec = importlib.util.spec_from_file_location("repair_pdf_paths", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_repair_copies_identical_pdf_and_preserves_conflicting_file(tmp_path):
    folder = tmp_path / ("论文" * 30)
    native_file_path(folder).mkdir()
    original = folder / ("标题" * 70 + ".pdf")
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with native_file_path(original).open("wb") as stream:
        writer.write(stream)
    destination = shell_safe_file_path(original)
    assert module.repair(tmp_path)[0]["status"] == "planned"
    assert not destination.exists()
    result = module.repair(tmp_path, apply=True)
    assert result[0]["status"] == "copied"
    assert module.digest(original) == module.digest(destination)
    source_hash = module.digest(original)
    destination.write_bytes(b"existing data")
    result = module.repair(tmp_path, apply=True)
    assert any(item["status"] == "failed" for item in result)
    assert destination.read_bytes() == b"existing data"
    assert module.digest(original) == source_hash
