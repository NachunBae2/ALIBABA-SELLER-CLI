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
import { pythonCmd } from "./platform.mjs";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
export const ENGINE = path.join(ROOT, "engine");
export const DEFAULT_CONFIG = path.join(homedir(), ".aliops-ops/seller-report/accounts.json");

const INQ = ["inquiries"];
const ADS = ["ads-report", "ads-campaigns"];
const ANALYZE = ["inquiries", "ads-report", "ads-campaigns", "analytics-product", "analytics-keyword"];

export const READ_COMMANDS = {
  "inquiries list": { pages: INQ, report: "inquiry-list", help: "문의 목록(허용 필드만) [--unread] [--type product|company|tm] [--country XX] [--status N] [--limit N]" },
  "inquiries stats": { pages: INQ, report: "inquiry-stats", help: "유형·진입점·기기·국가·답장 상태·일자별 문의 수" },
  "inquiries by-product": { pages: ["inquiries", "ads-report", "analytics-product"], report: "inquiry-by-product", help: "상품ID → 문의 (TM 은 'TM 유입')" },
  "inquiries fields": { pages: INQ, report: "inquiry-fields", help: "문의 목록 응답의 키 이름(값 없음): 바이어 레벨 필드 찾기" },
  "ads report": { pages: ["ads-report"], report: "ads-report", help: "광고 리포트 표: 상품×일, 캠페인×일, 계정×일" },
  "ads products": { pages: [...ADS, "analytics-product"], report: "ads-products", help: "상품별 광고비(실측/추정)·노출·클릭·CPC·전환율·문의 1건 비용" },
  "ads conversions": { pages: ["ads-report", "analytics-product"], report: "ads-conversions", help: "AI 캠페인 전환 명세(상점 전체, 유형 51): 시각·광고/전환 상품·국가·바이어 등급 2종·유형 + 화면 값·광고비÷전환 (상품 이름은 Product Analyzer 제목)" },
  "ads conversion-fields": { pages: ["ads-report"], report: "ads-conversion-fields", help: "AI 전환 명세 응답의 키 이름(값 없음): 유입 채널 열 찾기" },
  "ads quality": { pages: ADS, report: "ads-quality", help: "바이어 품질: L1+ 바이어 클릭 비율(먼저)·문의·L3+·CPC·전환율·문의 1건 비용, 캠페인별·월별·기간 합 (Manual·AI)" },
  "ads analyze": { pages: ANALYZE, report: "ads-analyze", help: "광고 분석 CSV 4개 + summary.md (스킬 alibaba-ad-analysis 가 부름)" },
  keywords: { pages: ["ads-report", "analytics-keyword", "analytics-product"], report: "keywords", kwIndex: true,
    help: "키워드 raw: 광고 Word Report(키워드·검색어)·Product Analyzer 유입 검색어·Keyword Index(검색지수·판매자 규모) [--seed 'serum' …] [--nd 30d] [--no-index]" },
  "keywords recommend": { pages: ["ads-report", "analytics-product", "inquiries"], report: "keywords-recommend", kwIndex: true,
    help: "상품별 추천 키워드: 검색지수·경쟁·입찰가·우리 상품 노출/클릭/광고/문의 근거 칸 [--top 10] [--seed …] [--max-seeds 5] [--nd 30d]" },
  bids: { pages: ADS, report: "bids", help: "캠페인 입찰가(목표 전환단가)·키워드 입찰가 필드" },
  "analytics products": { pages: ["analytics-product"], report: "analytics-products", granularity: "week",
    help: "Product Analyzer: 상품별 Views·Clicks·CTR·Visitors·찜·공유·비교·문의·문의율·담당자·P4P/KWA + 유입 검색어 [--granularity week|month]" },
  coverage: { pages: [...ANALYZE, "showcase"], report: "coverage", help: "항목별 추출됨(행 수·출처)/없음(사유) 표" },
};

// Old names of the inquiry data commands (they lived in alibaba-inquiry before 2026-11-17).
// Still accepted by every CLI; they run the alibaba-data command and say so on stderr.
export const LEGACY = {
  "inquiry list": "inquiries list", "inquiry stats": "inquiries stats",
  "inquiry by-product": "inquiries by-product", "inquiry fields": "inquiries fields",
};

const FLAGS_WITH_VALUE = new Set(["account", "start", "end", "days", "weeks", "granularity", "out", "from", "pages", "config", "type", "country", "status", "limit", "max-pages", "dump-dir",
  "text", "text-file", "path", "product-id", "template", "tag", "inquiry-id", "trade-id", "button",
  "seed", "nd", "top", "max-seeds", "kw-pages"]);

