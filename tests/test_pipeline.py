from pathlib import Path

from doi_harvester.config import ElsevierCredentials
from doi_harvester.elsevier import ElsevierDownload
from doi_harvester.metadata import MetadataError
from doi_harvester.models import (
    ArticleMetadata,
    Attempt,
    DownloadCandidate,
    DownloadResult,
    SupplementArtifact,
)
from doi_harvester.pipeline import Harvester


class FakeCrossref:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def fetch(self, doi: str) -> ArticleMetadata:
        if self.fail:
            raise MetadataError("offline")
        return ArticleMetadata(
            doi=doi,
            title="Test title",
            publisher="Test publisher",
            landing_url=f"https://doi.org/{doi}",
            candidates=[DownloadCandidate("https://example.test/article.pdf", "crossref", True)],
        )


class FakeOpenAlex:
    def __init__(self, candidates: list[DownloadCandidate] | None = None) -> None:
        self.candidates = candidates or []

    def fetch_candidates(self, _doi: str) -> list[DownloadCandidate]:
        return self.candidates


class FakeTransport:
    def __init__(self, *, succeeds: bool) -> None:
        self.succeeds = succeeds
        self.urls: list[str] = []

    def download(self, *, url: str, destination: Path, referer: str) -> Attempt:
        del referer
        self.urls.append(url)
        if self.succeeds:
            destination.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
        return Attempt(
            source="http",
            url=url,
            success=self.succeeds,
            reason="downloaded" if self.succeeds else "http_403",
            status_code=200 if self.succeeds else 403,
        )


class FakeElsevier:
    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.calls: list[str] = []

    def download(self, *, doi: str, destination: Path, **_kwargs: object) -> ElsevierDownload:
        self.calls.append(doi)
        if self.succeeds:
            destination.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
            return ElsevierDownload(
                True,
                "downloaded",
                source="elsevier_api:object_eid:direct",
            )
        return ElsevierDownload(False, "not_entitled")


def test_pipeline_downloads_only_pdf(tmp_path: Path) -> None:
    transport = FakeTransport(succeeds=True)
    harvester = Harvester(
        output_dir=tmp_path,
        crossref=FakeCrossref(),
        openalex=FakeOpenAlex(),
        transport=transport,
    )

    result = harvester.download("10.1000/example")

    assert result.success is True
    assert result.source == "crossref"
    assert result.pdf_path is not None and result.pdf_path.is_file()
    assert set(result.article_dir.iterdir()) == {result.pdf_path}


def test_pipeline_downloads_into_requested_paper_folder(tmp_path: Path) -> None:
    requested_folder = tmp_path / "81 测试论文，IC=界面研究"
    harvester = Harvester(
        output_dir=tmp_path / "unused",
        crossref=FakeCrossref(),
        openalex=FakeOpenAlex(),
        transport=FakeTransport(succeeds=True),
    )

    result = harvester.download("10.1000/example", article_dir=requested_folder)

    assert result.article_dir == requested_folder
    assert result.pdf_path == requested_folder / "article.pdf"
    assert set(requested_folder.iterdir()) == {requested_folder / "article.pdf"}


def test_pipeline_prefers_openalex_candidate(tmp_path: Path) -> None:
    transport = FakeTransport(succeeds=True)
    harvester = Harvester(
        output_dir=tmp_path,
        crossref=FakeCrossref(),
        openalex=FakeOpenAlex(
            [DownloadCandidate("https://repository.test/paper.pdf", "openalex", True)]
        ),
        transport=transport,
    )

    result = harvester.download("10.1000/example")

    assert result.source == "openalex"
    assert transport.urls[0] == "https://repository.test/paper.pdf"


def test_pipeline_uses_elsevier_after_oa_and_before_generic_candidate(tmp_path: Path) -> None:
    transport = FakeTransport(succeeds=False)
    elsevier = FakeElsevier()
    harvester = Harvester(
        output_dir=tmp_path,
        crossref=FakeCrossref(),
        openalex=FakeOpenAlex(
            [DownloadCandidate("https://repository.test/missing.pdf", "openalex", True)]
        ),
        transport=transport,
        elsevier=elsevier,
        elsevier_credentials=ElsevierCredentials(api_key="secret"),
    )

    result = harvester.download("10.1016/example")

    assert result.success is True
    assert result.source == "elsevier_api:object_eid:direct"
    assert transport.urls == ["https://repository.test/missing.pdf"]
    assert elsevier.calls == ["10.1016/example"]


def test_pipeline_elsevier_failure_does_not_block_generic_fallback(tmp_path: Path) -> None:
    class ElsevierCrossref(FakeCrossref):
        def fetch(self, doi: str) -> ArticleMetadata:
            return ArticleMetadata(
                doi=doi,
                publisher="Elsevier",
                landing_url=f"https://doi.org/{doi}",
                candidates=[DownloadCandidate("https://fallback.test/article.pdf", "crossref")],
            )

    transport = FakeTransport(succeeds=True)
    harvester = Harvester(
        output_dir=tmp_path,
        crossref=ElsevierCrossref(),
        openalex=FakeOpenAlex(),
        transport=transport,
        elsevier=FakeElsevier(succeeds=False),
        elsevier_credentials=ElsevierCredentials(api_key="secret"),
    )

    result = harvester.download("10.1016/example")

    assert result.success is True
    assert result.source == "crossref"
    assert transport.urls == ["https://fallback.test/article.pdf"]


