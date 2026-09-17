from pathlib import Path

import pytest

from doi_harvester.doctor import run_doctor


def test_doctor_checks_target_directory_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("DOI_HARVESTER_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("AUTOPAPER_CONFIG_PATH", str(tmp_path / "missing-config.json"))

    result = run_doctor(network=False, target_dir=tmp_path)

    checks = {item["name"]: item for item in result["checks"]}
    assert checks["target_dir"]["status"] == "ok"
    assert checks["publisher_profiles"]["message"].startswith("已加载 21")
