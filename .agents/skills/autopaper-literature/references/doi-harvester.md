# DOI Harvester 联动规则

## 解析项目位置

按以下顺序定位 AutoPaper 项目根目录，每个候选都必须包含 `scripts/doi-harvester.ps1` 和 `pyproject.toml`：

1. 当前工作目录所属 Git 仓库的根目录；
2. 环境变量 `AUTOPAPER_ROOT` 指向的目录；
3. 当前仓库 Skill 目录向上三级得到的仓库根目录。

全部失败时停止并要求用户提供 AutoPaper 项目绝对路径，不得搜索整块磁盘或猜测路径。

解析成功后使用：

- 启动脚本：`<项目根目录>\scripts\doi-harvester.ps1`
- 任务根目录：`<项目根目录>\temp\doi-harvester\jobs`
- 浏览器配置：`<项目根目录>\temp\doi-harvester\profiles\default`

每次创建唯一任务目录，名称使用安全时间戳和简短主题，例如 `20260917-143015-solid-electrolyte`。任务目录必须解析为任务根目录的子目录。

`temp` 只保存可恢复的运行环境、浏览器会话缓存、交换文件和任务报告。论文根目录、Excel 与下载的 PDF 不得放入该目录。运行入口会保守清理过期成功任务和可再生成缓存；失败任务、未完成 Excel 回写的报告和登录会话数据必须保留。

## 生成下载清单

根据 Excel 脚本输出的 `resolved-records.json`，只把有可靠 DOI 的记录写入 UTF-8 `papers.json`：

```json
{
  "schema_version": 1,
  "papers": [
    {
      "rank": 1,
      "doi": "10.1007/s10853-013-7226-8",
      "title": "Paper title",
      "folder_path": "D:/papers/1论文重点，IC=待查正文"
    }
  ]
}
```

`rank` 使用稳定 `sequence`，`folder_path` 必须是已核验的绝对路径。缺少 DOI 的论文保留在 Excel，但不得写入下载清单。

如果本批次没有任何可靠 DOI，不创建空 `papers.json`，也不调用下载器；验证 Excel 与目录后即可清理一次性任务目录。

## 首次下载

下载默认使用 `--browser-display foreground --browser-channel msedge`：每篇在外部 Edge 专用标签请求对应 DOI。不要求焦点、页面加载成功或已获得内容元数据；真实标签与本次导航记录绑定后可继续缓存及公开入口。浏览器提取链接仍完整核验 DOI；Edge 无法连接或标签关闭时暂停。每次独立验证缓冲最多10秒，再按确认策略处理。只有用户明确选择无界面运行时使用 off/headless；detach 不改变此要求。

下载时无需配置 Elsevier API Key。独立 PDF 或附件请求返回 401、403、429 只记录该请求失败；仅当前可见页面确实显示验证码或登录页时才按已确认策略暂停或跳过。浏览器断连须单独报告。

```powershell
& '<项目根目录>\scripts\doi-harvester.ps1' download `
  --papers-file '<任务目录>\papers.json' `
  --output-dir '<论文根目录>' `
  --report-dir '<任务目录>' `
  --browser-fallback `
  --challenge-policy pause `
  --challenge-timeout 600 `
  --desktop-assist auto `
  --delay 1
```

`--desktop-assist` 控制「验证页交给 windows-mcp 用真鼠标点击」：`auto`（默认）探测到可用 windows-mcp 就启用，`off` 关闭。实现位于 `src/doi_harvester/desktop_assist.py`：定位 `challenges.cloudflare.com` 的 iframe，按其 bounding box 的 `(0.10, 0.50)` 换算屏幕物理像素后点击，点击后页面变 `ready`/`authenticated` 即通过；打开 DOI 后与 401/403/429 后各触发一次，每次最多 2 轮、每轮等 1.5 秒。环境变量 `DOI_HARVESTER_DESKTOP_ASSIST=off` 可整体关闭，`DOI_HARVESTER_DESKTOP_COMMAND`/`_ARGS` 指定启动命令，`WINDOWS_MCP_HOME` 默认 `~/.windows-mcp`。该辅助只在真浏览器窗口上有意义；Playwright 自建上下文里点击送达也不勾选，仍须走 CDP 附着 Edge 取文。

