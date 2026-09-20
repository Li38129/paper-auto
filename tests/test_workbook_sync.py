from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from doi_harvester import workbook_sync


def _prepare_runtime(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    root = tmp_path / "project"
    script = (
        root
        / ".agents"
        / "skills"
        / "autopaper-literature"
        / "scripts"
        / "literature-workbook.mjs"
    )
    script.parent.mkdir(parents=True)
    script.write_text("// 测试占位脚本\n", encoding="utf-8")
    modules = tmp_path / "node_modules"
    modules.mkdir()
    monkeypatch.setenv("AUTOPAPER_ROOT", str(root))
    return tmp_path / "node.exe", modules, script


def test_update_workbook_uses_utf8_and_accepts_empty_output(tmp_path, monkeypatch):
    node, modules, _ = _prepare_runtime(tmp_path, monkeypatch)
    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=None, stderr=None)

    monkeypatch.setattr(workbook_sync.subprocess, "run", fake_run)

    result = workbook_sync.update_workbook(
        workbook_path=tmp_path / "index.xlsx",
        report_path=tmp_path / "report.json",
        node_path=node,
        node_modules=modules,
    )

    assert result == {"success": True, "details": {"output": ""}}
    assert seen["encoding"] == "utf-8"
    assert seen["errors"] == "replace"


def test_update_workbook_handles_empty_error_streams(tmp_path, monkeypatch):
    node, modules, _ = _prepare_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(
        workbook_sync.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=None,
            stderr=None,
        ),
    )

    result = workbook_sync.update_workbook(
        workbook_path=tmp_path / "index.xlsx",
        report_path=tmp_path / "report.json",
        node_path=node,
        node_modules=modules,
    )

    assert result == {
        "success": False,
        "error": "excel_update_failed",
        "message": "",
    }
