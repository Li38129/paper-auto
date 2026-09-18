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

批次含 Elsevier DOI 时，先检查一次全局配置：

```powershell
& '<项目根目录>\scripts\doi-harvester.ps1' elsevier-setup --show
& '<项目根目录>\scripts\doi-harvester.ps1' elsevier-setup --validate
```

若 API Key 未配置，说明可以运行 `elsevier-setup --set-key --validate` 完成一次性配置；不得索要或代填密钥。Key 缺失不会阻断 OpenAlex、出版社入口和浏览器回退。若验证返回 HTTP 403 及 `Requestor configuration settings insufficient`，表示该 Key 已被程序读取，但尚未获准访问 Elsevier Article Retrieval/ScienceDirect API；校园网网页访问权限不会自动赋予开发者 API 权限。此时优先让用户在 Elsevier Developer Portal 为现有 Key 补充相应 API 权限，不必立即新建 Key；只有旧 Key 无法修改权限或已经失效时才重新申请。

```powershell
& '<项目根目录>\scripts\doi-harvester.ps1' download `
  --papers-file '<任务目录>\papers.json' `
  --output-dir '<论文根目录>' `
  --report-dir '<任务目录>' `
  --browser-fallback `
  --challenge-policy pause `
  --challenge-timeout 600 `
  --delay 1
```

前台可见下载遇到验证页时保持当前工作标签并等待用户，验证完成前不得切换下一篇。任务较多时增加 `--detach`，记录返回的 `job_id`，再用 `jobs status <job_id>` 监控直至终态。后台任务遇验证时进入 `waiting_for_user` 并保留后续条目；完成授权后运行一次 `jobs resume <job_id>`。任务变为 `stalled` 时也只恢复一次；不要重建编号目录或重新编号。

如果 AutoPaper MCP 已注册，可用 `download` 创建相同任务，并用 `job_status` 等待终态。`papers_file`、`output_dir` 和可选 `report_dir` 必须是绝对路径；`report_dir` 只能位于 `<项目根目录>\temp\doi-harvester\jobs`。

默认不要传 `--supplements`。集中报告只能写到任务目录，不得写进论文根目录或编号目录。命令结束后先按 [Excel 汇总规则](workbook.md) 回写报告，再判断重试与清理。

## 状态判断与一次重试

- `subscription_required`：机构没有正文权限，作为最终失败，不重试。
- 普通失败：保留任务目录并报告，不循环重试。
- `challenge_required` 或 `authentication_required`：前台保持当前页面等待；后台进入 `waiting_for_user`，人工授权后只恢复一次。

ACS、Elsevier 或 RSC 授权命令：

```powershell
& '<项目根目录>\scripts\doi-harvester.ps1' auth `
  --publisher acs `
  --doi '<首个失败 DOI>' `
  --cdp `
  --auth-timeout 600
```

Elsevier 或 RSC 将 `--publisher acs` 分别替换为 `--publisher elsevier` 或 `--publisher rsc`。优先验证并使用 Elsevier API；只有 API 未覆盖或失败时才启用浏览器授权。`--cdp` 浏览器必须保持打开，后续下载复用同一进程、工作标签页和持久化配置。

Elsevier 浏览器授权示例：

```powershell
& '<项目根目录>\scripts\doi-harvester.ps1' auth `
  --publisher elsevier `
  --doi '<首个失败 DOI>' `
  --cdp `
  --auth-timeout 600
```

可见浏览器打开后，由用户本人完成安全验证或学校 SSO。不得代填凭据、破解验证或绕过付费墙。授权成功后在同一任务目录创建只包含授权失败项的 `retry-papers.json`，使用与首次下载相同的参数再运行一次，并把重试报告回写 Excel。仍失败即停止。

## 清理与汇报

只有满足以下全部条件时才能删除一次性任务目录：

1. 所有进入下载器的记录均成功；
2. 对应 `article.pdf` 以 `%PDF-` 开头并达到下载器最小尺寸；
3. 最终报告已经成功回写 `文献检索汇总.xlsx`；
4. 任务目录的绝对路径确认位于任务根目录之下。

否则保留 `literature-records.json`、`resolved-records.json`、下载清单和批次报告。最终报告总数、成功数、缓存命中数、缺少 DOI 条目、失败 DOI 与原因、Excel 路径及保留任务路径。论文编号目录中只新增正文和用户明确要求的补充材料。
