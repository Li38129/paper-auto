---
name: autopaper-literature
description: Search, verify, deduplicate, and rank academic literature, maintain a persistent Excel literature index, create stable numbered folders, and use the bundled AutoPaper DOI Harvester to download legally accessible article PDFs. Use for end-to-end literature search and download workflows in this repository.
---

# AutoPaper Literature

把研究主题转换为经过核验、去重和排序的论文清单，在用户指定的论文根目录维护 `文献检索汇总.xlsx`，创建稳定编号目录，并使用本仓库的 DOI Harvester 下载可合法获取的正文 PDF 或用户指定的补充材料。

## 确定目标目录与任务口径

每次调用本 Skill 开始一个新的下载任务或目标，必须先统一询问并取得两项设置的明确回答，即使用户请求已包含设置，也汇总确认一次：

1. 保存路径：展示用户提供的路径或同一会话已有路径作为候选，确认最终绝对路径；未提供路径时询问，不自行选择默认目录。论文根目录必须是长期保存位置，不得使用项目 `temp`。
2. 下载内容：仅正文、仅补充材料、正文与补充材料；不得默认为正文。

人工验证不再作为启动设置：验证页一律由 Agent 按 `AGENTS.md` 4.1 自助通过；自助未通过时暂停该篇并保留断点待人工接管，不提供 skip/pause 预选。

固定确认模板（Agent 不能自己输入 `/computer`，必须由用户在对话框授予）：

> 请确认本次新任务的两项设置：
> 1. 保存路径：<用户提供或已有的绝对路径；没有候选时请用户填写>。
> 2. 下载内容：仅正文 / 仅补充材料 / 正文与补充材料。
>
> 另外请在本对话框输入一次 `/computer` 授予电脑操控（验证页由我自助点选通过；
> 不给也能跑，但验证页会停下来等你手动处理）。

两项回答齐全前不得写入 Excel、创建论文目录或启动下载。确认由对话完成，CLI/MCP 传入已确认的显式参数，不重复终端询问。设置归属整个目标：内部子批次不重复询问，同一任务验证后继续或断点恢复也不重新询问；用户主动修改设置时，只重新确认受影响的项目。

`/computer` 授权是启动前的独立前置项（见 `AGENTS.md` 4.2）：每个新任务开口确认时顺带索取一次；授权到位后验证页全部由 Agent 自助通过，任务中途不再重复索要。

路径确定后，再从请求中提取研究主题、材料或方法边界、关注指标、论文数量和文献类型限制。未指定数量时以 15–20 篇为初始目标。

## 与已有 DOI 清单去重

两项确认齐全后、写入 Excel、创建论文目录或启动下载之前，先与用户确认的已有 DOI 清单核对（默认成功清单 `E:\Dataset\data_unprocesssed\整理清单_成功.csv` 与同目录 `整理清单_已尝试但未下载成功.xlsx`，可用参数覆盖；用户未提供且无候选时跳过本步骤，不自行猜测路径）：

1. 用 `scripts/dataset-list-sync.py check --candidates <本次清单> --list <整理清单.csv>` 生成「已存在 / 新增 / 按规则排除」三组结果。传入默认成功清单时自动联查同目录的失败表；自定义双表用 `--failed-list <失败清单.xlsx>`。成功、别名与失败 DOI 均纳入 already_listed，失败命中另列在 already_attempted_failed。
2. 已存在的 DOI 不重复下载：保留其在工作簿中的既有记录，`下载状态` 记为 `already_listed`，并在最终报告注明来源清单。
3. 新增 DOI 按正常流程写入 Excel、建目录并下载。
4. 去重前后都不得删除或改动用户清单中的既有行。


## 检索、核验与排序

