import base64
import json
from pathlib import Path

from doi_harvester.supplements import (
    BrowserSupplementDownloader,
    _read_cdp_endpoint,
    _supplement_filename,
)


def test_supplement_filename_prefers_content_disposition() -> None:
    name = _supplement_filename(
        url="https://publisher.test/download?id=1",
        disposition="attachment; filename*=UTF-8''supporting%20data.xlsx",
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        index=1,
    )

    assert name == "supporting data.xlsx"


def test_supplement_filename_reads_wiley_file_query() -> None:
    name = _supplement_filename(
        url="https://publisher.test/action/downloadSupplement?file=paper-sup.docx",
        disposition="",
        content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        index=1,
    )

    assert name == "paper-sup.docx"


def test_browser_supplement_fetch_rejects_html(tmp_path: Path) -> None:
    class FakePage:
        def evaluate(self, _script: str, _url: str) -> dict[str, object]:
            return {
                "status": 200,
                "contentType": "text/html; charset=utf-8",
                "disposition": "",
                "finalUrl": "https://publisher.test/login",
                "body": base64.b64encode(b"<html>login</html>").decode("ascii"),
            }

    downloader = BrowserSupplementDownloader(profile_dir=tmp_path)

    result = downloader._fetch(page=FakePage(), url="https://publisher.test/sup")

    assert result is None


def test_browser_supplement_fetch_accepts_docx(tmp_path: Path) -> None:
    body = b"PK" + b"x" * 2048

    class FakePage:
        def evaluate(self, _script: str, _url: str) -> dict[str, object]:
            return {
                "status": 200,
                "contentType": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "disposition": "attachment; filename=support.docx",
                "finalUrl": "https://publisher.test/support.docx",
                "body": base64.b64encode(body).decode("ascii"),
            }

    downloader = BrowserSupplementDownloader(profile_dir=tmp_path)

    result = downloader._fetch(page=FakePage(), url="https://publisher.test/sup")

    assert result is not None
    assert result[0] == body


def test_browser_supplement_downloads_discovered_file(tmp_path: Path) -> None:
    body = b"PK" + b"x" * 2048
    supplement_url = (
        "https://publisher.test/action/downloadSupplement?file=paper-sup.docx"
    )

    class FakePage:
        def goto(self, *_args: object, **_kwargs: object) -> None:
            return None

        def wait_for_timeout(self, _timeout: int) -> None:
            return None

        def evaluate(self, _script: str, *args: object) -> object:
            if not args:
                return [supplement_url, supplement_url]
            return {
                "status": 200,
                "contentType": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "disposition": "attachment; filename=paper-sup.docx",
                "finalUrl": supplement_url,
                "body": base64.b64encode(body).decode("ascii"),
            }

    downloader = BrowserSupplementDownloader(profile_dir=tmp_path)
    article_dir = tmp_path / "paper"

    status, artifacts, attempts = downloader._download_from_page(
        page=FakePage(),
        doi="10.1000/example",
        article_dir=article_dir,
    )

    assert status == "downloaded"
    assert len(artifacts) == 1
    assert len(attempts) == 1 and attempts[0].success is True
    assert (article_dir / "supplements" / "paper-sup.docx").read_bytes() == body


def test_browser_supplement_reports_not_found(tmp_path: Path) -> None:
    class FakePage:
        def goto(self, *_args: object, **_kwargs: object) -> None:
            return None

        def wait_for_timeout(self, _timeout: int) -> None:
            return None

        def evaluate(self, _script: str) -> list[str]:
            return []

    downloader = BrowserSupplementDownloader(profile_dir=tmp_path)

    status, artifacts, attempts = downloader._download_from_page(
        page=FakePage(),
        doi="10.1000/example",
        article_dir=tmp_path / "paper",
    )

    assert status == "not_found"
    assert artifacts == []
    assert attempts == []


def test_browser_supplement_requires_active_session(tmp_path: Path) -> None:
    downloader = BrowserSupplementDownloader(profile_dir=tmp_path)

    status, artifacts, attempts = downloader.download(
        doi="10.1000/example",
        article_dir=tmp_path / "paper",
    )

    assert status == "browser_session_required"
    assert artifacts == []
    assert attempts == []
    assert _read_cdp_endpoint(tmp_path) == ""


def test_read_cdp_endpoint_from_state(tmp_path: Path) -> None:
    (tmp_path / "auth-state.json").write_text(
        json.dumps({"cdp_endpoint": "http://127.0.0.1:9222"}),
        encoding="utf-8",
    )

    assert _read_cdp_endpoint(tmp_path) == "http://127.0.0.1:9222"


def test_browser_supplement_reports_undownloadable_link(tmp_path: Path) -> None:
    supplement_url = "https://publisher.test/action/downloadSupplement?file=sup.pdf"

    class FakePage:
        def goto(self, *_args: object, **_kwargs: object) -> None:
            return None

        def wait_for_timeout(self, _timeout: int) -> None:
            return None

        def evaluate(self, _script: str, *args: object) -> object:
            if not args:
                return [supplement_url]
            return {
                "status": 403,
                "contentType": "text/html",
                "disposition": "",
                "finalUrl": supplement_url,
                "body": base64.b64encode(b"blocked").decode("ascii"),
            }

    downloader = BrowserSupplementDownloader(profile_dir=tmp_path)

    status, artifacts, attempts = downloader._download_from_page(
        page=FakePage(),
        doi="10.1000/example",
        article_dir=tmp_path / "paper",
    )

    assert status == "not_downloadable"
    assert artifacts == []
    assert len(attempts) == 1 and attempts[0].success is False
