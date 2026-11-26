# RIS 批量下载 Runbook（2026-10-28 Elsevier 776 篇沉淀）

适用于“给定 RIS/DOI 清单，批量建目录、下载正文与 SI、回写工作簿和整理清单”的任务。
可复用脚本已固化在 `scripts/ris-batch/`（用法见该目录 README）；本文只讲顺序、判据和坑。
所有路径用占位符；不要把这些路径写进 `src`。命令写成单行，避免 PowerShell 反引号续行。

## 0. 开工前先确认环境

先向用户索取一次 `/computer` 授权（Agent 自己不能输入这条命令）：拿到了，验证页全部自助点选通过；拿不到就记下“自助验证不可用”，遇到验证页暂停该篇等人工接管，中途不再追问。同会话已授权就不重复问。

```powershell
curl.exe -s -o NUL -w "doi.org=%{http_code}\n" --max-time 15 https://doi.org/
curl.exe -s -o NUL -w "sd=%{http_code}\n" --max-time 15 https://www.sciencedirect.com/
```

- `doi.org` 走系统代理（常见 `127.0.0.1:7897`，fake-ip）。返回 `000`/超时说明后面所有导航都会
  `net::ERR_TIMED_OUT`，先让用户修代理，不要开下载任务。
- `computer` 工具需要时可用（用户输入 `/computer` 批准），否则验证页只能暂停等人工接管。

## 1. 解析 RIS → 记录与清单

脚本 `scripts/ris-batch/extract-ris.py`：解析、规范 DOI、去重、按规则排除 MDPI/IOP/ECS；
输出 `work/records.json`、`work/DOI清单.csv`、`work/期刊分布.csv`。

## 2. 去重（必须在写 Excel 之前）

```powershell
python scripts\dataset-list-sync.py check --candidates <DOI清单.csv> --list <整理清单.csv> --json > work\dedup-check.json
```

已存在条目：不建目录、不下载，工作簿标 `already_listed`。

## 3. 期刊指标

脚本 `scripts/ris-batch/journal-metrics.py`：ISSN 优先、刊名兜底，匹配中科院分区与 JCR IF（按表头识别分区表与 JCR 表，避免 FQBJCR 与 JCR 文件名互相误选）；
未匹配项如实写“未核实”，并在报告列出待人工核验期刊。

## 4. 合成记录并写工作簿

`scripts/ris-batch/build-records.py` → `literature-records.json`，然后：

```powershell
python scripts\literature-workbook-fallback.py --workbook <论文根>\文献检索汇总.xlsx --records work\literature-records.json --resolved <job>\resolved-records.json
```

## 5. 建目录 + 生成下载清单

`scripts/ris-batch/make-papers.py` 只为待下载条目建目录（已存在则复用），输出 `papers.json`；加 `--split-rank 500` 直接得到 A/B 段。
条目多时按 rank 500 拆成 `papers-seg-a.json` 与 `papers-seg-b.json`，
**顺序**跑两段；不要同时跑两个 Edge 实例（第二个会在 doi.org 上 ERR_ABORTED）。

## 6. 下载（必须后台执行）

```powershell
& .\scripts\doi-harvester.ps1 download --papers-file <papers-seg-a.json> --output-dir <论文根> --report-dir <job>\runA --supplements --browser-fallback --browser-channel msedge --browser-display foreground --challenge-policy pause --delay 1 --batch-size 50
```

- 放后台（`run_in_background` 或 `--detach`）。前台命令被上层超时杀掉后 stdout 关闭，
  下载器写日志抛 `[Errno 22] Invalid argument`，任务标 `failed` 且只处理了一部分条目。
- 每 50 篇把 `runA\batch-report.json` 回写工作簿一次。
- 验证页：先自助通过（AGENTS.md 第 4 条），失败才 pause；恢复用 `jobs resume <job_id>`。
- `ERR_ABORTED` / `ERR_TIMED_OUT`：先查 doi.org 与代理，再考虑重启该 Edge 配置目录。

## 6.1 验证页自助处理（默认动作，不需要用户提醒）

跑批过程中只要出现下列信号，就立刻自己动手，不要先写报告等用户：

- 任务状态 `waiting_for_user` / `needs_attention`，原因 `challenge_required`、`authorization_required_after_10s`、`auth_required`；
- 下载器输出「需要人工验证：DOI …；页面：…」；
- Edge 标签标题含「请稍候」「Just a moment」「Are you a robot」「请验证您是真人」。

SOP（2026-10-28 在 Elsevier 上实测通过）：

1. `computer_list_windows` 找标题「请稍候… 和另外 N 个页面」的 Edge 窗口，记 hwnd；
2. `computer_activate_window {hwnd}` 激活（后台窗口第一下点击会被吃掉）；
3. `computer_screenshot`（可 `region` + `scale` 放大）定位控件：Cloudflare Turnstile 是中部小方框 + 「请验证您是真人」+ CLOUDFLARE，复选框约 20px；
4. 按截图给的 `screen_per_image` 换算坐标 → `computer_click`；点偏重新截图再点，不盲点；
5. `computer_wait` 3–5 秒截图确认：出现 `Brought to you by: <机构名>`、`View PDF` 即通过；
6. 立刻 `jobs resume <job_id>`，继续按检查点轮询。

边界：单次最多 60 秒、最多 2 轮；`computer` 未批准就用一行话请用户输入 `/computer`，不得用脚本伪造点击；
不代填凭据、不破解验证码、不绕过付费墙；需要密码/短信/SSO 或连续 2 轮仍在验证页，才暂停该篇等人工接管。

实测案例：`10.1016/j.nanoen.2026.112115` 触发 Elsevier Turnstile（Reference `a52049be9aab062a`、出口 IP `42.49.109.217`），
点选后页面显示 `Brought to you by: Xiangtan University`，`jobs resume` 之后队列连续成功 22 篇。

## 7. 收尾

1. 合并多任务状态 `scripts/ris-batch/consolidate-report.py`（每 DOI 取最终状态，并按磁盘 `supplements/` 补登记 SI）。
2. 回写工作簿 `literature-workbook-fallback.py --report <job>\final\batch-report.json`。
3. 回写清单 `dataset-list-sync.py apply --report ... --list <整理清单.csv>`（先 `--dry-run`）。
4. 一致性两条线必须相等：工作簿 `下载成功=是` ↔ 磁盘含 PDF 的编号目录数；
   工作簿 `SI是否下载成功=是` ↔ 磁盘含 `supplements` 文件的目录数。
5. 用 `scripts/ris-batch/verify-run.py --manifest-csv` 生成失败清单，交人工补取（同时校验一致性标志）。
6. 单 DOI 探针会建不带规范编号的目录（如 `1_IF未核实_...`），确认无文件后删除。

## 8. 已知接口缺口

- `dataset-list-sync.py apply` 不更新既有行的 `SupplementFileCount`；需要时提通用接口增强
  （如 `--refresh-supplements`），不要手改用户清单。
- 单 DOI 模式用占位指标名建目录，与工作簿编号目录冲突；调试时改用只含目标 DOI 的临时 `papers.json`。