默认使用前台监督模式。人工验证统一按 `AGENTS.md` 4.1 自助通过：缓冲 10 秒 → 仍停验证页则 `computer` 点选 → 通过后 `jobs resume <job_id>`。自助未通过时该篇保留页面与检查点、进入 `waiting_for_user` 交人工接管，不再由用户在启动时预选 skip/pause，也不自动标记 `auth_skipped`。模型每次等待不超过 60 秒，并在状态变化、完成或需要人工操作时及时汇报。任务较多且用户明确要求后台运行时才增加 `--detach`，并同时传入 `--workbook`、`--node-path`、`--node-modules`，由后台任务每批自动回写 Excel。后台任务遇验证时同样先自助处理，未通过再等待人工接管并保留后续条目；完成授权后使用 `jobs resume <job_id>` 前台恢复，需要继续后台运行时显式增加 `--detach`。不得重建编号目录或重新编号。

若出版社 DOI 页明确提供 SI 附件，但自动链接发现未识别，可在核验附件确属该 DOI 后，对该 DOI 传入一个或多个 `--supplement-url DOI=HTTPS_URL`。链接随检查点任务配置保存并在恢复时复用；仍由 DOI Harvester 执行流式下载、文件校验、缓存与 SHA-256 记录。MCP `download` 使用 `supplement_urls={DOI: [HTTPS_URL, ...]}`。仅限 SI 或正文加 SI 模式；不得用此参数绕过访问控制，也不得把未核验的链接当作确认无 SI 的证据。

如果 AutoPaper MCP 已注册，可用 `download` 创建相同任务，并用 `job_status` 等待终态。`papers_file`、`output_dir` 和可选 `report_dir` 必须是绝对路径；`report_dir` 只能位于 `<项目根目录>\temp\doi-harvester\jobs`。

按本次确认的下载内容传参；仅正文不传 SI 参数。集中报告只能写到任务目录，不得写进论文根目录或编号目录。命令结束后先按 [Excel 汇总规则](workbook.md) 回写报告，再判断重试与清理。

需要补充材料时，`--supplements` 同时下载正文和 SI；`--supplements-only` 仅下载 SI，两者互斥。正文及两种 SI 模式均自动创建可恢复任务；`--doi`、`--doi-file`、`--papers-file` 共用任务执行器。所有模式禁止 `--overwrite`。SI 可为 PDF、Office 文档、表格、压缩包、视频或出版社提供的其他原始格式，保存在每篇目录的 `supplements` 子目录。`--results-csv` 指定逐附件结果清单，建议位于论文根目录。固定编号 CSV 可先运行 `scripts/si-csv-batch.py --csv ... --start ... --end ... --output-dir ... --job-dir ... --node ... --node-modules ...`；脚本先将原编号写入 Excel，随后生成 `papers.json` 和目标 CSV。无效或占位 DOI 保留为待核验记录，不进入下载器，也不能阻断其他有效 DOI。SI-only 的报告只更新工作簿“文献清单”的 `SI是否下载成功` 列和更新时间，不改正文下载字段。不同出版社的补充材料链接结构可能不同；链接未能确认、页面访问失败、验证页或解析失败均须保留为未解决状态，只有页面证据明确时才记录“无补充材料”。出版社要求验证时由 Agent 自助点选通过，未通过才暂停该篇等人工接管，再使用 `jobs resume <job_id>` 沿用断点。

## 状态判断与一次重试

- `subscription_required`：机构没有正文权限，作为最终失败，不重试。
- `policy_skipped`：已先尝试 OA，随后按本机期刊权限规则跳过付费入口。
- 普通失败：保留任务目录并报告，不循环重试。
- `challenge_required` 或 `authentication_required`：先按 `AGENTS.md` 4.1 自助通过；未通过则暂停该篇、保留页面与检查点进入 `waiting_for_user` 等人工接管，不自动标记跳过、不自动重试。

