"""提供 Windows 内部长路径操作与阅读器可打开的文件路径。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PureWindowsPath


def display_path(path: Path | str) -> str:
    """对外输出普通路径，扩展前缀仅供内部文件操作。"""
    value = str(path)
    # 兼容旧报告把 JSON 转义后的路径字符串直接写入工作簿的情况。
    if value.startswith("\\\\\\\\?\\\\"):
        value = value.replace("\\\\", "\\")
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def shell_safe_file_path(path: Path, *, reserve: int = 0) -> Path:
    """按完整路径预算缩短文件名，保留目录、扩展名及防冲突摘要。"""
    path = Path(display_path(path))
    absolute = path.absolute()
    def units(value: object) -> int:
        return len(str(value).encode("utf-16-le")) // 2

    if units(absolute) + reserve < 240:
        return path
    digest = hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:10]
    tail = f"-{digest}{path.suffix}"
    budget = 239 - reserve - units(absolute.parent) - 1 - units(tail)
    if budget < 1:
        raise ValueError(f"目录路径过长，请选择更短的保存根目录：{path.parent}")
    stem = path.stem
    while units(stem) > budget:
        stem = stem[:-1]
    return path.with_name(stem.rstrip(" .") + tail)


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