1. 用户显式指定 SciSpace 时优先使用 SciSpace，并传入完整研究问题。覆盖不足时，用出版社页面、Crossref、PubMed、机构知识库或可靠学术索引补充核验。
2. 至少使用两个有差异的检索表达式。优先保留可核验 DOI、摘要和来源入口的同行评议论文，只提供 DOI、出版社页面、开放获取全文或机构作者稿等合法入口。
   筛选时排除 MDPI 旗下期刊论文及其页面，同时排除 IOP 与 ECS 旗下期刊（`10.1149/`、`iopscience.iop.org`、`ecsdl.org`）：不为其生成候选、不调用下载，固定清单遇到即跳过并标注「按规则跳过（IOP/ECS）」；用户未明确要求综述时，排除综述、系统综述和元分析，优先选原始研究。用户明确指定的 DOI 或固定清单不擅自删改；若其中包含这些类型，保留并标注例外。
3. 先以规范化 DOI 去重；没有 DOI 时按规范化题名去重。补充材料 DOI 归并到正文，预印本与正式版本重复时保留正式同行评议版本。
4. 先按主题相关性筛选，再按用户关注指标排序。指标条件不可直接比较时，以相关性优先并明确条件差异，不得推测缺失数据。
5. 向用户展示最终排序表，至少包含体系或最佳组成、关注指标、题名、年份、DOI、入口、中科院分区和影响因子（IF），并区分实验、计算或综述。分区和 IF 按期刊核验，注明分区版本、IF 年份及各自来源；无法可靠核实时写“未核实”，不得推测或混用年份。

## 写入 Excel 并解析稳定目录

检索完成且目标目录已确定后，先读取并严格执行 [Excel 汇总规则](references/workbook.md)。必须先成功创建或更新 `文献检索汇总.xlsx`，再创建论文目录或启动下载。

所有新汇总表使用项目 `reference/文献检索汇总模板.xlsx` 的当前版式：列数、列序与列名以该模板为准，用户可随时更新模板，写脚本不得硬编码列序；模板可以没有表格对象，也不要求固定列数。模板缺少必需列时按 [Excel 汇总规则](references/workbook.md) 报错并说明缺哪一列。SI 汇总状态写在 `SI是否下载成功` 列（模板提供该列时），不创建“补充材料”工作表。

- Excel 中已有序号和目录名不可改变；新文献按本次排序追加到当前最大序号之后。
- 新记录由工作簿脚本生成 `序号_IF值_期刊简称_一区/顶刊/二区/三区_发表年份_论文题名`，各字段以下划线分隔；`folder_label` 仅兼容旧输入，不参与新目录命名。脚本返回最终 `folder_name` 和绝对 `folder_path`。
- 每条汇总记录填写 `cas_quartile`、`impact_factor` 和 `journal_metrics_source`；值缺乏可核验依据时写“未核实”，来源字段说明分区版本、IF 年份及对应来源。模板提供这些列时分别写入 `中科院分区`、`影响因子（IF）`、`分区/IF依据`；模板未提供的字段不写入，也不得为此改动模板。
- Excel 不兼容、被占用或写入失败时立即停止，不得留下未汇总的新目录或下载任务。

期刊简称通过 `journal_abbreviation` 明确提供（固定 CSV 可用“期刊简称”列），或读取 Crossref 的 `short-container-title`；缺少可靠简称时标为 `期刊简称未核实`，不自行生成首字母缩写。简称中的下划线替换为空格，已有目录继续保留。

## 创建论文目录与按确认模式下载

同一目标的子批次默认排队串行，逐篇复用外部 Edge 的一个工作标签。PDF 查看器或中转页用完即关，同一时刻最多保留一个下载临时页。遇到旧任务堆积标签，先在逐篇断点停下，只清理来源已确认的下载弹窗，保留验证页和用户其他页面，再按原任务 ID 依次恢复。

下载默认逐篇复用外部 Edge 专用 DOI 工作标签，不使用内置浏览器。标签存在且有本次目标 DOI 的导航记录即可继续缓存、HTTP/API；无需焦点，出版社加载失败或内容暂缺不阻断独立传输。浏览器提取正文或 SI 链接仍须完整核验页面 DOI，不能将内容未确认记成无 SI。

