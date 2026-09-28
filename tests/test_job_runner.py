import csv
import json
from pathlib import Path

import pytest

from doi_harvester.job_runner import _write_supplement_results_csv, run_job
from doi_harvester.job_store import JobStore
from doi_harvester.models import DownloadResult


def test_run_job_downloads_pending_items_and_writes_report(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    report_dir = tmp_path / "report"
    folder = tmp_path / "1 Paper"
    job_id = store.create_job(
        records=[
            {
                "rank": 1,
                "doi": "10.1000/example",
                "title": "Paper",
                "folder_path": str(folder),
            }
        ],
        output_dir=tmp_path,
        report_dir=report_dir,
        browser_fallback=False,
    )

    class FakeHarvester:
        def download(self, doi: str, *, article_dir: Path) -> DownloadResult:
            article_dir.mkdir(parents=True)
            pdf = article_dir / "article.pdf"
            pdf.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
            return DownloadResult(
                doi=doi,
                success=True,
                status="downloaded",
                article_dir=article_dir,
                pdf_path=pdf,
                source="test",
            )

    status = run_job(job_id, store=store, harvester=FakeHarvester())

    assert status == "completed"
    assert store.get_job(job_id)["counts"] == {"downloaded": 1}
    assert (report_dir / "batch-report.json").is_file()


def test_run_job_pauses_queue_when_auth_is_required(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=[
            {
                "rank": 1,
                "doi": "10.1000/one",
                "title": "One",
                "folder_path": str(tmp_path / "1 One"),
            },
            {
                "rank": 2,
                "doi": "10.1000/two",
                "title": "Two",
                "folder_path": str(tmp_path / "2 Two"),
            },
        ],
        output_dir=tmp_path,
        report_dir=tmp_path / "report",
        browser_fallback=True,
    )
    calls: list[str] = []

    class FakeHarvester:
        def download(self, doi: str, *, article_dir: Path) -> DownloadResult:
            calls.append(doi)
            return DownloadResult(
                doi=doi,
                success=False,
                status="challenge_required",
                article_dir=article_dir,
            )

    status = run_job(job_id, store=store, harvester=FakeHarvester())

    assert status == "waiting_for_user"
    assert calls == ["10.1000/one"]
    assert store.get_job(job_id)["counts"] == {
        "auth_required": 1,
        "pending": 1,
    }


def test_run_job_pauses_queue_when_browser_connection_breaks(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=[
            {
                "rank": 1,
                "doi": "10.1000/one",
                "title": "One",
                "folder_path": str(tmp_path / "1 One"),
            },
            {
                "rank": 2,
                "doi": "10.1000/two",
                "title": "Two",
                "folder_path": str(tmp_path / "2 Two"),
            },
        ],
        output_dir=tmp_path,
        report_dir=tmp_path / "report",
        browser_fallback=True,
    )
    calls: list[str] = []

    class FakeHarvester:
        def download(self, doi: str, *, article_dir: Path) -> DownloadResult:
            calls.append(doi)
            return DownloadResult(
                doi=doi,
                success=False,
                status="cdp_error:RuntimeError",
                article_dir=article_dir,
            )

    status = run_job(job_id, store=store, harvester=FakeHarvester())

    assert status == "needs_attention"
    assert calls == ["10.1000/one"]
    assert store.get_job(job_id)["counts"] == {
        "retryable": 1,
        "pending": 1,
    }
    assert store.get_job(job_id)["last_event"].startswith("browser_recoverable_error:")


def test_run_job_passes_saved_browser_supervision_options(monkeypatch, tmp_path: Path) -> None:
    from doi_harvester import job_runner

    store = JobStore(tmp_path / "jobs.sqlite3")
    captured: dict[str, object] = {}
    job_id = store.create_job(
        records=[
            {
                "rank": 1,
                "doi": "10.1000/example",
                "title": "Paper",
                "folder_path": str(tmp_path / "1 Paper"),
            }
        ],
        output_dir=tmp_path,
        report_dir=tmp_path / "report",
        browser_fallback=True,
        profile_dir=tmp_path / "profile",
        options={
            "challenge_policy": "pause",
            "challenge_timeout_seconds": 42,
            "keep_browser_open": True,
            "supplements_only": True,
            "supplement_urls": {"10.1000/example": ["https://publisher.test/si.pdf"]},
        },
    )

    class FakeHarvester:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def download(self, doi: str, *, article_dir: Path) -> DownloadResult:
            return DownloadResult(
                doi=doi,
                success=False,
                status="challenge_required",
                article_dir=article_dir,
            )

    monkeypatch.setattr(job_runner, "Harvester", FakeHarvester)
    assert run_job(job_id, store=store) == "waiting_for_user"

    browser_options = captured["browser_options"]
    assert isinstance(browser_options, dict)
    assert browser_options["challenge_policy"] == "pause"
    assert browser_options["challenge_timeout_seconds"] == 42
    assert browser_options["keep_browser_open"] is True
    assert captured["browser_display"] == "foreground"
    assert captured["supplement_urls"] == {"10.1000/example": ["https://publisher.test/si.pdf"]}


def test_run_job_checkpoints_excel_and_keeps_processing_skips(monkeypatch, tmp_path: Path) -> None:
    from doi_harvester import job_runner

    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=[
            {
                "rank": rank,
                "doi": f"10.1000/{rank}",
                "title": str(rank),
                "folder_path": str(tmp_path / str(rank)),
            }
            for rank in range(1, 4)
        ],
        output_dir=tmp_path,
        report_dir=tmp_path / "report",
        browser_fallback=False,
        workbook_path=tmp_path / "index.xlsx",
        node_path=tmp_path / "node.exe",
        node_modules=tmp_path / "node_modules",
        batch_size=2,
    )
    sync_calls: list[Path] = []
    monkeypatch.setattr(
        job_runner,
        "update_workbook",
        lambda **kwargs: sync_calls.append(kwargs["report_path"]) or {"success": True},
    )

    class FakeHarvester:
        def download(self, doi: str, *, article_dir: Path) -> DownloadResult:
            return DownloadResult(
                doi=doi,
                success=False,
                status="policy_skipped",
                reason="access_policy_skip_paid",
                article_dir=article_dir,
            )

    status = run_job(job_id, store=store, harvester=FakeHarvester())

    job = store.get_job(job_id)
    assert status == "completed"
    assert job["counts"] == {"policy_skipped": 3}
    assert job["excel_status"] == "updated"
    assert len(sync_calls) == 2


