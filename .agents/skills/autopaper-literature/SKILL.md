---
name: autopaper-literature
description: Search, verify, deduplicate, and rank academic literature, maintain a persistent Excel literature index, create stable numbered folders, and use the bundled AutoPaper DOI Harvester to download legally accessible article PDFs. Use for end-to-end literature search and download workflows in this repository.
---

# AutoPaper Literature

把研究主题转换为经过核验、去重和排序的论文清单，在用户指定的论文根目录维护 `文献检索汇总.xlsx`，创建稳定编号目录，并使用本仓库的 DOI Harvester 下载可合法获取的正文 PDF。

## 确定任务口径

从用户请求和同一对话中确定研究主题、材料或方法边界、关注指标、论文数量、文献类型限制和论文根目录。未指定数量时以 15–20 篇为初始目标。缺少目标目录不影响检索与展示，但写入 Excel、创建目录和下载前必须取得绝对路径，不得猜测。

## 检索、核验与排序

1. 用户显式指定 SciSpace 时优先使用 SciSpace，并传入完整研究问题。覆盖不足时，用出版社页面、Crossref、PubMed、机构知识库或可靠学术索引补充核验。
2. 至少使用两个有差异的检索表达式。优先保留可核验 DOI、摘要和来源入口的同行评议论文，只提供 DOI、出版社页面、开放获取全文或机构作者稿等合法入口。
3. 先以规范化 DOI 去重；没有 DOI 时按规范化题名去重。补充材料 DOI 归并到正文，预印本与正式版本重复时保留正式同行评议版本。
4. 先按主题相关性筛选，再按用户关注指标排序。指标条件不可直接比较时，以相关性优先并明确条件差异，不得推测缺失数据。
5. 向用户展示最终排序表，至少包含体系或最佳组成、关注指标、题名、年份、DOI 和入口，并区分实验、计算或综述。

## 写入 Excel 并解析稳定目录

检索完成且目标目录已确定后，先读取并严格执行 [Excel 汇总规则](references/workbook.md)。必须先成功创建或更新 `文献检索汇总.xlsx`，再创建论文目录或启动下载。

- Excel 中已有序号和目录名不可改变；新文献按本次排序追加到当前最大序号之后。
- 新记录使用简短 `folder_label`，不要自行加序号；工作簿脚本返回最终 `folder_name` 和绝对 `folder_path`。
- Excel 不兼容、被占用或写入失败时立即停止，不得留下未汇总的新目录或下载任务。

## 创建论文目录与下载正文

1. 只创建工作簿脚本在 `resolved-records.json` 中返回且尚不存在的目录。保留目标目录的全部既有内容，不覆盖、不删除、不移动。
2. 创建或确认目录后，读取并严格执行 [DOI Harvester 联动规则](references/doi-harvester.md)。用户授权本 Skill 创建目录即代表同时授权下载正文，无需再次确认。
3. 只把有可靠 DOI 的记录写入 `papers.json`。默认不下载补充材料，除非用户明确要求。
4. 每次下载或重试结束后，先把对应 `batch-report.json` 回写 Excel。只有 Excel 写回成功、所有可下载条目成功且 PDF 校验通过时，才能清理一次性任务目录。
5. Excel 最终写回失败、正文下载失败或仍需人工授权时保留任务目录，并报告可恢复路径和原因。

不得覆盖有效的既有 `article.pdf`，不得改写论文目录中的其他文件，不得代填凭据、破解验证码、绕过付费墙或机构授权。
