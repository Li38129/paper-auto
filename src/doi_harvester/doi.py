"""DOI 规范化与文件路径工具。"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit


class InvalidDoiError(ValueError):
    """输入无法规范化为 DOI。"""


_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_UNSAFE_PATH_CHARS = re.compile(r"[^0-9a-z._-]+", re.IGNORECASE)


def normalize_doi(raw: str) -> str:
    """把 DOI URL、`doi:` 形式或裸 DOI 统一为小写裸 DOI。"""
    value = unquote((raw or "").strip())
    lowered = value.lower()

    if lowered.startswith("doi:"):
        value = value[4:].strip()
    elif lowered.startswith(
        ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/")
    ):
        value = urlsplit(value).path.lstrip("/")

    value = value.strip().rstrip(".,;)").lower()
    if not _DOI_PATTERN.fullmatch(value):
        raise InvalidDoiError(f"无效 DOI：{raw!r}")
    return value


def doi_slug(doi: str) -> str:
    """生成稳定且兼容 Windows 的 DOI 目录名。"""
    normalized = normalize_doi(doi)
    slug = _UNSAFE_PATH_CHARS.sub("_", normalized).strip("._")
    return slug[:180]
