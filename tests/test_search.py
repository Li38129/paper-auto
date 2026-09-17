from doi_harvester.search import LiteratureSearchClient


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, url: str, **_kwargs: object) -> FakeResponse:
        self.calls += 1
        if "openalex" in url:
            return FakeResponse(
                {
                    "results": [
                        {
                            "display_name": "Paper A",
                            "doi": "https://doi.org/10.1000/a",
                            "publication_year": 2024,
                            "type": "article",
                            "primary_location": {"source": {"display_name": "Journal"}},
                        }
                    ]
                }
            )
        return FakeResponse(
            {
                "message": {
                    "items": [
                        {"title": ["Paper A"], "DOI": "10.1000/a"},
                        {"title": ["Paper B"], "DOI": "10.1000/b"},
                    ]
                }
            }
        )


def test_search_uses_crossref_to_fill_and_deduplicates() -> None:
    records = LiteratureSearchClient(session=FakeSession()).search("battery", limit=2)

    assert [record["doi"] for record in records] == ["10.1000/a", "10.1000/b"]
