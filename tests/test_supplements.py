import base64
import json
from pathlib import Path

import requests

from doi_harvester.supplements import (
    HttpSupplementDownloader,
    _read_cdp_endpoint,
    _supplement_filename,
    _SupplementLinks,
    _valid_payload,
)


def test_supplement_filename_prefers_content_disposition() -> None:
    name = _supplement_filename(
        url="https://publisher.test/download?id=1",
        disposition="attachment; filename*=UTF-8''supporting%20data.xlsx",
        content_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        index=1,
    )

    assert name == "supporting data.xlsx"


def test_supplement_filename_reads_wiley_file_query() -> None:
    name = _supplement_filename(
        url="https://publisher.test/action/downloadSupplement?file=paper-sup.docx",
        disposition="",
        content_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        index=1,
    )

    assert name == "paper-sup.docx"


def test_read_cdp_endpoint_from_state(tmp_path: Path) -> None:
    (tmp_path / "auth-state.json").write_text(
        json.dumps({"cdp_endpoint": "http://127.0.0.1:9222"}),
        encoding="utf-8",
    )

    assert _read_cdp_endpoint(tmp_path) == "http://127.0.0.1:9222"


def test_wiley_supplement_links_include_non_pdf_and_deduplicate() -> None:
    parser = _SupplementLinks("https://advanced.onlinelibrary.wiley.com/doi/10.1002/advs.76317")
    link = (
        "https://advanced.onlinelibrary.wiley.com/action/downloadSupplement"
        "?doi=10.1002%2Fadvs.76317&file=advs76317-sup-0001-SuppMat.docx"
    )
    parser.feed(f'<a href="{link}">Supporting Information</a>')
    assert parser.urls == [link]


def test_figshare_article_page_without_si_label_is_not_an_attachment() -> None:
    parser = _SupplementLinks("https://pubs.acs.org/doi/10.1021/example")
    parser.feed(
        '<a href="https://figshare.com/articles/journal_contribution/Research_Data/12345">'
        "Research data</a>"
    )
    assert parser.urls == []


def test_supplement_payload_rejects_html_and_invalid_pdf(tmp_path: Path) -> None:
    payload = tmp_path / "part"
    payload.write_bytes(b"<html>login</html>")
    assert not _valid_payload(payload, "application/octet-stream", "support.docx")
    payload.write_bytes(b"not a pdf")
    assert not _valid_payload(payload, "application/pdf", "support.pdf")


def test_http_supplement_requires_authorization_on_challenge(tmp_path: Path, monkeypatch) -> None:
    class FakeResponse:
        status_code = 403
        text = "<html>Just a moment...</html>"

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("doi_harvester.supplements.requests.Session", FakeSession)
    downloader = HttpSupplementDownloader(profile_dir=tmp_path / "profile")
    status, artifacts, attempts = downloader.download(
        doi="10.1002/advs.76317", article_dir=tmp_path
    )
    assert status == "challenge_required"
    assert artifacts == []
    assert attempts[0].reason == "challenge_required"


def test_http_supplement_keeps_distinct_files_with_same_name(tmp_path: Path) -> None:
    class FakeResponse:
        status_code = 200
        headers = {
            "content-type": "text/csv",
            "content-disposition": 'attachment; filename="data.csv"',
        }

        def __init__(self, url: str, body: bytes) -> None:
            self.url = url
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size: int):
            yield self.body

    class FakeSession:
        def get(self, url: str, **_kwargs):
            return FakeResponse(url, b"a,b\n1,2" if url.endswith("one") else b"a,b\n3,4")

    downloader = HttpSupplementDownloader()
    urls = ["https://example.test/one", "https://example.test/two"]
    status, artifacts, _ = downloader._download_urls(FakeSession(), urls, tmp_path)
    assert status == "downloaded"
    assert len({item.name for item in artifacts}) == 2
    assert all(Path(item.path).is_file() for item in artifacts)
    status, artifacts, _ = downloader._download_urls(FakeSession(), urls, tmp_path)
    assert status == "cached"
    assert len(artifacts) == 2


