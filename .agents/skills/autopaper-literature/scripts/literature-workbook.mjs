import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const HEADERS = [
  "序号",
  "目录名",
  "论文题名",
  "DOI",
  "年份",
  "期刊",
  "文献类型",
  "体系/最佳组成",
  "关注指标",
  "检索主题",
  "文献入口",
  "下载成功",
  "下载状态",
  "失败原因",
  "PDF路径",
  "下载来源",
  "首次收录时间",
  "最后更新时间",
];

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_TEMPLATE = path.resolve(
  SCRIPT_DIR,
  "..",
  "assets",
  "literature-index-template.xlsx",
);

function parseArgs(argv) {
  const values = new Map();
  for (let index = 0; index < argv.length; index += 1) {
    const item = argv[index];
    if (!item.startsWith("--")) {
      throw new Error(`无法识别的参数：${item}`);
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`参数 ${item} 缺少值。`);
    }
    values.set(item.slice(2), value);
    index += 1;
  }
  return values;
}

function requiredArg(args, name) {
  const value = args.get(name);
  if (!value) {
    throw new Error(`缺少必需参数 --${name}。`);
  }
  return path.resolve(value);
}

function normalizeDoi(value) {
  let text = String(value ?? "").trim().toLowerCase();
  text = text.replace(/^doi:\s*/i, "");
  text = text.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "");
  text = text.split(/[?#]/, 1)[0].trim();
  return text;
}

function normalizeTitle(value) {
  return String(value ?? "")
    .replace(/<[^>]*>/g, " ")
    .toLowerCase()
    .replace(/[\p{P}\p{S}\s]+/gu, "")
    .trim();
}

function nowText() {
  return new Date().toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "Z");
}

function cleanText(value) {
  return String(value ?? "").trim();
}

