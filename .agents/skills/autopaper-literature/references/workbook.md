# Excel 汇总规则

## 固定输出与表结构

论文根目录中的工作簿固定命名为 `文献检索汇总.xlsx`。模板位于本 Skill 的 `assets/literature-index-template.xlsx`，只包含工作表 `文献清单` 和表格 `LiteratureTable`。

固定字段依次为：

```text
序号
目录名
论文题名
DOI
年份
期刊
文献类型
体系/最佳组成
关注指标
检索主题
文献入口
下载成功
下载状态
失败原因
PDF路径
下载来源
首次收录时间
最后更新时间
```

已有工作簿必须同时具备上述工作表、表格和完整字段，否则视为不兼容。不得覆盖或自动修复不兼容文件。

## 准备检索记录

在本次 DOI Harvester 任务目录创建 UTF-8 `literature-records.json`，不得把交换 JSON 写入论文根目录：

```json
{
  "schema_version": 1,
  "topic": "研究主题",
  "records": [
    {
      "rank": 1,
      "folder_label": "In–O 共掺 LPSC，IC=2.67 mS·cm⁻¹",
      "title": "Paper title",
      "doi": "10.1000/example",
      "year": 2026,
      "journal": "Journal name",
      "paper_type": "实验",
      "system": "In–O 共掺 LPSC",
      "metric": "IC=2.67 mS·cm⁻¹",
      "source_url": "https://doi.org/10.1000/example"
    }
  ]
}
```

`rank` 必须是本次最终排序中的正整数且不可重复。`folder_label` 不含序号；使用简短“体系或组成 + 关注指标”，并将 Windows 非法字符替换为安全字符。缺少可靠 DOI 时使用空字符串，不得编造。

## 调用工作簿脚本

先通过工作区依赖加载能力取得 Node.js 可执行文件和 bundled `node_modules` 绝对路径。然后执行：

```powershell
& '<Node.js>' '<项目根目录>\.agents\skills\autopaper-literature\scripts\literature-workbook.mjs' `
  --node-modules '<bundled node_modules>' `
  --workbook '<论文根目录>\文献检索汇总.xlsx' `
  --records '<任务目录>\literature-records.json' `
  --resolved '<任务目录>\resolved-records.json'
```

脚本按规范化 DOI 去重；无 DOI 时按规范化题名去重。已有记录原位更新并保留序号、目录名和首次收录时间，新记录按 `rank` 顺序追加。写入成功后读取 `resolved-records.json`，以其中的 `sequence`、`folder_name`、`folder_path`、`title` 和 `doi` 为唯一目录与下载任务来源。

有 DOI 的新记录初始状态为 `未尝试 / pending`；无 DOI 的记录为 `不适用 / missing_doi`。输入或模板校验失败时脚本以非零状态退出，必须停止后续目录创建和下载。

## 回写下载报告

每次初始下载或定向重试结束后，立即执行：

```powershell
& '<Node.js>' '<项目根目录>\.agents\skills\autopaper-literature\scripts\literature-workbook.mjs' `
  --node-modules '<bundled node_modules>' `
  --workbook '<论文根目录>\文献检索汇总.xlsx' `
  --report '<任务目录>\batch-report.json'
```

重试报告也用相同方式回写，只更新报告中出现的 DOI：

- `success=true` 且状态为 `downloaded` 或 `cached`：`下载成功=是`；
- 其他结果：`下载成功=否`，保留最终状态和最后一次尝试原因；
- `PDF路径`、`下载来源` 和 `最后更新时间`来自报告与当前写回时间。

工作簿采用同目录临时文件和替换方式保存。Excel 被占用或替换失败时原文件必须保持不变；保留任务报告，关闭 Excel 后可用同一命令再次回写。
