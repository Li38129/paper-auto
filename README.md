# DOI Harvester

一个面向科研工作流的 DOI 文献下载器。当前版本聚焦期刊正文 PDF，支持 DOI 规范化、批量去重、多入口回退、PDF 真伪校验、缓存复用和可选审查报告。

> 项目只使用开放获取入口、出版社允许的下载入口或用户本人已有的机构订阅会话，不绕过付费墙或站点安全验证。

## 当前能力

- 输入裸 DOI、DOI URL、重复 `--doi` 参数或 UTF-8 DOI 文本文件；
- 优先尝试 OpenAlex 开放获取副本，再尝试 Crossref TDM/出版社正文入口；
- 支持 Springer 与 ACS 的稳定正文 URL 规则；
- 对 HTTP 200 的 HTML 登录页、验证码页等伪 PDF 做魔数与最小尺寸校验；
- 使用 `.part` 临时文件和原子替换，失败不会留下损坏 PDF；
- 默认只保存正文 `article.pdf`，不在论文目录附加清单；
- 仅在显式指定 `--report-dir` 时集中保存 `batch-report.json`；
- 内置 `$autopaper-literature` 仓库 Skill，持续维护 Excel 文献清单并把正文写入稳定编号目录；
- 可读取 Skill 生成的 `papers.json`，把正文直接写入既有编号目录；
- 可选 Playwright + Chrome/Edge 持久化会话，复用用户本人已有机构权限。

## 安装

项目以 Windows 为正式支持平台，需要 PowerShell、`uv`，以及 Chrome 或 Edge。
统一启动脚本会把虚拟环境和缓存放到工作区的 `tmp\doi-harvester`，不会在项目目录创建 `.venv`：

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

## 在 Codex 中完成检索与下载

仓库内置 `.agents\skills\autopaper-literature`。从本仓库或其子目录启动 Codex 后，
可以直接调用 `$autopaper-literature`，无需把 Skill 复制到个人目录。例如：

```text
$autopaper-literature 检索近五年 LPSC 氧掺杂实验论文，按室温离子电导率排序，
保存到 C:\papers\LPSC，并自动下载可合法获取的正文。
```

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

与 `literature-search-organizer` 联动，把正文保存到检索阶段创建的编号目录：

```powershell
.\scripts\doi-harvester.ps1 download `
  --papers-file "$PWD\tmp\doi-harvester\jobs\<任务ID>\papers.json" `
  --output-dir "C:\papers" `
  --browser-fallback
```

`papers.json` 中每条记录需包含 `rank`、`doi`、`title` 和绝对路径
`folder_path`。交换文件放在 `tmp\doi-harvester\jobs`，不要放入论文数据目录。
默认只下载期刊正文；只有显式传入 `--supplements` 时才会下载补充材料。

ACS/其他需要已有订阅会话的出版社：

```powershell
.\scripts\doi-harvester.ps1 auth `
  --publisher acs `
  --cdp `
  --auth-timeout 600
```

请在打开的可见 Chrome/Edge 中亲自完成安全验证和学校 SSO。程序会检测页面状态，只有 PDF 入口连续三次稳定出现才会确认 `ready`，不会再依赖固定等待时间。

`--cdp` 会让普通 Chrome 在授权命令结束后继续保持打开，下载命令自动读取 `auth-state.json` 中的本地 CDP 地址并连接同一进程。这可以避免 ACS 在浏览器重启后重新触发安全验证。完成批次后可以直接关闭该专用 Chrome 窗口。

授权完成后复用同一浏览器配置下载：

```powershell
.\scripts\doi-harvester.ps1 download `
  --doi-file examples\acceptance-dois.txt `
  --output-dir downloads `
  --browser-fallback
```

浏览器配置会保存在 `tmp\doi-harvester\profiles\default`，后续运行复用 Cookie、站点存储和机构授权状态。
显式传入 `--profile-dir` 仍可覆盖默认位置。程序不会自动填写凭据、处理验证码或绕过订阅限制。请勿把 `--profile-dir` 指向日常 Chrome 的默认用户目录；同一配置目录也不能被两个下载任务同时使用。

启动脚本会在浏览器未运行时保守清理网页缓存、着色器缓存、扩展文件和浏览器模型，保留登录会话所需的 Cookies、Local/Session Storage、IndexedDB、Preferences 与 Local State。也可手动维护：

```powershell
# 只瘦身浏览器配置和过期成功任务
.\scripts\prune-runtime.ps1

# 同时清理可重建的 uv、pytest、Ruff 和 coverage 缓存
.\scripts\prune-runtime.ps1 -IncludePackageCaches
```

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
  --papers-file "$PWD\tmp\doi-harvester\jobs\<任务ID>\papers.json" `
  --output-dir "C:\papers" `
  --report-dir "$PWD\tmp\doi-harvester\jobs\<任务ID>"
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
$env:UV_PROJECT_ENVIRONMENT = "$PWD\tmp\doi-harvester\venv"
uv run --project . --extra dev pytest -m network
```

网络测试不默认启用浏览器兜底，因此受限 ACS 论文需要用上面的 CLI 浏览器命令验收。

完整产品路线见 [docs/PRODUCT_PLAN.md](docs/PRODUCT_PLAN.md)。
