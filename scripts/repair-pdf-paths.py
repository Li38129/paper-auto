"""检查已有 PDF，为超长路径创建兼容副本；不移动或覆盖原件。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from doi_harvester.filesystem import display_path, native_file_path, shell_safe_file_path


def digest(path: Path) -> str:
    with native_file_path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def repair(root: Path, *, apply: bool = False) -> list[dict]:
    """先验证内容，再以独占方式复制，逐篇保留可审计结果。"""
    results = []
    for original in sorted(root.rglob("*.pdf")):
        item = {"original": display_path(original), "status": "failed"}
        results.append(item)
        try:
            with native_file_path(original).open("rb") as stream:
                item["pages"] = len(PdfReader(stream).pages)
            destination = shell_safe_file_path(original)
            item["pdf_path"] = display_path(destination)
            if destination == original:
                item["status"] = "unchanged"
                continue
            if not apply:
                item["status"] = "planned"
                continue
            original_hash = digest(original)
            if destination.exists():
                if digest(destination) != original_hash:
                    raise ValueError("兼容文件已存在且内容不同，保留两者并停止该篇修复")
                item["status"] = "existing"
            else:
                with native_file_path(original).open("rb") as source, destination.open("xb") as out:
                    shutil.copyfileobj(source, out)
                if digest(destination) != original_hash:
                    raise ValueError("副本校验失败，保留文件供检查")
                item["status"] = "copied"
            item["sha256"] = original_hash
        except Exception as error:
            item["status"] = "failed"
            item["error"] = str(error)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="实际创建副本，默认仅检查")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if not root.is_dir():
        parser.error("根路径必须是目录")
    if args.report.exists():
        parser.error("报告已存在，请使用新的报告路径")
    results = repair(root, apply=args.apply)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({"schema_version": 1, "results": results},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
    counts = {status: sum(item["status"] == status for item in results)
              for status in {item["status"] for item in results}}
    print(json.dumps(counts, ensure_ascii=False))
    return int(any(item["status"] == "failed" for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
