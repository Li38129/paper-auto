from typing import Any

from doi_harvester.metadata import CrossrefMetadataClient, OpenAlexMetadataClient


class FakeResponse:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def get(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        return FakeResponse(self.payload)


def test_crossref_extracts_metadata_and_deduplicates_pdf_candidates() -> None:
    payload = {
        "message": {
            "title": ["A useful paper"],
            "publisher": "Example Publisher",
            "container-title": ["Journal of Examples"],
            "ISSN": ["1234-5678"],
            "published-online": {"date-parts": [[2025, 1, 2]]},
            "URL": "https://doi.org/10.1007/example",
            "link": [
                {"URL": "https://example.test/paper.pdf", "content-type": "application/pdf"},
                {"URL": "https://example.test/paper.pdf", "content-type": "unspecified"},
            ],
        }
    }
    client = CrossrefMetadataClient(session=FakeSession(payload))

    metadata = client.fetch("10.1007/example")

    assert metadata.title == "A useful paper"
    assert metadata.publisher == "Example Publisher"
    assert metadata.journal == "Journal of Examples"
    assert metadata.issns == ("1234-5678",)
    assert metadata.year == 2025
    assert [candidate.url for candidate in metadata.candidates].count(
        "https://example.test/paper.pdf"
    ) == 1
    assert any("link.springer.com/content/pdf" in item.url for item in metadata.candidates)


def test_crossref_adds_acs_direct_candidate() -> None:
    payload = {
        "message": {
            "title": ["ACS paper"],
            "publisher": "American Chemical Society (ACS)",
            "link": [],
        }
    }
    client = CrossrefMetadataClient(session=FakeSession(payload))

    metadata = client.fetch("10.1021/acsami.9b13313")

    assert any(item.url.endswith("/doi/pdf/10.1021/acsami.9b13313") for item in metadata.candidates)


def test_openalex_extracts_repository_pdf() -> None:
    payload = {
        "locations": [
            {"pdf_url": "https://repository.test/paper.pdf"},
            {"pdf_url": None},
            {"pdf_url": "https://repository.test/paper.pdf"},
        ]
    }
    client = OpenAlexMetadataClient(session=FakeSession(payload))

    candidates = client.fetch_candidates("10.1000/example")

    assert [item.url for item in candidates] == ["https://repository.test/paper.pdf"]
