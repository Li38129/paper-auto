from pathlib import Path

import pytest

from doi_harvester.mcp_server import _absolute_path, update_excel


def test_absolute_path_rejects_relative_paths() -> None:
    with pytest.raises(ValueError, match="绝对路径"):
        _absolute_path("relative/file.json", "papers_file")


def test_update_excel_protects_original_when_runtime_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workbook = tmp_path / "文献检索汇总.xlsx"
    workbook.write_bytes(b"original")
    records = tmp_path / "records.json"
    records.write_text("{}", encoding="utf-8")
    resolved = tmp_path / "resolved.json"
    monkeypatch.delenv("AUTOPAPER_ARTIFACT_NODE_MODULES", raising=False)

    result = update_excel(str(workbook), str(records), resolved_path=str(resolved))

    assert result["error"] == "artifact_runtime_unavailable"
    assert workbook.read_bytes() == b"original"