ACS、Elsevier 或 RSC 授权命令：

```powershell
& '<项目根目录>\scripts\doi-harvester.ps1' auth `
  --publisher acs `
  --doi '<首个失败 DOI>' `
  --cdp `
  --auth-timeout 600
```

Elsevier 或 RSC 将 `--publisher acs` 分别替换为 `--publisher elsevier` 或 `--publisher rsc`。需要浏览器授权时使用外部 Edge 工作标签。`--cdp` 浏览器必须保持打开，后续下载复用同一进程、工作标签页和持久化配置。

Elsevier 浏览器授权示例：

```powershell
& '<项目根目录>\scripts\doi-harvester.ps1' auth `
  --publisher elsevier `
  --doi '<首个失败 DOI>' `
  --cdp `
  --auth-timeout 600
```

可见浏览器打开后按 4.1 处理：机器人验证复选框由 Agent 自助点选；只有页面要求密码、短信或学校 SSO 凭据时才交给用户本人。不得代填凭据、破解验证或绕过付费墙。授权完成后优先使用 `jobs resume <job_id>` 沿用原任务断点并回写 Excel。审查后需要重试普通失败项时增加 `--retry-failed`；不得对同一失败原因循环重试。

使用 `access list/set/remove/probe` 维护本机机构访问规则。用户确认的未订阅期刊可以长期保存；自动探测默认 30 天过期。期刊规则只跳过付费入口，必须保留可靠 OA 尝试。单篇购买提示、403、验证码或技术失败不得自动升级为整刊规则。

## 清理与汇报

只有满足以下全部条件时才能删除一次性任务目录：

1. 所有进入下载器的记录均成功；
2. 报告中的实际正文 PDF（新下载为 `<论文目录名>.pdf`，旧缓存可能为 `article.pdf`）以 `%PDF-` 开头并达到下载器最小尺寸；
3. 最终报告已经成功回写 `文献检索汇总.xlsx`；
4. 任务目录的绝对路径确认位于任务根目录之下。

否则保留 `literature-records.json`、`resolved-records.json`、下载清单和批次报告。最终报告总数、成功数、缓存命中数、缺少 DOI 条目、失败 DOI 与原因、Excel 路径及保留任务路径。论文编号目录中只新增正文和用户明确要求的补充材料。


每篇开始前和完成后分别持久化状态及结果，报告/CSV/Excel 按 `--batch-size` 同步，默认 100。报告未指定时放在 `temp/doi-harvester/jobs/<job_id>`；Ctrl+C 保留任务并同步报告，恢复时继续使用原任务 ID。浏览器发现附件后先走 HTTP，失败附件才回退浏览器；以最终结果验收，不把回退过程中的失败另计为失败附件。DOI 页面核验使用完整规范化 DOI，相互冲突或缺少证据时不复用页面。


每个新下载目标先在 Skill 对话中确认保存绝对路径与下载内容两项，再进行 Excel 写入、建目录及下载。请求已含设置时也汇总确认一次；同一目标子批次和恢复不重复询问。人工验证不作为确认项：CLI 固定传 `--challenge-policy pause`，MCP `download` 使用 `challenge_policy`（默认 pause），语义是“自助未通过时保留页面与检查点等人工接管”，不是让用户预选跳过。最终交付必须报告自助通过数、待人工接管数与失败数，不得把队列 completed 等同于全部下载成功。该规则不允许跳过浏览器连接或 DOI 核验错误。


正常调用显式指定 `--browser-channel msedge --browser-display foreground`，使用外部 Edge 专用标签，不使用内置浏览器；off/headless 仅用于用户明确选择的高级运行方式。本次目标 DOI 的导航记录与真实工作标签/会话绑定即可允许缓存及公开入口 继续，焦点、页面加载和内容匹配不是独立传输的前置条件。浏览器提取链接仍严格核验 DOI。

