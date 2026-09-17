"""DOI Harvester 命令行入口。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .browser import BrowserAuthorizer, ProfileInUseError
from .doi import InvalidDoiError, normalize_doi
from .papers import PaperJob, PapersFileError, load_paper_jobs
from .pipeline import Harvester


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="初始化出版社授权会话，或根据 DOI 列表下载期刊正文 PDF。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser(
        "download",
        help="下载一个或多个 DOI；省略子命令时保持向后兼容。",
    )
    download.add_argument("--doi", action="append", default=[], help="一个 DOI；可重复传入。")
    download.add_argument("--doi-file", type=Path, help="UTF-8 DOI 文本文件，每行一个 DOI。")
    download.add_argument(
        "--papers-file",
        type=Path,
        help="literature-search-organizer 生成的 papers.json；按 folder_path 原位保存。",
    )
    download.add_argument(
        "--output-dir", type=Path, default=Path("downloads"), help="下载根目录。"
    )
    download.add_argument(
        "--report-dir",
        type=Path,
        help="可选审查报告目录；未指定时不写入批次报告。",
    )
    download.add_argument("--email", help="Crossref/OpenAlex 礼貌池联系邮箱。")
    download.add_argument("--overwrite", action="store_true", help="覆盖已验证的现有 PDF。")
    download.add_argument(
        "--supplements",
        action="store_true",
        help="在正文完成后发现并保存补充材料；需要可用浏览器会话。",
    )
    download.add_argument(
        "--browser-fallback",
        action="store_true",
        help="HTTP 失败后使用持久化浏览器会话；不会绕过付费墙。",
    )
    download.add_argument(
        "--browser-channel",
        default=None,
        help="Playwright 浏览器通道；Windows 自动选择 Edge 或 Chrome。",
    )
    download.add_argument("--profile-dir", type=Path, help="持久化浏览器配置目录。")
    download.add_argument("--headless", action="store_true", help="以无界面模式运行浏览器兜底。")
    download.add_argument(
        "--interactive-wait",
        type=float,
        default=0.0,
        help="兼容参数；推荐先使用 auth 子命令初始化会话。",
    )
    download.add_argument("--delay", type=float, default=1.0, help="不同 DOI 之间的等待秒数。")
    download.add_argument("--verbose", action="store_true", help="输出调试日志。")

    auth = subparsers.add_parser("auth", help="在专用浏览器配置中初始化出版社授权会话。")
    auth.add_argument("--publisher", choices=["acs"], required=True, help="要初始化的出版社。")
    auth.add_argument("--doi", help="用于验证访问权限的 DOI；不填时使用内置探针 DOI。")
    auth.add_argument("--profile-dir", type=Path, help="专用持久化浏览器配置目录。")
    auth.add_argument("--browser-channel", default=None, help="Chrome/Edge 浏览器通道。")
    auth.add_argument(
        "--cdp",
        action="store_true",
        help="启动普通 Chrome 并通过本地 CDP 连接；浏览器在授权后保持打开。",
    )
    auth.add_argument(
        "--auth-timeout",
        type=float,
        default=600.0,
        help="等待用户完成验证和 SSO 的最长秒数。",
    )
    auth.add_argument("--verbose", action="store_true", help="输出调试日志。")
    return parser


def _load_dois(raw_dois: list[str], doi_file: Path | None) -> list[str]:
    values = list(raw_dois)
    if doi_file:
        if not doi_file.is_file():
            raise ValueError(f"DOI 文件不存在：{doi_file}")
        for line in doi_file.read_text(encoding="utf-8-sig").splitlines():
            cleaned = line.strip()
            if cleaned and not cleaned.startswith("#"):
                values.append(cleaned)

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        doi = normalize_doi(value)
        if doi not in seen:
            seen.add(doi)
            normalized.append(doi)
    if not normalized:
        raise ValueError("至少需要一个 DOI；请使用 --doi 或 --doi-file。")
    return normalized


def _write_batch_report(report_dir: Path, results: list[dict[str, object]]) -> Path:
    path = report_dir / "batch-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.part")
    temporary.write_text(
        json.dumps({"schema_version": 1, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def main(argv: list[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if not arguments or arguments[0] not in {"auth", "download", "-h", "--help"}:
        arguments.insert(0, "download")
    args = build_parser().parse_args(arguments)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    if args.command == "auth":
        try:
            doi = normalize_doi(args.doi) if args.doi else None
        except InvalidDoiError as exc:
            raise SystemExit(str(exc)) from exc
        options: dict[str, object] = {}
        if args.profile_dir is not None:
            options["profile_dir"] = args.profile_dir
        if args.browser_channel is not None:
            options["channel"] = args.browser_channel
        options["cdp"] = args.cdp
        authorizer = BrowserAuthorizer(**options)
        try:
            result = authorizer.authorize(
                publisher=args.publisher,
                doi=doi,
                timeout_seconds=args.auth_timeout,
            )
        except ProfileInUseError as exc:
            print(f"[失败] {exc}")
            return 4
        marker = "成功" if result.success else "未完成"
        print(f"[{marker}] 授权状态：{result.status}；页面：{result.final_url or '未打开'}")
        print(f"浏览器配置：{result.profile_dir}")
        return 0 if result.success else 3

    try:
        if args.papers_file is not None:
            if args.doi or args.doi_file is not None:
                raise ValueError("--papers-file 不能与 --doi 或 --doi-file 同时使用。")
            paper_jobs = load_paper_jobs(args.papers_file)
            tasks: list[tuple[str, PaperJob | None]] = [
                (job.doi, job) for job in paper_jobs
            ]
        else:
            tasks = [(doi, None) for doi in _load_dois(args.doi, args.doi_file)]
    except (ValueError, InvalidDoiError, PapersFileError) as exc:
        raise SystemExit(str(exc)) from exc

    browser_options = {
        "headless": args.headless,
        "interactive_wait_seconds": args.interactive_wait,
    }
    if args.browser_channel is not None:
        browser_options["channel"] = args.browser_channel
    if args.profile_dir is not None:
        browser_options["profile_dir"] = args.profile_dir

    harvester = Harvester(
        output_dir=args.output_dir,
        browser_fallback=args.browser_fallback,
        email=args.email,
        browser_options=browser_options,
        download_supplements=args.supplements,
    )
    serialized: list[dict[str, object]] = []
    for index, (doi, paper_job) in enumerate(tasks):
        if paper_job is None:
            result = harvester.download(doi, overwrite=args.overwrite)
        else:
            result = harvester.download(
                doi,
                overwrite=args.overwrite,
                article_dir=paper_job.folder_path,
            )
        result_payload = result.to_dict()
        if paper_job is not None:
            result_payload.update(
                {
                    "rank": paper_job.rank,
                    "requested_title": paper_job.title,
                    "requested_folder_path": str(paper_job.folder_path),
                }
            )
        serialized.append(result_payload)
        marker = "成功" if result.success else "失败"
        print(f"[{marker}] {doi} -> {result.pdf_path or result.article_dir}")
        if index < len(tasks) - 1 and args.delay > 0:
            time.sleep(args.delay)

    succeeded = sum(bool(item["success"]) for item in serialized)
    if args.report_dir is None:
        print(f"汇总：{succeeded}/{len(serialized)} 成功；未生成审查报告。")
    else:
        report_path = _write_batch_report(args.report_dir, serialized)
        print(f"汇总：{succeeded}/{len(serialized)} 成功；报告：{report_path}")
    return 0 if succeeded == len(serialized) else 2