def test_http_supplement_reports_partial_success(tmp_path: Path) -> None:
    class FakeResponse:
        headers = {"content-type": "text/csv", "content-disposition": ""}

        def __init__(self, url: str) -> None:
            self.url = url
            self.status_code = 500 if url.endswith("bad.csv") else 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError("server failure")

        def iter_content(self, chunk_size: int):
            yield b"a,b\n1,2"

    class FakeSession:
        def get(self, url: str, **_kwargs):
            return FakeResponse(url)

    downloader = HttpSupplementDownloader()
    status, artifacts, attempts = downloader._download_urls(
        FakeSession(), ["https://example.test/good.csv", "https://example.test/bad.csv"], tmp_path
    )
    assert status == "partial"
    assert len(artifacts) == 1
    assert [item.success for item in attempts] == [True, False]


class StreamResponse:
    def __init__(self, url, status=200, body=b"a,b\n1,2"):
        self.url, self.status_code, self.body = url, status, body
        self.headers = {"content-type": "text/csv"}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def iter_content(self, chunk_size):
        yield self.body


class StreamPage:
    def __init__(self, body=b"a,b\n1,2", fail=False):
        self.body, self.fail = body, fail
        self.calls = 0
        self.sent = False

    def evaluate(self, script, *args):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Failed to fetch")
        if args:
            self.sent = False
            return {"status": 200, "contentType": "text/csv", "finalUrl": args[0]}
        if "delete" in script:
            return None
        if self.sent:
            return None
        self.sent = True
        return base64.b64encode(self.body).decode()


def test_discovered_links_use_http_before_browser_and_reuse_cache(tmp_path):
    from types import SimpleNamespace

    page = StreamPage(fail=True)
    session = SimpleNamespace(get=lambda url, **_: StreamResponse(url))
    downloader = HttpSupplementDownloader()
    url = "https://cdn.test/si.csv"
    status, files, attempts = downloader._download_discovered_urls(session, page, [url], tmp_path)
    assert status == "downloaded" and len(files) == 1
    assert attempts[0].source == "supplement:http" and page.calls == 0
    status, files, _ = downloader._download_discovered_urls(session, page, [url], tmp_path)
    assert status == "cached" and page.calls == 0


def test_http_failure_browser_success_is_not_partial(tmp_path):
    import csv
    from dataclasses import asdict
    from types import SimpleNamespace

    from doi_harvester.job_runner import _write_supplement_results_csv

    session = SimpleNamespace(get=lambda url, **_: StreamResponse(url, status=500))
    status, files, attempts = HttpSupplementDownloader()._download_discovered_urls(
        session, StreamPage(), ["https://cdn.test/si.csv"], tmp_path
    )
    assert status == "downloaded"
    assert [a.success for a in attempts] == [False, True]
    destination = tmp_path / "result.csv"
    _write_supplement_results_csv(
        [
            {
                "rank": 1,
                "doi": "10.1000/example",
                "status": "downloaded",
                "supplement_status": status,
                "supplements": [asdict(f) for f in files],
                "supplement_attempts": [asdict(a) for a in attempts],
            }
        ],
        destination,
    )
    with destination.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["附件结果"] == "downloaded"
    assert rows[0]["失败原因"] == ""


def test_discovered_links_report_only_final_unresolved_failure(tmp_path):
    from types import SimpleNamespace

    session = SimpleNamespace(
        get=lambda url, **_: StreamResponse(url, status=500 if "bad" in url else 200)
    )
    status, files, attempts = HttpSupplementDownloader()._download_discovered_urls(
        session,
        StreamPage(fail=True),
        ["https://cdn.test/good.csv", "https://cdn.test/bad.csv"],
        tmp_path,
    )
    assert status == "partial" and len(files) == 1
    assert [a.success for a in attempts] == [True, False, False]
    assert "Failed to fetch" in attempts[-1].reason
    assert "CORS" not in attempts[-1].reason


def test_browser_stream_rejects_html_and_preserves_existing_file(tmp_path):
    downloader = HttpSupplementDownloader()
    url = "https://cdn.test/si.csv"
    status, files, _ = downloader._download_browser_urls(StreamPage(), [url], tmp_path)
    original = Path(files[0].path).read_bytes()
    status, files, _ = downloader._download_browser_urls(
        StreamPage(b"<html>Login</html>"), [url], tmp_path
    )
    assert status == "not_downloadable" and not files
    assert (tmp_path / "supplements" / "si.csv").read_bytes() == original
    assert not list(tmp_path.rglob("*.part"))
    status, files, _ = downloader._download_browser_urls(StreamPage(b"a,b\n3,4"), [url], tmp_path)
    assert status == "downloaded" and files[0].name != "si.csv"