1. 只创建工作簿脚本在 `resolved-records.json` 中返回且尚不存在的目录。保留目标目录的全部既有内容，不覆盖、不删除、不移动。
2. 创建或确认目录后，读取并严格执行 [DOI Harvester 联动规则](references/doi-harvester.md)。只执行此前确认的下载内容，不把创建目录视为授权下载正文。
3. 只把有可靠 DOI 的记录写入 `papers.json`。必须按已确认下载内容选择正文、SI-only 或正文加 SI。
   已提供固定编号 DOI CSV 且明确要求补充材料时，使用 `scripts/si-csv-batch.py` 保留原序号；先写入工作簿，再创建论文目录。无效或占位 DOI 仍保留在目标清单与工作簿，标为待核验且不进入下载器；不得因个别无效 DOI 放弃整批。正文和 SI 分别维护状态。下载模式见 [DOI Harvester 联动规则](references/doi-harvester.md)。
4. 每次下载或重试结束后，先把对应 `batch-report.json` 回写 Excel。只有 Excel 写回成功、所有可下载条目成功且 PDF 校验通过时，才能清理一次性任务目录。
5. 下载结束（成功、失败、暂停或部分完成）后，用 `scripts/dataset-list-sync.py apply --report <batch-report.json> --list <整理清单.csv>` 把逐篇结果 upsert 回用户清单：按规范化 DOI 匹配更新 `OutputFolder` 与 `Status`，新 DOI 追加行且 `Sequence` 取现有最大值加一、四位补零；保留原列顺序与 UTF-8-sig 编码，默认不覆盖已有非空 `Status`（需要覆盖时显式传 `--overwrite-status`）。双表模式按正文文件实际存在分流，已尝试且无正文的记录进入失败表；只排队未尝试的记录不登记。后续获得正文时沿用序号转入成功表。写入失败时报告临时文件路径，重放同一报告可继续。
6. Excel 最终写回失败、正文下载失败或仍需人工授权时保留任务目录，并报告可恢复路径和原因。

新目录和正文 PDF 使用 `序号_IF值_期刊简称_一区/顶刊/二区/三区_发表年份_论文题名`（正文加 `.pdf`）；新版目录中的 SI 使用 `序号_IF值_期刊简称_一区/顶刊/二区/三区_发表年份_si序号_论文题名`，保留附件实际扩展名，保存于 `supplements` 子目录。已核实为四区时如实使用 `四区`；明确标注 TOP 或顶刊时使用 `顶刊`，不从 IF 推断分区或顶刊。缺失值使用 `IF未核实`、`分区未核实`、`年份未核实` 或 `题名未核实`；IF 年份与分区版本留在工作簿依据列，命名年份只使用论文发表年份。Windows 非法字符替换为安全字符，过长名称截断。已有目录、有效 `article.pdf` 和旧目录附件沿用原路径与缓存规则，不移动、不覆盖；正文和 SI 的实际路径以下载报告为准。不得改写论文目录中的其他文件，不得代填凭据、破解验证码、绕过付费墙或机构授权。

如果当前会话已经注册 AutoPaper MCP，优先使用其 `search`、`download`、`job_status` 和 `update_excel` 工具执行相同步骤；工具不可用时使用仓库脚本，不得因此改变 Excel 先写入、报告后回写和失败任务留档规则。


### 阅读器路径兼容