每次独立验证先最多缓冲 10 秒，提前消失即继续；仍在验证页时按“自助优先”处理（`AGENTS.md` 4.1）：

启动前置：新任务确认两项设置时同一条消息索取一次 `/computer` 授权（见 `AGENTS.md` 4.2）；授权到位后验证页全部自助通过，中途不再索要。

**触发条件（出现即做，不等用户提醒）**：任务状态 `waiting_for_user`/`needs_attention` 且原因是
`challenge_required`、`authorization_required_after_10s`、`auth_required`；或输出
「需要人工验证：DOI …；页面：…」；或 Edge 标签标题含「请稍候」「Just a moment」「Are you a robot」
「请验证您是真人」。

**SOP**：

1. `computer_list_windows` → 找标题「请稍候… 和另外 N 个页面」或含该 DOI 题名的 Edge 窗口，取 hwnd。
2. `computer_activate_window {hwnd}`（后台窗口第一下点击会被吞，必须先激活）。
3. `computer_screenshot`，必要时用 `region`+`scale` 放大验证区；Elsevier 的 Cloudflare Turnstile 是页面中部
   一个小方框 + 「请验证您是真人」+ CLOUDFLARE 标识（复选框约 20px），Cookie 拦截页则是「请稍候」过渡页。
4. 用截图返回的 `screen_per_image` 换算屏幕坐标后 `computer_click`；点偏就重新截图再点，不盲点。
5. `computer_wait` 3–5 秒再截图确认：页面出现 `Brought to you by: <机构名>`、`View PDF`、`Download full issue` 即通过。
6. 立即 `jobs resume <job_id>` 恢复队列，然后继续按检查点轮询，不要空转等待。
7. 单次最多观察 60 秒、最多 2 轮；`computer` 未批准时用一行话请用户 `/computer`，不得改用脚本伪造点击。
8. 自助失败（无可点控件、需密码/短信/SSO、用户明确拒绝操控、连续 2 轮仍在验证页）才暂停该篇、保留页面与检查点等人工接管；
   pause 保留页面与检查点，人工完成后 `jobs resume`；报告记录 DOI、页面 URL、点选位置、页面状态与是否恢复。

不代填凭据、不破解验证码、不绕过付费墙。

不重复等待同一次检测结果。旧下载等待参数不延长十秒缓冲，独立 auth 保留原等待。临时包装脚本放在 `temp/doi-harvester/task-scripts/<任务或目标ID>/`，遵守项目 AGENTS.md，不修改真实任务数据库或把一次性配置混入生产代码。

## 标签占用

同一目标的分片默认依次运行。下载窗口复用一个工作标签，正文点击或中转最多临时占用一个附加标签，成功、超时和失败后均释放。清理只针对下载器自己打开的页面；保留验证断点与来源未确认的页面。

## 内存与标签回收（2026-11-12 实测）

长时间运行时，除工作标签外的 PDF/SI 页面会持续累积：一次 8 个残留标签的清理把 Edge 合计工作集从 6.9 GB 降到 4.0 GB，进程数从 77 降到 51。因此：

- 定时执行 `scripts/close-stale-tabs.py`（DevTools HTTP `/json/list` + `/json/close/<id>`，不依赖 Playwright），默认每 3–5 分钟或每 50 篇一次；
- 该脚本从 `<profile>/auth-state.json` 读 `cdp_endpoint`、从 `<profile>/work-target.json` 读工作标签 ID 和 DOI，结合 `<profile>/temporary-tabs/` 的登记只清理同一会话内上一 DOI 遗留的临时页；当前 DOI 的页、验证页和未登记的标签均保留；
- 端点只监听 `127.0.0.1` 时直接可用；配置目录没有 `auth-state.json` 或浏览器未启动时脚本跳过，不视为失败；
- 清理后仍超过约 4 GB 时，先结束已完成批次的配置目录浏览器，再用原任务 ID `jobs resume` 恢复断点，不复用残留进程。
