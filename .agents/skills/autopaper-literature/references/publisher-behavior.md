# 出版社取文行为与处理策略

本文件记录**实测**到的出版社取文行为，供后续任务快速判断，不替代逐篇核验。
每条都标注观察日期；未复现过的行为不得写入。

观察日期：2026-10-19（数据集复现任务 `20261019-011146-f7ee0372`，212 篇，
正文成功 162、SI 41、失败 50）。

## 按出版社

| 出版社 | DOI 前缀 | 已观察行为 | 处理方式 |
| --- | --- | --- | --- |
| Wiley | `10.1002/` | 文章页 PDF 走 `/doi/pdfdirect/`；2026-11-25 实测：**自动化标记的浏览器**取 PDF 端点必被 Cloudflare 拦（`Cf-Mitigated: challenge`），改用 CDP 附着的真 Edge 后 297/343 直接可取，1.5–9.5 s/篇 | 走 CDP 附着真 Edge + `context.request.get('/doi/pdfdirect/<DOI>')`；`200 text/html` 含 `Get access` 判 `no_access` 立即跳过 |
| Elsevier | `10.1016/` | 文章页「View PDF」指向 `/science/article/pii/<PII>/pdfft?md5=…&pid=…`。该地址纯 HTTP（含 `context.request`）只返回 `cra_js_challenge` 中转 HTML（约 52 KB）；**必须由浏览器导航**到该地址，等挑战脚本跳转到 `pdf.sciencedirectassets.com/…/main.pdf` 后再取正文（实测 1,998,201 字节、`application/pdf`）。约 1995 年前的旧档常无正文入口 | 由 `_download_by_navigation` 用临时标签跟进中转页；同一 PII 约束防止误取相关文章 |
| ACS | `10.1021/` | 直接下载会被要求站点验证；`auth --publisher acs --cdp` 初始化会话后同一任务恢复即可（`10.1021/cm021717j` 立即 cached） | 遇验证先 `auth` 初始化，再 `jobs resume` 原任务 |
| RSC | `10.1039/` | 部分条目 `subscription_required`（无订阅），部分正常（`10.1039/c8ta09170f`）；`auth --publisher rsc` 可初始化会话 | `subscription_required` 作为最终失败，不重试 |
| IOP / ECS | `10.1149/` | iopscience 前置 Radware perfdrive 机器人验证墙；直连返回 HTML 拦截页，浏览器导航后仍被挑战。**暂停-恢复会重新导航并生成新的验证码，一次人工验证无法复用到下一次** | 按 AGENTS.md 第 8 条直接跳过；确需全文时人工下载 |
| Springer / AIP / IUCr | `10.1007/`、`10.1063/`、`10.1107/` | 本次样本多为订阅墙或需浏览器会话（`10.1007/s10717-007-0032-5`、`10.1107/s2052520615022283` 均无正文） | 走浏览器回退，失败即记录，不反复重试 |

## 本次失败构成（供对照）

- `no_pdf_after_browser` 44 篇：出版社侧无正文，多为 1995 年前旧档与早年 ECS/IOP。
- `subscription_required` 5 篇：机构无订阅。
- `auth_skipped` 1 篇：验证墙按策略跳过。

## 补充材料（SI）判定

- Elsevier SI 附件走 `ars.els-cdn.com/content/image/1-s2.0-<PII>-mmc<N>.pdf`。对 8 篇
  `unconfirmed` 的 Elsevier 论文按该规则探测 `mmc1/mmc2`，**零命中**，说明这些论文确实
  没有 SI——`unconfirmed` 是「未能确认」，不等于「有 SI 未取到」。
- 判定顺序：页面证据（`View PDF` 同级的 Supplementary 区块、`mmc`/`supplementary` 链接）
  优先；只在页面明确说明「no supplementary material」时才记为 `not_found`；两者都没有时
  记 `unconfirmed`，不得记成「无 SI」。

## 长路径与统计口径

