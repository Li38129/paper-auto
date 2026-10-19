"""验证后备接口的稳定编号、拒绝冲突和SI独立回写。"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/literature-workbook-fallback.py"
spec = importlib.util.spec_from_file_location("workbook_fallback", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WorkbookFallbackTest(unittest.TestCase):
    def setUp(self):
        self.rows = []
        self.payload = {
            "schema_version": 1,
            "topic": "测试",
            "records": [
                {
                    "rank": 1,
                    "doi": "10.1038/s41524-021-00545-1",
                    "title": "论文甲",
                    "year": 2021,
                    "journal": "期刊",
                    "impact_factor": "IF 13.1（2025）",
                }
            ],
        }
        module.upsert(self.rows, self.payload, Path.cwd())

    def test_upsert_keeps_number_and_download(self):
        folder = self.rows[0]["目录名"]
        self.rows[0]["下载成功"] = "是"
        self.payload["records"][0]["title"] = "更新题名"
        resolved = module.upsert(self.rows, self.payload, Path.cwd())
        self.assertEqual(len(self.rows), 1)
        self.assertEqual(resolved[0]["sequence"], 1)
        self.assertEqual(resolved[0]["folder_name"], folder)
        self.assertEqual(self.rows[0]["下载成功"], "是")

    def test_sequence_conflict_is_rejected(self):
        self.payload["records"][0]["sequence"] = 2
        with self.assertRaises(ValueError):
            module.upsert(self.rows, self.payload, Path.cwd())

    def test_si_only_preserves_article(self):
        self.rows[0].update({"下载成功": "是", "下载状态": "cached", "PDF路径": "existing.pdf"})
        module.apply_report(
            self.rows,
            {
                "schema_version": 1,
                "results": [
                    {
                        "doi": self.rows[0]["DOI"],
                        "download_mode": "supplements_only",
                        "status": "downloaded",
                        "supplement_status": "downloaded",
                        "success": True,
                    }
                ],
            },
        )
        self.assertEqual(self.rows[0]["SI是否下载成功"], "是")
        self.assertEqual(self.rows[0]["PDF路径"], "existing.pdf")
        self.assertEqual(self.rows[0]["下载状态"], "cached")

    def test_si_failure_keeps_saved_article_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "article.pdf"
            pdf.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
            module.apply_report(
                self.rows,
                {
                    "schema_version": 1,
                    "results": [
                        {
                            "doi": self.rows[0]["DOI"],
                            "download_mode": "article_and_supplements",
                            "success": False,
                            "status": "failed",
                            "pdf_path": str(pdf),
                            "supplement_status": "unconfirmed",
                        }
                    ],
                },
            )
            self.assertEqual(self.rows[0]["下载成功"], "是")
            self.assertEqual(self.rows[0]["SI是否下载成功"], "否")


if __name__ == "__main__":
    unittest.main()
