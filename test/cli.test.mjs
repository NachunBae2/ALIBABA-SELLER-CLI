process.env.ALIBABA_SELLER_PYTHON = "python3"; // same launcher name on every OS in these offline tests
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { resolveRange, splitRange, parseDate } from "../lib/dates.mjs";
import { main, parseArgs, cachedPages, incompletePages } from "../lib/cli.mjs";
import { latestReportDay } from "../lib/dates.mjs";

const today = new Date(Date.UTC(2026, 10, 17));

test("start/end pass through unchanged", () => {
  assert.deepEqual(resolveRange({ start: "2026-06-01", end: "2026-11-15" }, today), { start: "2026-06-01", end: "2026-11-15", warnings: [] });
});

test("invalid dates, reversed range and mixed presets are rejected", () => {
  assert.throws(() => parseDate("2026-02-30"), /YYYY-MM-DD/);
  assert.throws(() => resolveRange({ start: "2026/06/01", end: "2026-06-02" }, today), /형식/);
  assert.throws(() => resolveRange({ start: "2026-06-10", end: "2026-06-01" }, today), /늦다/);
  assert.throws(() => resolveRange({ start: "2026-06-10", end: "2026-06-11", days: "3" }, today), /하나만/);
  assert.throws(() => resolveRange({ days: "0" }, today), /정수/);
  assert.throws(() => resolveRange({}, today), /--start/);
});

test("future end is clipped to today with a warning", () => {
  const r = resolveRange({ start: "2026-11-01", end: "2027-01-01" }, today);
  assert.equal(r.end, "2026-11-17");
  assert.match(r.warnings[0], /집계 전/);
});

test("--days and --all become start/end", () => {
  assert.deepEqual(resolveRange({ days: "7" }, today), { start: "2026-11-11", end: "2026-11-17", warnings: [] });
  const all = resolveRange({ all: true }, today);
  assert.equal(all.end, "2026-11-17");
  assert.equal(all.start, "2026-05-21");
});

test("ranges longer than 91 days split into contiguous windows", () => {
  assert.deepEqual(splitRange("2026-06-01", "2026-06-05"), [{ start: "2026-06-01", end: "2026-06-05" }]);
  const w = splitRange("2026-06-01", "2026-11-15");
  assert.deepEqual(w, [
    { start: "2026-06-01", end: "2026-08-30" },
    { start: "2026-08-31", end: "2026-11-15" },
  ]);
  const days = w.reduce((n, x) => n + (Date.parse(x.end) - Date.parse(x.start)) / 864e5 + 1, 0);
  assert.equal(days, 168);
  assert.equal(splitRange("2026-01-01", "2026-12-31", 91).length, 5);
});

test("args parse values, repeats --from, and booleans", () => {
  const { pos, opt } = parseArgs(["ads", "analyze", "--from", "a", "--from=b", "--json", "--start", "2026-06-01"]);
  assert.deepEqual(pos, ["ads", "analyze"]);
  assert.deepEqual(opt.from, ["a", "b"]);
  assert.equal(opt.json, true);
  assert.equal(opt.start, "2026-06-01");
});

function fakeDumpDir() {
  const d = mkdtempSync(path.join(tmpdir(), "asc-"));
  mkdirSync(path.join(d, "raw"));
  return d;
}

test("offline --from runs only the report, with the queried range", () => {
  const calls = [];
  const d = fakeDumpDir();
  const code = main(["ads", "products", "--from", d, "--start", "2026-06-01", "--end", "2026-06-30", "--csv"], { run: (c, a) => { calls.push([c, a]); return 0; }, log() {}, err() {} });
  assert.equal(code, 0);
  assert.equal(calls.length, 1);
  const [cmd, args] = calls[0];
  assert.equal(cmd, "python3");
  assert.match(args[0], /report\.py$/);
  assert.deepEqual(args.slice(1, 4), ["ads-products", "--from", d]);
  assert.ok(args.join(" ").includes("--start 2026-06-01 --end 2026-06-30"));
  assert.ok(!calls.some(([c]) => c === "aside"));
});

