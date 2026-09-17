"""AutoPaper 本机配置与运行依赖诊断。"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .broker import BrokerManager, default_runtime_dir
from .config import ConfigError, GlobalConfigStore, load_elsevier_credentials, mask_secret
from .elsevier import ElsevierApiClient
from .job_store import JobStore
from .publisher_profiles import PUBLISHER_PROFILES


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: str
    message: str


def run_doctor(*, network: bool = False, target_dir: Path | None = None) -> dict[str, object]:
    checks: list[DoctorCheck] = []
    store = GlobalConfigStore()
    try:
        credentials = load_elsevier_credentials(store)
        checks.append(
            DoctorCheck(
                "elsevier_config",
                "ok" if credentials.api_key else "warning",
                f"配置路径 {store.path}；API Key {mask_secret(credentials.api_key)}",
            )
        )
    except ConfigError as exc:
        credentials = None
        checks.append(DoctorCheck("elsevier_config", "failed", str(exc)))

    runtime = default_runtime_dir().resolve()
    try:
        database = JobStore(runtime / "jobs" / "jobs.sqlite3")
        database.list_jobs(limit=1)
        checks.append(DoctorCheck("sqlite", "ok", str(database.path)))
    except Exception as exc:  # noqa: BLE001 - 诊断器必须继续完成其他检查
        checks.append(DoctorCheck("sqlite", "failed", str(exc)))

    checks.append(
        DoctorCheck(
            "publisher_profiles",
            "ok" if len(PUBLISHER_PROFILES) == 21 else "failed",
            f"已加载 {len(PUBLISHER_PROFILES)} 家出版社 Profile",
        )
    )
    browser = shutil.which("msedge") or shutil.which("chrome")
    checks.append(
        DoctorCheck(
            "browser",
            "ok" if browser else "warning",
            browser or "PATH 中未找到 Chrome/Edge；Playwright 仍可能自动定位浏览器。",
        )
    )
    profile = runtime / "profiles" / "default"
    broker = BrokerManager(profile_dir=profile, runtime_dir=runtime)
    state = broker.read_state()
    checks.append(
        DoctorCheck(
            "broker",
            "ok" if broker.is_alive() else "info",
            f"状态：{state.get('status') if state else '未运行'}；{broker.state_path}",
        )
    )
    lock = profile / ".doi-harvester.lock"
    checks.append(
        DoctorCheck(
            "profile_lock",
            "warning" if lock.exists() and not broker.is_alive() else "ok",
            str(lock) if lock.exists() else "未发现配置目录锁冲突。",
        )
    )
    auth_state = profile / "auth-state.json"
    checks.append(
        DoctorCheck(
            "browser_session",
            "ok" if auth_state.is_file() else "info",
            str(auth_state) if auth_state.is_file() else "尚未创建浏览器授权状态。",
        )
    )
    if target_dir is not None:
        target = Path(target_dir).resolve()
        writable_parent = target if target.exists() else target.parent
        writable = writable_parent.is_dir() and os.access(writable_parent, os.W_OK)
        checks.append(
            DoctorCheck(
                "target_dir",
                "ok" if writable else "failed",
                str(target),
            )
        )

    if network:
        if not credentials or not credentials.api_key:
            checks.append(
                DoctorCheck("elsevier_network", "warning", "缺少 API Key，未发起网络验证。")
            )
        else:
            with tempfile.TemporaryDirectory(prefix="autopaper-doctor-") as directory:
                result = ElsevierApiClient().download(
                    doi="10.1016/j.watres.2024.121507",
                    destination=Path(directory) / "article.pdf",
                    api_key=credentials.api_key,
                    inst_token=credentials.inst_token,
                    proxy_url=credentials.proxy_url,
                )
            checks.append(
                DoctorCheck(
                    "elsevier_network",
                    "ok" if result.success else "failed",
                    result.source if result.success else result.reason,
                )
            )

    statuses = {item.status for item in checks}
    overall = "failed" if "failed" in statuses else "warning" if "warning" in statuses else "ok"
    return {
        "schema_version": 1,
        "status": overall,
        "checks": [asdict(item) for item in checks],
    }
