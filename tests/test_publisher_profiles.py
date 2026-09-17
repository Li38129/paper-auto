from doi_harvester.publisher_profiles import (
    PUBLISHER_PROFILES,
    infer_publisher_profile,
)


def test_registry_contains_twenty_one_unique_publishers() -> None:
    assert len(PUBLISHER_PROFILES) == 21
    aliases = [alias for profile in PUBLISHER_PROFILES.values() for alias in profile.aliases]
    assert len(aliases) == len(set(aliases))
    assert all(profile.support_level for profile in PUBLISHER_PROFILES.values())
    assert {profile.support_level for profile in PUBLISHER_PROFILES.values()} <= {
        "verified_api",
        "verified_http",
        "verified_browser",
        "configured_only",
    }
    assert all(
        profile.last_verified
        for profile in PUBLISHER_PROFILES.values()
        if profile.support_level != "configured_only"
    )


def test_infer_elsevier_from_doi_publisher_or_domain() -> None:
    assert infer_publisher_profile("10.1016/example").key == "elsevier"
    assert infer_publisher_profile("10.0000/example", publisher="Elsevier BV").key == "elsevier"
    assert (
        infer_publisher_profile(
            "10.0000/example", landing_url="https://www.sciencedirect.com/science/article/pii/x"
        ).key
        == "elsevier"
    )
