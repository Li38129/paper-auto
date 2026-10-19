"""指定DOI恢复只重新入队已审查普通失败项。"""

from pathlib import Path

import pytest

from doi_harvester.job_store import JobStore
from doi_harvester.models import DownloadResult


def setup_job(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    records = [
        {"rank": i, "doi": f"10.1000/{i}", "title": str(i), "folder_path": str(tmp_path / str(i))}
        for i in (1, 2, 3)
    ]
    job = store.create_job(
        records=records, output_dir=tmp_path, report_dir=None, browser_fallback=False
    )
    for i, status in ((1, "worker_error"), (2, "subscription_required"), (3, "auth_skipped")):
        store.record_result(
            job,
            f"10.1000/{i}",
            DownloadResult(
                doi=f"10.1000/{i}", success=False, status=status, article_dir=tmp_path / str(i)
            ),
        )
    store.set_job_status(job, "completed")
    return store, job


def test_selected_retry_keeps_other_failures_and_auth_skip(tmp_path):
    store, job = setup_job(tmp_path)
    store.prepare_resume(job, retry_dois=["10.1000/1"])
    states = {x["doi"]: x["status"] for x in store.get_job(job)["items"]}
    assert states == {
        "10.1000/1": "pending",
        "10.1000/2": "subscription_required",
        "10.1000/3": "auth_skipped",
    }


def test_selected_retry_rejects_auth_skip_and_unknown_doi(tmp_path):
    store, job = setup_job(tmp_path)
    for doi in ("10.1000/3", "10.1000/missing"):
        with pytest.raises(ValueError, match="普通失败"):
            store.prepare_resume(job, retry_dois=[doi])
    assert store.get_job(job)["counts"].get("pending", 0) == 0
