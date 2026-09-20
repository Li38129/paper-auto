"""可恢复下载任务的前台与后台执行逻辑。"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from typing import Protocol

from .access_policy import AccessPolicyStore
from .broker import BrokerManager, default_runtime_dir
from .job_store import JobStore
from .models import DownloadResult
from .pipeline import Harvester
from .workbook_sync import update_workbook


class HarvesterLike(Protocol):
    def download(self, doi: str, *, article_dir: Path) -> DownloadResult: ...


def _heartbeat_loop(store: JobStore, job_id: str, stopped: Event) -> None:
    while not stopped.wait(5):
        store.heartbeat(job_id)


def _broker_heartbeat_loop(manager: BrokerManager, job_id: str, stopped: Event) -> None:
    while not stopped.wait(5):
        manager.write_state(pid=os.getpid(), status="running", job_id=job_id)


def _write_report(store: JobStore, job_id: str) -> None:
    job = store.get_job(job_id)
    report_dir = job.get("report_dir")
    if not report_dir:
        return
    destination = Path(str(report_dir))
    destination.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "job_id": job_id,
        "status": job["status"],
        "generated_at": datetime.now(UTC).isoformat(),
        "results": [
            {
                "rank": item["rank"],
                "doi": item["doi"],
                "title": item["title"],
                "requested_folder_path": item["folder_path"],
                "success": item["status"] in {"downloaded", "cached"},
                "status": item["status"],
                "article_dir": item["folder_path"],
                "pdf_path": item["pdf_path"] or None,
                "source": item["source"],
                "failure_reason": item["failure_reason"],
                "attempts": item["attempts"],
            }
            for item in job["items"]
            if item["status"] not in {"pending", "running"}
        ],
    }
    report_path = destination / "batch-report.json"
    part = report_path.with_suffix(".json.part")
    part.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(part, report_path)


def _sync_excel(store: JobStore, job_id: str) -> bool:
    """把当前检查点报告写回 Excel；失败时保留任务以便恢复。"""
    job = store.get_job(job_id)
    workbook = str(job.get("workbook_path") or "")
    if not workbook:
        return True
    report_path = Path(str(job["report_dir"])) / "batch-report.json"
    store.set_job_status(job_id, "awaiting_excel")
    result = update_workbook(
        workbook_path=Path(workbook),
        report_path=report_path,
        node_path=Path(str(job["node_path"])) if job.get("node_path") else None,
        node_modules=Path(str(job["node_modules"])) if job.get("node_modules") else None,
    )
    if result.get("success"):
        store.set_excel_status(job_id, "updated")
        store.set_job_status(job_id, "running")
        return True
    message = str(result.get("message") or result.get("error") or "Excel 回写失败")
    store.set_excel_status(job_id, "failed", error=message)
    store.set_event(job_id, "excel_update_failed")
    store.set_job_status(job_id, "needs_attention", error=message)
    return False


def run_job(
    job_id: str,
    *,
    store: JobStore | None = None,
    harvester: HarvesterLike | None = None,
) -> str:
    """执行一个任务，并始终尝试生成兼容的批次报告。"""
    active_store = store or JobStore()
    job = active_store.get_job(job_id)
    active_store.set_job_status(job_id, "running")
    try:
        options = json.loads(str(job.get("options_json") or "{}"))
    except json.JSONDecodeError:
        options = {}
    browser_options = (
        {
            "profile_dir": Path(str(job["profile_dir"])),
            "challenge_policy": "skip",
            "headless": bool(options.get("headless", False)),
            **(
                {"channel": str(options["browser_channel"])}
                if options.get("browser_channel")
                else {}
            ),
        }
        if job.get("profile_dir")
        else {"challenge_policy": "skip"}
    )
    worker = harvester or Harvester(
        output_dir=Path(str(job["output_dir"])),
        browser_fallback=bool(job["browser_fallback"]),
        browser_options=browser_options,
        email=str(options.get("email") or "") or None,
        access_store=AccessPolicyStore(
            environment=str(options.get("access_environment") or "default")
        ),
    )
    stopped = Event()
    heartbeat = Thread(
        target=_heartbeat_loop,
        args=(active_store, job_id, stopped),
        daemon=True,
    )
    heartbeat.start()
    try:
        if job.get("workbook_path") and job.get("excel_status") == "failed":
            _write_report(active_store, job_id)
            if not _sync_excel(active_store, job_id):
                return "needs_attention"
        delay_seconds = max(float(options.get("delay_seconds") or 0.0), 0.0)
        batch_size = min(max(int(job.get("batch_size") or 100), 1), 100)
        processed_since_sync = 0
        pending = active_store.pending_items(job_id)
        for index, item in enumerate(pending):
            if active_store.get_job(job_id)["status"] == "canceled":
                break
            doi = str(item["doi"])
            active_store.set_item_status(job_id, doi, "running")
            try:
                result = worker.download(doi, article_dir=Path(str(item["folder_path"])))
            except Exception as exc:  # noqa: BLE001 - 单条失败不应终止整个批次
                result = DownloadResult(
                    doi=doi,
                    success=False,
                    status="worker_error",
                    article_dir=Path(str(item["folder_path"])),
                    reason=f"{type(exc).__name__}: {exc}",
                    outcome="blocked",
                )
            active_store.record_result(job_id, doi, result)
            active_store.heartbeat(job_id)
            processed_since_sync += 1
            reason = result.reason or result.status
            if reason in {"challenge_required", "authentication_required"}:
                active_store.set_event(job_id, "authentication_required")
                break
            if processed_since_sync >= batch_size:
                _write_report(active_store, job_id)
                if not _sync_excel(active_store, job_id):
                    return "needs_attention"
                processed_since_sync = 0
            if index < len(pending) - 1 and delay_seconds:
                time.sleep(delay_seconds)
        _write_report(active_store, job_id)
        if not _sync_excel(active_store, job_id):
            return "needs_attention"
        status = active_store.finalize_job(job_id)
        active_store.set_event(
            job_id,
            "completed" if status == "completed" else status,
        )
    except Exception as exc:
        active_store.set_job_status(job_id, "failed", error=str(exc))
        status = "failed"
        raise
    finally:
        stopped.set()
        heartbeat.join(timeout=2)
        _write_report(active_store, job_id)
    return status


def run_broker(
    *,
    profile_dir: Path,
    store: JobStore | None = None,
    idle_seconds: int = 10,
) -> None:
    """串行处理同一浏览器配置目录下的排队任务。"""
    active_store = store or JobStore()
    manager = BrokerManager(profile_dir=profile_dir, runtime_dir=default_runtime_dir())
    manager.clear_stop()
    idle_started = time.monotonic()
    try:
        while not manager.stop_requested():
            manager.write_state(pid=os.getpid(), status="running")
            job = active_store.claim_next_job(profile_dir=profile_dir)
            if job is None:
                if time.monotonic() - idle_started >= idle_seconds:
                    break
                time.sleep(1)
                continue
            idle_started = time.monotonic()
            job_id = str(job["id"])
            manager.write_state(pid=os.getpid(), status="running", job_id=job_id)
            stopped = Event()
            heartbeat = Thread(
                target=_broker_heartbeat_loop,
                args=(manager, job_id, stopped),
                daemon=True,
            )
            heartbeat.start()
            try:
                run_job(job_id, store=active_store)
            finally:
                stopped.set()
                heartbeat.join(timeout=2)
        manager.write_state(pid=os.getpid(), status="stopped")
    finally:
        manager.release_lock()
