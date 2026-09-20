# DOI Harvester

一个面向科研工作流的 DOI 文献下载器。当前版本聚焦期刊正文 PDF，支持 DOI 规范化、Elsevier API、配置驱动的出版社 Profile、可恢复任务、浏览器会话串行化、MCP 工具和 Excel 文献索引。

> 项目只使用开放获取入口、出版社允许的下载入口或用户本人已有的机构订阅会话，不绕过付费墙或站点安全验证。

## 当前能力

- 输入裸 DOI、DOI URL、重复 `--doi` 参数或 UTF-8 DOI 文本文件；
- 固定执行“有效缓存 → OpenAlex OA → Elsevier API → Crossref/出版社入口 → 浏览器兜底”；
- Elsevier 使用 `view=FULL → MAIN object EID → PDF`，凭据由当前 Windows 用户的 DPAPI 加密；
- 内置 21 家出版社 Profile，并明确区分 API、HTTP、浏览器已验证和仅配置状态；
- SQLite 任务支持后台执行、心跳、stalled 检测、恢复、取消和阶段日志；
- 后台任务保存限速、浏览器和工作簿参数，每 100 条以内建立检查点并自动回写 Excel；
- 本机机构访问策略支持按 ISSN、期刊名和年份跳过付费入口，同时保留 OA 获取；
- 可选 MCP 提供 `search`、`download`、`job_status`、`update_excel` 四个工具；
- 支持 Springer 与 ACS 的稳定正文 URL 规则；
- 对 HTTP 200 的 HTML 登录页、验证码页等伪 PDF 做魔数与最小尺寸校验；
- 使用 `.part` 临时文件和原子替换，失败不会留下损坏 PDF；
- 默认只保存正文 `article.pdf`，不在论文目录附加清单；
- 前台下载仅在指定 `--report-dir` 时保存报告；后台任务自动在任务目录保存报告；
- 内置 `$autopaper-literature` 仓库 Skill，持续维护 Excel 文献清单并把正文写入稳定编号目录；
- 可读取 Skill 生成的 `papers.json`，把正文直接写入既有编号目录；
- 可选 Playwright + Chrome/Edge 持久化会话，复用用户本人已有机构权限。

## 安装

项目以 Windows 为正式支持平台，需要 PowerShell、`uv`，以及 Chrome 或 Edge。
统一启动脚本会把虚拟环境、缓存和批次交换文件放到工作区的 `temp\doi-harvester`，不会在项目根目录创建 `.venv`、`.coverage` 或 `.pytest_cache`：

```powershell
git clone https://github.com/Li38129/paper-auto.git
cd paper-auto
.\scripts\doi-harvester.ps1 --help
```

首次运行会自动安装所需依赖。Windows 会优先选择已安装的 Edge，其次 Chrome。
若两者都不存在，需要按 Playwright 官方方式安装 Chromium。

运行测试和静态检查：

```powershell
.\scripts\check.ps1
```

## 一次配置 Elsevier API

API Key 是当前 Windows 用户的全局配置，一次录入后可供所有 AutoPaper 项目使用。密钥保存到 `%LOCALAPPDATA%\AutoPaper\config.json`，其中只有 DPAPI 密文；命令行、报告和日志只显示末四位掩码。Inst Token 仅在图书馆明确提供时才需要配置。

```powershell
.\scripts\doi-harvester.ps1 elsevier-setup --set-key --show
.\scripts\doi-harvester.ps1 elsevier-setup --validate
```

`--validate` 默认使用 `10.1016/j.watres.2024.121507`，文件只写入系统临时目录并在结束后清理。也可使用 `--set-inst-token`、`--proxy-url URL`、`--clear-key`、`--clear-inst-token` 和 `--clear-proxy`。不提供明文 `--api-key` 参数。

读取优先级为 `ELSEVIER_API_KEY` / `ELS_API_KEY` 环境变量，其次是 DPAPI 本地配置。网络先使用 `trust_env=False` 的 direct 路由，让校园网、学校 VPN 或规则 VPN 决定实际出口；只有配置了专用代理且 direct 遇到连接、超时或授权错误时才尝试代理。项目不保存校园账号，也不处理验证码。

