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

```powershell
& '<项目根目录>\scripts\doi-harvester.ps1' download `
  --papers-file '<任务目录>\papers.json' `
  --output-dir '<论文根目录>' `
  --report-dir '<任务目录>' `
  --browser-fallback `
  --delay 1
```

默认不要传 `--supplements`。集中报告只能写到任务目录，不得写进论文根目录或编号目录。命令结束后先按 [Excel 汇总规则](workbook.md) 回写报告，再判断重试与清理。

## 状态判断与一次重试

- `subscription_required`：机构没有正文权限，作为最终失败，不重试。
- 普通失败：保留任务目录并报告，不循环重试。
- `challenge_required` 或 `authentication_required`：等待首次批次完成其他论文后，只对这些失败项进行一次人工授权和一次重试。

ACS 授权命令：

```powershell
& '<项目根目录>\scripts\doi-harvester.ps1' auth `
  --publisher acs `
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
