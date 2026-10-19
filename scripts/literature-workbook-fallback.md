# 单页工作簿后备入口

`literature-workbook-fallback.py` 仅在主入口的 Artifact Tool 不可用时使用，依赖 openpyxl。支持项目22列单页模板；不自动迁移其他旧结构。题名、DOI、稳定编号与目录遵循现有规则，命名直接复用生产纯函数。

写入：`python scripts/literature-workbook-fallback.py --workbook <绝对工作簿路径> --records <清单JSON> --resolved <解析JSON>`。

回写：`python scripts/literature-workbook-fallback.py --workbook <绝对工作簿路径> --report <批次报告JSON>`。

接口中的清单、报告和解析字段与现有Node入口一致。写入采用同目录临时文件与原子替换。未知DOI、冲突序号或不兼容结构拒绝写入。仅SI报告保留正文状态。此入口不访问下载任务数据库，也不启动下载。

下载器内置Excel同步仍使用主Node入口；使用后备入口时，不传不可用的Node同步配置，由任务监督者在检查点和结束后显式回写报告。失败报告必须保留。测试：`python tests/test_workbook_fallback.py`。

同批通用兼容修复：Windows内部文件操作在需要时使用扩展路径，保留原编号、目录与文件名。`jobs resume <job_id> --retry-doi <DOI>` 可重复传入，只重新入队原任务中已审查的普通失败项；不能与 `--retry-failed` 同用，也不重新入队人工验证跳过项。原批次及SI子批次的报告分别保留。
