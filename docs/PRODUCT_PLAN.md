# DOI Harvester 产品实现方案

## 1. 产品目标

把“检索到 DOI”到“论文正文落入对应论文目录”做成一个可恢复、可扩展、合法合规的自动化闭环；审查清单仅在用户明确要求时集中生成。

第一阶段只承诺核心能力：给定 DOI 列表，尽可能成功获取对应期刊正文 PDF；若当前机器没有开放获取入口、机构订阅或有效登录会话，应输出明确且可操作的失败原因，而不是把 HTML 页面伪装成成功结果。

## 2. 用户流程

```text
研究主题
  → literature-search-organizer 检索、筛选、去重、排序
  → tmp/doi-harvester/jobs/papers.json（序号、DOI、题名、目标目录）
  → DOI Harvester 下载正文
  → 每篇目录中只保存 article.pdf
  → 可选 temp/batch-report.json 汇总审查信息
```

## 3. 分阶段范围

### 阶段一：正文下载 MVP（本版本）

- DOI URL/裸 DOI 规范化、校验、去重；
- Crossref 元数据与正文链接；
- OpenAlex 开放获取副本；
- ACS、Springer 出版社正文规则；
- HTTP 流式下载、临时文件、PDF 魔数/尺寸校验；
- 本地 PDF 缓存和可选集中批次报告；
- Playwright 持久化浏览器兜底；
- 对安全验证、登录、无订阅权限分别给出状态。

验收标准：

1. 已授权入口下载后，文件以 `%PDF-` 开头且大于 1 KiB；
2. 重跑不重复下载有效缓存；
3. 403、验证码 HTML、登录页不会被当作 PDF；
4. 三个指定 DOI 都有独立目录且目录内只保存正文 PDF；
5. Springer 样例可用纯 HTTP 完成；ACS 样例在用户完成站点验证且机构有订阅时由持久化浏览器完成。

### 阶段二：补充材料（暂停，不在当前范围）

- 出版社专用解析器优先，通用 HTML/JSON-LD 解析器兜底；
- 支持 PDF、ZIP、XLSX、CSV、CIF、视频等类型，不只限 PDF；
- 先读取 `Content-Length` 并设置单文件/单论文上限；
- 记录文件名、MIME、哈希、来源 URL、正文/补充材料关系；
- ACS Figshare、Springer Supplementary Information 作为首批适配器；
- 对 JS 动态页面复用浏览器上下文，不再另开匿名 HTTP 会话。

当前产品和批次验收均不启用这一阶段；默认命令只下载正文。

### 阶段三：与 literature-search-organizer 闭环（已实现）

定义 `papers.json` 交换格式：

```json
{
  "schema_version": 1,
  "papers": [
    {
      "rank": 1,
      "doi": "10.1007/s10853-013-7226-8",
      "title": "Suppression of H2S gas generation...",
      "folder_path": "D:/papers/1H2S抑制，IC=待查正文"
    }
  ]
}
```

联动原则：

- 检索技能负责主题检索、筛选、排序和目标文件夹命名；
- 下载器负责 DOI 再规范化、正文落盘和下载状态；
- `rank + normalized_doi` 是幂等键；
- 下载器默认只向 `folder_path` 新增 `article.pdf`，不移动、不覆盖用户已有文件；
- 无 DOI 记录保留在检索结果，但标记为 `not_downloadable_without_identifier`；
- DOI 变化或版本归并由检索技能确认，下载器不猜测论文身份。

实现方式采用现有 `literature-search-organizer` Skill 联动：当用户授权创建编号目录后，Skill 在项目运行区生成一次性任务文件，自动调用下载器，并依据集中报告完成一次授权重试。检索规则和下载器仍各自独立，可单独测试和升级。

### 阶段四：规模化与可观测性

- SQLite 作业队列与断点续跑；
- 按出版社限速、指数退避和 `Retry-After`；
- 并发只用于不同出版社，同一出版社保持低速；
- SHA-256 去重、PDF 元数据核验、损坏文件隔离；
- 可导出的 CSV/JSON 报告与失败重试清单；
- 针对出版社页面变化的选择器回归测试。

## 4. 模块边界

| 模块 | 职责 |
|---|---|
| `doi.py` | DOI 规范化、校验、Windows 安全目录名 |
| `metadata.py` | Crossref/OpenAlex 元数据和候选入口 |
| `transport.py` | HTTP 流式下载、临时文件、PDF 验证 |
| `browser.py` | 持久化浏览器会话、登录/挑战状态 |
| `pipeline.py` | 候选排序、PDF 缓存与回退 |
| `cli.py` | 批量输入、参数、退出码与可选集中报告 |

## 5. 失败状态设计

- `http_403` / `http_429`：出版社拒绝或限流；
- `not_pdf`：响应不是有效 PDF；
- `challenge_required`：需要用户处理站点安全验证；
- `authentication_required`：需要用户本人完成机构登录；
- `no_pdf_after_browser`：页面可访问但没有可用正文入口，常见于无订阅权限；
- `playwright_not_installed`：未安装浏览器可选依赖；
- `browser_timeout`：页面或下载超时。

状态始终输出到控制台；只有显式指定 `--report-dir` 时才写入集中式 `batch-report.json`，便于审查和人工接管。

## 6. 合规边界

产品不集成盗版来源，不破解 DRM，不自动绕过验证码或付费墙，不把学校账号凭据写入配置。浏览器兜底只复用用户本人合法拥有的会话和机构权限。