def test_interrupted_task_reuses_id_and_committed_results(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=[
            {
                "rank": i,
                "doi": f"10.1000/{i}",
                "title": str(i),
                "folder_path": str(tmp_path / str(i)),
            }
            for i in (1, 2)
        ],
        output_dir=tmp_path,
        report_dir=None,
        browser_fallback=False,
    )
    seen = []

    class Worker:
        def download(self, doi, *, article_dir):
            state = store.get_job(job_id)
            assert (
                next(item for item in state["items"] if item["doi"] == doi)["status"] == "running"
            )
            seen.append(doi)
            if doi.endswith("2") and len(seen) == 2:
                assert state["items"][0]["status"] == "downloaded"
                raise KeyboardInterrupt()
            return DownloadResult(
                doi=doi, success=True, status="downloaded", article_dir=article_dir
            )

    assert run_job(job_id, store=store, harvester=Worker()) == "needs_attention"
    assert store.get_job(job_id)["counts"] == {"downloaded": 1, "running": 1}
    assert (Path(store.get_job(job_id)["report_dir"]) / "batch-report.json").exists()
    store.prepare_resume(job_id)
    assert run_job(job_id, store=store, harvester=Worker()) == "completed"
    assert seen == ["10.1000/1", "10.1000/2", "10.1000/2"]


def test_old_task_defaults_to_article_mode(monkeypatch, tmp_path):
    from doi_harvester import job_runner

    captured = {}
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=[
            {"doi": "10.1000/example", "title": "Paper", "folder_path": str(tmp_path / "paper")}
        ],
        output_dir=tmp_path,
        report_dir=None,
        browser_fallback=False,
        options={"elsevier_api_key": "legacy-placeholder", "elsevier_inst_token": "legacy"},
    )

    class Worker:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def download(self, doi, *, article_dir):
            return DownloadResult(doi=doi, success=True, status="cached", article_dir=article_dir)

    monkeypatch.setattr(job_runner, "Harvester", Worker)
    assert run_job(job_id, store=store) == "completed"
    assert captured["download_supplements"] is False
    assert captured["supplements_only"] is False
    assert captured["browser_display"] == "foreground"
    assert "elsevier" not in " ".join(captured)


