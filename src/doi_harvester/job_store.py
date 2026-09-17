"""SQLite 作业状态、条目与阶段尝试记录。"""

from __future__ import annotations

import os
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import DownloadResult

JOB_STATUSES = {
    "queued",
    "running",
    "stalled",
    "needs_attention",
    "completed",
    "failed",
    "canceled",
}
ITEM_STATUSES = {
    "pending",
    "running",
    "downloaded",
    "cached",
    "retryable",
    "auth_required",
    "subscription_required",
    "failed",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def default_job_database() -> Path:
    """返回项目运行区内的默认任务数据库。"""
    runtime = os.getenv("DOI_HARVESTER_RUNTIME_DIR", "").strip()
    root = Path(runtime) if runtime else Path.cwd() / "temp" / "doi-harvester"
    return root / "jobs" / "jobs.sqlite3"


class JobStore:
    """以短连接事务维护可恢复下载任务。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_job_database()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    report_dir TEXT,
                    browser_fallback INTEGER NOT NULL DEFAULT 0,
                    profile_dir TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    heartbeat_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS job_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    rank INTEGER,
                    doi TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    folder_path TEXT NOT NULL,
                    publisher TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    pdf_path TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    failure_reason TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, doi)
                );
                CREATE TABLE IF NOT EXISTS stage_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    item_id INTEGER NOT NULL REFERENCES job_items(id) ON DELETE CASCADE,
                    stage TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    route TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    http_status INTEGER,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    source_url TEXT NOT NULL DEFAULT '',
                    bytes_written INTEGER NOT NULL DEFAULT 0,
                    response_status TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_job_items_job_status
                    ON job_items(job_id, status);
                CREATE INDEX IF NOT EXISTS idx_stage_attempts_item
                    ON stage_attempts(item_id, id);
                """
            )
            columns = {
                str(row["name"]) for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "profile_dir" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN profile_dir TEXT NOT NULL DEFAULT ''"
                )
            connection.commit()

    def create_job(
        self,
        *,
        records: list[dict[str, object]],
        output_dir: Path,
        report_dir: Path | None,
        browser_fallback: bool,
        profile_dir: Path | None = None,
    ) -> str:
        if not records:
            raise ValueError("任务至少需要一条论文记录。")
        timestamp = _now()
        job_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs(
                    id, status, output_dir, report_dir, browser_fallback, profile_dir,
                    created_at, updated_at, heartbeat_at
                ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    str(Path(output_dir).resolve()),
                    str(
                        Path(report_dir).resolve()
                        if report_dir
                        else (self.path.parent / job_id).resolve()
                    ),
                    int(browser_fallback),
                    str(Path(profile_dir).resolve()) if profile_dir else "",
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            for record in records:
                folder = Path(str(record.get("folder_path") or ""))
                if not folder.is_absolute():
                    raise ValueError("任务 folder_path 必须是绝对路径。")
                connection.execute(
                    """
                    INSERT INTO job_items(
                        job_id, rank, doi, title, folder_path, publisher,
                        status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        job_id,
                        record.get("rank"),
                        str(record.get("doi") or ""),
                        str(record.get("title") or ""),
                        str(folder.resolve()),
                        str(record.get("publisher") or ""),
                        timestamp,
                    ),
                )
            connection.commit()
        return job_id

    def set_job_status(self, job_id: str, status: str, *, error: str = "") -> None:
        if status not in JOB_STATUSES:
            raise ValueError(f"未知任务状态：{status}")
        timestamp = _now()
        fields = ["status = ?", "updated_at = ?", "heartbeat_at = ?", "error = ?"]
        values: list[object] = [status, timestamp, timestamp, error]
        if status == "running":
            fields.append("started_at = COALESCE(started_at, ?)")
            values.append(timestamp)
        if status in {"completed", "failed", "canceled", "needs_attention"}:
            fields.append("finished_at = ?")
            values.append(timestamp)
        values.append(job_id)
        with self.connect() as connection:
            cursor = connection.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)
            if cursor.rowcount != 1:
                raise KeyError(f"任务不存在：{job_id}")
            connection.commit()

    def heartbeat(self, job_id: str) -> None:
        timestamp = _now()
        with self.connect() as connection:
            connection.execute(
                "UPDATE jobs SET heartbeat_at = ?, updated_at = ? WHERE id = ?",
                (timestamp, timestamp, job_id),
            )
            connection.commit()

    def mark_stalled_jobs(self, *, timeout_seconds: int = 60) -> list[str]:
        cutoff = (datetime.now(UTC) - timedelta(seconds=timeout_seconds)).isoformat()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id FROM jobs
                WHERE status = 'running' AND COALESCE(heartbeat_at, updated_at) < ?
                """,
                (cutoff,),
            ).fetchall()
            job_ids = [str(row["id"]) for row in rows]
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                timestamp = _now()
                connection.execute(
                    f"UPDATE jobs SET status = 'stalled', updated_at = ? "
                    f"WHERE id IN ({placeholders})",
                    (timestamp, *job_ids),
                )
                connection.execute(
                    f"UPDATE job_items SET status = 'retryable', updated_at = ? "
                    f"WHERE job_id IN ({placeholders}) AND status = 'running'",
                    (timestamp, *job_ids),
                )
                connection.commit()
        return job_ids

    def set_item_status(self, job_id: str, doi: str, status: str) -> None:
        if status not in ITEM_STATUSES:
            raise ValueError(f"未知论文状态：{status}")
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE job_items SET status = ?, updated_at = ?
                WHERE job_id = ? AND doi = ?
                """,
                (status, _now(), job_id, doi),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"任务中不存在 DOI：{doi}")
            connection.commit()

    def record_result(self, job_id: str, doi: str, result: DownloadResult) -> None:
        item_status = _item_status(result)
        timestamp = _now()
        with self.connect() as connection:
            item = connection.execute(
                "SELECT id FROM job_items WHERE job_id = ? AND doi = ?",
                (job_id, doi),
            ).fetchone()
            if item is None:
                raise KeyError(f"任务中不存在 DOI：{doi}")
            item_id = int(item["id"])
            connection.execute(
                """
                UPDATE job_items
                SET status = ?, pdf_path = ?, source = ?, failure_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    item_status,
                    str(result.pdf_path or ""),
                    result.source,
                    result.reason or ("" if result.success else result.status),
                    timestamp,
                    item_id,
                ),
            )
            connection.execute("DELETE FROM stage_attempts WHERE item_id = ?", (item_id,))
            for attempt in result.attempts:
                connection.execute(
                    """
                    INSERT INTO stage_attempts(
                        job_id, item_id, stage, provider, route, status, reason,
                        http_status, duration_ms, source_url, bytes_written,
                        response_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        item_id,
                        attempt.stage,
                        attempt.provider,
                        attempt.route,
                        "success" if attempt.success else "failed",
                        attempt.reason,
                        attempt.status_code,
                        attempt.duration_ms,
                        attempt.url,
                        attempt.bytes_written,
                        attempt.response_status,
                        timestamp,
                    ),
                )
            connection.commit()

    def prepare_resume(self, job_id: str) -> int:
        with self.connect() as connection:
            timestamp = _now()
            cursor = connection.execute(
                """
                UPDATE job_items SET status = 'pending', updated_at = ?
                WHERE job_id = ? AND status IN ('retryable', 'running')
                """,
                (timestamp, job_id),
            )
            connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', updated_at = ?, heartbeat_at = ?,
                    finished_at = NULL, error = ''
                WHERE id = ?
                """,
                (timestamp, timestamp, job_id),
            )
            connection.commit()
            return int(cursor.rowcount)

    def pending_items(self, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT rank, doi, title, folder_path, publisher, status
                FROM job_items WHERE job_id = ? AND status = 'pending'
                ORDER BY COALESCE(rank, id), id
                """,
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def finalize_job(self, job_id: str) -> str:
        job = self.get_job(job_id)
        if job["status"] == "canceled":
            return "canceled"
        counts = job["counts"]
        if counts and set(counts) <= {"downloaded", "cached"}:
            status = "completed"
        elif any(
            counts.get(name)
            for name in (
                "auth_required",
                "subscription_required",
                "failed",
                "retryable",
            )
        ):
            status = "needs_attention"
        else:
            status = "failed"
        self.set_job_status(job_id, status)
        return status

    def cancel(self, job_id: str) -> None:
        self.set_job_status(job_id, "canceled")

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        self.mark_stalled_jobs()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (max(limit, 1),)
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_next_job(self, *, profile_dir: Path | None = None) -> dict[str, Any] | None:
        """以排他事务领取最早的排队任务。"""
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if profile_dir is None:
                row = connection.execute(
                    "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
                ).fetchone()
            else:
                resolved_profile = str(Path(profile_dir).resolve())
                row = connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = 'queued' AND profile_dir IN ('', ?)
                    ORDER BY created_at LIMIT 1
                    """,
                    (resolved_profile,),
                ).fetchone()
            if row is None:
                connection.commit()
                return None
            timestamp = _now()
            connection.execute(
                """
                UPDATE jobs
                SET status = 'running', updated_at = ?, heartbeat_at = ?,
                    started_at = COALESCE(started_at, ?)
                WHERE id = ?
                """,
                (timestamp, timestamp, timestamp, row["id"]),
            )
            connection.commit()
            claimed = dict(row)
            claimed["status"] = "running"
            return claimed

    def get_job(self, job_id: str) -> dict[str, Any]:
        self.mark_stalled_jobs()
        with self.connect() as connection:
            job_row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job_row is None:
                raise KeyError(f"任务不存在：{job_id}")
            item_rows = connection.execute(
                "SELECT * FROM job_items WHERE job_id = ? ORDER BY COALESCE(rank, id), id",
                (job_id,),
            ).fetchall()
            items: list[dict[str, Any]] = []
            for row in item_rows:
                item = dict(row)
                attempts = connection.execute(
                    "SELECT * FROM stage_attempts WHERE item_id = ? ORDER BY id",
                    (row["id"],),
                ).fetchall()
                item["attempts"] = [dict(attempt) for attempt in attempts]
                items.append(item)
        job = dict(job_row)
        job["items"] = items
        job["counts"] = dict(Counter(item["status"] for item in items))
        return job


def _item_status(result: DownloadResult) -> str:
    if result.success:
        return "cached" if result.status == "cached" else "downloaded"
    reason = result.reason or result.status
    if reason in {"challenge_required", "authentication_required"}:
        return "auth_required"
    if reason in {"subscription_required", "not_entitled"}:
        return "subscription_required"
    if reason in {
        "timeout",
        "browser_timeout",
        "rate_limited",
        "http_500",
        "http_502",
        "http_503",
        "http_504",
    }:
        return "retryable"
    return "failed"