def test_browser_stream_accepts_office_archive(tmp_path):
    import io
    import zipfile

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")

    class OfficePage(StreamPage):
        def evaluate(self, script, *args):
            result = super().evaluate(script, *args)
            if args:
                result["contentType"] = (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
            return result

    status, files, _ = HttpSupplementDownloader()._download_browser_urls(
        OfficePage(payload.getvalue()), ["https://cdn.test/si.docx"], tmp_path
    )
    assert status == "downloaded" and files[0].name == "si.docx"
    assert zipfile.is_zipfile(files[0].path)


def test_browser_attachment_authorization_is_not_generic_failure(tmp_path):
    class AuthPage:
        def evaluate(self, *_):
            return {"status": 403}

    status, files, attempts = HttpSupplementDownloader()._download_browser_urls(
        AuthPage(), ["https://cdn.test/si.docx"], tmp_path
    )
    assert status == "challenge_required" and not files
    assert attempts[0].status_code == 403


def test_browser_discovery_reuses_verified_page_and_downloads_via_http(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace

    url = "https://pubs.acs.org/doi/10.1021/example"

    class Page:
        def __init__(self):
            self.url = url

        def evaluate(self, script, *args):
            assert "fetch(" not in script
            return ["10.1021/example"]

        def goto(self, *_args, **_kwargs):
            raise AssertionError("已核验的工作页不应重新导航")

        def content(self):
            return (
                '<a href="https://cdn.test/supporting-information.csv">Supporting Information</a>'
            )

    page = Page()
    browser = SimpleNamespace(contexts=[object()])

    class Playwright:
        def __enter__(self):
            return SimpleNamespace(
                chromium=SimpleNamespace(connect_over_cdp=lambda *_args, **_kwargs: browser)
            )

        def __exit__(self, *_):
            pass

    monkeypatch.setitem(
        sys.modules, "playwright.sync_api", SimpleNamespace(sync_playwright=Playwright)
    )
    monkeypatch.setattr(
        "doi_harvester.supplements._read_cdp_endpoint", lambda _: "http://localhost:9222"
    )
    monkeypatch.setattr("doi_harvester.visible_browser.work_page", lambda *_: page)
    monkeypatch.setattr("doi_harvester.browser.classify_page", lambda _: "ready")
    session = SimpleNamespace(get=lambda target, **_: StreamResponse(target))
    status, files, attempts = HttpSupplementDownloader(profile_dir=tmp_path)._browser_or_auth(
        doi="10.1021/example",
        article_dir=tmp_path,
        session=session,
        page_url="https://doi.org/10.1021/example",
    )
    assert status == "downloaded" and len(files) == 1
    assert attempts[0].source == "supplement:http"


def test_browser_gate_stops_remaining_attachment_requests(tmp_path):
    class Page(StreamPage):
        def evaluate(self, *_args):
            self.calls += 1
            return {"status": 403}

    page = Page()
    status, files, _ = HttpSupplementDownloader()._download_browser_urls(
        page, ["https://cdn.test/one.docx", "https://cdn.test/two.docx"], tmp_path
    )
    assert status == "challenge_required" and not files
    assert page.calls == 1


def test_http_gate_preserves_files_and_stops_remaining_requests(tmp_path):
    from types import SimpleNamespace

    calls = []

    def get(url, **_):
        calls.append(url)
        return StreamResponse(url, status=403 if "gate" in url else 200)

    urls = [
        "https://cdn.test/good.csv",
        "https://cdn.test/gate.csv",
        "https://cdn.test/untouched.csv",
    ]
    status, files, _ = HttpSupplementDownloader()._download_urls(
        SimpleNamespace(get=get), urls, tmp_path
    )
    assert status == "challenge_required" and len(files) == 1
    assert calls == urls[:2]
    assert Path(files[0].path).read_bytes() == b"a,b\n1,2"
