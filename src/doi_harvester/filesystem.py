"""提供Windows长路径文件操作兼容，不改变用户目录和文件名。"""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath


def extended_windows_path(value: str) -> str:
    """为绝对Windows路径添加扩展前缀，已有前缀保持不变。"""
    if value.startswith("\\\\?\\"):
        return value
    if not PureWindowsPath(value).is_absolute():
        raise ValueError("Windows扩展路径必须是绝对路径")
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def native_file_path(path: Path, *, reserve: int = 0) -> Path:
    """仅Windows内部文件操作使用扩展路径，其他系统保持原路径。"""
    resolved = str(path.resolve())
    if os.name == "nt" and len(resolved) + reserve >= 240:
        return Path(extended_windows_path(resolved))
    return path
