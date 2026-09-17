"""AutoPaper 的轻量 MCP 工具服务器。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .broker import BrokerManager, default_runtime_dir
from .job_runner import run_job
from .job_store import JobStore
from .papers import load_paper_jobs
from .search import LiteratureSearchClient


def _absolute_path(value: str, field: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{field} 必须是绝对路径。")
    return path.resolve()


def search(
    query: str,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 20,
) -> dict[str, object]:
    records = LiteratureSearchClient().search(
        query, year_from=year_from, year_to=year_to, limit=limit
    )
    return {"schema_version": 1, "query": query, "records": records}


def download(
    papers_file: str,
    output_dir: str,
    report_dir: str | None = None,
    browser_fallback: bool = True,
    detach: bool = True,
) -> dict[str, object]:
    papers_path = _absolute_path(papers_file, "papers_file")
    output_path = _absolute_path(output_dir, "output_dir")
    jobs = load_paper_jobs(papers_path)
    for item in jobs:
        try:
            item.folder_path.resolve().relative_to(output_path)
        except ValueError as exc:
            raise ValueError(f"论文目录必须位于 output_dir 下：{item.folder_path}") from exc

    runtime = default_runtime_dir().resolve()
    report_path = _absolute_path(report_dir, "report_dir") if report_dir else None
    if report_path is not None:
        try:
            report_path.relative_to((runtime / "jobs").resolve())
        except ValueError as exc:
            raise ValueError("report_dir 必须位于 AutoPaper 任务目录下。") from exc
    profile_dir = runtime / "profiles" / "default"
    store = JobStore(runtime / "jobs" / "jobs.sqlite3")
    job_id = store.create_job(
        records=[
            {
                "rank": item.rank,
                "doi": item.doi,
                "title": item.title,
                "folder_path": str(item.folder_path),
            }
            for item in jobs
        ],
        output_dir=output_path,
        report_dir=report_path,
        browser_fallback=browser_fallback,
        profile_dir=profile_dir,
    )
    if detach:
        BrokerManager(profile_dir=profile_dir, runtime_dir=runtime).ensure_started()
        status = "queued"
    else:
        status = run_job(job_id, store=store)
    return {"schema_version": 1, "job_id": job_id, "status": status}


def job_status(job_id: str) -> dict[str, object]:
    store = JobStore()
    job = store.get_job(job_id)
    attention = [
        {
            "doi": item["doi"],
            "status": item["status"],
            "reason": item["failure_reason"],
        }
        for item in job["items"]
        if item["status"] in {"auth_required", "subscription_required", "failed"}
    ]
    return {
        "schema_version": 1,
        "job_id": job_id,
        "status": job["status"],
        "counts": job["counts"],
        "needs_attention": attention,
        "report_dir": job["report_dir"],
    }


def _project_root() -> Path:
    candidates = [
        Path.cwd(),
        Path(os.getenv("AUTOPAPER_ROOT", "")) if os.getenv("AUTOPAPER_ROOT") else None,
        Path(__file__).resolve().parents[2],
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        root = candidate.resolve()
        if (root / ".agents" / "skills" / "autopaper-literature").is_dir():
            return root
    raise RuntimeError("无法定位 AutoPaper 项目根目录，请设置 AUTOPAPER_ROOT。")


def update_excel(
    workbook_path: str,
    records_path: str | None = None,
    report_path: str | None = None,
    resolved_path: str | None = None,
) -> dict[str, object]:
    workbook = _absolute_path(workbook_path, "workbook_path")
    records = _absolute_path(records_path, "records_path") if records_path else None
    report = _absolute_path(report_path, "report_path") if report_path else None
    resolved = _absolute_path(resolved_path, "resolved_path") if resolved_path else None
    if records and not resolved:
        raise ValueError("提供 records_path 时必须同时提供 resolved_path。")
    if not records and not report:
        raise ValueError("至少需要 records_path 或 report_path。")

    node = os.getenv("AUTOPAPER_NODE", "").strip() or shutil.which("node")
    node_modules = os.getenv("AUTOPAPER_ARTIFACT_NODE_MODULES", "").strip()
    if not node or not node_modules or not Path(node_modules).is_dir():
        return {
            "schema_version": 1,
            "success": False,
            "error": "artifact_runtime_unavailable",
            "message": (
                "需要设置 AUTOPAPER_NODE 和 AUTOPAPER_ARTIFACT_NODE_MODULES，原 Excel 未被修改。"
            ),
        }
    script = (
        _project_root()
        / ".agents"
        / "skills"
        / "autopaper-literature"
        / "scripts"
        / "literature-workbook.mjs"
    )
    command = [
        node,
        str(script),
        "--node-modules",
        str(Path(node_modules).resolve()),
        "--workbook",
        str(workbook),
    ]
    if records:
        command.extend(["--records", str(records), "--resolved", str(resolved)])
    if report:
        command.extend(["--report", str(report)])
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return {
            "schema_version": 1,
            "success": False,
            "error": "excel_update_failed",
            "message": completed.stderr.strip() or completed.stdout.strip(),
        }
    try:
        details: Any = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        details = {"output": completed.stdout.strip()}
    return {"schema_version": 1, "success": True, "details": details}


def create_server() -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("MCP 依赖未安装；请运行 uv sync --extra agent。") from exc
    server = FastMCP("AutoPaper")
    server.tool()(search)
    server.tool()(download)
    server.tool()(job_status)
    server.tool()(update_excel)
    return server


def main() -> None:
    create_server().run()
