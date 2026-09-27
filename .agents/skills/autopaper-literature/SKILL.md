---
name: autopaper-literature
description: Search, verify, deduplicate, and rank academic literature, maintain a persistent Excel literature index, create stable numbered folders, and use the bundled AutoPaper DOI Harvester to download legally accessible article PDFs. Use for end-to-end literature search and download workflows in this repository.
---

# AutoPaper Literature

把研究主题转换为经过核验、去重和排序的论文清单，在用户指定的论文根目录维护 `文献检索汇总.xlsx`，创建稳定编号目录，并使用本仓库的 DOI Harvester 下载可合法获取的正文 PDF 或用户指定的补充材料。

## 确定目标目录与任务口径

每次调用本 Skill 开始一个新的下载任务或目标，必须先统一询问并取得三项设置的明确回答，即使用户请求已包含设置，也汇总确认一次：

1. 保存路径：展示用户提供的路径或同一会话已有路径作为候选，确认最终绝对路径；未提供路径时询问，不自行选择默认目录。论文根目录必须是长期保存位置，不得使用项目 `temp`。
2. 下载内容：仅正文、仅补充材料、正文与补充材料；不得默认为正文。
3. 人工验证处理：跳过当前文献继续，或暂停任务等待人工验证；不得根据前台、后台或无头模式替用户选择。

固定确认模板：

> 请确认本次新任务的三项设置：
> 1. 保存路径：<用户提供或已有的绝对路径；没有候选时请用户填写>。
> 2. 下载内容：仅正文 / 仅补充材料 / 正文与补充材料。
> 3. 遇人工验证：跳过当前篇继续 / 暂停等待验证。

三项回答齐全前不得写入 Excel、创建论文目录或启动下载。确认由对话完成，CLI/MCP 传入已确认的显式参数，不重复终端询问。三项设置归属整个目标：内部子批次不重复询问，同一任务验证后继续或断点恢复也不重新询问；用户主动修改设置时，只重新确认受影响的项目。

路径确定后，再从请求中提取研究主题、材料或方法边界、关注指标、论文数量和文献类型限制。未指定数量时以 15–20 篇为初始目标。

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

## 创建论文目录与按确认模式下载

下载默认逐篇在浏览器工作标签打开并核对对应 DOI 的出版社论文页面；页面未打开或无法核对时保留任务并暂停，不能将其记为无补充材料。Windows 前台焦点不影响已打开页面的下载。

1. 只创建工作簿脚本在 `resolved-records.json` 中返回且尚不存在的目录。保留目标目录的全部既有内容，不覆盖、不删除、不移动。
2. 创建或确认目录后，读取并严格执行 [DOI Harvester 联动规则](references/doi-harvester.md)。只执行此前三项确认所授权的下载模式，不把创建目录视为授权下载正文。
3. 只把有可靠 DOI 的记录写入 `papers.json`。必须按已确认下载内容选择正文、SI-only 或正文加 SI。
   已提供固定编号 DOI CSV 且明确要求补充材料时，使用 `scripts/si-csv-batch.py` 保留原序号；先写入工作簿，再创建论文目录。无效或占位 DOI 仍保留在目标清单与工作簿，标为待核验且不进入下载器；不得因个别无效 DOI 放弃整批。正文和 SI 分别维护状态。下载模式见 [DOI Harvester 联动规则](references/doi-harvester.md)。
4. 每次下载或重试结束后，先把对应 `batch-report.json` 回写 Excel。只有 Excel 写回成功、所有可下载条目成功且 PDF 校验通过时，才能清理一次性任务目录。
5. Excel 最终写回失败、正文下载失败或仍需人工授权时保留任务目录，并报告可恢复路径和原因。

不得覆盖有效的既有 `article.pdf`，不得改写论文目录中的其他文件，不得代填凭据、破解验证码、绕过付费墙或机构授权。

如果当前会话已经注册 AutoPaper MCP，优先使用其 `search`、`download`、`job_status` 和 `update_excel` 工具执行相同步骤；工具不可用时使用仓库脚本，不得因此改变 Excel 先写入、报告后回写和失败任务留档规则。


## 统一检查点下载规则

正文、仅 SI、正文加 SI 均通过 CLI/MCP 自动创建可恢复任务。CLI 的单 DOI、DOI 列表与固定编号清单共用任务执行器；默认当前终端执行，`--detach` 仅切换 Broker。不得使用 `--overwrite`。每篇保存数据库检查点，默认每 100 篇同步报告、结果 CSV 与已配置的 Excel，并在暂停和结束时同步。未指定报告目录时使用 `temp/doi-harvester/jobs/<job_id>`；授权等待或中断后通过原 ID 恢复，不新建重复任务。

SI 链接由浏览器发现后先尝试 HTTP 流式获取，失败附件再使用浏览器回退。逐次失败尝试不能覆盖最终成功结果；JSON 保留尝试过程，CSV/Excel 记录最终附件结果。页面 DOI 须完整匹配，不能依据子串复用页面；页面不可确认或验证失败保留未解决/授权状态。旧任务缺少 SI 参数时仍按正文模式恢复。有效缓存、编号和目录保持原有规则。


CLI 始终显式传入确认的 `--output-dir`、下载模式和 `--challenge-policy pause|skip`；仅正文不传 SI 参数，正文加 SI 传 `--supplements`，仅 SI 传 `--supplements-only`。MCP 传对应 `output_dir`、`supplements`、`supplements_only`、`challenge_policy`。恢复读取原任务配置。

人工验证选择 skip 时，该篇标记 `auth_skipped`，记录 DOI、页面、原因和已有文件后继续下一篇，不等待、不自动重试。选择 pause 时，保留页面及检查点，进入 `waiting_for_user`。浏览器连接或 DOI 核验错误仍暂停，不可用 skip 掩盖。报告须分别统计人工验证跳过数与成功数；队列执行完成不等于全部下载成功。跳过项保留恢复数据，普通恢复不重新入队。
