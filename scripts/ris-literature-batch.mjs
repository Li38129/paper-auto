import fs from "node:fs/promises";
import path from "node:path";

function parseArgs(argv) {
  const values = new Map();
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith("--")) {
      throw new Error(`无法识别的参数：${key}`);
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`参数 ${key} 缺少值。`);
    }
    const name = key.slice(2);
    const current = values.get(name);
    values.set(name, current === undefined ? value : [...(Array.isArray(current) ? current : [current]), value]);
    index += 1;
  }
  return values;
}

function required(args, name) {
  const value = args.get(name);
  if (!value || Array.isArray(value)) {
    throw new Error(`缺少唯一参数 --${name}。`);
  }
  return value;
}

function integerArg(args, name) {
  const value = Number(required(args, name));
  if (!Number.isInteger(value) || value < 1) {
    throw new Error(`--${name} 必须是正整数。`);
  }
  return value;
}

function normalizeDoi(value) {
  return String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/^doi:\s*/i, "")
    .replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")
    .replace(/[?#].*$/, "");
}

function firstValue(record, ...tags) {
  for (const tag of tags) {
    const values = record.get(tag);
    if (values?.length) {
      return values[0].trim();
    }
  }
  return "";
}

function addValue(record, tag, value) {
  const values = record.get(tag) ?? [];
  values.push(value);
  record.set(tag, values);
}

function parseRis(text) {
  const records = [];
  let current = null;
  let lastTag = "";
  for (const rawLine of text.replace(/^\uFEFF/, "").split(/\r\n|\n|\r/)) {
    const match = rawLine.match(/^([A-Z0-9]{2})  - ?(.*)$/);
    if (match) {
      const [, tag, value] = match;
      if (tag === "TY") {
        if (current) {
          records.push(current);
        }
        current = new Map();
      }
      if (!current) {
        continue;
      }
      addValue(current, tag, value);
      lastTag = tag;
      if (tag === "ER") {
        records.push(current);
        current = null;
        lastTag = "";
      }
      continue;
    }
    if (current && lastTag && rawLine.trim()) {
      const values = current.get(lastTag);
      values[values.length - 1] = `${values[values.length - 1]} ${rawLine.trim()}`;
    }
  }
  if (current) {
    records.push(current);
  }
  return records;
}

function folderLabel(title) {
  const cleaned = title
    .replace(/<[^>]*>/g, " ")
    .replace(/[<>:"/\\|?*]/g, "-")
    .replace(/\s+/g, " ")
    .replace(/[. ]+$/g, "")
    .trim();
  if (!cleaned) {
    throw new Error("论文题名清理后为空，无法生成目录标签。");
  }
  return `-${Array.from(cleaned).slice(0, 72).join("")}`;
}

async function writeJson(destination, payload) {
  const output = path.resolve(destination);
  await fs.mkdir(path.dirname(output), { recursive: true });
  const temporary = `${output}.${process.pid}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  await fs.rename(temporary, output);
}

async function buildRecords(args) {
  const inputsValue = args.get("input");
  const inputs = Array.isArray(inputsValue) ? inputsValue : inputsValue ? [inputsValue] : [];
  if (!inputs.length) {
    throw new Error("至少需要一个 --input RIS 文件。");
  }
  const start = integerArg(args, "start");
  const end = integerArg(args, "end");
  if (end < start) {
    throw new Error("--end 不能小于 --start。");
  }

  const all = [];
  for (const input of inputs) {
    all.push(...parseRis(await fs.readFile(path.resolve(input), "utf8")));
  }
  if (end > all.length) {
    throw new Error(`请求范围 ${start}-${end} 超过 RIS 总记录数 ${all.length}。`);
  }

  const seenDois = new Set();
  const records = all.slice(start - 1, end).map((record, offset) => {
    const rank = start + offset;
    const title = firstValue(record, "TI", "T1");
    const doi = normalizeDoi(firstValue(record, "DO"));
    if (!title || !doi) {
      throw new Error(`第 ${rank} 条记录缺少题名或 DOI。`);
    }
    if (seenDois.has(doi)) {
      throw new Error(`批次中存在重复 DOI：${doi}`);
    }
    seenDois.add(doi);
    const yearText = firstValue(record, "PY", "Y1");
    const yearMatch = yearText.match(/\d{4}/);
    return {
      rank,
      folder_label: folderLabel(title),
      title,
      doi,
      year: yearMatch ? Number(yearMatch[0]) : null,
      journal: firstValue(record, "T2", "JO", "JF"),
      paper_type: "期刊论文",
      system: "",
      metric: "",
      source_url: `https://doi.org/${doi}`,
    };
  });

  await writeJson(required(args, "output"), {
    schema_version: 1,
    topic: "RIS导入：固态锂电池与固态电解质",
    records,
  });
  process.stdout.write(`${JSON.stringify({ total_ris: all.length, start, end, records: records.length })}\n`);
}

