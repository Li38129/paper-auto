#!/usr/bin/env python3
"""回收下载器登记的上一篇论文的临时标签，可用 --dry-run 预览。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def http_json(url: str, timeout: float = 5.0) -> object:
    """读取 CDP HTTP 接口的 JSON。"""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def close_target(url: str, timeout: float = 5.0) -> bool:
    """调用 /json/close/<id>；该接口返回纯文本，不能按 JSON 解析。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            response.read()
        return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def read_record(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def clean_profile(profile_dir: Path, keep: int, dry_run: bool) -> int:
    """只回收同一工作会话内、已切换 DOI 的临时标签。"""
    directory = profile_dir / "temporary-tabs"
    if not directory.is_dir():
        return 0
    work_path = profile_dir / "work-target.json"
    work = read_record(work_path)
    endpoint = read_record(profile_dir / "auth-state.json").get("cdp_endpoint")
    if not (
        endpoint
        and work.get("endpoint") == endpoint
        and work.get("doi")
        and work.get("target_id")
        and urlsplit(endpoint).scheme == "http"
        and urlsplit(endpoint).hostname in {"127.0.0.1", "::1", "localhost"}
    ):
        return 0
    try:
        targets = http_json(f"{endpoint}/json/list")
    except (OSError, ValueError) as exc:
        print(f"[跳过] {profile_dir.name}: {exc}")
        return 0
    pages = {t["id"]: t for t in targets if t.get("type") == "page" and t.get("id")}
    if work["target_id"] not in pages:
        return 0
    candidates = []
    for path in directory.glob("*.json"):
        tab_id = path.stem
        record = read_record(path)
        if not (
            re.fullmatch(r"[0-9a-fA-F]+", tab_id)
            and tab_id != work["target_id"]
            and record.get("endpoint") == endpoint
            and record.get("doi")
            and record.get("target_id") == work["target_id"]
        ):
            continue
        if tab_id not in pages:
            if not dry_run:
                path.unlink(missing_ok=True)
            continue
        if record["doi"] == work["doi"]:
            continue
        title = str(pages[tab_id].get("title", "")).casefold()
        url = str(pages[tab_id].get("url", "")).casefold()
        if any(
            word in title
            for word in (
                "just a moment",
                "verify",
                "captcha",
                "sign in",
                "log in",
                "请稍候",
                "验证",
                "登录",
            )
        ) or any(word in url for word in ("/login", "/signin", "/sso", "/authorize")):
            continue
        try:
            candidates.append((path.stat().st_mtime_ns, path))
        except FileNotFoundError:
            continue
    closed = 0
    for _, path in sorted(candidates, reverse=True)[max(keep, 0) :]:
        if read_record(work_path) != work:
            break
        if dry_run:
            print(f"[预览] {profile_dir.name}: {path.stem}")
            closed += 1
        elif close_target(f"{endpoint}/json/close/{path.stem}"):
            path.unlink(missing_ok=True)
            closed += 1
    return closed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles-dir", default="temp/doi-harvester/profiles")
    parser.add_argument("--only", action="append", help="只清理指定配置目录名")
    parser.add_argument("--keep", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.profiles_dir)
    if not root.is_dir():
        print(f"配置目录不存在：{root}")
        return 1
    total = 0
    for profile_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if args.only and profile_dir.name not in args.only:
            continue
        total += clean_profile(profile_dir, args.keep, args.dry_run)
    print(f"合计关闭 {total} 个标签")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
