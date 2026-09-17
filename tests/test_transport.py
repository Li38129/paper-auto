from collections.abc import Iterator
from pathlib import Path
from typing import Any

from doi_harvester.transport import HttpPdfTransport


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status_code: int = 200,
        content_type: str = "application/pdf",
    ) -> None:
        self.body = body
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.url = "https://example.test/final"

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        del chunk_size
        yield self.body[:8]
        yield self.body[8:]


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.headers: dict[str, str] = {}

    def get(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        return self.response


def test_http_transport_saves_valid_pdf_atomically(tmp_path: Path) -> None:
    body = b"%PDF-1.7\n" + (b"valid-payload" * 200)
    destination = tmp_path / "article.pdf"
    transport = HttpPdfTransport(session=FakeSession(FakeResponse(body)), minimum_bytes=100)

    outcome = transport.download(
        url="https://example.test/paper.pdf",
        destination=destination,
        referer="https://doi.org/10.1000/example",
    )

    assert outcome.success is True
    assert destination.read_bytes() == body
    assert not destination.with_suffix(".pdf.part").exists()


def test_http_transport_rejects_html_disguised_as_pdf(tmp_path: Path) -> None:
    destination = tmp_path / "article.pdf"
    response = FakeResponse(b"<html>Access denied</html>", content_type="text/html")
    transport = HttpPdfTransport(session=FakeSession(response), minimum_bytes=10)

    outcome = transport.download(
        url="https://example.test/paper.pdf",
        destination=destination,
        referer="https://doi.org/10.1000/example",
    )

    assert outcome.success is False
    assert outcome.reason == "not_pdf"
    assert not destination.exists()
    assert not destination.with_suffix(".pdf.part").exists()