浏览器或 Windows 系统代理不会被 direct 路由自动继承；若规则 VPN 仅提供本机 HTTP
代理，需要使用 `--proxy-url http://127.0.0.1:端口` 显式配置。返回
`AUTHENTICATION_ERROR` 时会标记为 `api_configuration_error`，用于区分开发者应用/API
权限配置问题与论文订阅不足；返回 `NOT_ENTITLED` 才标记为机构订阅问题。

## 在 Codex 中完成检索与下载

仓库内置 `.agents\skills\autopaper-literature`。从本仓库或其子目录启动 Codex 后，
可以直接调用 `$autopaper-literature`，无需把 Skill 复制到个人目录。例如：

```text
$autopaper-literature 检索近五年 LPSC 氧掺杂实验论文，按室温离子电导率排序，
保存到 C:\papers\LPSC，并自动下载可合法获取的正文。
```

调用时如果已经给出论文根目录的绝对路径，Skill 会直接使用并在开始时复述；如果没有给出路径，Skill 会先要求提供一个绝对路径，再开始检索。论文根目录用于长期保存 Excel、编号目录和 PDF，不应指向项目的 `temp`。

Skill 会按顺序完成检索与去重、维护 `C:\papers\LPSC\文献检索汇总.xlsx`、
创建稳定编号目录、调用 DOI Harvester，并把 `downloaded`、`cached` 或失败原因回写工作簿。
重复检索不会改变已有序号和目录名，新论文追加到末尾。缺少 DOI 的论文仍保留在 Excel，
但不会进入下载器。

如果从其他目录调用个人版 `$literature-search-organizer`，可将 `AUTOPAPER_ROOT`
设置为本仓库的绝对路径；仓库版 Skill 会优先从当前 Git 根目录自动定位项目。

## 使用

单篇 DOI：

```powershell
.\scripts\doi-harvester.ps1 download `
  --doi 10.1007/s10853-013-7226-8 `
  --output-dir downloads
```

批量 DOI：

```powershell
.\scripts\doi-harvester.ps1 download `
  --doi-file examples\acceptance-dois.txt `
  --output-dir downloads
```

可恢复后台任务：

```powershell
.\scripts\doi-harvester.ps1 download `
  --papers-file "$PWD\temp\doi-harvester\jobs\<任务ID>\papers.json" `
  --output-dir "C:\papers" `
  --browser-fallback `
  --delay 1.5 `
  --workbook "C:\papers\文献检索汇总.xlsx" `
  --node-path "C:\path\to\node.exe" `
  --node-modules "C:\path\to\node_modules" `
  --detach

.\scripts\doi-harvester.ps1 jobs list
.\scripts\doi-harvester.ps1 jobs status <任务ID>
.\scripts\doi-harvester.ps1 jobs status <任务ID> --compact
.\scripts\doi-harvester.ps1 jobs tail <任务ID>
.\scripts\doi-harvester.ps1 jobs resume <任务ID>
.\scripts\doi-harvester.ps1 jobs cancel <任务ID>
```

`--compact` 只返回任务状态、计数、最近进展、Excel 回写状态和待处理事件，适合每 30 分钟低频检查。后台任务遇验证会停止在当前 DOI，后续论文保持待处理；`jobs resume` 对同一任务只允许一次。

本机机构访问策略：

```powershell
.\scripts\doi-harvester.ps1 access list
.\scripts\doi-harvester.ps1 access set `
  --journal "Nature Energy" `
  --issn 2058-7546 `
  --source user_confirmed
.\scripts\doi-harvester.ps1 access probe `
  --doi 10.1000/example `
  --browser-fallback
