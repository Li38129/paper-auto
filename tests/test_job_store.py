from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from doi_harvester.job_store import JobStore
from doi_harvester.models import Attempt, DownloadResult, SupplementArtifact


def test_supplement_result_survives_job_store_reopen(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=sample_records(tmp_path),
        output_dir=tmp_path,
        report_dir=tmp_path / "report",
        browser_fallback=False,
        options={"supplements_only": True},
    )
    store.record_result(
        job_id,
        "10.1000/one",
        DownloadResult(
            doi="10.1000/one",
            success=True,
            status="downloaded",
            article_dir=tmp_path / "1 One",
            supplement_status="downloaded",
            supplements=[
                SupplementArtifact(
                    name="si.csv",
                    path=str(tmp_path / "1 One" / "supplements" / "si.csv"),
                    url="https://example.test/si.csv",
                    content_type="text/csv",
                    bytes_written=10,
                    sha256="A" * 64,
                )
            ],
        ),
    )
    item = JobStore(tmp_path / "jobs.sqlite3").get_job(job_id)["items"][0]
    assert item["supplement_status"] == "downloaded"
    assert item["supplements"][0]["name"] == "si.csv"


def sample_records(root: Path) -> list[dict[str, object]]:
    return [
        {
            "rank": 1,
            "doi": "10.1000/one",
            "title": "One",
            "folder_path": str(root / "1 One"),
        },
        {
            "rank": 2,
            "doi": "10.1000/two",
            "title": "Two",
            "folder_path": str(root / "2 Two"),
        },
    ]


def test_job_store_creates_job_and_records_stage_attempts(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=sample_records(tmp_path),
        output_dir=tmp_path,
        report_dir=tmp_path / "report",
        browser_fallback=True,
    )

    job = store.get_job(job_id)
    assert job["status"] == "queued"
    assert job["counts"] == {"pending": 2}

    result = DownloadResult(
        doi="10.1000/one",
        success=True,
        status="downloaded",
        article_dir=tmp_path / "1 One",
        pdf_path=tmp_path / "1 One" / "article.pdf",
        source="test",
        attempts=[
            Attempt(
                source="test",
                url="https://example.test/article.pdf",
                success=True,
                reason="downloaded",
                stage="publisher_candidate",
                provider="example",
                route="direct",
                duration_ms=15,
            )
        ],
    )
    store.record_result(job_id, "10.1000/one", result)

    updated = store.get_job(job_id)
    first = next(item for item in updated["items"] if item["doi"] == "10.1000/one")
    assert first["status"] == "downloaded"
    assert first["attempts"][0]["stage"] == "publisher_candidate"


def test_job_store_marks_expired_running_job_stalled(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=sample_records(tmp_path),
        output_dir=tmp_path,
        report_dir=None,
        browser_fallback=False,
    )
    store.set_job_status(job_id, "running")
    stale = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    with store.connect() as connection:
        connection.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ?", (stale, job_id))
        connection.commit()

    assert store.mark_stalled_jobs(timeout_seconds=60) == [job_id]
    assert store.get_job(job_id)["status"] == "stalled"


def test_resume_resets_only_retryable_items(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=sample_records(tmp_path),
        output_dir=tmp_path,
        report_dir=None,
        browser_fallback=False,
    )
    store.set_item_status(job_id, "10.1000/one", "downloaded")
    store.set_item_status(job_id, "10.1000/two", "retryable")
    store.set_job_status(job_id, "needs_attention")

    count = store.prepare_resume(job_id)

    job = store.get_job(job_id)
    assert count == 1
    assert job["counts"] == {"downloaded": 1, "pending": 1}
    assert job["status"] == "queued"


def test_cdp_error_is_retryable(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=[sample_records(tmp_path)[0]],
        output_dir=tmp_path,
        report_dir=None,
        browser_fallback=True,
    )

    store.record_result(
        job_id,
        "10.1000/one",
        DownloadResult(
            doi="10.1000/one",
            success=False,
            status="cdp_error:RuntimeError",
            article_dir=tmp_path / "1 One",
        ),
    )

    assert store.get_job(job_id)["counts"] == {"retryable": 1}


def test_resume_resets_auth_required_items(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=sample_records(tmp_path),
        output_dir=tmp_path,
        report_dir=None,
        browser_fallback=True,
    )
    store.set_item_status(job_id, "10.1000/one", "auth_required")
    store.set_job_status(job_id, "waiting_for_user")

    count = store.prepare_resume(job_id)

    job = store.get_job(job_id)
    assert count == 1
    assert job["counts"] == {"pending": 2}
    assert job["status"] == "queued"

    with pytest.raises(ValueError, match="不能重复启动"):
        store.prepare_resume(job_id)

    store.set_item_status(job_id, "10.1000/one", "auth_required")
    store.set_job_status(job_id, "waiting_for_user")
    assert store.prepare_resume(job_id) == 1


def test_resume_can_include_reviewed_failures(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=sample_records(tmp_path),
        output_dir=tmp_path,
        report_dir=None,
        browser_fallback=True,
    )
    store.set_item_status(job_id, "10.1000/one", "failed")
    store.set_item_status(job_id, "10.1000/two", "downloaded")
    store.set_job_status(job_id, "needs_attention")

    assert store.prepare_resume(job_id, retry_failed=True) == 1
    assert store.get_job(job_id)["counts"] == {"pending": 1, "downloaded": 1}