test("live run dumps one engine call per 91-day window, then merges them in one report", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "asc-cfg-"));
  const cfg = path.join(dir, "accounts.json");
  writeFileSync(cfg, JSON.stringify({ fixture: { loginId: "fixture-login", outDir: dir } }));
  const calls = [];
  const code = main(["ads", "analyze", "--account", "fixture", "--config", cfg, "--start", "2026-06-01", "--end", "2026-11-15"],
    { run: (c, a) => { calls.push(a); return 0; }, log() {}, err() {} });
  assert.equal(code, 0);
  const dumps = calls.filter((a) => a[0].endsWith("seller_central_raw.py"));
  assert.equal(dumps.length, 2);
  assert.ok(dumps[0].join(" ").includes("--start 2026-06-01 --end 2026-08-30"));
  assert.ok(dumps[1].join(" ").includes("--start 2026-08-31 --end 2026-11-15"));
  assert.ok(dumps.every((a) => a.includes("--pages")));
  const report = calls.at(-1);
  assert.match(report[0], /report\.py$/);
  assert.equal(report.filter((x) => x === "--from").length, 2);
});

test("engine failure stops before the report and keeps the exit code", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "asc-cfg-"));
  const cfg = path.join(dir, "accounts.json");
  writeFileSync(cfg, JSON.stringify({ fixture: { loginId: "fixture-login", outDir: dir } }));
  const calls = [];
  const code = main(["inquiry", "list", "--account", "fixture", "--config", cfg, "--days", "3"], { run: (c, a) => { calls.push(a); return 2; }, log() {}, err() {} });
  assert.equal(code, 2);
  assert.equal(calls.length, 1);
});

test("account config errors: unknown key, blank loginId, bad aside profile", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "asc-cfg-"));
  const cfg = path.join(dir, "accounts.json");
  const run = () => 0;
  for (const acc of [{ loginId: "" }, { loginId: "x", asideAccount: "../u1" }]) {
    writeFileSync(cfg, JSON.stringify({ fixture: acc }));
    assert.throws(() => main(["bids", "--account", "fixture", "--config", cfg, "--days", "3"], { run, log() {}, err() {} }));
  }
  assert.throws(() => main(["bids", "--account", "nope", "--config", cfg, "--days", "3"], { run, log() {}, err() {} }), /없다/);
});

test("--weeks N covers the last N complete PST weeks (Sunday..Saturday)", async () => {
  const { latestCompleteWeek } = await import("../lib/dates.mjs");
  const tue = new Date("2026-11-17T09:00:00+09:00"); // Monday 2026-11-16 in PST
  const w = latestCompleteWeek(tue);
  assert.deepEqual([w.start.toISOString().slice(0, 10), w.end.toISOString().slice(0, 10)], ["2026-11-08", "2026-11-14"]);
  assert.deepEqual(resolveRange({ weeks: "2" }, today, tue).start, "2026-11-01");
  assert.deepEqual(resolveRange({ weeks: "2" }, today, tue).end, "2026-11-14");
  const sat = new Date("2026-11-14T12:00:00-08:00");
  assert.equal(latestCompleteWeek(sat).end.toISOString().slice(0, 10), "2026-11-07");
});

test("analytics products dumps with week granularity and passes it to the report", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "asc-cfg-"));
  const cfg = path.join(dir, "accounts.json");
  writeFileSync(cfg, JSON.stringify({ fixture: { loginId: "fixture-login", outDir: dir } }));
  const calls = [];
  main(["analytics", "products", "--account", "fixture", "--config", cfg, "--start", "2026-10-01", "--end", "2026-10-31"], { run: (c, a) => { calls.push(a); return 0; }, log() {}, err() {} });
  assert.ok(calls[0].join(" ").includes("--pages analytics-product --granularity week"));
  assert.ok(calls[1].join(" ").includes("analytics-products"));
  assert.ok(calls[1].join(" ").endsWith("--granularity week"));
});

test("two CLIs: alibaba-inquiry = OneTalk actions only, alibaba-data = all data incl. inquiries", () => {
  const out = [], errs = [];
  const deps = (scope) => ({ scope, log: (m) => out.push(m), err: (m) => errs.push(m), run: () => { throw new Error("no run"); } });
  assert.equal(main(["ads", "report", "--account", "x"], deps("inquiry")), 64);
  assert.match(errs.pop(), /alibaba-data ads report/);
  assert.equal(main(["inquiries", "stats", "--account", "x"], deps("inquiry")), 64);
  assert.match(errs.pop(), /alibaba-data inquiries stats/);
  assert.equal(main(["reply"], deps("data")), 64);
  assert.equal(main(["--help"], deps("inquiry")), 0);
  const ih = out.pop();
  assert.match(ih, /^alibaba-inquiry \(문의대응\)[\s\S]*\n  send-card /);
  assert.doesNotMatch(ih, /\n  inquiries list /);
  assert.equal(main(["--help"], deps("data")), 0);
  const help = out.pop();
  assert.match(help, /ads quality/);
  assert.doesNotMatch(help, /send-card/);
  assert.match(help, /\n  inquiries list /);
});

