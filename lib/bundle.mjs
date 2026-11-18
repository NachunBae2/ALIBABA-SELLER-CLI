// 번들 실행기: 셀러센터 읽기 명령 묶음을 "순서대로" 자식 프로세스로 돌려 한 폴더에 모은다.
//   <root>/bundle-<account>-<start>_<end>-<YYYYMMDDHHMMSS>/
//     manifest.json, <slug>/result.json|<slug>/*.csv|<slug>/stderr.txt
// 명령 코드는 고치지 않고 `node bin/alibaba-data.mjs <명령> --account A --json --out …` 으로 재사용한다.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { resolveRange } from "./dates.mjs";
import { loadBundles, pickBundles, commandsOf } from "./bundles.mjs";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
export const BIN = path.join(ROOT, "bin", "alibaba-data.mjs");
export const BUNDLE_SCHEMA = "alibaba-seller-bundle/1";
export const DEFAULT_TIMEOUT_MS = 900_000; // 명령 하나
const PERIOD_NOTE_DEFAULT = "화면 선택지 %s 만 지원 → 화면 기본값";
const DISCOVERY_RANGE_NOTE = "기간 선택 API 아님(서버 상대 기간·스냅샷) → 기간 인자 미전달";

export const cliVersion = () => {
  try { return JSON.parse(readFileSync(path.join(ROOT, "package.json"), "utf8")).version || null; } catch { return null; }
};