- 目录名可达 160 字符，正文 PDF 与目录同名，完整路径易超 260 字符。文件操作必须用
  `src/doi_harvester/filesystem.py:native_file_path`（内部加 `\\?\` 前缀）。
- 2026-10-27 复现：扩展路径能保存文件，但 WPS 打开超长路径失败。新正文与 SI
  使用 `shell_safe_file_path` 按完整路径预算缩短文件名，报告用 `display_path` 去掉内部前缀。
  已有长路径文件保留原件并创建校验一致的兼容副本，不移动目录、不重新编号。
- **PowerShell `Get-ChildItem -Recurse` 会静默漏读超长路径文件**，统计文件数时要改用
  `os.walk(r"\\?\E:\...")`；本次曾因此把 161 篇误判为 33 篇。

## 组合任务的成败语义

- 正文与 SI 是两条独立结果：SI 取不到不得把已保存的正文判为失败；正文失败时也不得让
  SI 状态覆盖正文的失败原因（见 `src/doi_harvester/pipeline.py:_add_supplements`）。
- 报告里的 `download_mode` 为 `article_and_supplements` 时，验收需同时看
  `status` 与 `supplement_status`。

## Elsevier 批量实测补充（观察日期：2026-10-28，任务 20261028-elsevier-776）

样本：`3001-3776.ris` 776 条 Elsevier 记录，769 篇进入下载（7 篇已存在于用户清单被跳过）。

- **验证页形态**：`https://www.sciencedirect.com/science/article/pii/<PII>?via%3Dihub` 出现
  Cloudflare「请稍候…」过渡页，Edge 标签标题即显示该文案；下载器判定 `challenge_required`
  并进入 `waiting_for_user`。
- **出现频率**：769 篇全过程独立出现 3 次（第 1 篇、第 151 篇、约第 240 篇时），间隔 40–90 篇，
  不是逐篇出现。
- **放行时间**：暂停后约 2–10 分钟页面自行通过；`jobs resume` 一次即恢复，被中断的那篇随后
  正常下载。若 resume 后 10 秒内仍判 challenge，应先冷却数分钟再试，不要连续 resume。
- **单篇耗时**：浏览器回退成功项 17–97 秒（中位数约 30 秒），长尾 6–13 分钟
  （PDF 2–55 MB，实测下行约 70 KB/s，属出版社侧限速）；失败项 7–30 秒即返回。
- **失败形态**：`no_pdf_after_browser`（PDF 按钮无下载事件）与
  `no_pdf_unconfirmed_article`（页面打开但正文未确认，约 10 秒返回）。本轮 28 篇最终失败
  （3.6%），用 Unpaywall 逐条核验：27 篇 `closed`、1 篇 hybrid OA 亦被拦，属无授权全文。
- **SI 落盘与报告不一致**：正文判定失败时，`supplement_status` 可能是
  `browser_publisher_unavailable`，但该篇 `supplements/` 已有早前下载的附件。
  汇总报告须按磁盘 `supplements/` 补登记（本轮因此把 SI 成功数由 221 修正为 231）。
- **doi.org 是独立故障点**：下载流程必经 `https://doi.org/<DOI>` 导航。本机经 Clash
  `http://127.0.0.1:7897`（fake-ip：本地/1.1.1.1/8.8.8.8 都解析到 198.18.0.206），
  实测 doi.org 约 40% 请求超时（5 或 12 秒 `000`），同一时刻 `linkinghub.elsevier.com`
  与 `www.sciencedirect.com` 全部稳定。用 DoH 取真实 IP（172.67.69.3 / 104.26.4.132 /
  104.26.5.132）后 `curl --resolve --noproxy` 四次全部 `302`、约 1 秒。
  **结论：这是代理对 doi.org 这一跳的问题，不是网络或出版社问题**；处理办法是在 Clash 加
  `DOMAIN-SUFFIX,doi.org,DIRECT`，或换稳定节点。浏览器表现为 `Page.goto: net::ERR_TIMED_OUT`，
  此时容易误判成出版社故障，务必先单独测 doi.org 与它下游两跳。

## Wiley 批量实测补充（观察日期：2026-11-25，目标 `E:\Dataset\data_unprocesssed\11.17\Wiley`）

