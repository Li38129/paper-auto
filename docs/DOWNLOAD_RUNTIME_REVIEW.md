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