async function buildPapers(args) {
  const payload = JSON.parse(await fs.readFile(path.resolve(required(args, "resolved")), "utf8"));
  if (payload?.schema_version !== 1 || !Array.isArray(payload.records)) {
    throw new Error("resolved-records.json 格式无效。");
  }
  const start = integerArg(args, "start");
  const end = integerArg(args, "end");
  const excludedValue = args.get("exclude-journal");
  const excludedJournals = new Set(
    (Array.isArray(excludedValue) ? excludedValue : excludedValue ? [excludedValue] : []).map(
      (value) => String(value).trim().toLowerCase(),
    ),
  );
  const recordsPath = args.get("records");
  let metadataByRank = new Map();
  if (recordsPath) {
    if (Array.isArray(recordsPath)) {
      throw new Error("--records 只能指定一次。");
    }
    const recordsPayload = JSON.parse(await fs.readFile(path.resolve(recordsPath), "utf8"));
    metadataByRank = new Map(
      recordsPayload.records.map((record) => [Number(record.rank), record]),
    );
  }
  const inRange = payload.records.filter(
    (record) => Number(record.rank) >= start && Number(record.rank) <= end && record.doi,
  );
  const selected = inRange.filter(
    (record) => !excludedJournals.has(
      String(metadataByRank.get(Number(record.rank))?.journal ?? "").trim().toLowerCase(),
    ),
  );
  const skipped = inRange.filter(
    (record) => excludedJournals.has(
      String(metadataByRank.get(Number(record.rank))?.journal ?? "").trim().toLowerCase(),
    ),
  );
  if (!selected.length) {
    throw new Error(`resolved-records.json 中没有 ${start}-${end} 范围的 DOI。`);
  }
  for (const record of inRange) {
    await fs.mkdir(path.resolve(record.folder_path), { recursive: true });
  }
  const papers = selected.map((record) => {
    const metadata = metadataByRank.get(Number(record.rank)) ?? {};
    return {
      rank: Number(record.sequence),
      doi: normalizeDoi(record.doi),
      title: String(record.title).trim(),
      folder_path: path.resolve(record.folder_path),
      journal: String(metadata.journal ?? "").trim(),
      issn: String(metadata.issn ?? "").trim(),
      year: Number.isInteger(metadata.year) ? metadata.year : null,
    };
  });
  await writeJson(required(args, "output"), { schema_version: 1, papers });
  const skippedReport = args.get("skipped-report");
  if (skippedReport) {
    if (Array.isArray(skippedReport)) {
      throw new Error("--skipped-report 只能指定一次。");
    }
    await writeJson(skippedReport, {
      schema_version: 1,
      results: skipped.map((record) => ({
        rank: Number(record.sequence),
        doi: normalizeDoi(record.doi),
        title: String(record.title).trim(),
        requested_folder_path: path.resolve(record.folder_path),
        success: false,
        status: "subscription_required",
        article_dir: path.resolve(record.folder_path),
        pdf_path: null,
        source: "",
        attempts: [
          {
            source: "workflow",
            url: `https://doi.org/${normalizeDoi(record.doi)}`,
            success: false,
            reason: "subscription_required",
          },
        ],
      })),
    });
  }
  process.stdout.write(
    `${JSON.stringify({ start, end, papers: papers.length, skipped: skipped.length })}\n`,
  );
}

const args = parseArgs(process.argv.slice(2));
const command = required(args, "command");
if (command === "records") {
  await buildRecords(args);
} else if (command === "papers") {
  await buildPapers(args);
} else {
  throw new Error(`未知命令：${command}`);
}
