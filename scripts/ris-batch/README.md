# RIS 批量文献下载工具集

把“给定 RIS/DOI 清单 → 建编号目录 → 下载正文与 SI → 回写工作簿与整理清单”的流程固化成可复用脚本。
所有路径都通过参数传入，脚本内不写死任何用户目录、DOI 或下载顺序。

## 脚本与顺序

| 顺序 | 脚本 | 作用 | 主要参数 | 主要输出 |
| --- | --- | --- | --- | --- |
| 1 | `extract-ris.py` | 解析 RIS，规范 DOI，按规则排除 MDPI/IOP/ECS | `--ris --work` | `records.json`、`DOI清单.csv`、`期刊分布.csv` |
| 2 | `journal-metrics.py` | 按 ISSN/刊名匹配中科院分区与 JCR IF | `--records --metrics-dir --out [--partition-file --jcr-file]` | `期刊指标.csv`、`metrics-report.json` |
| 3 | `build-records.py` | 合成工作簿记录 + 去重标记 + 待下载清单 | `--records --metrics --dedup --topic --out` | `literature-records.json`、`dedup-report.json`、`pending.csv` |
| 4 | `make-papers.py` | 建编号目录、生成下载清单，可按 rank 拆 A/B 段 | `--resolved --out [--dedup --split-rank]` | `papers.json`、`papers-seg-a/b.json` |
| 5 | `consolidate-report.py` | 合并多任务逐篇状态，按磁盘补登记 SI | `--db --job ... --out` | `batch-report.json` |
| 6 | `verify-run.py` | 核对正文/SI 落盘、生成失败清单 | `--root --report [--verify-out --manifest-csv]` | 统计、`verify-report.json`、失败清单 CSV |

工作簿读写与清单去重/回写仍用既有入口：

- `scripts/literature-workbook-fallback.py --workbook ... --records ... --resolved ...`（写记录）
- `scripts/literature-workbook-fallback.py --workbook ... --report ...`（回写下载结果）
- `scripts/dataset-list-sync.py check/apply`（去重与清单 upsert）

## 典型调用

```powershell
python scripts\ris-batch\extract-ris.py --ris <输入.ris> --work <工作目录>
python scripts\dataset-list-sync.py check --candidates <工作目录>\DOI清单.csv --list <整理清单.csv> --json > <工作目录>\dedup-check.json
python scripts\ris-batch\journal-metrics.py --records <工作目录>\records.json --metrics-dir <ShowJCR 数据目录> --out <工作目录>
python scripts\ris-batch\build-records.py --records <工作目录>\records.json --metrics <工作目录>\期刊指标.csv --dedup <工作目录>\dedup-check.json --topic "<主题>" --out <工作目录>
python scripts\literature-workbook-fallback.py --workbook <论文根>\文献检索汇总.xlsx --records <工作目录>\literature-records.json --resolved <任务目录>\resolved-records.json
python scripts\ris-batch\make-papers.py --resolved <任务目录>\resolved-records.json --out <任务目录>\papers.json --dedup <工作目录>\dedup-check.json --split-rank 500
```

下载与收尾见 `docs/ris-batch-runbook.md`。

## 验收口径

`verify-run.py` 输出的两个一致性标志必须为 true：

- `consistent_body`：报告成功数 == 磁盘含 PDF 的编号目录数；
- `consistent_si`：报告 SI 成功数 == 磁盘含 `supplements` 附件的目录数。

## 约定

- 期刊指标按期刊与年份核验，缺证据写“未核实”，不猜数值；
- 正文失败不代表 SI 未落盘，合并报告时必须按磁盘补登记；
- 不覆盖已有有效文件、不重排编号；修复既有条目时优先提通用接口增强。
