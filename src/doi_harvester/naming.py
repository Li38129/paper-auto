"""生成包含稳定序号、期刊指标和发表年份的安全目录名。"""

from __future__ import annotations

import re


def literature_name(
    sequence: int,
    title: str,
    year: int | None = None,
    impact_factor: str = "",
    cas_quartile: str = "",
    journal_abbreviation: str = "",
) -> str:
    """缺失指标明确标注，指标年份不作为发表年份。"""
    metric = str(impact_factor).strip()
    number = re.fullmatch(r"(?:IF\s*)?(\d+(?:\.\d+)?)", metric, re.I) or re.search(
        r"(?:JCR|IF|影响因子)\s*[:：=]?\s*(\d+(?:\.\d+)?)(?![\d.])", metric, re.I
    )
    impact = number[1] if number and "未核实" not in metric else "未核实"
    quartile = str(cas_quartile).strip()
    match = re.search(r"([一二三四1-4])\s*区", quartile)
    zone = "分区未核实"
    if "未核实" not in quartile:
        if re.search(r"顶刊|\btop\b", quartile, re.I):
            zone = "顶刊"
        elif match:
            zone = f"{dict(zip('1234', '一二三四', strict=True)).get(match[1], match[1])}区"
    journal = re.sub(r"[_\s]+", " ", journal_abbreviation).strip() or "期刊简称未核实"
    name = (
        f"{sequence}_IF{impact}_{journal}_{zone}_"
        f"{year or '年份未核实'}_{title or '题名未核实'}"
    )
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name)
    return re.sub(r"\s+", " ", name).strip().rstrip(" .")[:160].rstrip(" .")
