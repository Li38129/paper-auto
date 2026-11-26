"""核对落盘结果并生成失败清单（通用版）。

用途：按回写报告逐目录核对正文 PDF 与 supplements 附件，输出统计与失败清单；
      验收口径：报告成功数必须等于磁盘含 PDF 的目录数，SI 成功数等于含附件的目录数。

用法：
  python scripts/ris-batch/verify-run.py --root E:\\论文根 --report E:\\job\\final\\batch-report.json \\
      [--verify-out E:\\job\\verify-report.json] [--manifest-csv E:\\job\\失败清单.csv]

输出：标准输出统计；可选写核对报告与失败清单 CSV。
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="核对正文与 SI 落盘")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--verify-out", type=Path)
    parser.add_argument("--manifest-csv", type=Path)
    args = parser.parse_args()

    results = json.loads(args.report.read_text(encoding="utf-8"))["results"]
    folders = [path for path in args.root.iterdir() if path.is_dir()]
    with_pdf, without_pdf, with_si, si_files = [], [], [], 0
    bad_pdf = []
    for folder in folders:
        pdfs = [item for item in folder.iterdir() if item.is_file() and item.suffix.lower() == ".pdf"]
        supplements = folder / "supplements"
        if supplements.is_dir():
            files = [item for item in supplements.rglob("*") if item.is_file()]
            if files:
                with_si.append(folder.name)
                si_files += len(files)
        if pdfs:
            with_pdf.append(folder.name)
            for pdf in pdfs:
                try:
                    with pdf.open("rb") as handle:
                        if handle.read(5) != b"%PDF-":
                            bad_pdf.append({"folder": folder.name, "file": pdf.name, "reason": "非 PDF 文件头"})
                except OSError as exc:
                    bad_pdf.append({"folder": folder.name, "file": pdf.name, "reason": str(exc)})
        else:
            without_pdf.append(folder.name)

    failures = [
        {"doi": item["doi"], "title": item["title"], "reason": item["failure_reason"],
         "folder": item["requested_folder_path"]}
        for item in results if not item["success"]
    ]
    success = sum(1 for item in results if item["success"])
    summary = {
        "folders": len(folders),
        "folders_with_pdf": len(with_pdf),
        "folders_without_pdf": len(without_pdf),
        "folders_with_si": len(with_si),
        "si_files": si_files,
        "report_success": success,
        "report_failed": len(failures),
        "pdf_header_problems": len(bad_pdf),
        "consistent_body": success == len(with_pdf),
        "consistent_si": sum(1 for item in results
                             if str(item.get("supplement_status")) in {"downloaded", "cached"}) == len(with_si),
        "failures": failures,
    }
    if args.verify_out:
        args.verify_out.parent.mkdir(parents=True, exist_ok=True)
        args.verify_out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.manifest_csv and failures:
        args.manifest_csv.parent.mkdir(parents=True, exist_ok=True)
        with args.manifest_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["DOI", "题名", "失败原因", "目录"])
            writer.writeheader()
            for item in failures:
                writer.writerow({"DOI": item["doi"], "题名": item["title"],
                                 "失败原因": item["reason"], "目录": item["folder"]})

    print(json.dumps({key: value for key, value in summary.items() if key != "failures"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