def test_pipeline_reports_failure_without_creating_pdf(tmp_path: Path) -> None:
    harvester = Harvester(
        output_dir=tmp_path,
        crossref=FakeCrossref(),
        openalex=FakeOpenAlex(),
        transport=FakeTransport(succeeds=False),
    )

    result = harvester.download("10.1000/example")

    assert result.success is False
    assert result.pdf_path is None
    assert list(result.article_dir.iterdir()) == []


def test_pipeline_classifies_elsevier_api_configuration_error(tmp_path: Path) -> None:
    result = DownloadResult(
        doi="10.1016/example",
        success=False,
        status="api_configuration_error",
        article_dir=tmp_path,
    )

    Harvester._classify_result(result)

    assert result.outcome == "config_needed"
    assert result.next_action is not None
    assert "Article Retrieval" in result.next_action.message


def test_pipeline_uses_valid_cache(tmp_path: Path) -> None:
    article_dir = tmp_path / "10.1000_example"
    article_dir.mkdir()
    cached = article_dir / "article.pdf"
    cached.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
    harvester = Harvester(
        output_dir=tmp_path,
        crossref=FakeCrossref(),
        openalex=FakeOpenAlex(),
        transport=FakeTransport(succeeds=False),
    )

    result = harvester.download("10.1000/example")

    assert result.status == "cached"
    assert result.pdf_path == cached
    assert set(article_dir.iterdir()) == {cached}


def test_pipeline_falls_back_to_publisher_rules_when_metadata_is_offline(tmp_path: Path) -> None:
    transport = FakeTransport(succeeds=True)
    harvester = Harvester(
        output_dir=tmp_path,
        crossref=FakeCrossref(fail=True),
        openalex=FakeOpenAlex(),
        transport=transport,
    )

    result = harvester.download("10.1007/s10853-013-7226-8")

    assert result.success is True
    assert "link.springer.com/content/pdf" in transport.urls[0]


def test_pipeline_preserves_browser_failure_reason(monkeypatch, tmp_path: Path) -> None:
    from doi_harvester import browser

    class FakeBrowser:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def download(self, **_kwargs: object) -> Attempt:
            return Attempt(
                source="browser",
                url="https://pubs.acs.org/doi/example",
                success=False,
                reason="subscription_required",
            )

    monkeypatch.setattr(browser, "BrowserPdfDownloader", FakeBrowser)
    harvester = Harvester(
        output_dir=tmp_path,
        browser_fallback=True,
        crossref=FakeCrossref(),
        openalex=FakeOpenAlex(),
        transport=FakeTransport(succeeds=False),
    )

    result = harvester.download("10.1000/example")

    assert result.status == "subscription_required"


def test_pipeline_adds_supplements_only_when_explicitly_requested(
    monkeypatch, tmp_path: Path
) -> None:
    from doi_harvester import supplements

    article_dir = tmp_path / "paper"
    article_dir.mkdir()
    (article_dir / "article.pdf").write_bytes(b"%PDF-1.7\n" + b"x" * 2048)

    class FakeSupplementDownloader:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def download(self, **_kwargs: object):
            artifact = SupplementArtifact(
                name="support.docx",
                path=str(article_dir / "supplements" / "support.docx"),
                url="https://publisher.test/support.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                bytes_written=2048,
                sha256="A" * 64,
            )
            return "downloaded", [artifact], []

    monkeypatch.setattr(
        supplements,
        "BrowserSupplementDownloader",
        FakeSupplementDownloader,
    )
    harvester = Harvester(
        output_dir=tmp_path,
        crossref=FakeCrossref(),
        openalex=FakeOpenAlex(),
        transport=FakeTransport(succeeds=False),
        browser_options={"profile_dir": tmp_path / "profile"},
        download_supplements=True,
    )

    result = harvester.download("10.1000/example", article_dir=article_dir)

    assert result.supplement_status == "downloaded"
    assert result.supplements[0].name == "support.docx"
    assert not (article_dir / "manifest.json").exists()


def test_pipeline_does_not_call_supplement_downloader_by_default(
    monkeypatch, tmp_path: Path
) -> None:
    from doi_harvester import supplements

    class UnexpectedSupplementDownloader:
        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError("未显式请求时不得初始化补充材料下载器")

    monkeypatch.setattr(
        supplements,
        "BrowserSupplementDownloader",
        UnexpectedSupplementDownloader,
    )
    harvester = Harvester(
        output_dir=tmp_path,
        crossref=FakeCrossref(),
        openalex=FakeOpenAlex(),
        transport=FakeTransport(succeeds=True),
        browser_options={"profile_dir": tmp_path / "profile"},
    )

    result = harvester.download("10.1000/example")

    assert result.success is True
    assert result.supplement_status == "not_requested"
    assert not (result.article_dir / "supplements").exists()