```

访问顺序为有效缓存、可靠 OA、访问策略、出版社付费入口。规则保存在 `%LOCALAPPDATA%\AutoPaper\access-policies.json`，可用 `AUTOPAPER_ACCESS_ENVIRONMENT` 区分校园网或学校 VPN。自动探测规则应设置过期时间；单篇失败不会自动变成整刊规则。

运行诊断；默认不访问出版社，只有 `--network` 才执行真实 Elsevier 探针：

```powershell
.\scripts\doi-harvester.ps1 doctor
.\scripts\doi-harvester.ps1 doctor --target-dir "C:\papers"
.\scripts\doi-harvester.ps1 doctor --network
```

## MCP 工具

安装 `agent` 可选依赖后，可把以下 stdio 启动脚本注册为本地 MCP 服务器：

```powershell
.\scripts\autopaper-mcp.ps1 `
  -NodePath '<Codex 提供的 Node.js>' `
  -ArtifactNodeModules '<Codex 提供的 bundled node_modules>'
```

服务器提供：

- `search(query, year_from, year_to, limit)`：OpenAlex 主检索、Crossref 补充并去重；
- `download(papers_file, output_dir, report_dir, browser_fallback, detach)`：创建任务并返回 `job_id`；
- `job_status(job_id)`：返回状态、计数和需要人工处理的 DOI；
- `update_excel(workbook_path, records_path, report_path, resolved_path)`：复用仓库 Skill 的工作簿脚本。

所有 MCP 文件参数必须是绝对路径。PDF 只能写入 `output_dir` 下，报告只能写入 `temp\doi-harvester\jobs`。Excel 工具还需要 Codex 工作区注入 `AUTOPAPER_NODE` 与 `AUTOPAPER_ARTIFACT_NODE_MODULES`；缺失时返回 `artifact_runtime_unavailable`，不会覆盖原文件。

与 `literature-search-organizer` 联动，把正文保存到检索阶段创建的编号目录：

```powershell
.\scripts\doi-harvester.ps1 download `
  --papers-file "$PWD\temp\doi-harvester\jobs\<任务ID>\papers.json" `
  --output-dir "C:\papers" `
  --browser-fallback `
  --challenge-policy pause `
  --challenge-timeout 600
```

`papers.json` 中每条记录需包含 `rank`、`doi`、`title` 和绝对路径
`folder_path`。交换文件放在 `temp\doi-harvester\jobs`，不要放入论文数据目录。
默认只下载期刊正文；只有显式传入 `--supplements` 时才会下载补充材料。

ACS、Elsevier 或 RSC 等需要已有订阅会话的出版社：

```powershell
.\scripts\doi-harvester.ps1 auth `
  --publisher acs `
  --cdp `
  --auth-timeout 600
```

Elsevier 或 RSC 可把 `--publisher acs` 分别替换为 `--publisher elsevier` 或
`--publisher rsc`。前台可见下载遇到
`challenge_required` 或 `authentication_required` 时默认暂停最多 600 秒，不再切换到下一篇；
`--challenge-policy skip` 可恢复原来的跳过行为，`fail-fast` 会停止后续 DOI。无头模式和
`--detach` 后台任务默认不等待，后台任务会停在 `waiting_for_user`，完成授权后用
`jobs resume <任务ID>` 恢复。

请在打开的可见 Chrome/Edge 中亲自完成安全验证和学校 SSO。程序会检测页面状态，只有 PDF 入口连续三次稳定出现才会确认 `ready`，不会再依赖固定等待时间。

`--cdp` 会让普通 Chrome 在授权命令结束后继续保持打开，下载命令自动读取 `auth-state.json` 中的本地 CDP 地址并连接同一进程。下载器复用当前工作标签页和同一配置中的 Cookie、Local Storage 与 IndexedDB，避免不断新建标签或在浏览器重启后重新触发安全验证。完成批次后可以直接关闭该专用 Chrome 窗口。

授权完成后复用同一浏览器配置下载：

```powershell
.\scripts\doi-harvester.ps1 download `
  --doi-file examples\acceptance-dois.txt `
  --output-dir downloads `
  --browser-fallback
```

浏览器配置会保存在 `temp\doi-harvester\profiles\default`，后续运行复用 Cookie、站点存储和机构授权状态。
显式传入 `--profile-dir` 仍可覆盖默认位置。程序不会自动填写凭据、处理验证码或绕过订阅限制。请勿把 `--profile-dir` 指向日常 Chrome 的默认用户目录；同一配置目录也不能被两个下载任务同时使用。

