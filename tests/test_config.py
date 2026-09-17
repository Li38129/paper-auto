import json
import sys
from pathlib import Path

import pytest

from doi_harvester.config import (
    ConfigError,
    ElsevierConfig,
    GlobalConfig,
    GlobalConfigStore,
    load_elsevier_credentials,
    mask_secret,
)


class FakeProtector:
    def protect(self, value: str) -> str:
        return f"encrypted:{value[::-1]}"

    def unprotect(self, value: str) -> str:
        if not value.startswith("encrypted:"):
            raise ConfigError("密文格式无效")
        return value.removeprefix("encrypted:")[::-1]


def test_global_config_saves_secrets_atomically_without_plaintext(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    store = GlobalConfigStore(path=path, protector=FakeProtector())

    store.save(
        GlobalConfig(
            elsevier=ElsevierConfig(
                api_key="secret-key",
                inst_token="secret-token",
                proxy_url="http://127.0.0.1:7890",
            )
        )
    )

    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert "secret-key" not in text
    assert "secret-token" not in text
    assert payload["schema_version"] == 1
    assert payload["elsevier"]["api_key_dpapi"].startswith("encrypted:")
    assert not path.with_suffix(".json.part").exists()
    assert store.load().elsevier.api_key == "secret-key"


def test_global_config_rejects_corrupt_ciphertext(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "elsevier": {"api_key_dpapi": "broken", "inst_token_dpapi": ""},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="密文"):
        GlobalConfigStore(path=path, protector=FakeProtector()).load()


def test_environment_credentials_override_local_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = GlobalConfigStore(path=tmp_path / "config.json", protector=FakeProtector())
    store.save(GlobalConfig(elsevier=ElsevierConfig(api_key="local", inst_token="local-token")))
    monkeypatch.setenv("ELSEVIER_API_KEY", "environment")
    monkeypatch.setenv("ELS_INST_TOKEN", "environment-token")

    credentials = load_elsevier_credentials(store)

    assert credentials.api_key == "environment"
    assert credentials.inst_token == "environment-token"


def test_mask_secret_only_exposes_last_four_characters() -> None:
    assert mask_secret("abcdefghijkl") == "********ijkl"
    assert mask_secret("") == "(未配置)"


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI 仅在 Windows 验证")
def test_windows_dpapi_round_trip_uses_current_user_scope(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    store = GlobalConfigStore(path=path)
    test_value = "-".join(("current", "user", "test", "value"))
    store.save(GlobalConfig(elsevier=ElsevierConfig(api_key=test_value)))

    assert store.load().elsevier.api_key == test_value
    assert test_value not in path.read_text(encoding="utf-8")