export function parseArgs(argv) {
  const pos = [], opt = { from: [], seed: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) { pos.push(a); continue; }
    const [k, inline] = a.slice(2).split(/=(.*)/s, 2);
    if (FLAGS_WITH_VALUE.has(k)) {
      const v = inline ?? argv[++i];
      if (v === undefined) throw new Error(`--${k} 값이 없다`);
      if (k === "from" || k === "seed") opt[k].push(v); else opt[k] = v;
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

const python = (args, run) => { const [exe, ...pre] = pythonCmd(); return run(exe, [...pre, ...args], { stdio: "inherit" }); };

export function defaultCapture(cmd, args) {
  const r = spawnSync(cmd, args, { encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] });
  if (r.error) throw r.error;
  return { code: r.status ?? 1, stdout: r.stdout || "" };
}

// Keyword Index needs seeds: --seed values, else head terms from our own dump (report.py keyword-seeds).
// The index page is dumped into the last window folder so the report reads it with the rest.
export function dumpKeywordIndex({ account, configPath, dirs, range, opt, run, capture, err }) {
  let seeds = opt.seed.map((x) => x.trim()).filter(Boolean);
  if (!seeds.length) {
    const [exe, ...pre] = pythonCmd();
    const r = capture(exe, [...pre, path.join(ENGINE, "report.py"), "keyword-seeds", ...dirs.flatMap((d) => ["--from", d]),
      "--format", "json", ...(opt["max-seeds"] ? ["--max-seeds", String(opt["max-seeds"])] : [])]);
    if (r.code !== 0) return r.code;
    const rows = (JSON.parse(r.stdout || "{}").keyword_seeds) || [];
    rows.forEach((x) => err(`시드: ${x.seed} — ${x.basis}`));
    seeds = rows.map((x) => x.seed);
  }
  if (!seeds.length) { err("Keyword Index 건너뜀: 시드 없음(--seed 로 지정)"); return 0; }
  if (seeds.some((x) => x.includes("|"))) throw new Error("--seed 에 | 는 못 쓴다");
  const last = dirs[dirs.length - 1];
  const w = splitRange(range.start, range.end).at(-1); // the last window's own dates, so its manifest keeps them
  const args = [path.join(ENGINE, "seller_central_raw.py"), "--account", account.name, "--config", configPath,
    "--start", w.start, "--end", w.end, "--out", last, "--pages", "keyword-index", "--seeds", seeds.join("|"),
    "--kw-nd", opt.nd || "30d"];
  if (opt["kw-pages"]) args.push("--kw-pages", String(opt["kw-pages"]));
  return python(args, run);
}

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

// Two CLIs share this runner. alibaba-inquiry (문의대응) = OneTalk conversation actions only
// (reply, card, file, product card, template, tag). alibaba-data (데이터분석) = every read and
// analysis command, inquiry data included (inquiries list|stats|by-product|fields).
// alibaba-seller (scope "all") keeps every command for old scripts.
export const SCOPES = {
  inquiry: { prog: "alibaba-inquiry", title: "문의대응", shorthand: ["list", "stats", "by-product", "fields"] },
  data: { prog: "alibaba-data", title: "데이터분석", shorthand: [] },
  all: { prog: "alibaba-seller", title: "전체", shorthand: [] },
};
const DATA_ONLY = ["dump", "pages"];

export function commandScope(cmd) {
  if (cmd === "login" || cmd === "help") return "both";
  if (ONETALK[cmd]) return "inquiry";
  if (READ_COMMANDS[cmd] || DATA_ONLY.includes(cmd)) return "data";
  return null;
}

export function main(argv, deps = {}) {
  const { run = defaultRun, log = console.log, err = console.error, scope = "all", capture = defaultCapture } = deps;
  const { pos, opt } = parseArgs(argv);
  if (SCOPES[scope].shorthand.includes(pos[0])) pos.unshift("inquiry");
  const configPath = opt.config || DEFAULT_CONFIG;
  let name2 = pos.slice(0, 2).join(" ");
  const legacy = LEGACY[name2];
  if (legacy) {
    err(`옮김: '${name2}' → alibaba-data ${legacy} (옛 이름도 그대로 동작)`);
    name2 = legacy;
  }
  const cmd = READ_COMMANDS[name2] || ONETALK[name2] ? name2 : pos[0];

  if (!cmd || opt.help || cmd === "help") { log(usage(scope)); return 0; }
  const owner = commandScope(cmd);
  if (!legacy && scope !== "all" && owner && owner !== "both" && owner !== scope) {
    err(`'${pos.join(" ")}' 은 ${SCOPES[owner].prog}(${SCOPES[owner].title}) 명령이다: ${SCOPES[owner].prog} ${pos.join(" ")} …`);
    return 64;
  }
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
  if (!spec) { err(`모르는 명령: ${pos.join(" ")}\n` + usage(scope)); return 64; }

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
    if (spec.kwIndex && !opt["no-index"]) {
      const code = dumpKeywordIndex({ account, configPath, dirs, range, opt, run, capture, err });
      if (code !== 0) { err(`Keyword Index 덤프 실패(종료 코드 ${code})`); return code; }
    }
  } else {
    for (const d of dirs) if (!existsSync(path.join(d, "raw")) || !statSync(path.join(d, "raw")).isDirectory()) throw new Error(`덤프 폴더가 아님(raw/ 없음): ${d}`);
  }
  const args = [path.join(ENGINE, "report.py"), spec.report, ...dirs.flatMap((d) => ["--from", d])];
  if (range) args.push("--start", range.start, "--end", range.end);
  args.push("--format", opt.json ? "json" : opt.csv ? "csv" : "table");
  let out = opt.out;
  if (!out && spec.report === "ads-analyze") out = path.join(dirs[0], "analysis", "ad_analysis");
  if (out) args.push("--out", out);
  for (const k of ["limit", "type", "country", "status", "top", "max-seeds"]) if (opt[k] != null) args.push("--" + k, String(opt[k]));
  if (opt.unread) args.push("--unread");
  if (granularity) args.push("--granularity", granularity);
  return python(args, run);
}

