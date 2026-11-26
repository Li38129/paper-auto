# 下载运行复盘与维护说明

## 人机验证为何在首轮后减少

下载器授权和正文下载共用专用浏览器配置目录，并优先连接仍在运行的 CDP 浏览器。人工完成验证后，站点 Cookie、Local Storage 和机构登录状态继续保留；后续论文复用同一进程和工作标签页，因此通常不会逐篇重新验证。站点仍可按会话时限或风险判断重新发起验证。

`auth-state.json` 分别记录安全验证、机构登录和当前文章访问状态；`session-state.json` 记录连接方式、浏览器是否重启、脱敏后的页面地址，以及相关 Cookie 的名称、域和到期时间。两个文件都不保存 Cookie 值、密码或带授权参数的 URL。

## 前台监督与可选后台任务

批量 `papers.json` 默认使用前台可恢复任务。模型跟随命令输出监督当前 DOI、处理结果和计数，每次等待不超过 60 秒；显式传入 `download --detach` 时才交给后台 Broker。两种模式都在 SQLite 中保存每条论文的检查点，并按最多 100 条回写一次报告和 Excel。

遇到验证或登录时，普通 Chrome/Edge 与当前标签保持打开，任务进入 `waiting_for_user` 并停止处理后续 DOI。人工授权后使用 `jobs resume` 前台恢复，需要后台运行时增加 `--detach`。每次新的独立验证都可以恢复；正在排队、运行或回写 Excel 的任务会拒绝重复启动。Excel 被占用或运行时依赖缺失时，任务保留为 `needs_attention`；恢复时先补写已有报告，再继续下载。

## 期刊访问策略

机构访问规则保存在当前用户的 `%LOCALAPPDATA%\AutoPaper\access-policies.json`，也可用 `AUTOPAPER_ACCESS_POLICY_PATH` 覆盖。`AUTOPAPER_ACCESS_ENVIRONMENT` 用于区分校园网、学校 VPN 等访问环境。

规则只跳过付费入口。执行顺序为有效缓存、可靠 OA、访问规则、出版社/API/浏览器入口。用户确认的规则长期有效；自动探测规则默认设置 30 天过期时间。单篇 403、验证码、超时或购买提示只能形成论文级证据，不能自动封禁整刊。

常用命令：

```powershell
.\scripts\doi-harvester.ps1 access list
.\scripts\doi-harvester.ps1 access set --journal "Journal name" --issn 1234-5678
.\scripts\doi-harvester.ps1 access remove --journal "Journal name"
.\scripts\doi-harvester.ps1 access probe --doi 10.1000/example --browser-fallback
```

## 权限探测口径

材料与能源期刊首轮覆盖 RSC、ACS、Elsevier、Wiley 和 Springer Nature。每刊最多使用三类样本：近两年非 OA、较早年份非 OA、OA 对照。结果分为全文可访问、OA 可访问、文章无权限、需要登录、需要验证和技术失败。只有用户确认或明确的机构订阅范围证据才能写成整刊规则。
## 2026-10-28 Elsevier 776 篇批量复盘（任务 20261028-elsevier-776）

### 验证页处理（已固化到 AGENTS.md 第 4 条）

- 本轮出现 3 次 ScienceDirect「请稍候…」验证页，当时按 pause 策略暂停后等页面自行放行再
  `jobs resume`，三次都成功；但每次停摆 2–10 分钟。
- 固化后的顺序：先由 Agent 自己操控电脑过验证（`computer` 工具需用户输入 `/computer` 批准），
  单次最多观察 60 秒、最多 2 轮；自查不通过（无可点控件、需凭据、连续 2 轮仍在验证页）才暂停。

### 运行方式

- 下载命令必须放后台执行（`run_in_background` 或 `--detach`）。前台命令被上层超时杀掉后
  stdout 关闭，下载器写日志即抛 `[Errno 22] Invalid argument`，任务标 `failed` 且只处理了一部分条目。
- 本机 shell 差异：`ops_bash`（msys/git-bash）无法写文件（`mkdir`/`touch` 均 Permission denied），
  `pwsh` 正常。所有写盘操作走 `pwsh`，并注意嵌套引号——复杂查询写成脚本文件再执行。
- 双 Edge 实例并行不可行：复制 `profiles/default` 为 `profiles/el2` 后同时跑两个任务，
  第二个实例反复在 `https://doi.org/<DOI>` 抛出 `Page.goto: net::ERR_ABORTED`。
  结论保持单任务串行；长任务用分批清单（如 rank<500 / ≥500）顺序推进。

### 状态一致性

- 同一个 DOI 可能出现在多个任务的报告里（取消、重试）。逐份报告回写会让状态倒退，
  必须先把多任务 `job_items` 合并成一份“每 DOI 最终状态”报告再回写工作簿与清单。
- 正文失败不代表 SI 没落盘：汇总时要按磁盘 `supplements/` 目录补登记 SI 状态。
- `scripts/dataset-list-sync.py apply` 只在**新插入**行写 `SupplementFileCount`，
  不更新既有行；需要修正时只能提出通用接口增强，不要手工改清单。
- 一致性验收要两条线对齐：工作簿 `下载成功/SI是否下载成功` 计数 与 磁盘
  “有 PDF 的目录数 / 有 supplements 的目录数” 必须相等。
- 单篇探针（`--doi`）会在输出目录建一个不带编号规范的目录（如 `1_IF未核实_<刊名>_...`），
  探针结束后确认其为空再删除，避免污染编号目录区。
## 验证页自助处理的落地记录（2026-10-29）

规则写在 `AGENTS.md` 4.1、`SKILL.md`「验证缓冲与临时脚本」与 `references/doi-harvester.md`；
这里只记实测事实，供判断“为什么这样做有效”。

- 触发形态：Elsevier 先用 `Are you a robot?` 页（Cloudflare Turnstile，复选框文案「请验证您是真人」），
  Edge 标签标题停留在「请稍候…」；页面给出 Reference number、IP Address（本次 `42.49.109.217`）、
  User Agent、Timestamp。任务侧状态是 `challenge_required` → `waiting_for_user`。
- 该拦截是**出口 IP 级**：不挂 cookie 的 `curl` 请求同一文章也收到同一张 robot 页（HTTP 403、1.2 MB），
  说明与登录态无关；本机经代理的出口 IP 与 ipify 显示的 IP 不同，是按域名分流到不同节点造成的。
- 人工/自助点击整条路径：`computer_activate_window` 激活窗口 → 截图定位（该窗口 1251×1413，
  页面中部复选框约 20px）→ `computer_click` → 3–5 秒后页面变成正文并出现
  `Brought to you by: Xiangtan University`、`View PDF`。
- 点击通过后 `jobs resume` 一次即可，队列连续处理 22 篇成功，未再复现验证页；
  说明 Turnstile 通过后写入的 Cookie 复用有效，不必逐篇点。
- 反面经验：只 `jobs resume` 不点击，会在 10 秒缓冲后再次 `challenge_required` 并回到 `waiting_for_user`，
  白耗一轮；`Page.reload` 也无法绕过该拦截。
