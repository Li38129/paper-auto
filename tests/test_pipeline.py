from pathlib import Path

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


def test_pipeline_uses_public_candidate_for_elsevier(tmp_path: Path) -> None:
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


def test_pipeline_uses_detected_publisher_in_auth_command(tmp_path: Path) -> None:
    result = DownloadResult(
        doi="10.1039/d6eb00090h",
        success=False,
        status="challenge_required",
        article_dir=tmp_path,
        publisher="Royal Society of Chemistry (RSC)",
    )

    Harvester._classify_result(result)

    assert result.next_action is not None
    assert result.next_action.command == "doi-harvester auth --publisher rsc --cdp"


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
        "HttpSupplementDownloader",
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


def test_supplements_only_never_requests_article(monkeypatch, tmp_path: Path) -> None:
    from doi_harvester import supplements

    class FakeSupplementDownloader:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def download(self, **_kwargs: object):
            return "not_found", [], []

    monkeypatch.setattr(supplements, "HttpSupplementDownloader", FakeSupplementDownloader)
    harvester = Harvester(
        output_dir=tmp_path,
        download_supplements=True,
        supplements_only=True,
    )
    result = harvester.download("10.1000/example")
    assert result.success is True
    assert result.status == "not_found"
    assert not (result.article_dir / "article.pdf").exists()


def test_pipeline_does_not_call_supplement_downloader_by_default(
    monkeypatch, tmp_path: Path
) -> None:
    from doi_harvester import supplements

    class UnexpectedSupplementDownloader:
        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError("未显式请求时不得初始化补充材料下载器")

    monkeypatch.setattr(
        supplements,
        "HttpSupplementDownloader",
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


def test_body_authorization_gate_does_not_start_supplement_request(monkeypatch, tmp_path):
    worker = Harvester.__new__(Harvester)
    worker.download_supplements = True
    result = DownloadResult(
        doi="10.1000/example", success=False, status="authentication_required", article_dir=tmp_path
    )

    def forbidden_download(*_args, **_kwargs):
        raise AssertionError("正文遇到验证后不应请求 SI")

    monkeypatch.setattr(
        "doi_harvester.supplements.HttpSupplementDownloader.download", forbidden_download
    )
    worker._add_supplements(result)
    assert result.supplement_status == "not_requested"


def test_pipeline_passes_verified_supplement_urls_to_downloader(monkeypatch, tmp_path):
    doi = "10.1002/anie.200701144"
    url = "https://www.wiley-vch.de/contents/jc_2002/2007/z701144_s.pdf"
    calls = {}

    def download(self, *, doi, article_dir, explicit_urls=None):
        calls.update(doi=doi, article_dir=article_dir, explicit_urls=explicit_urls)
        return "downloaded", [], []

    monkeypatch.setattr("doi_harvester.supplements.HttpSupplementDownloader.download", download)
    worker = Harvester(
        output_dir=tmp_path,
        download_supplements=True,
        supplements_only=True,
        supplement_urls={doi: [url]},
    )
    result = DownloadResult(
        doi=doi, success=False, status="pending", article_dir=tmp_path / "paper"
    )

    worker._add_supplements(result)

    assert calls["doi"] == doi
    assert calls["explicit_urls"] == [url]
    assert result.supplement_status == "downloaded"


def test_opened_edge_with_unconfirmed_content_does_not_block_body_cache(tmp_path):
    from types import SimpleNamespace

    from doi_harvester.doi import doi_slug
    from doi_harvester.visible_browser import DisplayResult

    doi = "10.1000/example"
    folder = tmp_path / doi_slug(doi)
    folder.mkdir()
    pdf = folder / "article.pdf"
    pdf.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
    worker = Harvester(output_dir=tmp_path, browser_display="foreground")
    worker.visible_browser = SimpleNamespace(
        show=lambda **_: DisplayResult(
            "visible", "edge-error://edgewebdata/", "article_content_unconfirmed"
        ),
        update=lambda **_: True,
    )
    result = worker.download(doi)
    assert result.success and result.status == "cached"
    assert result.pdf_path == pdf
    assert worker.browser_options["channel"] == "msedge"