export function usage(scope = "all") {
  const { prog, title } = SCOPES[scope];
  const inq = scope !== "data", data = scope !== "inquiry";
  const rows = data ? Object.entries(READ_COMMANDS).map(([k, v]) => `  ${k.padEnd(20)} ${v.help}`) : [];
  return [
    ...(data ? [
      `${prog} (${title}) <명령> --account <계정키> --start YYYY-MM-DD --end YYYY-MM-DD [--json|--csv] [--out DIR]`,
      "                       (기간 대신 --days N | --weeks N | --all, 덤프가 있으면 --account 대신 --from <덤프 폴더> 여러 번)",
    ] : [`${prog} (${title}) <명령> --account <계정키> (--inquiry-id N | --trade-id N) [--send]`]),
    scope === "all" ? "  같은 명령을 둘로 나눈 것: alibaba-inquiry(문의대응) · alibaba-data(데이터분석)"
      : `  다른 쪽: ${scope === "inquiry" ? "alibaba-data(데이터분석: 문의 데이터·광고·키워드·입찰·Product Analyzer)" : "alibaba-inquiry(문의대응: OneTalk 답장·명함·PDF/파일·상품 카드·템플릿·태그)"}`,
    ...(data ? ["", "읽기·분석 명령 (문의 데이터 = inquiries …):", ...rows] : []),
    ...(scope === "inquiry" ? ["", "  문의 목록·통계·상품별 문의는 alibaba-data inquiries list|stats|by-product 로 옮김",
      "  (옛 이름 alibaba-inquiry list|stats|by-product|fields 도 그대로 동작)"] : []),
    ...(inq ? [
      "",
      "문의창(OneTalk) 조작: OneTalk 대화창 자체 기능(명함·파일 첨부·상품 카드·템플릿)을 Aside 로 누른다. 메일 등 다른 경로 없음.",
      "  기본 dry-run(클릭·입력 0회). 실발송 = --send + ALIBABA_SELLER_ALLOW_SEND=owner-approved,",
      "  안 읽은 대화는 --allow-mark-read 필요. 대화 = --inquiry-id N | --trade-id N",
      ...Object.entries(ONETALK).map(([k, v]) => `  ${k.padEnd(20)} ${v.help}`),
    ] : []),
    "",
    "덤프·계정:",
    ...(data ? [
      "  dump                 셀러센터 페이지 raw 덤프 [--pages a,b] [--resume] [--max-pages N]",
      "  pages                덤프 가능한 페이지 키 목록",
    ] : []),
    "  login                키체인 비밀번호로 Aside 로그인(비밀번호는 stdin 으로만)",
  ].join("\n");
}
