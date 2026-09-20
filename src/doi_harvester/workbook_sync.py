"""从后台任务原子回写 literature-search-organizer 工作簿。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def project_root() -> Path:
    """解析包含仓库 Skill 的 AutoPaper 根目录。"""
    configured = os.getenv("AUTOPAPER_ROOT", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.extend((Path.cwd(), Path(__file__).resolve().parents[2]))
    for candidate in candidates:
        root = candidate.resolve()
        if (root / ".agents" / "skills" / "autopaper-literature").is_dir():
            return root
    raise RuntimeError("无法定位 AutoPaper 项目根目录。")


def update_workbook(
    *,
    workbook_path: Path,
    report_path: Path,
    node_path: Path | None = None,
    node_modules: Path | None = None,
) -> dict[str, object]:
    """调用统一工作簿脚本，并返回可持久化结果。"""
    resolved_node = str(node_path) if node_path else os.getenv("AUTOPAPER_NODE", "").strip()
    resolved_node = resolved_node or shutil.which("node") or ""
    resolved_modules = (
        str(node_modules)
        if node_modules
        else os.getenv("AUTOPAPER_ARTIFACT_NODE_MODULES", "").strip()
    )
    if not resolved_node or not resolved_modules or not Path(resolved_modules).is_dir():
        return {
            "success": False,
            "error": "artifact_runtime_unavailable",
            "message": "缺少 Node.js 或工作簿依赖路径。",
        }
    script = (
        project_root()
        / ".agents"
        / "skills"
        / "autopaper-literature"
        / "scripts"
        / "literature-workbook.mjs"
    )
    completed = subprocess.run(
        [
            resolved_node,
            str(script),
            "--node-modules",
            str(Path(resolved_modules).resolve()),
            "--workbook",
            str(Path(workbook_path).resolve()),
            "--report",
            str(Path(report_path).resolve()),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode != 0:
        return {
            "success": False,
            "error": "excel_update_failed",
            "message": stderr or stdout,
        }
    try:
        details = json.loads(stdout.splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        details = {"output": stdout}
    return {"success": True, "details": details}
