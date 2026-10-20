# 单页工作簿后备入口

`literature-workbook-fallback.py` 仅在主入口的 Artifact Tool 不可用时使用，依赖 openpyxl。它按 `reference/文献检索汇总模板.xlsx` 的当前表头读写：列数、列序与列名都在运行时从模板读取，模板含不含 `LiteratureTable` 表格对象都能写。模板缺少本次操作必需的列时报错并说明缺哪一列。题名、DOI、稳定编号与目录遵循现有规则，命名直接复用生产纯函数。

写入：`python scripts/literature-workbook-fallback.py --workbook <绝对工作簿路径> --records <清单JSON> --resolved <解析JSON>`。

回写：`python scripts/literature-workbook-fallback.py --workbook <绝对工作簿路径> --report <批次报告JSON>`。

迁移：`python scripts/literature-workbook-fallback.py --workbook <绝对工作簿路径> --migrate-only true`。已有工作簿表头与模板不一致时按表头名称迁移，保留序号、目录名、DOI 与下载状态；模板没有的列会被丢弃。写入记录或回写报告时若检测到表头不一致，也会先迁移再写入。

接口中的清单、报告和解析字段与现有Node入口一致。写入采用同目录临时文件与原子替换。未知DOI、冲突序号或缺少必需列时拒绝写入。仅SI报告保留正文状态。此入口不访问下载任务数据库，也不启动下载。

下载器内置Excel同步仍使用主Node入口；使用后备入口时，不传不可用的Node同步配置，由任务监督者在检查点和结束后显式回写报告。失败报告必须保留。测试：`python -m pytest tests/test_workbook_fallback.py tests/test_workbook_schema.py`。

同批通用兼容修复：Windows内部文件操作在需要时使用扩展路径，保留原编号、目录与文件名。`jobs resume <job_id> --retry-doi <DOI>` 可重复传入，只重新入队原任务中已审查的普通失败项；不能与 `--retry-failed` 同用，也不重新入队人工验证跳过项。原批次及SI子批次的报告分别保留。
