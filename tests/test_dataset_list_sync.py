"""验证整理清单去重与结果回写：序号补零、状态映射、默认不覆盖。"""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/dataset-list-sync.py"
spec = importlib.util.spec_from_file_location("dataset_list_sync", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

HEADERS = [
    "Sequence",
    "DOI",
    "Article",
    "SupplementFolder",
    "SupplementFileCount",
    "OutputFolder",
    "Status",
]


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_check_separates_existing_excluded_and_new(tmp_path: Path, monkeypatch) -> None:
    listing = tmp_path / "list.csv"
    write_csv(listing, [{"Sequence": "0001", "DOI": "10.1000/known", "Status": "copied"}])
    candidates = tmp_path / "candidates.csv"
    with candidates.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["序号", "DOI"])
        writer.writerow([1, "10.1000/known"])
        writer.writerow([2, "10.1000/fresh"])
        writer.writerow([3, "10.1149/2.024205esl"])
    monkeypatch.setattr(
        "sys.argv",
        ["x", "check", "--candidates", str(candidates), "--list", str(listing), "--json"],
    )
    assert module.main() == 0


def test_check_keeps_rule_exclusions(tmp_path: Path, capsys) -> None:
    listing = tmp_path / "list.csv"
    write_csv(listing, [{"Sequence": "0001", "DOI": "10.1000/known", "Status": "copied"}])
    candidates = tmp_path / "c.txt"
    candidates.write_text("10.1149/1.2988731\n10.1000/fresh\n", encoding="utf-8")
    args = type(
        "Args",
        (),
        {
            "candidates": candidates,
            "list": listing,
            "exclude_prefix": ("10.1149/",),
            "json": True,
        },
    )()
    module.command_check(args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["excluded_by_rule"] == ["10.1149/1.2988731"]
    assert payload["new"] == ["10.1000/fresh"]


def _report(path: Path, results: list[dict]) -> Path:
    payload = {"schema_version": 1, "results": results}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_apply_appends_rows_with_padded_sequence(tmp_path: Path) -> None:
    listing = tmp_path / "list.csv"
    write_csv(listing, [{"Sequence": "0999", "DOI": "10.1000/known", "Status": "copied"}])
    report = _report(
        tmp_path / "report.json",
        [
            {
                "doi": "10.1000/new-a",
                "status": "downloaded",
                "supplement_status": "downloaded",
                "article_dir": "E:/papers/a",
                "supplements": [{"name": "s1.pdf"}],
            },
            {
                "doi": "10.1000/new-b",
                "status": "cached",
                "supplement_status": "unconfirmed",
                "article_dir": "E:/papers/b",
            },
            {
                "doi": "10.1000/failed",
                "status": "failed",
                "article_dir": "E:/papers/c",
            },
        ],
    )
    args = type(
        "Args",
        (),
        {"report": report, "list": listing, "dry_run": False, "overwrite_status": False, "show": 5},
    )()
    module.command_apply(args)
    rows = read_csv(listing)
    assert [row["Sequence"] for row in rows] == ["0999", "1000", "1001", "1002"]
    by_doi = {row["DOI"]: row for row in rows}
    assert by_doi["10.1000/new-a"]["Status"] == "copied"
    assert by_doi["10.1000/new-b"]["Status"] == "body_only_copied"
    assert by_doi["10.1000/failed"]["Status"] == "failed"
    assert by_doi["10.1000/new-a"]["OutputFolder"] == "E:/papers/a"


def test_apply_keeps_existing_status_by_default(tmp_path: Path) -> None:
    listing = tmp_path / "list.csv"
    write_csv(listing, [{"Sequence": "0001", "DOI": "10.1000/known", "Status": "copied"}])
    report = _report(
        tmp_path / "report.json",
        [{"doi": "10.1000/known", "status": "failed", "article_dir": "E:/new"}],
    )
    args = type(
        "Args",
        (),
        {"report": report, "list": listing, "dry_run": False, "overwrite_status": False, "show": 5},
    )()
    module.command_apply(args)
    row = read_csv(listing)[0]
    assert row["Status"] == "copied"
    assert row["OutputFolder"] == "E:/new"


def test_apply_overwrites_status_when_requested(tmp_path: Path) -> None:
    listing = tmp_path / "list.csv"
    write_csv(listing, [{"Sequence": "0001", "DOI": "10.1000/known", "Status": "copied"}])
    report = _report(
        tmp_path / "report.json",
        [{"doi": "10.1000/known", "status": "failed"}],
    )
    args = type(
        "Args",
        (),
        {"report": report, "list": listing, "dry_run": False, "overwrite_status": True, "show": 5},
    )()
    module.command_apply(args)
    assert read_csv(listing)[0]["Status"] == "failed"


def test_apply_dry_run_does_not_write(tmp_path: Path) -> None:
    listing = tmp_path / "list.csv"
    write_csv(listing, [{"Sequence": "0001", "DOI": "10.1000/known", "Status": "copied"}])
    before = listing.read_bytes()
    report = _report(tmp_path / "report.json", [{"doi": "10.1000/new", "status": "downloaded"}])
    args = type(
        "Args",
        (),
        {"report": report, "list": listing, "dry_run": True, "overwrite_status": False, "show": 5},
    )()
    module.command_apply(args)
    assert listing.read_bytes() == before


def test_paired_lists_block_failures_and_promote_verified_body(tmp_path, monkeypatch, capsys):
    from openpyxl import Workbook, load_workbook

    success = tmp_path / "整理清单_成功.csv"
    failed = tmp_path / "整理清单_已尝试但未下载成功.xlsx"
    write_csv(success, [])
    book = Workbook()
    book.active.append(["Sequence", "DOI", "失败原因", "Status"])
    book.save(failed)
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("https://doi.org/10.1000/ABC\n10.1000/abc\n", encoding="utf-8")
    result = {"doi": "10.1000/abc", "status": "failed"}
    report = _report(
        tmp_path / "report.json", [result, {"doi": "10.1000/wait", "status": "pending"}]
    )

    def run(*args):
        monkeypatch.setattr("sys.argv", ["sync", *map(str, args), "--list", str(success)])
        assert module.main() == 0
        return json.loads(capsys.readouterr().out)

    run("apply", "--report", report)
    check = run("check", "--candidates", candidate, "--json")
    assert check["already_attempted_failed"] == check["already_listed"] == ["10.1000/abc"]
    assert not check["new"] and not read_csv(success)

    from pypdf import PdfWriter

    body = tmp_path / "article.pdf"
    writer = PdfWriter()
    for _ in range(10):
        writer.add_blank_page(width=72, height=72)
    writer.write(body)
    result.update(pdf_path=str(body), status="cached")
    _report(report, [result])
    run("apply", "--report", report)
    run("apply", "--report", report)
    assert [r["Sequence"] for r in read_csv(success)] == ["0001"]
    assert not run("check", "--candidates", candidate, "--json")["already_attempted_failed"]
    book = load_workbook(failed, read_only=True)
    try:
        assert len(list(book.active.values)) == 1
    finally:
        book.close()
