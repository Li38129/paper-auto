"""回归测试阅读器路径预算、缓存副本与对外报告。"""

from pathlib import Path

import pytest

from doi_harvester.filesystem import display_path, native_file_path, shell_safe_file_path
from doi_harvester.models import DownloadResult
from doi_harvester.pipeline import Harvester


def test_report_strips_drive_and_unc_prefixes():
    assert display_path(r"\\?\E:\paper\file.pdf") == r"E:\paper\file.pdf"
    assert display_path(r"\\\\?\\E:\\paper\\file.pdf") == r"E:\paper\file.pdf"
    assert display_path(r"\\?\UNC\server\share\file.pdf") == r"\\server\share\file.pdf"
    result = DownloadResult(
        doi="10.1000/test", success=True, status="cached",
        article_dir=Path(r"\\?\E:\paper"), pdf_path=Path(r"\\?\E:\paper\file.pdf"),
    )
    assert result.to_dict()["pdf_path"] == r"E:\paper\file.pdf"


def test_full_path_budget_preserves_directory_extension_and_uniqueness(tmp_path):
    parent = tmp_path / ("论文" * 30)
    original = parent / ("论文😀" * 60 + ".pdf")
    short = shell_safe_file_path(original, reserve=16)
    assert short.parent == parent
    assert short.suffix == ".pdf"
    assert len(str(short.absolute()).encode("utf-16-le")) // 2 + 16 < 240
    assert short == shell_safe_file_path(original, reserve=16)
    assert short != shell_safe_file_path(original.with_name(original.stem + "x.pdf"), reserve=16)
    assert shell_safe_file_path(tmp_path / "short.pdf") == tmp_path / "short.pdf"


def test_parent_too_long_has_actionable_error(tmp_path):
    with pytest.raises(ValueError, match="更短的保存根目录"):
        shell_safe_file_path(tmp_path / ("a" * 240) / "article.pdf")


def test_long_cached_pdf_is_copied_without_network_or_changing_original(tmp_path):
    folder = tmp_path / ("1_IF未核实_Test_分区未核实_2024_" + "Long title " * 8).rstrip()
    native_file_path(folder).mkdir()
    original = native_file_path(folder / f"{folder.name}.pdf")
    data = b"%PDF-1.7\n" + b"x" * 2048
    original.write_bytes(data)
    harvester = Harvester(output_dir=tmp_path)
    harvester._metadata = lambda doi: pytest.fail("缓存命中不应调用网络")
    result = harvester.download("10.1000/test", article_dir=folder)
    assert result.status == "cached"
    assert result.pdf_path == shell_safe_file_path(original)
    assert result.pdf_path.read_bytes() == original.read_bytes() == data
    assert not result.to_dict()["pdf_path"].startswith("\\\\?\\")
    again = harvester.download("10.1000/test", article_dir=folder)
    assert again.pdf_path == result.pdf_path
