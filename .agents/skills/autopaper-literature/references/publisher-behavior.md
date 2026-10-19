# 出版社取文行为与处理策略

本文件记录**实测**到的出版社取文行为，供后续任务快速判断，不替代逐篇核验。
每条都标注观察日期；未复现过的行为不得写入。

观察日期：2026-10-19（数据集复现任务 `20261019-011146-f7ee0372`，212 篇，
正文成功 162、SI 41、失败 50）。

## 按出版社

| 出版社 | DOI 前缀 | 已观察行为 | 处理方式 |
| --- | --- | --- | --- |
| Wiley | `10.1002/` | 文章页 PDF 走 `/doi/pdfdirect/`，在已登录的持久化会话里通常直接可取（如 `10.1002/anie.200701144` 358 KB、`10.1002/aenm.201700971` 1.7 MB）；个别旧刊（`10.1002/zaac.201000121`）首轮失败、任务重启后以 cached 成功 | 走浏览器回退；单篇偶发失败可重试一次，不连续重试 |
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
- **PowerShell `Get-ChildItem -Recurse` 会静默漏读超长路径文件**，统计文件数时要改用
  `os.walk(r"\\?\E:\...")`；本次曾因此把 161 篇误判为 33 篇。

## 组合任务的成败语义

- 正文与 SI 是两条独立结果：SI 取不到不得把已保存的正文判为失败；正文失败时也不得让
  SI 状态覆盖正文的失败原因（见 `src/doi_harvester/pipeline.py:_add_supplements`）。
- 报告里的 `download_mode` 为 `article_and_supplements` 时，验收需同时看
  `status` 与 `supplement_status`。
