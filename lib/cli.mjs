// Command registry and runner. Read commands = (seller-center pages to dump) + (report over the dump).
// With --from <dump folder> nothing is fetched; otherwise the engine dumps those pages for the
// requested range (split into windows of at most MAX_WINDOW_DAYS) and the report merges the windows.
import { spawnSync } from "node:child_process";
import { readFileSync, existsSync, statSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { resolveRange, splitRange } from "./dates.mjs";
import { ONETALK, onetalkMain, sendGate } from "./onetalk.mjs";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
export const ENGINE = path.join(ROOT, "engine");
export const DEFAULT_CONFIG = path.join(homedir(), ".aliops-ops/seller-report/accounts.json");

const INQ = ["inquiries"];
const ADS = ["ads-report", "ads-campaigns"];
const ANALYZE = ["inquiries", "ads-report", "ads-campaigns", "analytics-product", "analytics-keyword"];

export const READ_COMMANDS = {
  "inquiry list": { pages: INQ, report: "inquiry-list", help: "문의 목록(허용 필드만) [--unread] [--type product|company|tm] [--country XX] [--status N] [--limit N]" },
  "inquiry stats": { pages: INQ, report: "inquiry-stats", help: "유형·진입점·기기·국가·답장 상태·일자별 문의 수" },
  "inquiry by-product": { pages: ["inquiries", "ads-report", "analytics-product"], report: "inquiry-by-product", help: "상품ID → 문의 (TM 은 'TM 유입')" },
  "inquiry fields": { pages: INQ, report: "inquiry-fields", help: "문의 목록 응답의 키 이름(값 없음): 바이어 레벨 필드 찾기" },
  "ads report": { pages: ["ads-report"], report: "ads-report", help: "광고 리포트 표: 상품×일, 캠페인×일, 계정×일" },
  "ads products": { pages: ADS, report: "ads-products", help: "상품별 광고비(실측/추정)·노출·클릭·CPC·전환율·문의 1건 비용" },
  "ads conversions": { pages: ["ads-report"], report: "ads-conversions", help: "AI 캠페인 전환 명세(상점 전체, 유형 51): 시각·광고/전환 상품·국가·바이어 등급·유입·유형 + 광고비÷전환" },
  "ads analyze": { pages: ANALYZE, report: "ads-analyze", help: "광고 분석 CSV 4개 + summary.md (스킬 alibaba-ad-analysis 가 부름)" },
  keywords: { pages: ["ads-report", "analytics-keyword", "analytics-product"], report: "keywords", help: "키워드·노출·클릭·입찰가/CPC" },
  bids: { pages: ADS, report: "bids", help: "캠페인 입찰가(목표 전환단가)·키워드 입찰가 필드" },
  "analytics products": { pages: ["analytics-product"], report: "analytics-products", granularity: "week",
    help: "Product Analyzer: 상품별 Views·Clicks·CTR·Visitors·찜·공유·비교·문의·문의율·담당자·P4P/KWA + 유입 검색어 [--granularity week|month]" },
  coverage: { pages: [...ANALYZE, "showcase"], report: "coverage", help: "항목별 추출됨(행 수·출처)/없음(사유) 표" },
};

const FLAGS_WITH_VALUE = new Set(["account", "start", "end", "days", "weeks", "granularity", "out", "from", "pages", "config", "type", "country", "status", "limit", "max-pages", "dump-dir",
  "text", "text-file", "path", "product-id", "template", "tag", "inquiry-id", "trade-id", "button"]);

export function parseArgs(argv) {
  const pos = [], opt = { from: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) { pos.push(a); continue; }
    const [k, inline] = a.slice(2).split(/=(.*)/s, 2);
    if (FLAGS_WITH_VALUE.has(k)) {
      const v = inline ?? argv[++i];
      if (v === undefined) throw new Error(`--${k} 값이 없다`);
      if (k === "from") opt.from.push(v); else opt[k] = v;
    } else opt[k] = true;
  }
  return { pos, opt };
}

export function loadAccount(configPath, name) {
  if (!name) throw new Error("--account <계정키> 가 필요하다 (accounts.json 의 키)");
  if (!existsSync(configPath)) throw new Error(`계정 설정 없음: ${configPath} (README 의 accounts.json 절)`);
  const all = JSON.parse(readFileSync(configPath, "utf8"));
  const acc = all[name];
  if (!acc) throw new Error(`계정 ${name} 이 ${configPath} 에 없다 (있는 키: ${Object.keys(all).sort().join(", ")})`);
  if (typeof acc.loginId !== "string" || !acc.loginId.trim()) throw new Error("loginId 가 비어 있다");
  const asideAccount = acc.asideAccount ?? "u0";
  if (!/^u\d+$/.test(asideAccount)) throw new Error("asideAccount 는 u0, u1 … 형식");
  return { ...acc, asideAccount, name };
}

const python = (args, run) => run("python3", args, { stdio: "inherit" });

export function defaultRun(cmd, args, opts) {
  const r = spawnSync(cmd, args, opts);
  if (r.error) throw r.error;
  return r.status ?? 1;
}

// Live dump of `pages` over the range, one engine run per window. -> list of dump folders.
export function dumpWindows({ account, configPath, pages, range, root, opt, run, granularity }) {
  const windows = splitRange(range.start, range.end);
  const dirs = [];
  for (const w of windows) {
    const dir = windows.length === 1 ? root : path.join(root, `w-${w.start}_${w.end}`);
    const args = [path.join(ENGINE, "seller_central_raw.py"), "--account", account.name, "--config", configPath,
      "--start", w.start, "--end", w.end, "--out", dir];
    if (pages) args.push("--pages", pages.join(","));
    if (granularity) args.push("--granularity", granularity);
    if (opt.resume) args.push("--resume");
    if (opt["max-pages"]) args.push("--max-pages", String(opt["max-pages"]));
    const code = python(args, run);
    if (code !== 0) return { dirs, code, failedWindow: w };
    dirs.push(dir);
  }
  return { dirs, code: 0 };
}