启动脚本会在浏览器未运行时保守清理网页缓存、着色器缓存、扩展文件和浏览器模型，保留登录会话所需的 Cookies、Local/Session Storage、IndexedDB、Preferences 与 Local State。也可手动维护：

```powershell
# 只瘦身浏览器配置和过期成功任务
.\scripts\prune-runtime.ps1

# 同时清理可重建的 uv、pytest、Ruff 和 coverage 缓存
.\scripts\prune-runtime.ps1 -IncludePackageCaches

# 注册每周日 03:00 执行的 Windows 定时清理任务
.\scripts\register-temp-cleanup.ps1
```

定时清理默认删除 30 天前且已全部成功的下载任务，以及 `temp\work` 中 7 天前的可丢弃构建产物。失败任务、Excel 尚未成功回写的批次报告、浏览器登录会话和当前虚拟环境不会被定时删除。可以用 `-RetentionDays` 和 `-At HH:mm` 调整保留期与执行时间；包管理和测试缓存只通过上面的手动命令清理，避免与正在运行的任务冲突。

可能的授权状态：

- `challenge_required`：仍停留在站点安全验证页；
- `authentication_required`：仍停留在学校 SSO/登录页；
- `subscription_required`：已经进入文章页，但机构没有正文访问权限；
- `authenticated`：已进入正常页面，但尚未发现 PDF 入口；
- `ready`：PDF 入口连续稳定出现，可以下载。

建议提供联系邮箱，以进入 Crossref/OpenAlex 礼貌池：

```powershell
.\scripts\doi-harvester.ps1 download `
  --doi-file dois.txt `
  --email your-email@example.edu
```

## 输出结构

```text
downloads/
└── 10.1007_s10853-013-7226-8/
    └── article.pdf
```

默认不会生成 `manifest.json` 或 `batch-report.json`。如需审查下载来源和失败原因，显式指定报告目录：

```powershell
.\scripts\doi-harvester.ps1 download `
  --papers-file "$PWD\temp\doi-harvester\jobs\<任务ID>\papers.json" `
  --output-dir "C:\papers" `
  --report-dir "$PWD\temp\doi-harvester\jobs\<任务ID>"
```

此时只会在任务目录写入集中报告，论文编号目录仍只保存正文。Skill 联动仅在全部下载成功、PDF 校验通过且报告已写回 Excel 后删除任务目录；若有失败则保留，供定向重试和审查。

授权配置目录中还会生成 `auth-state.json`，只记录检查时间、状态和页面地址，不保存或导出账号、密码与 Cookie 内容。

## 测试

单元测试与覆盖率：

```powershell
.\scripts\check.ps1
```

真实网络验收默认跳过。启用后会访问出版社：

```powershell
$env:DOI_HARVESTER_NETWORK_TESTS = "1"
$env:UV_PROJECT_ENVIRONMENT = "$PWD\temp\doi-harvester\venv"
uv run --project . --extra dev pytest -m network
```

网络测试不默认启用浏览器兜底，因此受限 ACS 论文需要用上面的 CLI 浏览器命令验收。

完整产品路线见 [docs/PRODUCT_PLAN.md](docs/PRODUCT_PLAN.md)。

## 项目目录约定

```text
.agents/skills/     Codex 仓库级技能及 Excel 模板
docs/               产品与验收文档
examples/           可复用的最小输入示例
scripts/            启动、检查和临时目录维护脚本
src/                Python 包源码
tests/              自动化测试
temp/               被 Git 忽略的运行环境、缓存、交换文件和临时构建产物
```

长期成果只放在用户指定的论文根目录；项目根目录只保留源码和维护文件。升级前版本使用的 `tmp\doi-harvester` 会在新版启动器首次运行且 `temp\doi-harvester` 尚不存在时自动迁移。旧虚拟环境因包含绝对路径会被转存到 `temp\work` 等待清理，随后由 `uv` 在新位置自动重建；任务报告和浏览器会话会继续保留。
