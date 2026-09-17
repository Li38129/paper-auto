"""按浏览器配置目录隔离的后台会话 Broker 状态管理。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def default_runtime_dir() -> Path:
    """返回后台任务运行目录。"""
    configured = os.getenv("DOI_HARVESTER_RUNTIME_DIR", "").strip()
    return Path(configured) if configured else Path.cwd() / "temp" / "doi-harvester"


class BrokerManager:
    """维护一个浏览器 profile 对应的 Broker 心跳与停止标记。"""

    def __init__(self, *, profile_dir: Path, runtime_dir: Path | None = None) -> None:
        self.profile_dir = Path(profile_dir).resolve()
        digest = hashlib.sha256(str(self.profile_dir).casefold().encode("utf-8")).hexdigest()
        self.key = digest[:16]
        root = Path(runtime_dir) if runtime_dir is not None else default_runtime_dir()
        self.root = root.resolve() / "brokers" / self.key
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.stop_path = self.root / "stop.requested"
        self.lock_path = self.root / "broker.lock"
        self.log_path = self.root / "broker.log"

    def write_state(
        self,
        *,
        pid: int,
        status: str,
        job_id: str = "",
        cdp_url: str = "",
    ) -> None:
        payload = {
            "schema_version": 1,
            "profile_dir": str(self.profile_dir),
            "pid": pid,
            "status": status,
            "job_id": job_id,
            "cdp_url": cdp_url,
            "heartbeat_at": datetime.now(UTC).isoformat(),
            "log_path": str(self.log_path),
        }
        part = self.state_path.with_suffix(".json.part")
        part.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(part, self.state_path)

    def read_state(self) -> dict[str, Any] | None:
        if not self.state_path.is_file():
            return None
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def request_stop(self) -> None:
        part = self.stop_path.with_suffix(".part")
        part.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
        os.replace(part, self.stop_path)

    def stop_requested(self) -> bool:
        return self.stop_path.is_file()

    def clear_stop(self) -> None:
        self.stop_path.unlink(missing_ok=True)

    def is_alive(self, *, stale_after_seconds: int = 30) -> bool:
        state = self.read_state()
        if not state or state.get("status") not in {"starting", "running"}:
            return False
        try:
            heartbeat = datetime.fromisoformat(str(state["heartbeat_at"]))
        except (KeyError, TypeError, ValueError):
            return False
        age = (datetime.now(UTC) - heartbeat).total_seconds()
        return age <= stale_after_seconds

    def ensure_started(self, command: Sequence[str] | None = None) -> int:
        """若 Broker 未运行则隐藏启动一个后台进程。"""
        if self.is_alive():
            state = self.read_state() or {}
            return int(state.get("pid") or 0)
        if self.lock_path.exists() and not self.is_alive():
            self.lock_path.unlink(missing_ok=True)
        try:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            state = self.read_state() or {}
            return int(state.get("pid") or 0)
        os.close(descriptor)
        self.clear_stop()
        args = (
            list(command)
            if command
            else [
                sys.executable,
                "-m",
                "doi_harvester",
                "job-worker",
                "--broker-profile",
                str(self.profile_dir),
            ]
        )
        log_handle = self.log_path.open("a", encoding="utf-8")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                close_fds=True,
            )
        except Exception:
            self.lock_path.unlink(missing_ok=True)
            raise
        finally:
            log_handle.close()
        self.write_state(pid=process.pid, status="starting")
        return process.pid

    def release_lock(self) -> None:
        self.lock_path.unlink(missing_ok=True)
