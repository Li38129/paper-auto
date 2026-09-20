from datetime import UTC, datetime, timedelta
from pathlib import Path

from doi_harvester.access_policy import AccessPolicyStore, AccessRule
from doi_harvester.models import ArticleMetadata, DownloadCandidate
from doi_harvester.pipeline import Harvester


def test_access_rule_matches_issn_and_year(tmp_path: Path) -> None:
    store = AccessPolicyStore(tmp_path / "access.json", environment="campus")
    store.save_rule(
        AccessRule(
            journal="Journal of Materials Chemistry A",
            issns=["2050-7488"],
            year_from=2020,
            source="user_confirmed",
        )
    )

    matched = store.match(
        ArticleMetadata(
            doi="10.1039/example",
            journal="Different spelling",
            issns=("20507488",),
            year=2024,
        )
    )
    outside_range = store.match(
        ArticleMetadata(
            doi="10.1039/old",
            journal="Journal of Materials Chemistry A",
            issns=("2050-7488",),
            year=2019,
        )
    )

    assert matched is not None
    assert outside_range is None


def test_expired_automatic_rule_is_not_applied(tmp_path: Path) -> None:
    store = AccessPolicyStore(tmp_path / "access.json", environment="campus")
    store.save_rule(
        AccessRule(
            journal="Example Journal",
            source="automatic_probe",
            expires_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
        )
    )

    assert store.match(ArticleMetadata(doi="10.1000/example", journal="Example Journal")) is None


def test_pipeline_tries_oa_before_skipping_paid_route(tmp_path: Path) -> None:
    class FakeCrossref:
        def fetch(self, doi: str) -> ArticleMetadata:
            return ArticleMetadata(
                doi=doi,
                journal="Blocked Journal",
                candidates=[DownloadCandidate("https://publisher.test/paper.pdf", "publisher")],
            )

    class FakeOpenAlex:
        def fetch_candidates(self, _doi: str) -> list[DownloadCandidate]:
            return []

    class FakeTransport:
        def download(self, **_kwargs: object) -> object:
            raise AssertionError("命中期刊规则后不应访问付费入口")

    store = AccessPolicyStore(tmp_path / "access.json", environment="campus")
    store.save_rule(AccessRule(journal="Blocked Journal"))
    harvester = Harvester(
        output_dir=tmp_path,
        crossref=FakeCrossref(),
        openalex=FakeOpenAlex(),
        transport=FakeTransport(),
        access_store=store,
    )

    result = harvester.download("10.1000/example")

    assert result.success is False
    assert result.status == "policy_skipped"
    assert result.reason == "access_policy_skip_paid"