function dumpRoot(account, range, opt) {
  if (opt["dump-dir"]) return opt["dump-dir"];
  const base = account.outDir || path.join(homedir(), "30_Assets", account.name, "seller-report");
  return path.join(base, `sellercentral-raw-${range.start}_${range.end}`);
}

export function main(argv, deps = {}) {
  const { run = defaultRun, log = console.log, err = console.error } = deps;
  const { pos, opt } = parseArgs(argv);
  const configPath = opt.config || DEFAULT_CONFIG;
  const name2 = pos.slice(0, 2).join(" ");
  const cmd = READ_COMMANDS[name2] || ONETALK[name2] ? name2 : pos[0];

  if (!cmd || opt.help || cmd === "help") { log(usage()); return 0; }
  if (cmd === "pages") return python([path.join(ENGINE, "seller_central_raw.py"), "--list-pages"], run);
  if (cmd === "login") {
    loadAccount(configPath, opt.account);
    return python([path.join(ENGINE, "seller_central_raw.py"), "--account", opt.account, "--config", configPath, "--login"], run);
  }
  if (cmd === "dump") {
    const account = loadAccount(configPath, opt.account);
    const range = resolveRange(opt);
    range.warnings.forEach((w) => err("주의: " + w));
    const pages = opt.pages && opt.pages !== "all" ? opt.pages.split(",").map((s) => s.trim()).filter(Boolean) : null;
    const res = dumpWindows({ account, configPath, pages, range, root: opt.out || dumpRoot(account, range, opt), opt, run, granularity: opt.granularity });
    log(JSON.stringify({ query_start: range.start, query_end: range.end, windows: res.dirs }));
    return res.code;
  }
  if (ONETALK[cmd]) {
    sendGate(ONETALK[cmd], opt, deps.env || process.env);
    const account = loadAccount(configPath, opt.account);
    return onetalkMain(ONETALK[cmd], opt, { account, configPath, enginePath: path.join(ENGINE, "seller_central_raw.py"), run, log, ...(deps.env ? { env: deps.env } : {}), ...(deps.spawn ? { spawn: deps.spawn } : {}) });
  }
  const spec = READ_COMMANDS[cmd];
  if (!spec) { err(`모르는 명령: ${pos.join(" ")}\n` + usage()); return 64; }

  const granularity = spec.granularity ? (opt.granularity || spec.granularity) : null;
  if (granularity && !["week", "month"].includes(granularity)) throw new Error("--granularity 는 week 또는 month");
  let dirs = opt.from, range = null;
  if (opt.start || opt.end || opt.days || opt.weeks || opt.all) {
    range = resolveRange(opt);
    range.warnings.forEach((w) => err("주의: " + w));
  }
  if (!dirs.length) {
    const account = loadAccount(configPath, opt.account);
    if (!range) range = resolveRange(opt); // throws the usage message for a missing period
    const res = dumpWindows({ account, configPath, pages: spec.pages, range, root: dumpRoot(account, range, opt), opt, run, granularity });
    if (res.code !== 0) { err(`덤프 실패(종료 코드 ${res.code}, 창 ${res.failedWindow.start}~${res.failedWindow.end})`); return res.code; }
    dirs = res.dirs;
  } else {
    for (const d of dirs) if (!existsSync(path.join(d, "raw")) || !statSync(path.join(d, "raw")).isDirectory()) throw new Error(`덤프 폴더가 아님(raw/ 없음): ${d}`);
  }
  const args = [path.join(ENGINE, "report.py"), spec.report, ...dirs.flatMap((d) => ["--from", d])];
  if (range) args.push("--start", range.start, "--end", range.end);
  args.push("--format", opt.json ? "json" : opt.csv ? "csv" : "table");
  let out = opt.out;
  if (!out && spec.report === "ads-analyze") out = path.join(dirs[0], "analysis", "ad_analysis");
  if (out) args.push("--out", out);
  for (const k of ["limit", "type", "country", "status"]) if (opt[k] != null) args.push("--" + k, String(opt[k]));
  if (opt.unread) args.push("--unread");
  if (granularity) args.push("--granularity", granularity);
  return python(args, run);
}

export function usage() {
  const rows = Object.entries(READ_COMMANDS).map(([k, v]) => `  ${k.padEnd(20)} ${v.help}`);
  return [
    "alibaba-seller <명령> --account <계정키> --start YYYY-MM-DD --end YYYY-MM-DD [--json|--csv] [--out DIR]",
    "                       (기간 대신 --days N | --weeks N | --all, 덤프가 있으면 --account 대신 --from <덤프 폴더> 여러 번)",
    "",
    "읽기 명령:",
    ...rows,
    "",
    "문의창(OneTalk) 조작: 기본 dry-run(클릭·입력 0회). 실발송 = --send + ALIBABA_SELLER_ALLOW_SEND=owner-approved,",
    "                     안 읽은 대화는 --allow-mark-read 필요. 대화 = --inquiry-id N | --trade-id N",
    ...Object.entries(ONETALK).map(([k, v]) => `  ${k.padEnd(20)} ${v.help}`),
    "",
    "덤프·계정:",
    "  dump                 셀러센터 페이지 raw 덤프 [--pages a,b] [--resume] [--max-pages N]",
    "  pages                덤프 가능한 페이지 키 목록",
    "  login                키체인 비밀번호로 Aside 로그인(비밀번호는 stdin 으로만)",
  ].join("\n");
}