test("old inquiry data names still run (from either CLI) as alibaba-data inquiries …", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "asc-cfg-"));
  const cfg = path.join(dir, "accounts.json");
  writeFileSync(cfg, JSON.stringify({ fixture: { loginId: "fixture-login", outDir: dir } }));
  for (const [scope, argv] of [["inquiry", ["list"]], ["inquiry", ["inquiry", "stats"]], ["data", ["inquiry", "list"]], ["data", ["inquiries", "list"]]]) {
    const calls = [], errs = [];
    const code = main([...argv, "--account", "fixture", "--config", cfg, "--days", "3"],
      { scope, run: (c, a) => { calls.push(a); return 2; }, log() {}, err: (m) => errs.push(m) });
    assert.equal(code, 2, argv.join(" "));
    assert.ok(calls[0].join(" ").includes("--pages inquiries"));
    if (argv[0] !== "inquiries") assert.match(errs[0], /alibaba-data inquiries (list|stats)/);
  }
});

test("keywords recommend --product: seeds from that product, then the keyword-index page into the last window", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "kw-"));
  const cfg = path.join(dir, "accounts.json");
  writeFileSync(cfg, JSON.stringify({ acc: { loginId: "x", outDir: dir } }));
  const calls = [];
  const run = (cmd, args) => { calls.push(args); const o = args[args.indexOf("--out") + 1]; if (args[0].endsWith("seller_central_raw.py")) mkdirSync(path.join(o, "raw"), { recursive: true }); return 0; };
  const capture = (cmd, args) => { calls.push(args); return { code: 0, stdout: JSON.stringify({ keyword_seeds: [{ seed: "serum", basis: "b" }, { seed: "face cream", basis: "b" }] }) }; };
  const code = main(["keywords", "recommend", "--account", "acc", "--config", cfg, "--start", "2026-07-01", "--end", "2026-07-31", "--top", "5", "--product", "serum"],
    { run, capture, log: () => {}, err: () => {}, scope: "data" });
  assert.equal(code, 0);
  const seedCall = calls.find((a) => a.includes("keyword-seeds"));
  assert.equal(seedCall[seedCall.indexOf("--product") + 1], "serum");
  const idx = calls.find((a) => a.includes("keyword-index"));
  assert.equal(idx[idx.indexOf("--seeds") + 1], "serum|face cream");
  assert.equal(idx[idx.indexOf("--kw-nd") + 1], "30d");
  const rep = calls.at(-1);
  assert.equal(rep[1], "keywords-recommend");
  assert.equal(rep[rep.indexOf("--top") + 1], "5");
  assert.equal(rep[rep.indexOf("--product") + 1], "serum");
  // no --product: product list only, no Keyword Index fetch
  const pick = [];
  main(["keywords", "recommend", "--account", "acc", "--config", cfg, "--start", "2026-07-01", "--end", "2026-07-31"],
    { run: (c, a) => { pick.push(a); const o = a[a.indexOf("--out") + 1]; if (a[0].endsWith("seller_central_raw.py")) mkdirSync(path.join(o, "raw"), { recursive: true }); return 0; },
      capture: () => { throw new Error("no seed lookup without --product"); }, log: () => {}, err: () => {}, scope: "data" });
  assert.ok(!pick.some((a) => a.includes("keyword-index")));
  assert.ok(!pick.at(-1).includes("--product") && !pick.at(-1).includes("--all-products"));
  const own = [];
  main(["keywords", "--account", "acc", "--config", cfg, "--start", "2026-07-01", "--end", "2026-07-31", "--seed", "toner", "--nd", "7d"],
    { run: (c, a) => { own.push(a); const o = a[a.indexOf("--out") + 1]; if (a[0].endsWith("seller_central_raw.py")) mkdirSync(path.join(o, "raw"), { recursive: true }); return 0; },
      capture: () => { throw new Error("no seed lookup when --seed is given"); }, log: () => {}, err: () => {} });
  const idx2 = own.find((a) => a.includes("keyword-index"));
  assert.equal(idx2[idx2.indexOf("--seeds") + 1], "toner");
  assert.equal(idx2[idx2.indexOf("--kw-nd") + 1], "7d");
});

