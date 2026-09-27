from pathlib import Path

import pytest

from doi_harvester.mcp_server import _absolute_path, update_excel


def test_absolute_path_rejects_relative_paths() -> None:
    with pytest.raises(ValueError, match="绝对路径"):
        _absolute_path("relative/file.json", "papers_file")


def test_update_excel_protects_original_when_runtime_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workbook = tmp_path / "文献检索汇总.xlsx"
    workbook.write_bytes(b"original")
    records = tmp_path / "records.json"
    records.write_text("{}", encoding="utf-8")
    resolved = tmp_path / "resolved.json"
    monkeypatch.delenv("AUTOPAPER_ARTIFACT_NODE_MODULES", raising=False)

    result = update_excel(str(workbook), str(records), resolved_path=str(resolved))

    assert result["error"] == "artifact_runtime_unavailable"
    assert workbook.read_bytes() == b"original"


@pytest.mark.parametrize("mode", [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize("detach", [False, True])
@pytest.mark.parametrize("policy", ["pause", "skip"])
def test_mcp_persists_download_mode_and_task(monkeypatch, tmp_path, mode, detach, policy):
    import json

    from doi_harvester import mcp_server
    from doi_harvester.job_store import JobStore

    runtime = tmp_path / "runtime"
    monkeypatch.setenv("DOI_HARVESTER_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(mcp_server, "run_job", lambda job_id, **_: "completed")
    monkeypatch.setattr(mcp_server.BrokerManager, "ensure_started", lambda self: 123)
    output = tmp_path / "papers"
    source = tmp_path / "papers.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "papers": [
                    {
                        "rank": 301,
                        "doi": "10.1000/example",
                        "title": "Paper",
                        "folder_path": str(output / "0301 Paper"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = mcp_server.download(
        str(source),
        str(output),
        supplements=mode[0],
        supplements_only=mode[1],
        detach=detach,
        challenge_policy=policy,
    )
    job = JobStore(runtime / "jobs" / "jobs.sqlite3").get_job(result["job_id"])
    options = json.loads(job["options_json"])
    assert (options["supplements"], options["supplements_only"]) == mode
    assert options["browser_display"] == "foreground"
    assert options["challenge_policy"] == policy
    assert job["items"][0]["rank"] == 301
    assert result["status"] == ("queued" if detach else "completed")


def test_mcp_rejects_invalid_verification_policy_before_task_creation(tmp_path, monkeypatch):
    from doi_harvester.mcp_server import download

    monkeypatch.setenv("DOI_HARVESTER_RUNTIME_DIR", str(tmp_path / "runtime"))
    with pytest.raises(ValueError, match="challenge_policy"):
        download("invalid.json", str(tmp_path), challenge_policy="unknown")
    assert not (tmp_path / "runtime").exists()
