"""下载流程使用的数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class DownloadCandidate:
    """一个可能返回正文 PDF 的合法入口。"""

    url: str
    source: str
    open_access: bool | None = None


@dataclass(slots=True)
class ArticleMetadata:
    """下载所需的最小论文元数据。"""

    doi: str
    title: str = ""
    publisher: str = ""
    landing_url: str = ""
    candidates: list[DownloadCandidate] = field(default_factory=list)


@dataclass(slots=True)
class Attempt:
    """单次候选入口的下载诊断。"""

    source: str
    url: str
    success: bool
    reason: str
    status_code: int | None = None
    content_type: str = ""
    final_url: str = ""
    bytes_written: int = 0


@dataclass(frozen=True, slots=True)
class SupplementArtifact:
    """一份已验证并保存的补充材料。"""

    name: str
    path: str
    url: str
    content_type: str
    bytes_written: int
    sha256: str


@dataclass(slots=True)
class DownloadResult:
    """单篇论文的最终结果。"""

    doi: str
    success: bool
    status: str
    article_dir: Path
    title: str = ""
    publisher: str = ""
    pdf_path: Path | None = None
    source: str = ""
    attempts: list[Attempt] = field(default_factory=list)
    supplement_status: str = "not_requested"
    supplements: list[SupplementArtifact] = field(default_factory=list)
    supplement_attempts: list[Attempt] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """返回可直接写入 JSON 的结构。"""
        payload = asdict(self)
        payload["article_dir"] = str(self.article_dir)
        payload["pdf_path"] = str(self.pdf_path) if self.pdf_path else None
        return payload
