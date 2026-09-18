"""配置驱动的出版社识别与下载能力目录。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class PublisherProfile:
    """一个出版社的稳定识别信息与已验证能力。"""

    key: str
    display_name: str
    aliases: tuple[str, ...]
    doi_prefixes: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    landing_template: str = "https://doi.org/{doi}"
    pdf_templates: tuple[str, ...] = ()
    browser_entry: str = ""
    rate_limit_seconds: float = 1.0
    support_level: str = "configured_only"
    last_verified: str = ""


def _profile(
    key: str,
    name: str,
    *,
    aliases: tuple[str, ...],
    prefixes: tuple[str, ...] = (),
    domains: tuple[str, ...] = (),
    landing: str = "https://doi.org/{doi}",
    pdf: tuple[str, ...] = (),
    browser: str = "",
    support: str = "configured_only",
    verified: str = "",
) -> PublisherProfile:
    return PublisherProfile(
        key=key,
        display_name=name,
        aliases=aliases,
        doi_prefixes=prefixes,
        domains=domains,
        landing_template=landing,
        pdf_templates=pdf,
        browser_entry=browser,
        support_level=support,
        last_verified=verified,
    )


_PROFILES = (
    _profile(
        "acs",
        "American Chemical Society",
        aliases=("american-chemical-society",),
        prefixes=("10.1021/",),
        domains=("pubs.acs.org",),
        landing="https://pubs.acs.org/doi/{doi}",
        pdf=("https://pubs.acs.org/doi/pdf/{doi}",),
        browser="https://pubs.acs.org/",
        support="verified_browser",
        verified="2026-09-17",
    ),
    _profile(
        "acm",
        "ACM",
        aliases=("association-for-computing-machinery",),
        prefixes=("10.1145/",),
        domains=("dl.acm.org",),
        browser="https://dl.acm.org/",
    ),
    _profile(
        "aps",
        "American Physical Society",
        aliases=("american-physical-society",),
        prefixes=("10.1103/",),
        domains=("journals.aps.org",),
        browser="https://journals.aps.org/",
    ),
    _profile(
        "annual-reviews",
        "Annual Reviews",
        aliases=("annualreviews",),
        prefixes=("10.1146/",),
        domains=("annualreviews.org",),
        browser="https://www.annualreviews.org/",
    ),
    _profile(
        "frontiers",
        "Frontiers",
        aliases=("frontiers-media",),
        prefixes=("10.3389/",),
        domains=("frontiersin.org",),
        browser="https://www.frontiersin.org/",
    ),
    _profile(
        "wiley",
        "Wiley",
        aliases=("wiley-online-library",),
        prefixes=("10.1002/",),
        domains=("onlinelibrary.wiley.com",),
        browser="https://onlinelibrary.wiley.com/",
    ),
    _profile(
        "elsevier",
        "Elsevier",
        aliases=("sciencedirect",),
        prefixes=("10.1016/",),
        domains=("sciencedirect.com", "elsevier.com"),
        browser="https://www.sciencedirect.com/",
        support="verified_api",
        verified="2026-09-17",
    ),
    _profile(
        "ieee",
        "IEEE",
        aliases=("ieee-xplore",),
        prefixes=("10.1109/",),
        domains=("ieeexplore.ieee.org",),
        browser="https://ieeexplore.ieee.org/",
    ),
    _profile(
        "iop",
        "IOP Publishing",
        aliases=("iopscience",),
        prefixes=("10.1088/",),
        domains=("iopscience.iop.org",),
        browser="https://iopscience.iop.org/",
    ),
    _profile(
        "rsc",
        "Royal Society of Chemistry",
        aliases=("royal-society-of-chemistry",),
        prefixes=("10.1039/",),
        domains=("pubs.rsc.org",),
        browser="https://pubs.rsc.org/",
        support="verified_browser",
        verified="2026-09-18",
    ),
    _profile(
        "springer",
        "Springer Nature",
        aliases=("springer-nature", "nature"),
        prefixes=("10.1007/", "10.1038/"),
        domains=("link.springer.com", "nature.com"),
        landing="https://link.springer.com/article/{doi}",
        pdf=("https://link.springer.com/content/pdf/{doi}.pdf",),
        browser="https://link.springer.com/",
        support="verified_http",
        verified="2026-09-17",
    ),
    _profile(
        "world-scientific",
        "World Scientific",
        aliases=("worldscientific",),
        prefixes=("10.1142/",),
        domains=("worldscientific.com",),
        browser="https://www.worldscientific.com/",
    ),
    _profile(
        "aip",
        "AIP Publishing",
        aliases=("american-institute-of-physics",),
        prefixes=("10.1063/",),
        domains=("pubs.aip.org",),
        browser="https://pubs.aip.org/",
    ),
    _profile(
        "ams",
        "American Meteorological Society",
        aliases=("ametsoc",),
        prefixes=("10.1175/",),
        domains=("journals.ametsoc.org",),
        browser="https://journals.ametsoc.org/",
    ),
    _profile(
        "copernicus",
        "Copernicus Publications",
        aliases=("copernicus-publications",),
        prefixes=("10.5194/",),
        domains=("copernicus.org",),
        browser="https://www.copernicus.org/",
    ),
    _profile(
        "mdpi",
        "MDPI",
        aliases=("mdpi-ag",),
        prefixes=("10.3390/",),
        domains=("mdpi.com",),
        browser="https://www.mdpi.com/",
    ),
    _profile(
        "oxford",
        "Oxford Academic",
        aliases=("oup", "oxford-academic"),
        prefixes=("10.1093/",),
        domains=("academic.oup.com",),
        browser="https://academic.oup.com/",
    ),
    _profile(
        "plos",
        "PLOS",
        aliases=("public-library-of-science",),
        prefixes=("10.1371/",),
        domains=("journals.plos.org",),
        browser="https://journals.plos.org/",
    ),
    _profile(
        "pnas",
        "PNAS",
        aliases=("proceedings-national-academy-sciences",),
        prefixes=("10.1073/",),
        domains=("pnas.org",),
        browser="https://www.pnas.org/",
    ),
    _profile(
        "royal-society",
        "Royal Society Publishing",
        aliases=("royalsocietypublishing",),
        prefixes=("10.1098/",),
        domains=("royalsocietypublishing.org",),
        browser="https://royalsocietypublishing.org/",
    ),
    _profile(
        "science",
        "Science / AAAS",
        aliases=("aaas",),
        prefixes=("10.1126/",),
        domains=("science.org",),
        browser="https://www.science.org/",
    ),
)

PUBLISHER_PROFILES = {profile.key: profile for profile in _PROFILES}


def infer_publisher_profile(
    doi: str,
    *,
    publisher: str = "",
    landing_url: str = "",
) -> PublisherProfile | None:
    """按 DOI 前缀、出版社名称和落地域名识别出版社。"""
    normalized_doi = doi.strip().lower()
    publisher_text = publisher.strip().lower()
    hostname = (urlparse(landing_url).hostname or "").lower()
    for profile in PUBLISHER_PROFILES.values():
        if any(normalized_doi.startswith(prefix) for prefix in profile.doi_prefixes):
            return profile
        names = (profile.key, profile.display_name.lower(), *profile.aliases)
        if publisher_text and any(name in publisher_text for name in names):
            return profile
        if hostname and any(
            hostname == domain or hostname.endswith(f".{domain}") for domain in profile.domains
        ):
            return profile
    return None
