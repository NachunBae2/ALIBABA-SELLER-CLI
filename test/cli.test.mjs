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
