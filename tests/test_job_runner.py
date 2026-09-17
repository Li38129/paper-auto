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
