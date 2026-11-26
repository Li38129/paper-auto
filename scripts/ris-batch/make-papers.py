"""按 resolved-records 建编号目录并生成下载清单（通用版）。

用途：只为待下载条目创建论文目录（已存在则复用），输出 papers.json；
      可选按 rank 拆成 A/B 两段，供顺序执行。

用法：
  python scripts/ris-batch/make-papers.py --resolved E:\\job\\resolved-records.json \\
      --out E:\\job\\papers.json [--dedup E:\\work\\dedup-check.json] [--split-rank 500]

--dedup 为 scripts/dataset-list-sync.py check --json 的输出；命中 already_listed 的条目不建目录、不下载。
--split-rank N 额外写出 <out>-seg-a.json（rank<N）与 <out>-seg-b.json（rank>=N）。
"""

from __future__ import annotations

import argparse
import json
import runpy
from pathlib import Path

candidate_key = runpy.run_path(str(Path(__file__).resolve().parents[1] / "dataset-list-sync.py"))[
    "candidate_key"
]


def main() -> int:
    parser = argparse.ArgumentParser(description="建目录并生成下载清单")
    parser.add_argument("--resolved", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dedup", type=Path)
    parser.add_argument("--split-rank", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    resolved = json.loads(args.resolved.read_text(encoding="utf-8"))["records"]
    already: set[str] = set()
    if args.dedup:
        payload = json.loads(args.dedup.read_text(encoding="utf-8-sig"))
        already = {str(doi).strip().lower() for doi in payload.get("already_listed", [])}

    created = existing = skipped = 0
    papers: list[dict] = []
    for record in resolved:
        if already and candidate_key(record["doi"]) in already:
            skipped += 1
            continue
        target = Path(record["folder_path"])
        if target.exists():
            existing += 1
        else:
            if not args.dry_run:
                target.mkdir(parents=True, exist_ok=False)
            created += 1
        papers.append(
            {
                "rank": int(record["sequence"]),
                "doi": record["doi"],
                "title": record["title"],
                "folder_path": str(target),
            }
        )

    payload = {"schema_version": 1, "papers": papers}
    if not args.dry_run:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.split_rank:
            for suffix, subset in (
                ("seg-a", [item for item in papers if item["rank"] < args.split_rank]),
                ("seg-b", [item for item in papers if item["rank"] >= args.split_rank]),
            ):
                target = args.out.with_name(f"{args.out.stem}-{suffix}.json")
                target.write_text(
                    json.dumps({"schema_version": 1, "papers": subset}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    print(json.dumps({
        "resolved": len(resolved),
        "papers": len(papers),
        "created": created,
        "existing": existing,
        "already_listed_skipped": skipped,
        "dry_run": args.dry_run,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
