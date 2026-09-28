import json
from pathlib import Path

import pytest

from doi_harvester.papers import PapersFileError, load_paper_jobs, validate_supplement_urls


def test_load_paper_jobs_validates_and_normalizes(tmp_path: Path) -> None:
    folder = tmp_path / "81 测试论文，IC=界面研究"
    papers_file = tmp_path / "papers.json"
    papers_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "papers": [
                    {
                        "rank": 81,
                        "doi": "https://doi.org/10.1000/ABC",
                        "title": "测试论文",
                        "folder_path": str(folder),
                        "journal": "Example Journal",
                        "issn": "1234-5678",
                        "year": 2025,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    jobs = load_paper_jobs(papers_file)

    assert len(jobs) == 1
    assert jobs[0].rank == 81
    assert jobs[0].doi == "10.1000/abc"
    assert jobs[0].folder_path == folder
    assert jobs[0].journal == "Example Journal"
    assert jobs[0].issn == "1234-5678"
    assert jobs[0].year == 2025


def test_load_paper_jobs_rejects_duplicate_doi(tmp_path: Path) -> None:
    papers_file = tmp_path / "papers.json"
    payload = {
        "schema_version": 1,
        "papers": [
            {
                "rank": 81,
                "doi": "10.1000/example",
                "title": "论文一",
                "folder_path": str(tmp_path / "81"),
            },
            {
                "rank": 82,
                "doi": "https://doi.org/10.1000/example",
                "title": "论文二",
                "folder_path": str(tmp_path / "82"),
            },
        ],
    }
    papers_file.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PapersFileError, match="重复 DOI"):
        load_paper_jobs(papers_file)


def test_load_paper_jobs_requires_absolute_folder(tmp_path: Path) -> None:
    papers_file = tmp_path / "papers.json"
    payload = {
        "schema_version": 1,
        "papers": [
            {
                "rank": 81,
                "doi": "10.1000/example",
                "title": "测试论文",
                "folder_path": "relative/folder",
            }
        ],
    }
    papers_file.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PapersFileError, match="绝对路径"):
        load_paper_jobs(papers_file)


def test_validate_supplement_urls_normalizes_and_deduplicates() -> None:
    urls = validate_supplement_urls(
        {"https://doi.org/10.1000/ABC": ["https://publisher.test/si.pdf"]},
        {"10.1000/abc"},
    )

    assert urls == {"10.1000/abc": ["https://publisher.test/si.pdf"]}


@pytest.mark.parametrize(
    ("values", "requested", "message"),
    [
        ({"10.1000/other": "https://publisher.test/si.pdf"}, {"10.1000/example"}, "不在本次任务"),
        ({"10.1000/example": "http://publisher.test/si.pdf"}, {"10.1000/example"}, "HTTPS"),
        (
            {"10.1000/example": "https://user:pass@publisher.test/si.pdf"},
            {"10.1000/example"},
            "HTTPS",
        ),
    ],
)
def test_validate_supplement_urls_rejects_invalid_entries(values, requested, message) -> None:
    with pytest.raises(PapersFileError, match=message):
        validate_supplement_urls(values, requested)
