"""按本机机构访问环境保存期刊付费入口策略。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import ArticleMetadata

POLICY_SKIP_PAID_KEEP_OA = "skip_paid_keep_oa"


def _now() -> datetime:
    return datetime.now(UTC)


def normalize_journal(value: str) -> str:
    """生成不受标点、大小写和多余空格影响的期刊键。"""
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def normalize_issn(value: str) -> str:
    """规范化 ISSN，保留末位 X。"""
    compact = re.sub(r"[^0-9xX]", "", value).upper()
    return compact if len(compact) == 8 else ""


def default_access_policy_path() -> Path:
    """返回当前 Windows 用户的本机访问策略文件。"""
    override = os.getenv("AUTOPAPER_ACCESS_POLICY_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "AutoPaper" / "access-policies.json"
    return Path.home() / ".config" / "AutoPaper" / "access-policies.json"


def default_access_environment() -> str:
    """返回本机当前使用的机构访问环境名称。"""
    return os.getenv("AUTOPAPER_ACCESS_ENVIRONMENT", "default").strip() or "default"


@dataclass(slots=True)
class AccessRule:
    """一条可审计的期刊付费入口策略。"""

    journal: str
    policy: str = POLICY_SKIP_PAID_KEEP_OA
    issns: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    source: str = "user_confirmed"
    evidence: str = ""
    sample_dois: list[str] = field(default_factory=list)
    checked_at: str = field(default_factory=lambda: _now().isoformat())
    expires_at: str | None = None

    def is_expired(self, *, at: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        try:
            expires = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return True
        return expires <= (at or _now())

    def matches(self, metadata: ArticleMetadata) -> bool:
        if self.is_expired():
            return False
        rule_issns = {normalize_issn(value) for value in self.issns if normalize_issn(value)}
        article_issns = {
            normalize_issn(value) for value in metadata.issns if normalize_issn(value)
        }
        if rule_issns and article_issns:
            identity_matches = bool(rule_issns & article_issns)
        else:
            names = {normalize_journal(self.journal)} | {
                normalize_journal(alias) for alias in self.aliases
            }
            identity_matches = bool(normalize_journal(metadata.journal) in names)
        if not identity_matches:
            return False
        if metadata.year is not None:
            if self.year_from is not None and metadata.year < self.year_from:
                return False
            if self.year_to is not None and metadata.year > self.year_to:
                return False
        return True


class AccessPolicyStore:
    """原子维护按访问环境隔离的期刊规则。"""

    def __init__(self, path: Path | None = None, *, environment: str | None = None) -> None:
        self.path = Path(path) if path is not None else default_access_policy_path()
        self.environment = environment or default_access_environment()

    def _load_payload(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema_version": 1, "environments": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取访问策略：{self.path}") from exc
        if payload.get("schema_version") != 1 or not isinstance(payload.get("environments"), dict):
            raise ValueError("访问策略文件格式不受支持。")
        return payload

    def list_rules(self, *, include_expired: bool = True) -> list[AccessRule]:
        payload = self._load_payload()
        environments = payload["environments"]
        raw_rules = environments.get(self.environment, {}).get("rules", [])
        rules = [AccessRule(**raw) for raw in raw_rules if isinstance(raw, dict)]
        return rules if include_expired else [rule for rule in rules if not rule.is_expired()]

    def save_rule(self, rule: AccessRule) -> None:
        if rule.policy != POLICY_SKIP_PAID_KEEP_OA:
            raise ValueError(f"不支持的访问策略：{rule.policy}")
        rule.issns = list(dict.fromkeys(filter(None, map(normalize_issn, rule.issns))))
        payload = self._load_payload()
        environments = payload["environments"]
        environment = environments.setdefault(self.environment, {"rules": []})
        rules = [AccessRule(**raw) for raw in environment.get("rules", [])]
        key_names = {normalize_journal(rule.journal)} | {
            normalize_journal(alias) for alias in rule.aliases
        }
        key_issns = set(rule.issns)

        def same_identity(existing: AccessRule) -> bool:
            existing_names = {normalize_journal(existing.journal)} | {
                normalize_journal(alias) for alias in existing.aliases
            }
            existing_issns = set(map(normalize_issn, existing.issns))
            return bool(key_issns & existing_issns) or bool(key_names & existing_names)

        rules = [existing for existing in rules if not same_identity(existing)]
        rules.append(rule)
        environment["rules"] = [asdict(item) for item in rules]
        self._save_payload(payload)

    def remove(self, *, journal: str = "", issn: str = "") -> int:
        normalized_name = normalize_journal(journal)
        normalized_issn = normalize_issn(issn)
        payload = self._load_payload()
        environment = payload["environments"].setdefault(self.environment, {"rules": []})
        rules = [AccessRule(**raw) for raw in environment.get("rules", [])]

        def selected(rule: AccessRule) -> bool:
            names = {normalize_journal(rule.journal)} | {
                normalize_journal(alias) for alias in rule.aliases
            }
            issns = set(map(normalize_issn, rule.issns))
            return bool(normalized_name and normalized_name in names) or bool(
                normalized_issn and normalized_issn in issns
            )

        kept = [rule for rule in rules if not selected(rule)]
        removed = len(rules) - len(kept)
        if removed:
            environment["rules"] = [asdict(item) for item in kept]
            self._save_payload(payload)
        return removed

    def match(self, metadata: ArticleMetadata) -> AccessRule | None:
        for rule in self.list_rules(include_expired=False):
            if rule.matches(metadata):
                return rule
        return None

    @staticmethod
    def automatic_expiry(*, days: int = 30) -> str:
        return (_now() + timedelta(days=days)).isoformat()

    def _save_payload(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.part")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
