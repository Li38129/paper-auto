# 项目工作规则

1. 复杂任务先制定实施计划。
2. 所有新增注释使用简体中文。
3. 新下载目标必须按 autopaper-literature 确认保存绝对路径与下载内容两项设置。人工验证不作为启动选项：一律由 Agent 自助通过（见 4.1），自助未通过时暂停该篇并保留断点，不再询问用户选择 skip/pause。子批次和原任务恢复沿用设置。
4. 正常下载使用外部 Edge 专用工作标签，不用内置浏览器。无需焦点；标签存在且本次已请求目标 DOI 即可继续缓存/API。页面链接提取仍须完整核验 DOI。

   同一下载目标的子批次默认串行，逐篇复用一个工作标签。正文下载需要查看器或中转页时，一次仅保留一个临时标签，每次尝试结束即清理，包括失败和超时。恢复任务沿用原断点和工作标签，保留等待人工处理的页面；只清理可确认由下载器产生的临时页。

   长时段下载必须定时回收标签：用 `python .agents/skills/autopaper-literature/scripts/close-stale-tabs.py --profiles-dir temp/doi-harvester/profiles` 回收此前 DOI 已登记的下载临时标签（工作标签、验证页、来源不明及当前 DOI 的页面均保留），建议每 3–5 分钟或每 50 篇执行一次；`msedge` 合计工作集超过约 4 GB 或单窗口标签超过 20 个时立即清理，清理后仍偏高则结束已完成批次的配置目录浏览器再按原任务 ID 恢复。新增并行配置目录须先经用户同意。

   **4.1 验证页自助处理（默认动作，不需要用户提醒）。** 出现下列任一信号时，立即执行下面的 SOP，不得先停下来问用户、不得只报告等待：
   - 任务状态变成 `waiting_for_user` / `needs_attention` 且报告里是 `challenge_required`、`authorization_required_after_10s`、`auth_required`；
   - 下载器输出「需要人工验证：DOI …；页面：…」；
   - Edge 工作标签标题含「请稍候」「Just a moment」「Are you a robot」「请验证您是真人」「Verify you are human」。

   SOP（2026-10-28 实测可用的顺序，照做即可）：
   1. `computer_list_windows` 找标题为「请稍候…」或含 DOI 题名的 Edge 窗口，记下 hwnd；
   2. `computer_activate_window {hwnd}` 把它提到前台（后台窗口第一下点击会被吃掉，必须先激活）；
   3. `computer_screenshot`（可带 `region`/`scale` 局部放大）定位验证控件：Elsevier 的 Cloudflare Turnstile 是页面中部一个小方框 + 「请验证您是真人」+ CLOUDFLARE 标识，复选框约 20px；
   4. 用截图返回的 `screen_per_image` 把图片坐标换算成屏幕坐标，`computer_click {coordinate}`；点偏就重新截图再点，不要连续盲点；
   5. `computer_wait` 3–5 秒后截图确认：页面变成正文（会出现 `Brought to you by: <机构名>`、`View PDF`、`Download full issue`）即为通过；
   6. 立即 `jobs resume <job_id>` 恢复队列，然后继续按检查点轮询，不要空转等待。

   约束：单次最多观察 60 秒、最多 2 轮；`computer` 工具未获批准时，用一行话请用户输入 `/computer`（批准后本轮及后续轮次有效），不得改用脚本伪造点击、不得代填凭据、不得破解验证码、不得绕过付费墙；用户明确拒绝操控电脑，或页面要密码/短信/SSO 凭据，或连续 2 轮仍在验证页，则暂停该篇、保留页面与检查点，在报告中记为待人工接管——不再要求用户在任务启动时预选 skip/pause，也不自动标记跳过。
   每次自助尝试都要写进报告：DOI、页面 URL、点选的控件/坐标、点击后页面状态、是否恢复队列。

   **4.2 任务启动时一次性取得 `computer` 授权。** Agent 无法自己输入 `/computer`（那是用户在对话框里的授权动作），所以：
   - 每个新的下载任务（`/autopaper-literature` 或等价请求）在确认两项设置时，**同一条消息里顺带索取一次 `/computer` 授权**，说明用途与“不给会怎样”；
   - 授权到位后，验证页完全由 Agent 自助通过；用户拒绝或不回应时，按“自助验证不可用”继续跑任务，遇到验证页暂停该篇保留断点，**不要在任务中途反复索要**；
   - 授权只对本会话/本轮生效，跨会话不保证；同一会话内已授权就不再重复询问；
   - 需要判断是否仍有效时，用一次无副作用的 `computer_activate_window`（或 `computer_list_windows` + 激活）试探，被拒即视为未授权。
