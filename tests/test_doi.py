import pytest

from doi_harvester.doi import InvalidDoiError, doi_slug, normalize_doi


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://doi.org/10.1021/ACS.CHEMMATER.9B01639",
            "10.1021/acs.chemmater.9b01639",
        ),
        ("doi:10.1021/acsami.9b13313 ", "10.1021/acsami.9b13313"),
        (
            "https://dx.doi.org/10.1007/s10853-013-7226-8?tracking=1",
            "10.1007/s10853-013-7226-8",
        ),
    ],
)
def test_normalize_doi_accepts_common_forms(raw: str, expected: str) -> None:
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize("raw", ["", "not-a-doi", "11.1000/example", "10.12/x"])
def test_normalize_doi_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(InvalidDoiError):
        normalize_doi(raw)


def test_doi_slug_is_windows_safe_and_stable() -> None:
    assert doi_slug("10.1007/s10853-013-7226-8") == "10.1007_s10853-013-7226-8"