function safeFolderLabel(value) {
  const cleaned = cleanText(value)
    .replace(/[<>:\"/\\|?*]/g, "-")
    .replace(/\s+/g, " ")
    .replace(/[. ]+$/g, "")
    .trim();
  if (!cleaned) {
    throw new Error("folder_label 清理后为空。请提供可辨认的论文目录标签。");
  }
  return Array.from(cleaned).slice(0, 96).join("");
}

function mergeTopic(existing, current) {
  const items = `${cleanText(existing)}；${cleanText(current)}`
    .split("；")
    .map((item) => item.trim())
    .filter(Boolean);
  return [...new Set(items)].join("；");
}

function isBlankRow(row) {
  return row.every((value) => value === null || value === undefined || String(value).trim() === "");
}

function rowToObject(row) {
  return Object.fromEntries(HEADERS.map((header, index) => [header, row[index] ?? ""]));
}

function objectToRow(record) {
  return HEADERS.map((header) => record[header] ?? "");
}

function validateExistingRows(rows) {
  const doiKeys = new Set();
  const titleKeys = new Map();
  for (const row of rows) {
    const doi = normalizeDoi(row.DOI);
    const title = normalizeTitle(row["论文题名"]);
    if (doi) {
      if (doiKeys.has(doi)) {
        throw new Error(`工作簿中存在重复 DOI：${doi}`);
      }
      doiKeys.add(doi);
    }
    if (title) {
      const previousDoi = titleKeys.get(title);
      if (previousDoi !== undefined && (!doi || !previousDoi)) {
        throw new Error(`工作簿中存在重复题名：${row["论文题名"]}`);
      }
      titleKeys.set(title, doi);
    }
  }
}

async function readJson(jsonPath) {
  const text = await fs.readFile(jsonPath, "utf8");
  return JSON.parse(text.replace(/^\uFEFF/, ""));
}

function validateRecordsPayload(payload) {
  if (!payload || payload.schema_version !== 1 || !Array.isArray(payload.records)) {
    throw new Error("literature-records.json 必须使用 schema_version=1 且包含 records 数组。");
  }
  if (!payload.records.length) {
    throw new Error("literature-records.json 的 records 不能为空。");
  }
  const ranks = new Set();
  const dois = new Set();
  const titles = new Map();
  const normalized = payload.records.map((raw, index) => {
    const rank = raw.rank;
    if (!Number.isInteger(rank) || rank < 1 || ranks.has(rank)) {
      throw new Error(`第 ${index + 1} 条记录的 rank 必须是唯一正整数。`);
    }
    ranks.add(rank);
    const title = cleanText(raw.title);
    if (!title) {
      throw new Error(`第 ${index + 1} 条记录缺少 title。`);
    }
    const doi = normalizeDoi(raw.doi);
    const titleKey = normalizeTitle(title);
    if (doi && dois.has(doi)) {
      throw new Error(`本次记录中存在重复 DOI：${doi}`);
    }
    const previousDoi = titles.get(titleKey);
    if (previousDoi !== undefined && (!doi || !previousDoi)) {
      throw new Error(`本次记录中存在重复题名：${title}`);
    }
    if (doi) {
      dois.add(doi);
    }
    titles.set(titleKey, doi);
    return {
      rank,
      folderLabel: safeFolderLabel(raw.folder_label),
      title,
      doi,
      year: Number.isInteger(raw.year) ? raw.year : "",
      journal: cleanText(raw.journal),
      paperType: cleanText(raw.paper_type),
      system: cleanText(raw.system),
      metric: cleanText(raw.metric),
      sourceUrl: cleanText(raw.source_url),
    };
  });
  return {
    topic: cleanText(payload.topic),
    records: normalized.sort((left, right) => left.rank - right.rank),
  };
}

function validateReportPayload(payload) {
  if (!payload || payload.schema_version !== 1 || !Array.isArray(payload.results)) {
    throw new Error("batch-report.json 必须使用 schema_version=1 且包含 results 数组。");
  }
  return payload.results;
}

function lastFailureReason(result) {
  const attempts = Array.isArray(result.attempts) ? result.attempts : [];
  for (let index = attempts.length - 1; index >= 0; index -= 1) {
    const reason = cleanText(attempts[index]?.reason);
    if (reason && reason !== "downloaded") {
      return reason;
    }
  }
  return cleanText(result.status) || "failed";
}

async function loadArtifactTool(nodeModulesPath) {
  const base = path.join(path.dirname(nodeModulesPath), "package.json");
  const require = createRequire(base);
  return require("@oai/artifact-tool");
}

async function loadWorkbook(artifact, workbookPath, templatePath) {
  const sourcePath = await fs
    .access(workbookPath)
    .then(() => workbookPath)
    .catch(() => templatePath);
  const input = await artifact.FileBlob.load(sourcePath);
  return artifact.SpreadsheetFile.importXlsx(input);
}

function resolveTable(workbook) {
  let sheet;
  try {
    sheet = workbook.worksheets.getItem("文献清单");
  } catch {
    throw new Error("工作簿缺少工作表“文献清单”。");
  }
  const table = sheet.tables.items.find((item) => item.name === "LiteratureTable");
  if (!table) {
    throw new Error("工作簿缺少表格 LiteratureTable。");
  }
  const actualHeaders = table.getHeaderRowRange().values[0].map((value) => cleanText(value));
  if (actualHeaders.length !== HEADERS.length || actualHeaders.some((value, index) => value !== HEADERS[index])) {
    throw new Error("LiteratureTable 字段与 AutoPaper 模板不兼容，原文件未修改。");
  }
  return { sheet, table };
}

function readTableRows(sheet) {
  const values = sheet.getRange("A5:R5000").values ?? [];
  return values.filter((row) => !isBlankRow(row)).map(rowToObject);
}

function updateRowMap(rows) {
  const byDoi = new Map();
  const byTitle = new Map();
  rows.forEach((row, index) => {
    const doi = normalizeDoi(row.DOI);
    const title = normalizeTitle(row["论文题名"]);
    if (doi) {
      byDoi.set(doi, index);
    }
    if (title) {
      byTitle.set(title, byTitle.has(title) ? null : index);
    }
  });
  return { byDoi, byTitle };
}

function upsertRecords(rows, payload, workbookPath, folderRoot) {
  validateExistingRows(rows);
  const timestamp = nowText();
  let maxSequence = rows.reduce((maximum, row) => {
    const value = Number(row["序号"]);
    return Number.isInteger(value) && value > maximum ? value : maximum;
  }, 0);
  const resolved = [];

  for (const record of payload.records) {
    let indexes = updateRowMap(rows);
    let rowIndex = record.doi ? indexes.byDoi.get(record.doi) : undefined;
    if (rowIndex === undefined) {
      const titleIndex = indexes.byTitle.get(normalizeTitle(record.title));
      if (titleIndex === null && !record.doi) {
        throw new Error(`缺少 DOI 且题名无法唯一匹配：${record.title}`);
      }
      rowIndex = typeof titleIndex === "number" ? titleIndex : undefined;
    }

    let row;
    if (rowIndex === undefined) {
      maxSequence += 1;
      row = Object.fromEntries(HEADERS.map((header) => [header, ""]));
      row["序号"] = maxSequence;
      row["目录名"] = `${maxSequence}${record.folderLabel}`;
      row["首次收录时间"] = timestamp;
      rows.push(row);
      rowIndex = rows.length - 1;
    } else {
      row = rows[rowIndex];
    }

    row["论文题名"] = record.title;
    row.DOI = record.doi || normalizeDoi(row.DOI);
    row["年份"] = record.year || row["年份"] || "";
    row["期刊"] = record.journal || row["期刊"] || "";
    row["文献类型"] = record.paperType || row["文献类型"] || "";
    row["体系/最佳组成"] = record.system || row["体系/最佳组成"] || "";
    row["关注指标"] = record.metric || row["关注指标"] || "";
    row["检索主题"] = mergeTopic(row["检索主题"], payload.topic);
    row["文献入口"] = record.sourceUrl || row["文献入口"] || "";
    row["最后更新时间"] = timestamp;
    row["PDF路径"] = "";
    row["下载来源"] = "";
    if (row.DOI) {
      row["下载成功"] = "未尝试";
      row["下载状态"] = "pending";
      row["失败原因"] = "";
    } else {
      row["下载成功"] = "不适用";
      row["下载状态"] = "missing_doi";
      row["失败原因"] = "缺少可靠 DOI，未进入下载器";
    }

    const folderName = cleanText(row["目录名"]);
    resolved.push({
      rank: record.rank,
      sequence: Number(row["序号"]),
      folder_name: folderName,
      folder_path: path.resolve(folderRoot, folderName),
      title: row["论文题名"],
      doi: row.DOI,
    });
  }
  return resolved;
}

function applyReport(rows, results) {
  validateExistingRows(rows);
  const indexes = updateRowMap(rows);
  const timestamp = nowText();
  for (const result of results) {
    const doi = normalizeDoi(result.doi);
    const rowIndex = indexes.byDoi.get(doi);
    if (rowIndex === undefined) {
      throw new Error(`下载报告中的 DOI 不在工作簿中：${doi || "<空>"}`);
    }
    const row = rows[rowIndex];
    const status = cleanText(result.status) || "failed";
    const success = result.success === true && ["downloaded", "cached"].includes(status);
    row["下载成功"] = success ? "是" : "否";
    row["下载状态"] = status;
    row["失败原因"] = success ? "" : lastFailureReason(result);
    row["PDF路径"] = cleanText(result.pdf_path);
    row["下载来源"] = cleanText(result.source);
    row["最后更新时间"] = timestamp;
  }
}

function writeRowsToTable(sheet, table, rows, existingCount) {
  const targetRows = rows.map(objectToRow);

  if (!targetRows.length) {
    throw new Error("工作簿不能保存空文献清单。");
  }

  const overwriteCount = Math.min(existingCount, targetRows.length);
  if (overwriteCount > 0) {
    sheet.getRangeByIndexes(4, 0, overwriteCount, HEADERS.length).values = targetRows.slice(
      0,
      overwriteCount,
    );
  }
  if (targetRows.length > existingCount) {
    table.rows.add(null, targetRows.slice(existingCount));
  }
  if (existingCount > targetRows.length) {
    throw new Error("工作簿包含无法安全收缩的多余表格行，原文件未修改。");
  }
}

async function saveAtomically(artifact, workbook, workbookPath) {
  await fs.mkdir(path.dirname(workbookPath), { recursive: true });
  const token = `${process.pid}-${Date.now()}`;
  const temporary = `${workbookPath}.${token}.tmp.xlsx`;
  const backup = `${workbookPath}.${token}.bak.xlsx`;
  const existed = await fs
    .access(workbookPath)
    .then(() => true)
    .catch(() => false);

  try {
    const output = await artifact.SpreadsheetFile.exportXlsx(workbook);
    await output.save(temporary);
    if (!existed) {
      await fs.rename(temporary, workbookPath);
      return;
    }
    await fs.rename(workbookPath, backup);
    try {
      await fs.rename(temporary, workbookPath);
      await fs.rm(backup, { force: true });
    } catch (error) {
      await fs.rename(backup, workbookPath).catch(() => undefined);
      throw error;
    }
  } finally {
    await fs.rm(temporary, { force: true }).catch(() => undefined);
    await fs.rm(`${temporary}.inspect.ndjson`, { force: true }).catch(() => undefined);
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const nodeModulesPath = requiredArg(args, "node-modules");
  const workbookPath = requiredArg(args, "workbook");
  const recordsPath = args.get("records") ? path.resolve(args.get("records")) : null;
  const reportPath = args.get("report") ? path.resolve(args.get("report")) : null;
  const resolvedPath = args.get("resolved") ? path.resolve(args.get("resolved")) : null;
  const templatePath = args.get("template") ? path.resolve(args.get("template")) : DEFAULT_TEMPLATE;
  const folderRoot = args.get("folder-root")
    ? path.resolve(args.get("folder-root"))
    : path.dirname(workbookPath);

  if (!recordsPath && !reportPath) {
    throw new Error("必须提供 --records 或 --report。");
  }
  if (recordsPath && !resolvedPath) {
    throw new Error("使用 --records 时必须同时提供 --resolved。");
  }
  if (reportPath) {
    await fs.access(workbookPath).catch(() => {
      throw new Error("回写下载报告前，文献检索汇总.xlsx 必须已经存在。");
    });
  }

  const artifact = await loadArtifactTool(nodeModulesPath);
  const workbook = await loadWorkbook(artifact, workbookPath, templatePath);
  const { sheet, table } = resolveTable(workbook);
  const rows = readTableRows(sheet);
  const existingCount = rows.length;
  let resolved = null;

  if (recordsPath) {
    const recordsPayload = validateRecordsPayload(await readJson(recordsPath));
    resolved = upsertRecords(rows, recordsPayload, workbookPath, folderRoot);
  }
  if (reportPath) {
    const results = validateReportPayload(await readJson(reportPath));
    applyReport(rows, results);
  }

  writeRowsToTable(sheet, table, rows, existingCount);
  workbook.recalculate();
  await saveAtomically(artifact, workbook, workbookPath);

  if (resolvedPath && resolved) {
    await fs.mkdir(path.dirname(resolvedPath), { recursive: true });
    const temporaryResolved = `${resolvedPath}.${process.pid}.tmp`;
    await fs.writeFile(
      temporaryResolved,
      `${JSON.stringify({ schema_version: 1, records: resolved }, null, 2)}\n`,
      "utf8",
    );
    await fs.rename(temporaryResolved, resolvedPath);
  }

  process.stdout.write(
    `${JSON.stringify({ workbook: workbookPath, records: rows.length, report_updated: Boolean(reportPath) })}\n`,
  );
}

main().catch((error) => {
  process.stderr.write(`[文献汇总失败] ${error.message}\n`);
  process.exitCode = 1;
});
