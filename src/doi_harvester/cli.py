"""DOI Harvester 命令行入口。"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from .access_policy import (
    POLICY_SKIP_PAID_KEEP_OA,
    AccessPolicyStore,
    AccessRule,
)
from .broker import BrokerManager, default_runtime_dir
from .browser import BrowserAuthorizer, ProfileInUseError
from .config import (
    ConfigError,
    GlobalConfig,
    GlobalConfigStore,
    load_elsevier_credentials,
    mask_secret,
)
from .doctor import run_doctor
from .doi import InvalidDoiError, doi_slug, normalize_doi
from .elsevier import ElsevierApiClient
from .job_runner import run_broker
from .job_store import JobStore
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
    download.add_argument("--output-dir", type=Path, default=Path("downloads"), help="下载根目录。")
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
        help="兼容参数；大于零时等价于 pause 策略并覆盖挑战等待秒数。",
    )
    download.add_argument(
        "--challenge-policy",
        choices=["pause", "skip", "fail-fast"],
        help="验证页处理策略；前台默认 pause，后台或无头模式默认 skip。",
    )
    download.add_argument(
        "--challenge-timeout",
        type=float,
        default=600.0,
        help="pause 策略等待用户完成验证或登录的最长秒数。",
    )
    download.add_argument("--delay", type=float, default=1.5, help="不同 DOI 之间的等待秒数。")
    download.add_argument("--verbose", action="store_true", help="输出调试日志。")
    download.add_argument(
        "--detach", action="store_true", help="创建可恢复任务并交给后台 Broker 串行处理。"
    )
    download.add_argument("--workbook", type=Path, help="后台任务完成批次后回写的 Excel 路径。")
    download.add_argument("--node-path", type=Path, help="工作簿脚本使用的 Node.js。")
    download.add_argument("--node-modules", type=Path, help="工作簿脚本依赖目录。")
    download.add_argument(
        "--batch-size", type=int, default=100, help="后台任务每次检查点的条目数，最大 100。"
    )
    download.add_argument(
        "--access-environment",
        default=None,
        help="本机机构访问策略环境；默认读取 AUTOPAPER_ACCESS_ENVIRONMENT。",
    )

    auth = subparsers.add_parser("auth", help="在专用浏览器配置中初始化出版社授权会话。")
    auth.add_argument(
        "--publisher",
        choices=["acs", "elsevier", "rsc"],
        required=True,
        help="要初始化的出版社。",
    )
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

    elsevier_setup = subparsers.add_parser(
        "elsevier-setup",
        help="配置本机全局 Elsevier API Key，并验证 XML/object-EID 下载链。",
    )
    elsevier_setup.add_argument(
        "--set-key", action="store_true", help="以隐藏输入录入并使用 DPAPI 保存 API Key。"
    )
    elsevier_setup.add_argument(
        "--set-inst-token",
        action="store_true",
        help="以隐藏输入录入可选的 Elsevier institutional token。",
    )
    elsevier_setup.add_argument("--proxy-url", help="direct 失败后的可选 HTTP(S) 代理。")
    elsevier_setup.add_argument("--clear-key", action="store_true", help="清除本地 API Key。")
    elsevier_setup.add_argument(
        "--clear-inst-token", action="store_true", help="清除本地 institutional token。"
    )
    elsevier_setup.add_argument(
        "--clear-proxy", action="store_true", help="清除 Elsevier 代理配置。"
    )
    elsevier_setup.add_argument("--show", action="store_true", help="显示脱敏配置状态。")
    elsevier_setup.add_argument(
        "--validate", action="store_true", help="验证 FULL XML 与 object-EID PDF 下载链。"
    )
    elsevier_setup.add_argument(
        "--test-doi",
        default="10.1016/j.watres.2024.121507",
        help="仅用于验证的 Elsevier DOI。",
    )
    elsevier_setup.add_argument("--verbose", action="store_true", help="输出调试日志。")

    jobs = subparsers.add_parser("jobs", help="查看、恢复或取消可恢复任务。")
    jobs.add_argument("--verbose", action="store_true", help=argparse.SUPPRESS)
    job_commands = jobs.add_subparsers(dest="jobs_command", required=True)
    jobs_list = job_commands.add_parser("list", help="列出最近任务。")
    jobs_list.add_argument("--limit", type=int, default=20)
    for command in ("status", "tail", "resume", "cancel"):
        item = job_commands.add_parser(command, help=f"{command} 指定任务。")
        item.add_argument("job_id")
        if command == "status":
            item.add_argument("--compact", action="store_true", help="仅输出低频监控所需字段。")
        if command == "tail":
            item.add_argument("--limit", type=int, default=30)

    doctor = subparsers.add_parser("doctor", help="检查配置、任务库、浏览器和出版社规则。")
    doctor.add_argument("--network", action="store_true", help="执行真实 Elsevier API 验证。")
    doctor.add_argument("--json", action="store_true", help="输出 JSON。")
    doctor.add_argument("--target-dir", type=Path, help="额外检查目标论文目录可写性。")
    doctor.add_argument("--verbose", action="store_true", help="输出调试日志。")

    access = subparsers.add_parser("access", help="维护和探测本机机构期刊访问策略。")
    access.add_argument("--verbose", action="store_true", help=argparse.SUPPRESS)
    access_commands = access.add_subparsers(dest="access_command", required=True)
    access_list = access_commands.add_parser("list", help="列出当前访问环境规则。")
    access_list.add_argument("--environment")
    access_list.add_argument("--include-expired", action="store_true")
    access_set = access_commands.add_parser("set", help="设置跳过付费入口、保留 OA 的期刊规则。")
    access_set.add_argument("--environment")
    access_set.add_argument("--journal", required=True)
    access_set.add_argument("--issn", action="append", default=[])
    access_set.add_argument("--alias", action="append", default=[])
    access_set.add_argument("--year-from", type=int)
    access_set.add_argument("--year-to", type=int)
    access_set.add_argument("--source", default="user_confirmed")
    access_set.add_argument("--evidence", default="")
    access_set.add_argument("--sample-doi", action="append", default=[])
    access_set.add_argument("--expires-days", type=int)
    access_remove = access_commands.add_parser("remove", help="删除期刊访问规则。")
    access_remove.add_argument("--environment")
    access_remove.add_argument("--journal", default="")
    access_remove.add_argument("--issn", default="")
    access_probe = access_commands.add_parser("probe", help="使用真实 DOI 生成访问证据报告。")
    access_probe.add_argument("--environment")
    access_probe.add_argument("--doi", action="append", required=True)
    access_probe.add_argument("--output-dir", type=Path)
    access_probe.add_argument("--report-file", type=Path)
    access_probe.add_argument("--profile-dir", type=Path)
    access_probe.add_argument("--browser-channel")
    access_probe.add_argument("--browser-fallback", action="store_true")
    access_probe.add_argument(
        "--challenge-policy",
        choices=["pause", "skip"],
        default="pause",
    )
    access_probe.add_argument("--challenge-timeout", type=float, default=600.0)
    access_probe.add_argument("--delay", type=float, default=1.5)

    worker = subparsers.add_parser("job-worker", help=argparse.SUPPRESS)
    worker.add_argument("--broker-profile", type=Path, required=True)
    worker.add_argument("--verbose", action="store_true", help=argparse.SUPPRESS)
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
    if not arguments or arguments[0] not in {
        "auth",
        "access",
        "download",
        "elsevier-setup",
        "jobs",
        "doctor",
        "job-worker",
        "-h",
        "--help",
    }:
        arguments.insert(0, "download")
    args = build_parser().parse_args(arguments)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    if args.command == "elsevier-setup":
        return _run_elsevier_setup(args)

    if args.command == "doctor":
        result = run_doctor(network=args.network, target_dir=args.target_dir)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            for check in result["checks"]:
                print(f"[{check['status']}] {check['name']}：{check['message']}")
        return 2 if result["status"] == "failed" else 0

    if args.command == "job-worker":
        run_broker(profile_dir=args.broker_profile)
        return 0

    if args.command == "jobs":
        return _run_jobs(args)

    if args.command == "access":
        return _run_access(args)

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
            tasks: list[tuple[str, PaperJob | None]] = [(job.doi, job) for job in paper_jobs]
        else:
            tasks = [(doi, None) for doi in _load_dois(args.doi, args.doi_file)]
    except (ValueError, InvalidDoiError, PapersFileError) as exc:
        raise SystemExit(str(exc)) from exc

    challenge_policy = args.challenge_policy
    if challenge_policy is None:
        challenge_policy = "skip" if args.headless or args.detach else "pause"
    challenge_timeout = (
        args.interactive_wait if args.interactive_wait > 0 else args.challenge_timeout
    )
    browser_options = {
        "headless": args.headless,
        "interactive_wait_seconds": args.interactive_wait,
        "challenge_policy": challenge_policy,
        "challenge_timeout_seconds": challenge_timeout,
    }
    if args.browser_channel is not None:
        browser_options["channel"] = args.browser_channel
    if args.profile_dir is not None:
        browser_options["profile_dir"] = args.profile_dir

    if args.detach:
        if args.overwrite or args.supplements:
            raise SystemExit("后台任务暂不支持 --overwrite 或 --supplements。")
        runtime = default_runtime_dir().resolve()
        profile_dir = Path(
            browser_options.get("profile_dir") or runtime / "profiles" / "default"
        ).resolve()
        store = JobStore(runtime / "jobs" / "jobs.sqlite3")
        records = []
        for rank, (doi, paper_job) in enumerate(tasks, start=1):
            records.append(
                {
                    "rank": paper_job.rank if paper_job else rank,
                    "doi": doi,
                    "title": paper_job.title if paper_job else doi,
                    "folder_path": str(
                        paper_job.folder_path
                        if paper_job
                        else (args.output_dir.resolve() / doi_slug(doi))
                    ),
                    "journal": paper_job.journal if paper_job else "",
                    "issn": paper_job.issn if paper_job else "",
                    "year": paper_job.year if paper_job else None,
                }
            )
        if args.workbook and not args.report_dir:
            raise SystemExit("后台 Excel 回写要求同时提供 --report-dir。")
        if bool(args.node_path) != bool(args.node_modules):
            raise SystemExit("--node-path 与 --node-modules 必须同时提供。")
        job_id = store.create_job(
            records=records,
            output_dir=args.output_dir.resolve(),
            report_dir=args.report_dir.resolve() if args.report_dir else None,
            browser_fallback=args.browser_fallback,
            profile_dir=profile_dir,
            options={
                "delay_seconds": args.delay,
                "email": args.email or "",
                "headless": args.headless,
                "browser_channel": args.browser_channel or "",
                "access_environment": args.access_environment or "default",
            },
            workbook_path=args.workbook,
            node_path=args.node_path,
            node_modules=args.node_modules,
            batch_size=args.batch_size,
        )
        pid = BrokerManager(profile_dir=profile_dir, runtime_dir=runtime).ensure_started()
        print(f"任务已排队：{job_id}；Broker PID：{pid}")
        print(f"查看状态：doi-harvester jobs status {job_id}")
        return 0

    harvester = Harvester(
        output_dir=args.output_dir,
        browser_fallback=args.browser_fallback,
        email=args.email,
        browser_options=browser_options,
        download_supplements=args.supplements,
        access_store=AccessPolicyStore(environment=args.access_environment),
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
        reason = result.reason or result.status
        if (
            challenge_policy == "fail-fast"
            and reason in {"challenge_required", "authentication_required"}
        ):
            print("检测到需要人工验证的页面，已按 fail-fast 策略停止后续 DOI。")
            break
        if index < len(tasks) - 1 and args.delay > 0:
            time.sleep(args.delay)

    succeeded = sum(bool(item["success"]) for item in serialized)
    if args.report_dir is None:
        print(f"汇总：{succeeded}/{len(serialized)} 成功；未生成审查报告。")
    else:
        report_path = _write_batch_report(args.report_dir, serialized)
        print(f"汇总：{succeeded}/{len(serialized)} 成功；报告：{report_path}")
    return 0 if succeeded == len(serialized) else 2


def _run_elsevier_setup(args: argparse.Namespace) -> int:
    """维护全局 Elsevier 配置，并按需进行无持久输出的验证。"""
    store = GlobalConfigStore()
    try:
        config = store.load()
    except ConfigError as exc:
        recovery_requested = any(
            (
                args.set_key,
                args.set_inst_token,
                args.clear_key,
                args.clear_inst_token,
                args.clear_proxy,
                args.proxy_url is not None,
            )
        )
        if not recovery_requested:
            print(f"[失败] {exc} 请使用 --set-key 重新录入配置。")
            return 2
        print(f"[警告] 原配置无法读取，将以本次输入重建：{exc}")
        config = GlobalConfig()
    changed = False
    if args.set_key:
        value = getpass.getpass("Elsevier API Key（隐藏输入）：").strip()
        if not value:
            print("[失败] API Key 不能为空。")
            return 2
        config.elsevier.api_key = value
        changed = True
    if args.set_inst_token:
        value = getpass.getpass("Elsevier Inst Token（隐藏输入）：").strip()
        if not value:
            print("[失败] Inst Token 不能为空。")
            return 2
        config.elsevier.inst_token = value
        changed = True
    if args.proxy_url is not None:
        proxy_url = args.proxy_url.strip()
        parsed_proxy = urlparse(proxy_url)
        if parsed_proxy.scheme not in {"http", "https"} or not parsed_proxy.netloc:
            print("[失败] --proxy-url 必须是完整的 http:// 或 https:// URL。")
            return 2
        config.elsevier.proxy_url = proxy_url
        changed = True
    if args.clear_key:
        config.elsevier.api_key = ""
        changed = True
    if args.clear_inst_token:
        config.elsevier.inst_token = ""
        changed = True
    if args.clear_proxy:
        config.elsevier.proxy_url = ""
        changed = True
    if changed:
        try:
            store.save(config)
        except ConfigError as exc:
            print(f"[失败] {exc}")
            return 2

    try:
        credentials = load_elsevier_credentials(store)
    except ConfigError as exc:
        print(f"[失败] {exc}")
        return 2

    if args.show or changed or not args.validate:
        print(f"配置文件：{store.path}")
        print(f"API Key：{mask_secret(credentials.api_key)}")
        print(f"Inst Token：{mask_secret(credentials.inst_token)}")
        print(f"代理：{credentials.proxy_url or '(未配置，使用 direct)'}")

    if not args.validate:
        return 0
    if not credentials.api_key:
        print("[失败] 尚未配置 Elsevier API Key。")
        return 2
    try:
        doi = normalize_doi(args.test_doi)
    except InvalidDoiError as exc:
        print(f"[失败] {exc}")
        return 2
    with tempfile.TemporaryDirectory(prefix="autopaper-elsevier-validate-") as directory:
        destination = Path(directory) / "article.pdf"
        result = ElsevierApiClient().download(
            doi=doi,
            destination=destination,
            api_key=credentials.api_key,
            inst_token=credentials.inst_token,
            proxy_url=credentials.proxy_url,
        )
    if not result.success:
        print(f"[失败] Elsevier API 验证失败：{result.reason}")
        if result.reason == "api_configuration_error":
            print(
                "请在 Elsevier Developer Portal 检查当前 Key 的 "
                "ScienceDirect / Article Retrieval API 配置。"
            )
        return 2
    warning = f"；警告：{','.join(result.warnings)}" if result.warnings else ""
    print(f"[成功] Elsevier XML/object-EID 下载链可用；来源：{result.source}{warning}")
    return 0


def _run_jobs(args: argparse.Namespace) -> int:
    store = JobStore()
    if args.jobs_command == "list":
        for job in store.list_jobs(args.limit):
            print(f"{job['id']}\t{job['status']}\t{job['created_at']}")
        return 0
    try:
        job = store.get_job(
            args.job_id,
            detect_stalled=not (args.jobs_command == "status" and args.compact),
        )
    except KeyError as exc:
        print(f"[失败] {exc}")
        return 2
    if args.jobs_command == "status":
        if args.compact:
            payload = {
                "job_id": job["id"],
                "status": job["status"],
                "counts": job["counts"],
                "updated_at": job["updated_at"],
                "heartbeat_at": job["heartbeat_at"],
                "excel_status": job.get("excel_status", "not_requested"),
                "excel_error": job.get("excel_error", ""),
                "event": job.get("last_event", ""),
            }
            print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            return 0
        payload = {
            "job_id": job["id"],
            "status": job["status"],
            "counts": job["counts"],
            "report_dir": job["report_dir"],
            "error": job["error"],
            "excel_status": job.get("excel_status", "not_requested"),
            "excel_error": job.get("excel_error", ""),
            "event": job.get("last_event", ""),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.jobs_command == "tail":
        attempts = [attempt for item in job["items"] for attempt in item.get("attempts", [])][
            -args.limit :
        ]
        print(json.dumps(attempts, ensure_ascii=False, indent=2))
        return 0
    if args.jobs_command == "cancel":
        store.cancel(args.job_id)
        print(f"任务已标记取消：{args.job_id}")
        return 0
    if args.jobs_command == "resume":
        try:
            count = store.prepare_resume(args.job_id)
        except ValueError as exc:
            print(f"[失败] {exc}")
            return 2
        runtime = default_runtime_dir().resolve()
        profile = Path(job["profile_dir"] or runtime / "profiles" / "default")
        pid = BrokerManager(profile_dir=profile, runtime_dir=runtime).ensure_started()
        print(f"任务已恢复：{args.job_id}；待处理 {count} 条；Broker PID：{pid}")
        return 0
    return 2


def _probe_category(result: object) -> str:
    """把下载结果映射为权限探测类别。"""
    status = str(getattr(result, "status", ""))
    source = str(getattr(result, "source", ""))
    if bool(getattr(result, "success", False)):
        return "oa_accessible" if source == "openalex" else "full_text_accessible"
    reason = str(getattr(result, "reason", "") or status)
    if reason in {"subscription_required", "not_entitled"}:
        return "article_not_entitled"
    if reason == "authentication_required":
        return "authentication_required"
    if reason == "challenge_required":
        return "challenge_required"
    return "technical_failure"


def _run_access(args: argparse.Namespace) -> int:
    """维护访问策略或执行真实 DOI 探测。"""
    store = AccessPolicyStore(environment=getattr(args, "environment", None))
    if args.access_command == "list":
        rules = store.list_rules(include_expired=args.include_expired)
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "environment": store.environment,
                    "path": str(store.path),
                    "rules": [
                        {
                            "journal": rule.journal,
                            "issns": rule.issns,
                            "aliases": rule.aliases,
                            "year_from": rule.year_from,
                            "year_to": rule.year_to,
                            "policy": rule.policy,
                            "source": rule.source,
                            "evidence": rule.evidence,
                            "sample_dois": rule.sample_dois,
                            "checked_at": rule.checked_at,
                            "expires_at": rule.expires_at,
                            "expired": rule.is_expired(),
                        }
                        for rule in rules
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.access_command == "set":
        if args.year_from and args.year_to and args.year_from > args.year_to:
            raise SystemExit("--year-from 不能大于 --year-to。")
        expires_at = (
            AccessPolicyStore.automatic_expiry(days=args.expires_days)
            if args.expires_days is not None
            else None
        )
        rule = AccessRule(
            journal=args.journal,
            policy=POLICY_SKIP_PAID_KEEP_OA,
            issns=args.issn,
            aliases=args.alias,
            year_from=args.year_from,
            year_to=args.year_to,
            source=args.source,
            evidence=args.evidence,
            sample_dois=[normalize_doi(value) for value in args.sample_doi],
            expires_at=expires_at,
        )
        store.save_rule(rule)
        print(f"已保存访问规则：{rule.journal}；环境：{store.environment}；文件：{store.path}")
        return 0
    if args.access_command == "remove":
        if not args.journal and not args.issn:
            raise SystemExit("access remove 至少需要 --journal 或 --issn。")
        removed = store.remove(journal=args.journal, issn=args.issn)
        print(f"已删除 {removed} 条规则；环境：{store.environment}")
        return 0 if removed else 2
    if args.access_command == "probe":
        runtime = default_runtime_dir().resolve()
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        output_dir = (args.output_dir or runtime / "access-probes" / timestamp).resolve()
        report_file = (args.report_file or output_dir / "access-probe-report.json").resolve()
        browser_options: dict[str, object] = {
            "challenge_policy": args.challenge_policy,
            "challenge_timeout_seconds": args.challenge_timeout,
            "profile_dir": args.profile_dir or runtime / "profiles" / "default",
        }
        if args.browser_channel:
            browser_options["channel"] = args.browser_channel
        harvester = Harvester(
            output_dir=output_dir,
            browser_fallback=args.browser_fallback,
            browser_options=browser_options,
            ignore_access_policy=True,
        )
        results = []
        dois = [normalize_doi(value) for value in args.doi]
        for index, doi in enumerate(dict.fromkeys(dois)):
            result = harvester.download(doi)
            payload = result.to_dict()
            payload["probe_category"] = _probe_category(result)
            results.append(payload)
            if payload["probe_category"] in {
                "authentication_required",
                "challenge_required",
            }:
                break
            if index < len(dois) - 1 and args.delay > 0:
                time.sleep(args.delay)
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "environment": store.environment,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"权限探测完成：{report_file}")
        return 0 if all(item["success"] for item in results) else 2
    return 2
