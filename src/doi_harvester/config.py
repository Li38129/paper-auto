"""AutoPaper 本机全局配置与 Windows DPAPI 密钥保护。"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class ConfigError(RuntimeError):
    """全局配置不可读取、不可解密或格式不兼容。"""


class SecretProtector(Protocol):
    """密钥加密器的最小接口，便于隔离 DPAPI 与单元测试。"""

    def protect(self, value: str) -> str: ...

    def unprotect(self, value: str) -> str: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class DpapiSecretProtector:
    """使用当前 Windows 用户作用域的 DPAPI 加密敏感配置。"""

    _flags = 0x1  # 禁止 DPAPI 弹出交互界面

    def protect(self, value: str) -> str:
        if not value:
            return ""
        encrypted = self._crypt(value.encode("utf-8"), decrypt=False)
        return base64.b64encode(encrypted).decode("ascii")

    def unprotect(self, value: str) -> str:
        if not value:
            return ""
        try:
            encrypted = base64.b64decode(value, validate=True)
        except (ValueError, TypeError) as exc:
            raise ConfigError("DPAPI 密文不是有效的 Base64。") from exc
        try:
            return self._crypt(encrypted, decrypt=True).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigError("DPAPI 密文解密后不是 UTF-8。") from exc

    def _crypt(self, payload: bytes, *, decrypt: bool) -> bytes:
        if sys.platform != "win32":
            raise ConfigError("DPAPI 本地密钥存储仅支持 Windows；请改用环境变量。")
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        buffer = ctypes.create_string_buffer(payload)
        input_blob = _DataBlob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        output_blob = _DataBlob()
        if decrypt:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(input_blob),
                None,
                None,
                None,
                None,
                self._flags,
                ctypes.byref(output_blob),
            )
        else:
            ok = crypt32.CryptProtectData(
                ctypes.byref(input_blob),
                None,
                None,
                None,
                None,
                self._flags,
                ctypes.byref(output_blob),
            )
        if not ok:
            code = ctypes.get_last_error()
            operation = "解密" if decrypt else "加密"
            raise ConfigError(f"Windows DPAPI {operation}失败，错误码 {code}。")
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            kernel32.LocalFree(output_blob.pbData)


@dataclass(slots=True)
class ElsevierConfig:
    """Elsevier API 的解密后内存配置。"""

    api_key: str = ""
    inst_token: str = ""
    proxy_url: str = ""


@dataclass(slots=True)
class GlobalConfig:
    """AutoPaper 本机全局配置。"""

    schema_version: int = 1
    elsevier: ElsevierConfig = field(default_factory=ElsevierConfig)


@dataclass(frozen=True, slots=True)
class ElsevierCredentials:
    """一次运行使用的 Elsevier 凭据与网络配置。"""

    api_key: str = ""
    inst_token: str = ""
    proxy_url: str = ""


def default_config_path() -> Path:
    """返回仅属于当前机器和用户的配置路径。"""
    override = os.getenv("AUTOPAPER_CONFIG_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if not local_app_data:
        if sys.platform != "win32":
            return (Path.home() / ".config" / "AutoPaper" / "config.json").resolve()
        local_app_data = str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / "AutoPaper" / "config.json"


class GlobalConfigStore:
    """原子读取与保存全局配置，敏感字段始终以 DPAPI 密文落盘。"""

    def __init__(
        self,
        *,
        path: Path | None = None,
        protector: SecretProtector | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else default_config_path()
        self.protector = protector or DpapiSecretProtector()

    def load(self) -> GlobalConfig:
        if not self.path.exists():
            return GlobalConfig()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"无法读取全局配置：{self.path}") from exc
        if payload.get("schema_version") != 1:
            raise ConfigError("全局配置 schema_version 不受支持。")
        raw_elsevier = payload.get("elsevier")
        if not isinstance(raw_elsevier, dict):
            raise ConfigError("全局配置缺少 elsevier 对象。")
        try:
            api_key = self.protector.unprotect(str(raw_elsevier.get("api_key_dpapi") or ""))
            inst_token = self.protector.unprotect(str(raw_elsevier.get("inst_token_dpapi") or ""))
        except ConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConfigError("Elsevier DPAPI 密文无法解密。") from exc
        return GlobalConfig(
            elsevier=ElsevierConfig(
                api_key=api_key,
                inst_token=inst_token,
                proxy_url=str(raw_elsevier.get("proxy_url") or "").strip(),
            )
        )

    def save(self, config: GlobalConfig) -> None:
        payload = {
            "schema_version": 1,
            "elsevier": {
                "api_key_dpapi": self.protector.protect(config.elsevier.api_key),
                "inst_token_dpapi": self.protector.protect(config.elsevier.inst_token),
                "proxy_url": config.elsevier.proxy_url.strip(),
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.part")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


def load_elsevier_credentials(store: GlobalConfigStore | None = None) -> ElsevierCredentials:
    """按环境变量优先、本机配置兜底的顺序解析 Elsevier 凭据。"""
    config = (store or GlobalConfigStore()).load().elsevier
    return ElsevierCredentials(
        api_key=(
            os.getenv("ELSEVIER_API_KEY", "").strip()
            or os.getenv("ELS_API_KEY", "").strip()
            or config.api_key
        ),
        inst_token=(
            os.getenv("ELSEVIER_INST_TOKEN", "").strip()
            or os.getenv("ELSEVIER_INSTTOKEN", "").strip()
            or os.getenv("ELS_INST_TOKEN", "").strip()
            or os.getenv("ELS_INSTTOKEN", "").strip()
            or config.inst_token
        ),
        proxy_url=(os.getenv("AUTOPAPER_ELSEVIER_PROXY", "").strip() or config.proxy_url),
    )


def mask_secret(value: str) -> str:
    """输出不暴露完整凭据的稳定掩码。"""
    if not value:
        return "(未配置)"
    visible = value[-4:]
    return "*" * max(len(value) - len(visible), 4) + visible