@pytest.mark.parametrize("policy", ["pause", "skip"])
@pytest.mark.parametrize("stage", ["browser_display", "browser_fallback", "supplement:browser"])
@pytest.mark.parametrize("gate", ["challenge_required", "authentication_required"])
@pytest.mark.parametrize("mode", [{}, {"supplements": True}, {"supplements_only": True}])
def test_authorization_policy_at_each_download_stage(tmp_path, policy, stage, gate, mode):
    from doi_harvester.models import Attempt, SupplementArtifact

    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=[
            {
                "rank": i,
                "doi": f"10.1000/{i}",
                "title": str(i),
                "folder_path": str(tmp_path / str(i)),
            }
            for i in (1, 2)
        ],
        output_dir=tmp_path,
        report_dir=tmp_path / "report",
        browser_fallback=True,
        options={**mode, "challenge_policy": policy, "results_csv": str(tmp_path / "si.csv")},
    )
    calls = []
    saved = tmp_path / "saved.csv"
    saved.write_bytes(b"a,b\n1,2")
    artifact = SupplementArtifact(
        name="saved.csv",
        path=str(saved),
        url="https://cdn.test/saved.csv",
        content_type="text/csv",
        bytes_written=7,
        sha256="saved-hash",
    )

    class Worker:
        def download(self, doi, *, article_dir):
            calls.append(doi)
            if doi.endswith("2"):
                return DownloadResult(
                    doi=doi, success=True, status="cached", article_dir=article_dir
                )
            attempt = Attempt(
                source=stage, url="https://publisher.test/verification", success=False, reason=gate
            )
            return DownloadResult(
                doi=doi,
                success=False,
                status=gate,
                article_dir=article_dir,
                supplements=[artifact] if stage == "supplement:browser" else [],
                supplement_status=gate if stage == "supplement:browser" else "not_requested",
                supplement_attempts=[attempt] if stage == "supplement:browser" else [],
                attempts=[] if stage == "supplement:browser" else [attempt],
            )

    status = run_job(job_id, store=store, harvester=Worker())
    job = store.get_job(job_id)
    first = job["items"][0]
    assert status == ("completed" if policy == "skip" else "waiting_for_user")
    assert calls == (["10.1000/1", "10.1000/2"] if policy == "skip" else ["10.1000/1"])
    assert first["status"] == ("auth_skipped" if policy == "skip" else "auth_required")
    assert first["failure_reason"] == gate
    if mode:
        import csv

        with (tmp_path / "si.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = [row for row in csv.DictReader(handle) if row["DOI"] == "10.1000/1"]
        assert rows
        if policy == "skip":
            assert all(row["SI状态"] == "auth_skipped" for row in rows)
    assert saved.read_bytes() == b"a,b\n1,2"
    if stage == "supplement:browser":
        assert first["supplements"][0]["sha256"] == "saved-hash"
    report = json.loads((tmp_path / "report" / "batch-report.json").read_text(encoding="utf-8"))
    assert report["results"][0]["success"] is False
    assert report["results"][0]["status"] == first["status"]
    if policy == "skip":
        with pytest.raises(ValueError, match="没有可恢复"):
            store.prepare_resume(job_id)


def test_auth_skipped_is_not_requeued_with_pending_items(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=[
            {"doi": f"10.1000/{i}", "title": str(i), "folder_path": str(tmp_path / str(i))}
            for i in (1, 2)
        ],
        output_dir=tmp_path,
        report_dir=None,
        browser_fallback=False,
    )
    store.record_result(
        job_id,
        "10.1000/1",
        DownloadResult(
            doi="10.1000/1",
            success=False,
            status="auth_skipped",
            reason="authentication_required",
            article_dir=tmp_path,
        ),
    )
    store.set_job_status(job_id, "needs_attention")
    store.prepare_resume(job_id, retry_failed=True)
    assert [item["doi"] for item in store.pending_items(job_id)] == ["10.1000/2"]


def test_pending_csv_checkpoint_preserves_existing_attachment_rows(tmp_path: Path) -> None:
    destination = tmp_path / "si-results.csv"
    destination.write_text(
        "序号,DOI,SI状态,附件名,本地路径,SHA256\n"
        "1,10.1000/one,downloaded,one.zip,C:\\papers\\one.zip,ABC123\n",
        encoding="utf-8-sig",
    )

    _write_supplement_results_csv(
        [
            {
                "rank": 1,
                "doi": "10.1000/one",
                "status": "pending",
                "supplement_status": "pending",
                "supplements": [],
                "supplement_attempts": [],
            },
            {
                "rank": 2,
                "doi": "10.1000/two",
                "status": "pending",
                "supplement_status": "pending",
                "supplements": [],
                "supplement_attempts": [],
            },
        ],
        destination,
    )

    rows = list(csv.DictReader(destination.open(encoding="utf-8-sig", newline="")))
    assert len(rows) == 2
    existing = next(row for row in rows if row["DOI"] == "10.1000/one")
    untouched = next(row for row in rows if row["DOI"] == "10.1000/two")
    assert existing["SI状态"] == "downloaded"
    assert existing["本地路径"] == "C:\\papers\\one.zip"
    assert existing["SHA256"] == "ABC123"
    assert untouched["SI状态"] == "pending"


def test_failed_retry_preserves_previous_successful_attachment_row(tmp_path: Path) -> None:
    destination = tmp_path / "si-results.csv"
    destination.write_text(
        "序号,DOI,SI状态,附件名,本地路径,SHA256\n"
        "1,10.1000/one,downloaded,one.zip,C:\\papers\\one.zip,ABC123\n",
        encoding="utf-8-sig",
    )

    _write_supplement_results_csv(
        [
            {
                "rank": 1,
                "doi": "10.1000/one",
                "status": "failed",
                "supplement_status": "unconfirmed",
                "failure_reason": "unconfirmed",
                "supplements": [],
                "supplement_attempts": [],
            }
        ],
        destination,
    )

    rows = list(csv.DictReader(destination.open(encoding="utf-8-sig", newline="")))
    success = next(row for row in rows if row["本地路径"])
    retry = next(row for row in rows if row["SI状态"] == "unconfirmed")
    assert success["SI状态"] == "downloaded"
    assert success["本地路径"] == "C:\\papers\\one.zip"
    assert retry["失败原因"] == "unconfirmed"


def test_skip_policy_does_not_hide_doi_confirmation_failure(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=[
            {"doi": f"10.1000/{i}", "title": str(i), "folder_path": str(tmp_path / str(i))}
            for i in (1, 2)
        ],
        output_dir=tmp_path,
        report_dir=None,
        browser_fallback=True,
        options={"challenge_policy": "skip"},
    )
    calls = []

    class Worker:
        def download(self, doi, *, article_dir):
            calls.append(doi)
            return DownloadResult(
                doi=doi,
                success=False,
                status="browser_publisher_unavailable",
                article_dir=article_dir,
            )

    assert run_job(job_id, store=store, harvester=Worker()) == "needs_attention"
    assert calls == ["10.1000/1"]
    assert store.get_job(job_id)["counts"] == {"retryable": 1, "pending": 1}


def test_skip_keeps_delay_and_batch_checkpoints(monkeypatch, tmp_path):
    from doi_harvester import job_runner

    calls, delays, syncs = [], [], []
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create_job(
        records=[
            {"doi": f"10.1000/{i}", "title": str(i), "folder_path": str(tmp_path / str(i))}
            for i in (1, 2)
        ],
        output_dir=tmp_path,
        report_dir=tmp_path / "report",
        browser_fallback=False,
        options={"challenge_policy": "skip", "delay_seconds": 1.5},
        batch_size=1,
    )
    monkeypatch.setattr(job_runner.time, "sleep", lambda seconds: delays.append(seconds))
    monkeypatch.setattr(job_runner, "_sync_excel", lambda *_: syncs.append(True) or True)

    class Worker:
        def download(self, doi, *, article_dir):
            calls.append(doi)
            return DownloadResult(
                doi=doi, success=False, status="challenge_required", article_dir=article_dir
            )

    assert run_job(job_id, store=store, harvester=Worker()) == "completed"
    assert len(calls) == 2 and delays == [1.5]
    assert len(syncs) == 3
    report = json.loads((tmp_path / "report" / "batch-report.json").read_text(encoding="utf-8"))
    assert report["counts"] == {"auth_skipped": 2}
    assert not any(item["success"] for item in report["results"])