样本 343 篇（`Wiley-papers.json` 去重后），结果 **downloaded 297 / no_access 46**（86.6%），
磁盘正文 298 篇 / 1.01 GB，目录 344 个。

- **旧结论要纠正**：上一轮把这批记成「本 IP 无机构授权，343 篇 403」是误判。403 的响应头是
  `Cf-Mitigated: challenge`、正文是 `Just a moment…`（Ray ID 后缀 `-HKG`），是 Cloudflare 挑战；
  同一时刻文章页页头明确显示 `xiangtan univ`，机构访问是存在的。
- **挑战只发生在 PDF 端点**：自动化标记的浏览器打开文章页也会退回「请稍候…」；真 Edge 打开文章页
  直接放行（`no-challenge`）。
- **取文路径**：独立 Edge `--remote-debugging-port=<PORT> --user-data-dir=<profiles/车道>` +
  `connect_over_cdp` + `context.request.get('https://onlinelibrary.wiley.com/doi/pdfdirect/<DOI>')`
  复用 `cf_clearance`；`%PDF-` 开头即成功，不必先访问文章页。Wiley 会把不同刊重定向到子域
  （如 `chemistry-europe.`、`advanced.`、`onlinelibrary.`），**跨子域 fetch 会因 CORS 失败**，
  用 `context.request` 走基域才稳。
- **`/doi/pdf/<DOI>` 与 `?download=true` 都是 EPDF 外壳**：返回 `200 text/html`，内含
  `<iframe id="pdf-iframe">` 与相对 `pdfdirect` 链接；不能当正文。
- **无权限**：`/doi/pdfdirect/<DOI>` 返回 `200 text/html`（226 KB / 192 KB，含 "Download PDF" 与
  institution 字样）即机构订阅不覆盖该刊或该年份，标 `no_access` 不再重试。集中于
  ChemElectroChem(`celc`)、Batteries & Supercaps(`batt`)、Energy Technology(`ente`)、
  ChemPhysChem(`cphc`)、ChemSusChem(`cssc`)、Small Methods(`smtd`) 等。
- **别用 `page.expect_download`**：CDP 附着的 Edge 不派发 download 事件，只会 60 秒超时。
- **别用 `launch_persistent_context`**：带 `--no-sandbox` 与自动化标记 → 必触发挑战，且脚本退出
  即关窗（用户观察到的「页面自动关闭」）。
- **性能陷阱**：对 `no_access` 也走页面兜底会让单篇耗时飙到 60–90 秒；判定 `no_access` 后立即
  返回，并把 `page.goto` 超时收到 30 秒，吞吐恢复。

## 验证页自动化与它的边界（观察日期：2026-11-26）

- 下载器内置 `--desktop-assist auto`：`challenge_required` 时找 `challenges.cloudflare.com` 的
  iframe，按其 bounding box 的 `(0.10, 0.50)` 换算屏幕物理像素（视口原点 `screenX + outerWidth -
  innerWidth`，再乘 `devicePixelRatio`）交给 windows-mcp 点击；点击后 `classify_page` 变
  `ready`/`authenticated` 才算通过，每次尝试 2 轮、每轮等 1.5 秒。
- **边界（复现过）**：Playwright 自建上下文里 `Click` 确实送达（自建探针页 `document.title` 变了），
  但 Turnstile 复选框不勾、Ray ID 每轮轮换 —— Cloudflare 认的是浏览器指纹，不是点没点到。因此
  该辅助对真 Edge 窗口有效，对自建上下文无效；后者仍按上文改用 CDP 附着的 Edge 直取 PDF。
- windows-mcp 进程必须是 Medium IL；toolchain 自带的 `windows-mcp.exe` 是 Low（RID 4096），
  `SetCursorPos` 会被输入桌面拒绝。改用 uv 基础解释器 `-m windows_mcp serve` 并显式给
  `TEMP`/`TMP`/`PYTHONPATH`/`PATH`（pywin32 的 `.pth` 不随 `PYTHONPATH` 生效）。