目录编号与目录名保持稳定。正文与 SI 的最终文件名必须按完整路径长度预算截断，保留扩展名与防冲突摘要；Windows 路径（UTF-16 单元）须小于 240，附件另留临时文件和冲突后缀空间。不要仅截断单个目录名，也不要把内部 `\\?\` 扩展前缀写入报告、工作簿或用户打开链接。父目录本身过长而无法生成兼容路径时，明确报错并建议缩短保存根目录。

已有长路径 PDF 可运行 `python scripts/repair-pdf-paths.py --root <论文目录> --report <新报告路径>` 检查，加 `--apply` 创建兼容副本；逐篇解析页数并核对 SHA-256，原件不移动、不覆盖。同步工作簿时按报告映射到副本，并遵守当前模板。此操作不启动新下载，不重复询问下载任务设置。

## 统一检查点下载规则

正文、仅 SI、正文加 SI 均通过 CLI/MCP 自动创建可恢复任务。CLI 的单 DOI、DOI 列表与固定编号清单共用任务执行器；默认当前终端执行，`--detach` 仅切换 Broker。不得使用 `--overwrite`。每篇保存数据库检查点，默认每 100 篇同步报告、结果 CSV 与已配置的 Excel，并在暂停和结束时同步。未指定报告目录时使用 `temp/doi-harvester/jobs/<job_id>`；授权等待或中断后通过原 ID 恢复，不新建重复任务。

SI 链接由浏览器发现后先尝试 HTTP 流式获取，失败附件再使用浏览器回退。逐次失败尝试不能覆盖最终成功结果；JSON 保留尝试过程，CSV/Excel 记录最终附件结果。浏览器提取链接时页面 DOI 须完整匹配，不能依据子串复用错误内容；展示标签已打开后可先尝试缓存及公开入口。旧任务缺少 SI 参数时仍按正文模式恢复。有效缓存、编号和目录保持原有规则。


CLI 始终显式传入确认的 `--output-dir`、下载模式、`--browser-channel msedge`、`--browser-display foreground` 和 `--challenge-policy pause`（固定值：验证页由 Agent 自助通过，自助未通过时保留页面与检查点等人工接管）；仅正文不传 SI 参数，正文加 SI 传 `--supplements`，仅 SI 传 `--supplements-only`。若出版社页面明确提供且已核验某篇 SI 附件直链，可对该 DOI 重复传 `--supplement-url DOI=HTTPS_URL`；MCP 传 `supplement_urls={DOI: [HTTPS_URL, ...]}`。直链写入可恢复任务配置，仍由 DOI Harvester 负责下载、类型校验、缓存和 SHA-256；不得用它绕过授权。MCP 传对应 `output_dir`、`supplements`、`supplements_only`、`challenge_policy`。恢复读取原任务配置。

验证页处理不再由用户预选：一律先按 `AGENTS.md` 4.1 自助点选，通过后 `jobs resume` 继续队列；自助未通过（无可点控件、需要密码/短信/SSO 凭据、连续 2 轮仍在验证页）时该篇保持 `waiting_for_user` 并保留页面与检查点，报告记录 DOI、页面 URL、点选位置与页面状态，由用户接管后恢复，不自动标记跳过。Edge 未安装、连接失败或标签关闭时暂停；页面内容未核验时不用于链接提取，但不阻断缓存及公开入口。报告须统计自助通过数、待人工接管数与成功数；队列执行完成不等于全部下载成功。

### 内存与标签控制（长时段下载必做）

下载器会在每次尝试结束时关闭临时页。长任务还可回收异常中断留下的临时页。

1. **单目标串行**：同一下载目标同时只保留一个下载进程（一个配置目录一个工作标签）。需要提速时先征得用户同意再开并行配置目录，不得默认多开。
2. **临时页用完即关**：正文查看器、SI 中转页在本次尝试结束（成功、失败或超时）后立即关闭，只保留工作标签；恢复任务时保留仍等待人工处理的验证页。
3. **定时清理**：`scripts/close-stale-tabs.py` 核对下载器的临时页登记、工作标签和会话，只回收已切换 DOI 的残留页：
   ```bash
   python .agents/skills/autopaper-literature/scripts/close-stale-tabs.py \
       --profiles-dir temp/doi-harvester/profiles            # 加 --dry-run 先看清单
   ```
   建议每 3–5 分钟或每 50 篇调用一次；长任务可放一个后台守护循环定时执行。
4. **阈值**：`Get-Process msedge` 合计工作集超过约 4 GB，或单窗口标签数超过 20 个，立即清理并复查；清理后仍偏高时结束已完成批次的配置目录浏览器，再恢复原任务断点。
5. 清理只允许关闭可确认由下载器产生的页面，不得关闭用户自己的浏览器窗口、验证页或进行中的下载页。


## 验证缓冲与临时脚本

处理顺序固定为：**先自己动手过验证，再考虑暂停**。细节规则见 `AGENTS.md` 第 4.1 条，这里是任务层的执行清单。

**触发即执行，不等用户提醒**：任务状态出现 `waiting_for_user`/`needs_attention` 且原因是
`challenge_required`/`auth_required`，或输出「需要人工验证：DOI …」，或 Edge 标签标题含
「请稍候」「Just a moment」「Are you a robot」「请验证您是真人」——立即进入下面流程，不要先写报告空等。

1. 每秒检查页面，最多缓冲 10 秒，验证提前消失立即继续，不要求已出现 PDF 按钮。
2. 仍在验证页时自助处理：`computer_list_windows` 找 Edge 工作标签（标题常是「请稍候…」+ 另外 N 个页面）→
   `computer_activate_window {hwnd}` 提到前台 → `computer_screenshot`（必要时 `region`/`scale` 局部放大）→
   按返回的 `screen_per_image` 换算坐标 → `computer_click` 点验证复选框/同意按钮 → `computer_wait` 3–5 秒 →
   再截图确认出现正文（`Brought to you by: <机构>`、`View PDF`）。
3. 通过后立刻 `jobs resume <job_id>`，继续按检查点轮询；把 DOI、页面 URL、点选位置、页面状态、是否恢复写进报告。
4. 单次最多观察 60 秒、最多 2 轮。`computer` 未批准时用一行话请用户输入 `/computer`（不要跳过，也不要用脚本伪造点击）。
5. 只有页面无可点控件、需要密码/短信/SSO 凭据、用户明确拒绝操控、或连续 2 轮仍在验证页，才暂停该篇、保留页面与检查点，并在报告中标为待人工接管；不预选 skip、不自动标记跳过。
6. 不代填凭据、不破解验证码、不绕过付费墙。

同一次结果在层间传递不重复等待；再次出现独立验证可再次缓冲。下载的旧等待时长参数不延长这 10 秒缓冲；独立 auth 命令保留人工授权等待。附件授权失败只有当前 Edge 页面确认恢复且 DOI 匹配后才重试该附件一次。

CLI/MCP 正常下载默认外部 Edge，不静默回退 Chrome；旧任务显式指定非 Edge 时提示，不能默默替换会话。只有用户明确选择无界面运行时使用 off/headless 高级接口。工作标签导航记录绑定真实标签 ID 和当前调试会话，不能仅凭有 Edge 进程就认定目标论文已打开。

临时追加少量 DOI、筛选重试条目、生成任务清单或一次性编排脚本放在 `temp/doi-harvester/task-scripts/<任务或目标ID>/`。附说明记录用途、任务ID、输入输出、命令和恢复方法，通过现有 CLI/MCP 或公开任务接口调用；禁止直接写数据库、改文献编号、覆盖有效文件、绕过已确认的保存路径与下载内容，或将临时路径/DOI/顺序写入生产代码。现有接口不足时单独提出通用接口变更。目录已被 Git 忽略，失败或未完成时保留。本规则不创建真实追加或排序任务。

## Cloudflare 挑战与 CDP 取文（2026-11-25 Wiley 实测，297/343）

Cloudflare 挑战和订阅墙是两件事，先分清再决定要不要重试：

| 现象 | 判定 | 动作 |
| --- | --- | --- |
| `403` 且响应头 `Cf-Mitigated: challenge`；正文含 `Just a moment…` / `请稍候` / `Cloudflare` / Ray ID | 挑战（自动化标记触发），非订阅问题 | 换真浏览器路径，不重试同一请求 |
| `200 text/html`，正文含 `Get access` / `purchase` / "Download PDF" 但无 PDF | 机构无该刊权限 | 记 `no_access`，立即跳过，不再逐篇试 |

取文正解（Wiley 全量实测）：

1. 先起**独立 Edge**（真浏览器，无自动化标记，脚本退出也不关窗）：
   `msedge.exe --remote-debugging-port=<PORT> --user-data-dir=<profiles/车道> --no-first-run --no-default-browser-check --disable-blink-features=AutomationControlled about:blank`
2. Playwright `connect_over_cdp("http://127.0.0.1:<PORT>")` 附着，单标签复用；
3. `context.request.get("<base>/doi/pdfdirect/<DOI>")` 复用该配置目录的 `cf_clearance` 直取 PDF，校验首字节 `%PDF-`（实测 1.5–9.5 s/篇，1–19 MB）；
4. 401/403 或返回 HTML 时按上表判定挑战还是无权限，不盲目重试。

禁用项（都会制造假失败）：

- `launch_persistent_context`：带 `--no-sandbox` 与自动化标记，必触发挑战，且脚本退出即关窗（用户看到「页面自动关闭」即此）。
- `page.expect_download`：CDP 附着的 Edge 不派发 download 事件，只会等到 `Timeout 60000ms exceeded while waiting for event "download"`。
- 拿 Turnstile 当「点击是否送达」的探针：自动化上下文里复选框点了不勾、Ray ID 每轮自动换新，永远 `challenge:true`。要验证点击链路，用自建整屏按钮页（`document.title` 变化即证明送达）。

挑战页窗口在后台时 `SetForegroundWindow` 常被前台锁拒绝（可与 `AttachThreadInput` 一起失败）；改用 `SetWindowPos(hwnd, HWND_TOPMOST=-1, x, y, w, h, SWP_SHOWWINDOW=0x0040)` 置顶，再截图按 `Coordinate Scale` 换算坐标点击。

windows-mcp 桥接要能真正点击，进程必须是 **Medium 完整性级别**；toolchain 自带的 `windows-mcp.exe` 实测为 Low（IL RID 4096），`SetCursorPos` 被输入桌面拒绝并报 `... failed. Check access to the input desktop ...`。改用 uv 基础解释器 `-m windows_mcp serve`，并显式传 `TEMP`/`TMP`（必须可写）、`PYTHONPATH`（含 `win32`、`win32\lib`、`Pythonwin`）、`PATH`（含 `pywin32_system32`）。配置改动后必须重启 DSH，桥接只在启动时注册；健康检查看 `windows_mcp_doctor` 的 `bridged` 是否大于 0。

各出版社的取文行为、失败构成与长路径统计口径见 [出版社行为与取文策略](references/publisher-behavior.md)；该文档只记录实测结果，遇到新行为时先复现再补记，并注明观察日期。

### 验证页自动化：`--desktop-assist`（2026-11-26 落地）

下载器已内置「挑战页交给 windows-mcp 用真鼠标点击」，无需人工介入：

- 模块 `src/doi_harvester/desktop_assist.py`：找到 `challenges.cloudflare.com` 的 iframe → 取 `frame_element().bounding_box()` → 视口原点换算屏幕物理像素（`screenX + outerWidth - innerWidth`，再乘 `devicePixelRatio`）→ 在组件框的 `(0.10, 0.50)` 位置点击（Turnstile 复选框所在）。点击后 `classify_page` 变 `ready`/`authenticated` 即视为通过。
- 接入点：`BrowserPdfDownloader.resolve_challenge`——打开 DOI 后一次、401/403/429 后再一次；每次最多 2 轮、每轮等 1.5 s，仍不过就按原逻辑返回 `challenge_required`。
- 开关：`download`/`jobs` 的 `--desktop-assist {auto,off}`（默认 `auto`：探测到可用的 windows-mcp 才启用）。`DOI_HARVESTER_DESKTOP_ASSIST=off` 整体关闭；`DOI_HARVESTER_DESKTOP_COMMAND`/`_ARGS` 指定启动命令；`WINDOWS_MCP_HOME` 默认 `~/.windows-mcp`。
- 边界：只在**真浏览器窗口**上有意义。Playwright 自建上下文里点击能送达但复选不勾（Cloudflare 判机器人），此时必须回到上面的 CDP 附着 Edge 取文，不要把辅助当万能钥匙。

推进顺序仍是：先按第 3 步 `pdfdirect` 直取；失败再让辅助点验证页；两者都不成就按挑战/无权限结论归档。
