from pathlib import Path

from doi_harvester.job_runner import run_job
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


def test_run_job_passes_saved_browser_supervision_options(
    monkeypatch, tmp_path: Path
) -> None:
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


def test_run_job_checkpoints_excel_and_keeps_processing_skips(
    monkeypatch, tmp_path: Path
) -> None:
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
