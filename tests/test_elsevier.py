from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter

from doi_harvester.elsevier import ElsevierApiClient, extract_main_pdf_eids


def make_pdf(path: Path, *, pages: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as handle:
        writer.write(handle)
    payload = path.read_bytes()
    if len(payload) < 10_000:
        payload += b"\n%padding\n" + b"x" * (10_000 - len(payload) + 32)
    return payload


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        text: str = "",
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        url: str = "https://api.elsevier.test/resource",
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.content = body
        self.headers = headers or {}
        self.url = url

    def iter_content(self, chunk_size: int = 64 * 1024):
        del chunk_size
        yield self.content

    def close(self) -> None:
        return None


class FakeSession:
    def __init__(self, responses: list[FakeResponse], calls: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls = calls
        self.trust_env = True
        self.proxies: dict[str, str] = {}

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(
            {
                "url": url,
                "kwargs": kwargs,
                "trust_env": self.trust_env,
                "proxies": dict(self.proxies),
            }
        )
        return self.responses.pop(0)

    def close(self) -> None:
        return None


def test_extract_main_pdf_eids_prefers_main_and_excludes_supplements() -> None:
    xml = """
    <root>
      <attachment type="MAIN" attachment-eid="1-s2.0-ABC-main.pdf" />
      <attachment type="supplement" attachment-eid="1-s2.0-ABC-mmc1.pdf" />
      <object-eid>1-s2.0-SECOND</object-eid>
    </root>
    """

    assert extract_main_pdf_eids(xml) == [
        "1-s2.0-ABC-main.pdf",
        "1-s2.0-SECOND-main.pdf",
    ]


def test_elsevier_client_downloads_object_pdf_direct_first(tmp_path: Path) -> None:
    xml = (
        "<root><body>" + "正文" * 800 + "</body>"
        '<attachment type="MAIN" attachment-eid="1-s2.0-ABC-main.pdf" /></root>'
    )
    responses = [
        FakeResponse(200, text=xml, headers={"X-ELS-Status": "ENTITLED"}),
        FakeResponse(
            200, body=make_pdf(tmp_path / "source.pdf"), headers={"Content-Type": "application/pdf"}
        ),
    ]
    calls: list[dict[str, Any]] = []
    client = ElsevierApiClient(session_factory=lambda: FakeSession(responses, calls))
    destination = tmp_path / "article.pdf"

    result = client.download(
        doi="10.1016/example",
        destination=destination,
        api_key="secret",
        proxy_url="http://proxy.test:8080",
    )

    assert result.success is True
    assert destination.is_file()
    assert result.source == "elsevier_api:object_eid:direct"
    assert all(call["trust_env"] is False for call in calls)
    assert all(call["proxies"] == {} for call in calls)
    assert not destination.with_suffix(".pdf.part").exists()


def test_elsevier_client_uses_proxy_after_direct_not_entitled(tmp_path: Path) -> None:
    responses_by_session = [
        [FakeResponse(403, headers={"X-ELS-Status": "NOT_ENTITLED"})],
        [
            FakeResponse(
                200,
                text='<root><attachment type="MAIN" attachment-eid="1-s2.0-ABC-main.pdf" /></root>',
            )
        ],
        [FakeResponse(200, body=make_pdf(tmp_path / "source.pdf"))],
    ]
    calls: list[dict[str, Any]] = []

    def factory() -> FakeSession:
        return FakeSession(responses_by_session.pop(0), calls)

    result = ElsevierApiClient(session_factory=factory).download(
        doi="10.1016/example",
        destination=tmp_path / "article.pdf",
        api_key="secret",
        proxy_url="http://proxy.test:8080",
    )

    assert result.success is True
    assert result.source.endswith(":configured_proxy")
    assert calls[-1]["proxies"]["https"] == "http://proxy.test:8080"


def test_elsevier_short_xml_is_warning_not_failure(tmp_path: Path) -> None:
    responses = [
        FakeResponse(
            200,
            text='<root><attachment type="MAIN" attachment-eid="1-s2.0-ABC-main.pdf" /></root>',
        ),
        FakeResponse(200, body=make_pdf(tmp_path / "source.pdf")),
    ]
    result = ElsevierApiClient(session_factory=lambda: FakeSession(responses, [])).download(
        doi="10.1016/example",
        destination=tmp_path / "article.pdf",
        api_key="secret",
    )

    assert result.success is True
    assert result.warnings == ["short_fulltext_under_1500"]


def test_elsevier_rejects_one_page_preview(tmp_path: Path) -> None:
    responses = [
        FakeResponse(
            200,
            text='<root><attachment type="MAIN" attachment-eid="1-s2.0-ABC-main.pdf" /></root>',
        ),
        FakeResponse(200, body=make_pdf(tmp_path / "source.pdf", pages=1)),
    ]
    destination = tmp_path / "article.pdf"

    result = ElsevierApiClient(session_factory=lambda: FakeSession(responses, [])).download(
        doi="10.1016/example", destination=destination, api_key="secret"
    )

    assert result.success is False
    assert result.reason == "preview_pdf"
    assert not destination.exists()


@pytest.mark.parametrize("status_code", [404, 429])
def test_elsevier_does_not_change_route_for_terminal_http_status(
    tmp_path: Path, status_code: int
) -> None:
    responses = [FakeResponse(status_code)]
    calls: list[dict[str, Any]] = []

    result = ElsevierApiClient(session_factory=lambda: FakeSession(responses, calls)).download(
        doi="10.1016/example",
        destination=tmp_path / "article.pdf",
        api_key="secret",
        proxy_url="http://proxy.test:8080",
    )

    assert result.success is False
    assert len(calls) == 1
    assert calls[0]["proxies"] == {}


def test_elsevier_retries_server_error_once_without_changing_route(tmp_path: Path) -> None:
    responses = [FakeResponse(503), FakeResponse(503)]
    calls: list[dict[str, Any]] = []

    result = ElsevierApiClient(session_factory=lambda: FakeSession(responses, calls)).download(
        doi="10.1016/example",
        destination=tmp_path / "article.pdf",
        api_key="secret",
        proxy_url="http://proxy.test:8080",
    )

    assert result.reason == "http_503"
    assert len(calls) == 2
    assert all(call["proxies"] == {} for call in calls)
