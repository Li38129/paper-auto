"""测试公共夹具。"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_desktop_assist(monkeypatch: pytest.MonkeyPatch) -> None:
    """单元测试默认关闭桌面辅助，避免真的拉起 windows-mcp 进程。"""
    monkeypatch.setenv("DOI_HARVESTER_DESKTOP_ASSIST", "off")