test("login --browser and session status pass through to the engine; plain login stays the Aside login", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "asc-cfg-"));
  const cfg = path.join(dir, "accounts.json");
  writeFileSync(cfg, JSON.stringify({ fixture: { loginId: "fixture-login" } }));
  const calls = [];
  const deps = { scope: "data", run: (c, a) => { calls.push(a.slice(a.findIndex((x) => x.endsWith("seller_central_raw.py")) + 1)); return 0; }, log() {}, err() {} };
  const base = ["--account", "fixture", "--config", cfg];
  assert.equal(main(["login", ...base, "--browser", "chrome", "--profile", "Profile 1", "--via-cdp"], deps), 0);
  assert.deepEqual(calls.pop(), [...base, "--browser-login", "chrome", "--profile", "Profile 1", "--via-cdp"]);
  assert.equal(main(["login", ...base, "--browser", "edge"], deps), 0);
  assert.deepEqual(calls.pop(), [...base, "--browser-login", "edge"]);
  assert.equal(main(["login", ...base, "--browser", "chrome", "--user-data-dir", "/tmp/other chrome"], deps), 0);
  assert.deepEqual(calls.pop(), [...base, "--browser-login", "chrome", "--user-data-dir", "/tmp/other chrome"]);
  assert.equal(main(["login", ...base, "--browser", "chrome", "--sign-in"], deps), 0);
  assert.deepEqual(calls.pop(), [...base, "--browser-login", "chrome", "--sign-in"]);
  assert.equal(main(["login", ...base], deps), 0);
  assert.deepEqual(calls.pop(), [...base, "--login"]);
  assert.equal(main(["session", "status", ...base], { ...deps, scope: "inquiry" }), 0);
  assert.deepEqual(calls.pop(), [...base, "--session-status"]);
  assert.equal(main(["session", "nope", ...base], deps), 64);
  assert.equal(calls.length, 0);
});

test("dump cache: a page fetched today (or settled after the window) is reused; filtered tables and other seeds are not", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "cache-"));
  mkdirSync(path.join(dir, "raw"));
  for (const k of ["ads-report", "analytics-product", "keyword-index"]) writeFileSync(path.join(dir, "raw", k + ".jsonl"), "");
  const now = new Date("2026-11-17T10:00:00");
  const today = new Date("2026-11-17T09:00:00").toISOString();
  writeFileSync(path.join(dir, "manifest.json"), JSON.stringify({ pages: [
    { key: "ads-report", status: "ok", fetchedAt: today, rangeTables: ["ai.product", "ai.keyword"], rangeTablesFiltered: true },
    { key: "analytics-product", status: "ok", fetchedAt: new Date("2026-11-01T09:00:00").toISOString(), granularity: "month" },
    { key: "keyword-index", status: "ok", fetchedAt: today, seeds: ["serum"], nd: "30d" }] }));
  const w = { start: "2026-10-01", end: "2026-10-20" };
  assert.deepEqual(cachedPages(dir, w, ["ads-report", "analytics-product"], { tables: ["ai.product"], now }), ["ads-report", "analytics-product"]);
  assert.deepEqual(cachedPages(dir, w, ["ads-report"], { now }), []); // full table set wanted
  assert.deepEqual(cachedPages(dir, { start: "2026-10-20", end: "2026-10-31" }, ["analytics-product"], { now }), []); // fetched before settled
  assert.deepEqual(cachedPages(dir, w, ["keyword-index"], { seeds: ["serum"], nd: "30d", now }), ["keyword-index"]);
  assert.deepEqual(cachedPages(dir, w, ["keyword-index"], { seeds: ["toner"], nd: "30d", now }), []);
});

test("dump cache: ads report short of totalRows is fetched again and continued", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "cache-short-"));
  mkdirSync(path.join(dir, "raw"));
  writeFileSync(path.join(dir, "raw", "ads-report.jsonl"), "");
  const now = new Date("2026-11-17T10:00:00");
  writeFileSync(path.join(dir, "manifest.json"), JSON.stringify({ pages: [
    { key: "ads-report", status: "ok", fetchedAt: new Date("2026-11-17T09:00:00").toISOString(), rangeIncomplete: 2 }] }));
  const w = { start: "2026-10-01", end: "2026-10-20" };
  assert.deepEqual(cachedPages(dir, w, ["ads-report"], { now }), []);
  assert.deepEqual(incompletePages(dir, ["ads-report", "inquiries"]), ["ads-report"]);
});

test("--days ends on yesterday in US Pacific time, like the seller center 'Last 7 days'", () => {
  // 2026-11-17 14:00 KST = 2026-11-16 21:00 PST -> latest report day 2026-11-15 (owner screen: 11-09..11-15)
  const day = latestReportDay(new Date("2026-11-17T05:00:00Z"));
  assert.equal(day.toISOString().slice(0, 10), "2026-11-15");
  assert.deepEqual(resolveRange({ days: "7" }, day), { start: "2026-11-09", end: "2026-11-15", warnings: [] });
  assert.equal(resolveRange({ days: "30" }, day).start, "2026-10-17");
});