const pad = (n) => String(n).padStart(2, "0");
// YYYYMMDDHHMMSS — 연도부터 붙여 폴더 이름 정렬이 시간 순서와 같게.
export const stampOf = (now = new Date()) => `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}` +
  `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
export const bundleDirName = (account, period, now = new Date()) =>
  `bundle-${account}-${period.start}_${period.end}-${stampOf(now)}`;

// engine/http_session.py SECRET 정규식과 같은 규칙: 키 이름이 걸리면 값 삭제, 문자열 속
// csrf/xsrf/token/sign=… 는 *** 로. stderr 는 마지막 줄들만 남긴다.
const SECRET = /csrf|xsrf|token|sign|cookie|authorization|_m_h5_tk|^ua$|^bx-|^x5sec$/i;
const secretKey = (k) => SECRET.test(String(k ?? "").replace(/^["']|["']$/g, ""));
export function redactText(text) {
  let out = String(text ?? "");
  if (!out) return "";
  // 이스케이프된 JSON(\\")도 같은 규칙으로 보이도록 되돌려 놓고 지운 뒤 다시 escapes.
  const escaped = /\\"/.test(out);
  if (escaped) out = out.replace(/\\"/g, '"').replace(/\\n/g, "\n");
  out = out.replace(/([\w-]*(?:csrf|xsrf|token|sign)[\w-]*=)[^&\s;"\\]+/gi, "$1***");
  out = out.replace(/(["'])([\w.-]+)\1\s*:\s*(["'])([^"'\\]*)\3/g, (m, q1, k, q2, v) => SECRET.test(String(k)) ? `${q1}${k}${q1}: ${q2}***${q2}` : m);
  // `Authorization: Bearer …` 처럼 키와 값 사이가 '=' 아닌 구분자(=,: ,공백)인 헤더도 값까지 지운다.
  out = out.replace(/\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]+/gi, "$1 ***");
  out = out.replace(/(^|[\s{,;&=])((?:authorization|cookie|x5sec)|[\w-]*(?:csrf|xsrf|token|sign|_m_h5_tk)[\w-]*)(\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^,}\]&;\s]+)/gi, "$1$2$3***");
  return out;
}
export const tailOf = (text, n = 5) => redactText(String(text || "").split(/\r?\n/).filter((l) => l.trim()).slice(-n).join("\n"));

// 기간 인자: --days/--start --end/--max 를 그대로 넘긴다. 허용 값이 정해진 discovery 명령은
// 허용 값이 아니면 넘기지 않고 period_note 를 남긴다.
export function periodArgs(spec, opt) {
  const days = opt.days != null ? Number(opt.days) : null;
  const args = opt.start || opt.end ? ["--start", opt.start, "--end", opt.end]
    : days != null ? ["--days", String(days)] : opt.max || opt.all ? ["--max"] : [];
  if (spec?.discovery && (opt.start || opt.end || opt.max || opt.all)) return { args: [], note: DISCOVERY_RANGE_NOTE };
  if (spec?.discovery && days != null) {
    const allowed = Array.isArray(spec.discovery.days) ? spec.discovery.days : [];
    if (!allowed.length) return { args: [], note: DISCOVERY_RANGE_NOTE };
    if (!allowed.includes(days)) return { args: [], note: PERIOD_NOTE_DEFAULT.replace("%s", allowed.join("/")) };
  }
  return { args, note: null };
}

// READ_COMMANDS 의 discovery 는 true/기간힌트만 있고 허용 일수는 engine/pages.json 의 page.discovery.days 에 있다.
// 번들 실행용 스펙 = 명령 스펙 + 그 페이지의 discovery 스키마(허용 days).
export function bundleSpecs(specs, pages = []) {
  const out = {};
  for (const [name, spec] of Object.entries(specs)) {
    const page = (spec.pages || []).map((key) => pages.find((p) => p.key === key)).find((p) => p?.discovery);
    out[name] = spec.discovery && page ? { ...spec, discovery: page.discovery } : spec;
  }
  return out;
}

// manifest.period = 명령 stdout(JSON) 의 period 표 첫 행. 없으면 null.
export const firstPeriodRow = (stdout) => {
  try {
    const json = JSON.parse(stdout || "");
    if (!json || typeof json !== "object" || Array.isArray(json)) return null;
    for (const rows of Object.values(json)) {
      if (Array.isArray(rows) && rows.length && rows[0] && typeof rows[0] === "object" && "period" in rows[0]) return rows[0].period ?? null;
    }
    return null;
  } catch { return null; }
};

// 명령 하나 실행 인자. 필수 인자가 없으면 던진다(→ skipped).
//   <명령어…> --account A --json --out <bundle>/<slug> <기간 인자> [--fresh] [--keyword …]
export function buildArgs(command, { spec, period2, opt, account, configPath, folder, slug }) {
  if (spec.required?.flag) {
    const v = opt[spec.required.flag];
    if (typeof v !== "string" || !v.trim()) throw new Error(`--${spec.required.flag} 필요`);
  }
  const args = [...command.split(" ").filter(Boolean), "--account", account.name, "--json", "--out", path.join(folder, slug), ...period2.args];
  if (configPath) args.push("--config", configPath);
  if (opt.fresh) args.push("--fresh");
  if (spec.required?.flag) args.push(`--${spec.required.flag}`, String(opt[spec.required.flag]));
  return args;
}

// 한 번의 spawn. timeout 초과면 SIGTERM, 10초 뒤 SIGKILL. -> {rc, stdout, stderr, timedOut}
export function runChild(command, { bin = BIN, env = process.env, timeout = DEFAULT_TIMEOUT_MS } = {}) {
  const isNodeScript = /\.(?:mjs|cjs|js)$/.test(bin);
  const child = spawn(process.execPath, [...(isNodeScript ? [bin] : []), ...command], { env, stdio: ["ignore", "pipe", "pipe"] });
  let stdout = "", stderr = "", timedOut = false;
  child.stdout.setEncoding("utf8").on("data", (d) => { stdout += d; });
  child.stderr.setEncoding("utf8").on("data", (d) => { stderr += d; });
  return new Promise((resolve) => {
    let kill;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
      kill = setTimeout(() => child.kill("SIGKILL"), 10_000);
    }, timeout);
    let settled = false;
    const done = (rc) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer); clearTimeout(kill);
      resolve({ rc, stdout, stderr, timedOut });
    };
    child.on("error", (e) => { stderr += `\n${e.message}`; done(1); });
    // 자식이 promise 를 돌려주면(테스트용 주입) 숫자로 풀어 준다.
    if (typeof child?.then === "function") child.then((v) => done(Number.isInteger(v) ? v : 0), (e) => { stderr += `\n${e?.message ?? e}`; done(1); });
    child.on("close", (code, signal) => done(code ?? (signal ? 1 : 0)));
  });
}

const readJson = (file) => { try { return JSON.parse(readFileSync(file, "utf8")); } catch { return null; } };
// 번들 명령 이름은 공백이 들어간다("store country-rules"). 같은 앞단어로 시작하는 긴 이름부터 맞춘다.
export function commandOfArgs(args, specs) {
  const words = args.slice(0, 3).map(String);
  return Object.keys(specs).sort((a, b) => b.length - a.length)
    .find((n) => n.split(" ").every((w, i) => words[i] === w)) || null;
}

// 실행기. deps = {bin, env, timeoutMs, run, now, log, err, specs}
// -> {code, manifest, folder}
export async function runBundles({ def = loadBundles(), selected, specs, opt = {}, account, configPath, deps = {} }) {
  const env = deps.env || process.env;
  const bin = deps.bin || env.ALIBABA_SELLER_BUNDLE_BIN || BIN;
  const timeoutMs = deps.timeoutMs ?? (Number(env.ALIBABA_SELLER_BUNDLE_TIMEOUT_MS) || DEFAULT_TIMEOUT_MS);
  const run = deps.run || ((command) => runChild(command, { bin, env, timeout: timeoutMs }));
  const now = deps.now || (() => new Date());
  const log = deps.log || console.log, errOut = deps.err || console.error;
  const ranges = resolveRange({ days: opt.days ?? 30, start: opt.start, end: opt.end, max: opt.max || opt.all });
  const period = {
    start: ranges.start, end: ranges.end,
    days: Math.round((Date.parse(ranges.end) - Date.parse(ranges.start)) / 864e5) + 1,
    mode: opt.max || opt.all ? "max" : opt.start || opt.end ? "range" : "days",
  };

  // --resume <bundle 폴더>: 이미 있는 manifest 를 읽어 status ok 가 아닌 명령만 다시 실행.
  const folder = opt.resume ? path.resolve(String(opt.resume))
    : path.join(opt.out || path.join(account.outDir || path.join(homedir(), "30_Assets", account.name, "seller-report"), "bundles"),
      bundleDirName(account.name, period, now()));
  const manifestFile = path.join(folder, "manifest.json");
  let manifest = null;
  if (opt.resume) {
    manifest = readJson(manifestFile);
    if (!manifest) throw new Error(`--resume: manifest.json 이 없다: ${manifestFile}`);
  }
  mkdirSync(folder, { recursive: true });
  const slugs = Object.fromEntries([...(manifest?.commands || []).map((c) => [c.command, c.slug]),
    ...commandsOf(def, selected).map((c) => [c.command, c.slug])]);
  const plans = manifest ? manifest.commands.map((c) => ({ bundle: c.bundle, command: c.command, slug: c.slug || slugs[c.command] }))
    : commandsOf(def, selected);
  if (!manifest) {
    manifest = {
      schema: BUNDLE_SCHEMA, account: account.name, period, started_at: now().toISOString(), finished_at: null,
      cli_version: cliVersion(), bundles: [...selected], commands: [], totals: { ok: 0, failed: 0, skipped: 0 },
    };
  }
  if (ranges.warnings?.length) manifest.period_note = ranges.warnings.join("; ");
  const entries = new Map((manifest.commands || []).map((c) => [c.command, c]));
  // 재개: 앞서 남긴 명령 기록(rc·files…)은 그대로 두고 순서만 정의 파일 순서로 맞춘다.
  manifest.commands = plans.map((p) => entries.get(p.command) || {
    bundle: p.bundle, command: p.command, slug: p.slug, rc: null, status: "pending", started_at: null, finished_at: null,
    tables: {}, files: [], error: null, period: null,
  });
  const write = () => {
    manifest.totals = { ok: 0, failed: 0, skipped: 0 };
    for (const c of manifest.commands) if (c.status in manifest.totals) manifest.totals[c.status]++;
    const tmp = manifestFile + ".tmp";
    writeFileSync(tmp, JSON.stringify(manifest, null, 1));
    renameSync(tmp, manifestFile);
  };

  for (const entry of manifest.commands) {
    if (opt.resume && entry.status === "ok") continue;
    const dir = path.join(folder, entry.slug);
    mkdirSync(dir, { recursive: true });
    const spec = specs[entry.command];
    const skip = (error, period2 = {}) => {
      Object.assign(entry, { rc: null, status: "skipped", started_at: null, finished_at: now().toISOString(), tables: {}, files: [], error, period: null });
      if (period2.note) entry.period_note = period2.note;
      errOut(`건너뜀: ${entry.command} — ${error}`);
      write();
    };
    if (!spec) { skip("번들에 없는 명령(모르는 명령)"); continue; }
    const period2 = periodArgs(spec, opt);
    if (period2.note) entry.period_note = period2.note;
    let args;
    try { args = buildArgs(entry.command, { spec, period2, opt, account, configPath, folder, slug: entry.slug }); }
    catch (e) { skip(e.message, period2); continue; }
    entry.started_at = now().toISOString();
    entry.period_arg = period2.args.join(" ") || null;
    errOut(`[${entry.bundle}] ${entry.command} …`);
    const res = await run(args);
    writeFileSync(path.join(dir, "stderr.txt"), redactText(res.stderr || ""));
    const result = res.rc === 0 ? readJsonText(res.stdout) : null;
    const resultFile = path.join(dir, "result.json");
    if (result && typeof result === "object" && !Array.isArray(result)) writeFileSync(resultFile, JSON.stringify(result, null, 1));
    const files = [];
    if (existsSync(resultFile)) files.push(`${entry.slug}/result.json`);
    for (const f of readdirSync(dir).sort()) if (f.endsWith(".csv")) files.push(`${entry.slug}/${f}`);
    files.push(`${entry.slug}/stderr.txt`);
    Object.assign(entry, {
      rc: res.rc, status: res.rc === 0 ? "ok" : "failed", finished_at: now().toISOString(),
      tables: result ? Object.fromEntries(Object.entries(result).filter(([, v]) => Array.isArray(v)).map(([k, v]) => [k, v.length])) : {},
      files,
      error: res.rc === 0 ? null : res.timedOut ? `시간 초과(${Math.round(timeoutMs / 1000)}초)` : tailOf(res.stderr) || `종료 코드 ${res.rc}`,
      period: result ? firstPeriodRow(res.stdout) : null,
    });
    write();
  }

  manifest.finished_at = now().toISOString();
  write();
  const { ok, failed, skipped } = manifest.totals;
  const code = failed + skipped === 0 ? 0 : ok > 0 ? 3 : 5;
  log(JSON.stringify({
    folder, account: manifest.account, period, bundles: manifest.bundles, totals: manifest.totals,
    manifest: manifestFile, exit: code,
  }));
  return { code, manifest, folder };
}

const readJsonText = (text) => { try { return JSON.parse(String(text || "").trim()); } catch { return null; } };
// bundle list: 번들 이름·ko·when·명령 목록 + 제외 목록(사유). --json = 같은 내용의 객체.
export function formatList(def, { json = false, log = console.log } = {}) {
  if (json) {
    log(JSON.stringify({ schema: def.schema, bundles: def.bundles.map((b) => ({ ...b, commands: [...b.commands] })), excluded: def.excluded }));
    return 0;
  }
  const lines = [];
  for (const b of def.bundles) {
    lines.push(`${b.name.padEnd(10)} ${b.ko}\n           언제: ${b.when}\n           명령 ${b.commands.length}개: ${b.commands.join(", ")}`);
  }
  lines.push("", `제외 ${def.excluded.length}개:`);
  for (const e of def.excluded) lines.push(`  ${e.command.padEnd(30)} ${e.reason}`);
  log(lines.join("\n"));
  return 0;
}
