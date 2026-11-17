process.env.ALIBABA_SELLER_PYTHON = "python3"; // same launcher name on every OS in these offline tests
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { resolveRange, splitRange, parseDate } from "../lib/dates.mjs";
import { main, parseArgs } from "../lib/cli.mjs";

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
  assert.match(r.warnings[0], /미래/);
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

test("keywords recommend: seeds from our dump, then the keyword-index page into the last window", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "kw-"));
  const cfg = path.join(dir, "accounts.json");
  writeFileSync(cfg, JSON.stringify({ acc: { loginId: "x", outDir: dir } }));
  const calls = [];
  const run = (cmd, args) => { calls.push(args); const o = args[args.indexOf("--out") + 1]; if (args[0].endsWith("seller_central_raw.py")) mkdirSync(path.join(o, "raw"), { recursive: true }); return 0; };
  const capture = (cmd, args) => { calls.push(args); return { code: 0, stdout: JSON.stringify({ keyword_seeds: [{ seed: "serum", basis: "b" }, { seed: "face cream", basis: "b" }] }) }; };
  const code = main(["keywords", "recommend", "--account", "acc", "--config", cfg, "--start", "2026-07-01", "--end", "2026-07-31", "--top", "5"],
    { run, capture, log: () => {}, err: () => {}, scope: "data" });
  assert.equal(code, 0);
  const idx = calls.find((a) => a.includes("keyword-index"));
  assert.equal(idx[idx.indexOf("--seeds") + 1], "serum|face cream");
  assert.equal(idx[idx.indexOf("--kw-nd") + 1], "30d");
  const rep = calls.at(-1);
  assert.equal(rep[1], "keywords-recommend");
  assert.equal(rep[rep.indexOf("--top") + 1], "5");
  const own = [];
  main(["keywords", "--account", "acc", "--config", cfg, "--start", "2026-07-01", "--end", "2026-07-31", "--seed", "toner", "--nd", "7d"],
    { run: (c, a) => { own.push(a); const o = a[a.indexOf("--out") + 1]; if (a[0].endsWith("seller_central_raw.py")) mkdirSync(path.join(o, "raw"), { recursive: true }); return 0; },
      capture: () => { throw new Error("no seed lookup when --seed is given"); }, log: () => {}, err: () => {} });
  const idx2 = own.find((a) => a.includes("keyword-index"));
  assert.equal(idx2[idx2.indexOf("--seeds") + 1], "toner");
  assert.equal(idx2[idx2.indexOf("--kw-nd") + 1], "7d");
});
