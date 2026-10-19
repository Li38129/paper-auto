"""验证长路径前缀不改变盘符、共享地址和文件名。"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src/doi_harvester/filesystem.py"
spec = importlib.util.spec_from_file_location("filesystem_helpers", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FilesystemTest(unittest.TestCase):
    def test_drive_prefix(self):
        self.assertEqual(
            module.extended_windows_path(r"E:\paper\article.pdf"), "\\\\?\\E:\\paper\\article.pdf"
        )

    def test_unc_prefix_and_idempotence(self):
        value = module.extended_windows_path(r"\\server\share\article.pdf")
        self.assertEqual(value, "\\\\?\\UNC\\server\\share\\article.pdf")
        self.assertEqual(module.extended_windows_path(value), value)

    def test_rejects_relative_path(self):
        with self.assertRaises(ValueError):
            module.extended_windows_path("relative.pdf")

    def test_long_path_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / ("论文" * 60)
            destination = module.native_file_path(folder / (("正文" * 60) + ".pdf"))
            module.native_file_path(folder).mkdir()
            destination.write_bytes(b"%PDF-test")
            self.assertEqual(destination.read_bytes(), b"%PDF-test")
            destination.unlink()
            module.native_file_path(folder).rmdir()


if __name__ == "__main__":
    unittest.main()
