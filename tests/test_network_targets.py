import os
from pathlib import Path

import pytest

from doi_harvester.pipeline import Harvester

TARGET_DOIS = [
    "10.1021/acs.chemmater.9b01639",
    "10.1021/acsami.9b13313",
    "10.1007/s10853-013-7226-8",
]


@pytest.mark.network
@pytest.mark.skipif(
    os.getenv("DOI_HARVESTER_NETWORK_TESTS") != "1",
    reason="设置 DOI_HARVESTER_NETWORK_TESTS=1 后运行真实下载验收",
)
@pytest.mark.parametrize("doi", TARGET_DOIS)
def test_download_acceptance_targets(doi: str, tmp_path: Path) -> None:
    result = Harvester(output_dir=tmp_path, browser_fallback=False).download(doi)

    assert result.success, result.to_dict()
    assert result.pdf_path is not None
    assert result.pdf_path.read_bytes().startswith(b"%PDF-")