5. 临时追加 DOI、筛选重试条目、生成清单或一次性编排脚本放在 `temp/doi-harvester/task-scripts/<任务或目标ID>/`，不得把临时 DOI、长期目标路径或下载顺序硬编码进 `src`、正式脚本或测试。
6. 每个临时脚本目录附说明：用途、关联任务、输入输出、命令、恢复方法。调用现有 CLI/MCP 或公开任务接口；不直接写数据库、不重排文献编号、不覆盖有效文件、不绕过已确认的保存路径与下载内容。接口不足时先提出通用接口变更，不能加入临时生产分支。
7. 临时脚本不提交 Git；失败或未完成目录不自动清理。生产代码与稳定测试、说明可以纳入版本控制。
8. 检索和推荐文献时排除 MDPI 旗下期刊论文，不将 MDPI 页面作为文献入口；同时排除 IOP 与 ECS 旗下期刊（DOI 前缀 `10.1149/`，域名 `iopscience.iop.org` 与 `ecsdl.org`），不为其检索候选 DOI、不调用下载；固定清单遇到这类条目直接跳过，在清单与工作簿标注「按规则跳过（IOP/ECS）」。用户未明确要求综述时，排除综述、系统综述和元分析，优先选原始研究。用户明确指定的 DOI 或固定清单应保留并说明例外，不擅自删除。
9. 文献清单和 `文献检索汇总.xlsx` 备注每篇期刊的中科院分区、影响因子（IF）及其对应年份或版本和可核验来源。按期刊与指标年份核对；无法可靠核实时标为“未核实”，不得猜测或沿用不明年份的数据。
10. 文献下载任务完成两项确认后，在写入 Excel、创建论文目录或启动下载之前，必须把本次 DOI 与用户确认的已有 DOI 清单（默认成功清单 `E:\Dataset\data_unprocesssed\整理清单_成功.csv` 与同目录 `整理清单_已尝试但未下载成功.xlsx`，可用参数覆盖）做规范化去重：命中成功 DOI、已核实别名或失败 DOI 均跳过下载，在工作簿与最终报告中注明命中的清单。双表模式按实际正文分流，有正文写成功 CSV，已尝试且无正文写失败 XLSX；补充材料单独成功不能充当正文。下载结束（含暂停、失败或部分完成）后，把逐篇结果 upsert 回该清单：保留原列顺序与 UTF-8-sig 编码，`Sequence` 取现有最大值加一并四位补零，不覆盖已有非空 `Status`；写入失败时保留原文件与临时文件并报告路径。统一使用 `scripts/dataset-list-sync.py`，脚本以参数接收清单路径，不把长期目标路径硬编码进 `src`、正式脚本或测试。
11. 文献汇总工作簿的版式唯一来源是 `reference/文献检索汇总模板.xlsx`，用户可随时更新该模板；工作簿必须始终与模板当前的表头一致（列数、列序、列名）。读写脚本在运行时读取模板表头并按字段名映射，不得硬编码列序或列数；模板含表格对象与否都要能写。模板缺少本次操作必需的列时（写记录至少 `序号`、`目录名`、`论文题名`；回写报告另需 `下载成功`、`下载状态`、`失败原因`、`PDF路径` 以及可定位行的 `DOI`），立即报错并说明缺哪一列，不得静默改版式或丢字段。已有工作簿与模板表头不一致时，按表头名称迁移并保留序号、目录名、DOI 与下载状态。

   **4.3 批次排队与配置复用（2026-11-25 复盘新增）。**
   - 排队前按 DOI 出版年预筛：`pre-2000` 条目单独成表、单独一轮跑，失败即记录不再重试；主队列只放 2010 年以后。老库只有摘要页，逐篇重试是纯损耗。
   - 出版社一次定论：同一出版社用 ≥3 条取回路径（CLI、HTTP 直取、页内 fetch、APIRequestContext、`page.goto`）全部返回 401/403 时，整批标 `authentication_required`/`subscription_required` 并停止逐篇重试，原因写入工作簿 `失败原因`。
   - 新并行车道只从已验证配置目录 `robocopy /E` 克隆（先停该配置的浏览器与守护）；不再为 ScienceDirect 使用全新配置目录点击验证页，实测新目录点了也拿不到正文。
   - 并发车道数以 3–4 条为上限：超过后 ScienceDirect 的 Turnstile 触发频率显著上升，净吞吐反而下降。

   **4.5 Cloudflare 挑战与浏览器取文（2026-11-25 Wiley 复盘新增）。**
   - 先分清「挑战」与「无权限」：`403` + `Cf-Mitigated: challenge`，或页内/接口返回正文含 `Just a moment…`、`请稍候`、`Cloudflare` = 挑战（自动化标记触发）；正文含 `Get access`/`purchase`/"Download PDF" 的 `200 text/html` = 真无权限，标 `no_access` 立即跳过，不重试。
   - 取文正解（Wiley 实测 297/343，1.5–9.5 s/篇）：先用 `--remote-debugging-port=<PORT> --user-data-dir=<profiles/车道>` 启动**独立 Edge**（真浏览器，无自动化标记），再用 Playwright `connect_over_cdp` 附着，最后由 `context.request.get('<base>/doi/pdfdirect/<doi>')` 复用 `cf_clearance` 直取 PDF。首字节是 `%PDF-` 才算成功。
   - 不要用 Playwright 的 `launch_persistent_context` 下载：它带 `--no-sandbox` 与自动化标记，会触发挑战，且脚本退出即关窗（用户看到的「页面自动关闭」）。也不要 `page.expect_download`——CDP 附着的 Edge 不派发 download 事件。
   - Playwright 自建浏览器里的 Cloudflare Turnstile 不要硬刚：被判机器人时复选框点了不勾、Ray ID 每次轮换、永远 `challenge:true`。同一目标连续 2 次不过即停并改走 CDP 附着真实 Edge（该路径不触发挑战）。
   - 挑战窗有前台锁，`SetForegroundWindow` 常失败；改用 `SetWindowPos(hwnd, HWND_TOPMOST=-1, x,y,w,h, SWP_SHOWWINDOW=0x0040)` 置顶，再 `Screenshot` 换算坐标（图片像素 × `Coordinate Scale`）。
   - windows-mcp 桥接要能点，进程必须是 **Medium 完整性级别**：toolchain 的 `Scripts\windows-mcp.exe` 实测为 Low IL（RID 4096），`SetCursorPos` 被输入桌面拒绝（`... failed. Check access to the input desktop ...`）。`harness\profiles\web\cordis.patch.yml` 的 `windows-mcp-bridge` 已改为 uv 基础解释器 `-m windows_mcp serve`，并显式给出 `TEMP/TMP=C:\Users\18229\AppData\Roaming\dsh-desktop\harness\tmp`、`PYTHONPATH`（需含 `win32`/`win32\lib`/`Pythonwin`）、`PATH`（需含 `pywin32_system32`）。**改配置后必须重启 DSH**，桥接只在启动时注册。
   - 验证桥接是否活着：`Get-CimInstance Win32_Process` 里应有命令行含 `windows_mcp` 的 `python.exe`；`windows_mcp_doctor` 的 `bridged` 应大于 0。
   - 验证「点击是否真的送达」用自建探针页（`temp/doi-harvester/task-scripts/11-17/click_verify.py`：整屏按钮改 `document.title`），**不要拿 Turnstile 当探针**。
   - **验证页自动化已固化在代码里（2026-11-26）**：`src/doi_harvester/desktop_assist.py` 负责「挑战页 → windows-mcp 真鼠标点击」。流程：`classify_page` 判出 `challenge_required` → 取 `challenges.cloudflare.com` iframe 的 `frame_element().bounding_box()` → 按 `(0.10, 0.50)` 相对位置换成屏幕物理像素（视口原点 `screenX + outerWidth - innerWidth`，再乘 `devicePixelRatio`）→ stdio JSON-RPC 调 windows-mcp 的 `Click`。通过条件：点击后 `classify_page` 变为 `ready`/`authenticated`。接入点是 `BrowserPdfDownloader.resolve_challenge`，在打开 DOI 后与 401/403/429 之后各触发一次，最多 2 轮、每轮等 1.5 s。
   - 开关：`download`/`jobs` 的 `--desktop-assist {auto,off}`（默认 `auto`：能探测到可用 windows-mcp 才启用）；`DOI_HARVESTER_DESKTOP_ASSIST=off` 可整体关闭（`tests/conftest.py` 用它避免单测拉起真实进程）；`DOI_HARVESTER_DESKTOP_COMMAND`/`_ARGS` 可指定启动命令，`WINDOWS_MCP_HOME` 默认 `~/.windows-mcp`。
   - 注意边界：这套辅助只在**真浏览器窗口**上有效；Playwright 自建上下文里即使点击送达也不会勾选（Cloudflare 判机器人），那时仍按上面的 CDP 附着 Edge 取文。
   **4.4 卡死任务（wedge）自助处置。** 任务状态停在 `running`、`pending` 不减，且没有带该任务号的下载进程时，判定为 wedge：不要反复 `jobs resume`（只会得到「任务当前为 running」）。处置顺序为 `jobs cancel <job_id>` → 用 `remaining.py` 按该车道清单重建剩余清单 → 以同一配置目录和同一输出目录起新任务 → 记入日志与报告。`temp/doi-harvester/task-scripts/11-17/wedge_guard.ps1` 已把这条路径脚本化，配合 `job_state.py`（只读数据库）判断状态；查任务清单用 `job_state.py --all`，`jobs list` 默认只列 20 条。
