# 第一阶段验收记录

验收日期：2026-09-16

## 结果

| DOI | HTTP | 浏览器兜底 | 最终结果 |
|---|---|---|---|
| `10.1021/acs.chemmater.9b01639` | `403` | CDP 会话内打开 PDF 标签并保存 | 成功 |
| `10.1021/acsami.9b13313` | `403` | CDP 会话内打开 PDF 标签并保存 | 成功 |
| `10.1007/s10853-013-7226-8` | `200` | 无需 | 成功 |

批量验收结果：`3/3` 成功。文件验证：

| DOI | 大小（字节） | 文件头 | SHA-256 |
|---|---:|---|---|
| `10.1021/acs.chemmater.9b01639` | 3,732,434 | `%PDF-` | `86B8D334366EA2A82F51B4B2BF2A1E2DE3C5A7C069F40454DDA639C21BAD1ACE` |
| `10.1021/acsami.9b13313` | 3,956,048 | `%PDF-` | `7265B5C4DB7569FB52001508BB2DD91585B9E390535FBF22FF5315BD59595F56` |
| `10.1007/s10853-013-7226-8` | 570,438 | `%PDF-` | `A73143624E540DA9B9E2D33312129B7EF459694233965F5F1B906D403F190EA4` |

两篇 ACS 的普通 HTTP 请求仍返回 `403`，但通过用户本人操作的专用浏览器会话可以合法访问。ACS 当前页面会在点击后新开内嵌 PDF 标签；下载器会跟踪该标签，在页面自身的会话中读取 PDF，并执行文件头和大小校验。

## 完成 ACS 人工接管验收

```powershell
.\scripts\doi-harvester.ps1 auth `
  --publisher acs `
  --cdp `
  --auth-timeout 600

.\scripts\doi-harvester.ps1 download `
  --doi 10.1021/acs.chemmater.9b01639 `
  --doi 10.1021/acsami.9b13313 `
  --output-dir acceptance-downloads `
  --browser-fallback
```

先在 `auth` 打开的专用浏览器窗口中由用户本人完成安全验证/学校 SSO。CDP 模式会保持该窗口运行，随后下载命令自动连接同一本地会话；若机构没有订阅，将记录 `subscription_required`，不尝试绕过付费墙。
