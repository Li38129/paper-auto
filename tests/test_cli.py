import json
from pathlib import Path

import pytest

from doi_harvester import cli
from doi_harvester.browser import AuthorizationResult
from doi_harvester.config import ElsevierConfig, GlobalConfig
from doi_harvester.elsevier import ElsevierDownload
from doi_harvester.models import DownloadResult


def test_load_dois_reads_file_normalizes_and_deduplicates(tmp_path: Path) -> None:
    doi_file = tmp_path / "dois.txt"
    doi_file.write_text(
        "# 注释\nhttps://doi.org/10.1000/ABC\n10.1000/abc\n10.1000/def\n",
        encoding="utf-8",
    )

    dois = cli._load_dois(["doi:10.1000/xyz"], doi_file)

    assert dois == ["10.1000/xyz", "10.1000/abc", "10.1000/def"]


def test_load_dois_requires_input() -> None:
    with pytest.raises(ValueError, match="至少需要"):
        cli._load_dois([], None)


def test_main_does_not_write_batch_report_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeHarvester:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def download(self, doi: str, *, overwrite: bool = False) -> DownloadResult:
            del overwrite
            article_dir = tmp_path / doi.replace("/", "_")
            article_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = article_dir / "article.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
            return DownloadResult(
                doi=doi,
                success=True,
                status="downloaded",
                article_dir=article_dir,
                pdf_path=pdf_path,
                source="test",
            )

    monkeypatch.setattr(cli, "Harvester", FakeHarvester)

    exit_code = cli.main(
        ["--doi", "10.1000/example", "--output-dir", str(tmp_path), "--delay", "0"]
    )

    assert exit_code == 0
    assert not (tmp_path / "batch-report.json").exists()


def test_main_writes_batch_report_to_explicit_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeHarvester:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def download(self, doi: str, *, overwrite: bool = False) -> DownloadResult:
            del overwrite
            article_dir = tmp_path / doi.replace("/", "_")
            article_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = article_dir / "article.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
            return DownloadResult(
                doi=doi,
                success=True,
                status="downloaded",
                article_dir=article_dir,
                pdf_path=pdf_path,
                source="test",
            )

    monkeypatch.setattr(cli, "Harvester", FakeHarvester)
    report_dir = tmp_path / "audit"

    exit_code = cli.main(
        [
            "--doi",
            "10.1000/example",
            "--output-dir",
            str(tmp_path),
            "--report-dir",
            str(report_dir),
            "--delay",
            "0",
        ]
    )

    assert exit_code == 0
    report = json.loads((report_dir / "batch-report.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == 1
    assert report["results"][0]["success"] is True
    assert not (tmp_path / "batch-report.json").exists()


def test_main_keeps_partial_failure_in_explicit_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeHarvester:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def download(self, doi: str, *, overwrite: bool = False) -> DownloadResult:
            del overwrite
            article_dir = tmp_path / doi.replace("/", "_")
            if doi.endswith("success"):
                article_dir.mkdir(parents=True)
                pdf_path = article_dir / "article.pdf"
                pdf_path.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
                return DownloadResult(
                    doi=doi,
                    success=True,
                    status="downloaded",
                    article_dir=article_dir,
                    pdf_path=pdf_path,
                    source="test",
                )
            return DownloadResult(
                doi=doi,
                success=False,
                status="challenge_required",
                article_dir=article_dir,
            )

    monkeypatch.setattr(cli, "Harvester", FakeHarvester)
    report_dir = tmp_path / "job"

    exit_code = cli.main(
        [
            "--doi",
            "10.1000/success",
            "--doi",
            "10.1000/failure",
            "--output-dir",
            str(tmp_path),
            "--report-dir",
            str(report_dir),
            "--delay",
            "0",
        ]
    )

    report_path = report_dir / "batch-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report_path.is_file()
    assert [result["status"] for result in report["results"]] == [
        "downloaded",
        "challenge_required",
    ]


def test_foreground_browser_fallback_defaults_to_pause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    options: dict[str, object] = {}

    class FakeHarvester:
        def __init__(self, **kwargs: object) -> None:
            options.update(kwargs)

        def download(self, doi: str, *, overwrite: bool = False) -> DownloadResult:
            del overwrite
            return DownloadResult(
                doi=doi,
                success=False,
                status="challenge_required",
                article_dir=tmp_path,
            )

    monkeypatch.setattr(cli, "Harvester", FakeHarvester)

    exit_code = cli.main(
        [
            "download",
            "--doi",
            "10.1000/example",
            "--output-dir",
            str(tmp_path),
            "--browser-fallback",
            "--delay",
            "0",
        ]
    )

    assert exit_code == 2
    browser_options = options["browser_options"]
    assert isinstance(browser_options, dict)
    assert browser_options["challenge_policy"] == "pause"
    assert browser_options["challenge_timeout_seconds"] == 600


def test_fail_fast_stops_after_auth_challenge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    class FakeHarvester:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def download(self, doi: str, *, overwrite: bool = False) -> DownloadResult:
            del overwrite
            calls.append(doi)
            return DownloadResult(
                doi=doi,
                success=False,
                status="challenge_required",
                article_dir=tmp_path,
            )

    monkeypatch.setattr(cli, "Harvester", FakeHarvester)

    exit_code = cli.main(
        [
            "download",
            "--doi",
            "10.1000/one",
            "--doi",
            "10.1000/two",
            "--output-dir",
            str(tmp_path),
            "--challenge-policy",
            "fail-fast",
            "--delay",
            "0",
        ]
    )

    assert exit_code == 2
    assert calls == ["10.1000/one"]


def test_main_downloads_papers_file_into_numbered_folders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target_folder = tmp_path / "81 测试论文，IC=界面研究"
    papers_file = tmp_path / "papers.json"
    papers_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "papers": [
                    {
                        "rank": 81,
                        "doi": "10.1000/example",
                        "title": "测试论文",
                        "folder_path": str(target_folder),
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, Path | None]] = []

    class FakeHarvester:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def download(
            self,
            doi: str,
            *,
            overwrite: bool = False,
            article_dir: Path | None = None,
        ) -> DownloadResult:
            del overwrite
            calls.append((doi, article_dir))
            assert article_dir is not None
            article_dir.mkdir(parents=True)
            pdf_path = article_dir / "article.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
            return DownloadResult(
                doi=doi,
                success=True,
                status="downloaded",
                article_dir=article_dir,
                pdf_path=pdf_path,
                source="test",
            )

    monkeypatch.setattr(cli, "Harvester", FakeHarvester)

    exit_code = cli.main(
        [
            "download",
            "--papers-file",
            str(papers_file),
            "--output-dir",
            str(tmp_path),
            "--delay",
            "0",
        ]
    )

    assert exit_code == 0
    assert calls == [("10.1000/example", target_folder)]
    assert set(target_folder.iterdir()) == {target_folder / "article.pdf"}
    assert not (tmp_path / "batch-report.json").exists()


def test_auth_command_initializes_persistent_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict[str, object] = {}

    class FakeAuthorizer:
        def __init__(self, **kwargs: object) -> None:
            calls["options"] = kwargs

        def authorize(
            self, *, publisher: str, doi: str | None, timeout_seconds: float
        ) -> AuthorizationResult:
            calls["authorize"] = (publisher, doi, timeout_seconds)
            return AuthorizationResult(
                success=True,
                status="ready",
                final_url="https://pubs.acs.org/doi/10.1021/example",
                profile_dir=tmp_path / "profile",
            )

    monkeypatch.setattr(cli, "BrowserAuthorizer", FakeAuthorizer)

    exit_code = cli.main(
        [
            "auth",
            "--publisher",
            "acs",
            "--doi",
            "10.1021/example",
            "--profile-dir",
            str(tmp_path / "profile"),
            "--auth-timeout",
            "10",
        ]
    )

    assert exit_code == 0
    assert calls["authorize"] == ("acs", "10.1021/example", 10.0)


def test_auth_command_accepts_elsevier_publisher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    class FakeAuthorizer:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def authorize(
            self, *, publisher: str, doi: str | None, timeout_seconds: float
        ) -> AuthorizationResult:
            del doi, timeout_seconds
            calls.append(publisher)
            return AuthorizationResult(
                success=True,
                status="ready",
                final_url="https://www.sciencedirect.com/",
                profile_dir=tmp_path / "profile",
            )

    monkeypatch.setattr(cli, "BrowserAuthorizer", FakeAuthorizer)

    exit_code = cli.main(["auth", "--publisher", "elsevier"])

    assert exit_code == 0
    assert calls == ["elsevier"]


def test_elsevier_setup_uses_hidden_input_and_masks_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    saved: list[GlobalConfig] = []

    class FakeStore:
        path = tmp_path / "config.json"

        def __init__(self) -> None:
            self.config = GlobalConfig()

        def load(self) -> GlobalConfig:
            return self.config

        def save(self, config: GlobalConfig) -> None:
            self.config = config
            saved.append(config)

    monkeypatch.setattr(cli, "GlobalConfigStore", FakeStore)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "top-secret-key")

    exit_code = cli.main(["elsevier-setup", "--set-key", "--show"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert saved[0].elsevier.api_key == "top-secret-key"
    assert "top-secret-key" not in output
    assert "**********-key" in output


def test_elsevier_setup_validate_does_not_keep_pdf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destinations: list[Path] = []

    class FakeStore:
        path = tmp_path / "config.json"

        def load(self) -> GlobalConfig:
            return GlobalConfig(elsevier=ElsevierConfig(api_key="secret"))

        def save(self, _config: GlobalConfig) -> None:
            return None

    class FakeElsevier:
        def download(self, *, destination: Path, **_kwargs: object) -> ElsevierDownload:
            destination.write_bytes(b"%PDF-1.7")
            destinations.append(destination)
            return ElsevierDownload(
                True,
                "downloaded",
                source="elsevier_api:object_eid:direct",
            )

    monkeypatch.setattr(cli, "GlobalConfigStore", FakeStore)
    monkeypatch.setattr(cli, "ElsevierApiClient", FakeElsevier)

    exit_code = cli.main(["elsevier-setup", "--validate"])

    assert exit_code == 0
    assert len(destinations) == 1
    assert not destinations[0].exists()


def test_elsevier_setup_explains_api_configuration_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class FakeStore:
        path = tmp_path / "config.json"

        def load(self) -> GlobalConfig:
            return GlobalConfig(elsevier=ElsevierConfig(api_key="secret"))

        def save(self, _config: GlobalConfig) -> None:
            return None

    class FakeElsevier:
        def download(self, **_kwargs: object) -> ElsevierDownload:
            return ElsevierDownload(False, "api_configuration_error")

    monkeypatch.setattr(cli, "GlobalConfigStore", FakeStore)
    monkeypatch.setattr(cli, "ElsevierApiClient", FakeElsevier)

    exit_code = cli.main(["elsevier-setup", "--validate"])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "Article Retrieval API" in output
