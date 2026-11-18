// 테스트 전용 파일 — 계정·쿠키·네트워크 없음. Product Analyzer 마감 주·달(tail window) 회귀 테스트.
process.env.ALIBABA_SELLER_PYTHON = "python3";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { dumpWindows } from "../lib/cli.mjs";
import { closedPeriodEnd, excludeOpenTail, planWindows } from "../lib/periods.mjs";

// 2026-11-17 (KST) = PST yesterday 2026-11-16: closed week ends 11-14, closed month ends 10-31.
const TODAY = new Date(Date.UTC(2026, 10, 16));
const PERIOD = { mode: "week", maxDays: 31, earliestDate: null, status: "unverified" };

function seedWeekCache(root, start, end) {
  const dir = path.join(root, `w-${start}_${end}--analytics-product`);
  mkdirSync(path.join(dir, "raw"), { recursive: true });
  const stamp = new Date().toISOString();
  writeFileSync(path.join(dir, "raw", "analytics-product.jsonl"), "");
  writeFileSync(path.join(dir, "manifest.json"), JSON.stringify({ start, end, generatedAt: stamp,
    pages: [{ key: "analytics-product", status: "ok", fetchedAt: stamp, granularity: "week" }] }));
  return dir;
}

test("closed period boundary follows the engine's closed week/month rule", () => {
  assert.equal(closedPeriodEnd(TODAY), "2026-11-14");
  assert.equal(closedPeriodEnd(new Date(Date.UTC(2026, 10, 1))), "2026-10-31");
  assert.equal(closedPeriodEnd(new Date(Date.UTC(2026, 10, 21))), "2026-11-14");
});

test("a tail window after the last closed week is dropped, not fetched", () => {
  const root = mkdtempSync(path.join(tmpdir(), "asc-tail-"));
  const errs = [], calls = [];
  try {
    const cached = { start: "2026-10-18", end: "2026-11-14", dir: seedWeekCache(root, "2026-10-18", "2026-11-14") };
    const windows = planWindows({ start: "2026-10-18", end: "2026-11-16" }, PERIOD, [cached]);
    assert.deepEqual(windows.map(w => [w.start, w.end]), [["2026-10-18", "2026-11-14"], ["2026-11-15", "2026-11-16"]]);
    const planned = excludeOpenTail(windows, PERIOD, TODAY);
    assert.deepEqual(planned.windows.map(w => [w.start, w.end]), [["2026-10-18", "2026-11-14"]]);
    assert.deepEqual(planned.excluded.map(w => [w.start, w.end, w.coverage_start, w.coverage_end]),
      [["2026-11-15", "2026-11-16", "2026-10-18", "2026-11-14"]]);

    const res = dumpWindows({ account: { name: "fixture" }, configPath: "fixture.json", pages: ["analytics-product"],
      range: { start: "2026-10-18", end: "2026-11-16" }, root, opt: {}, granularity: "week", today: TODAY,
      run: (c, a) => { calls.push(a); return 0; }, err: (m) => errs.push(m) });

    assert.equal(res.code, 0);
    assert.equal(calls.filter(a => a.includes("2026-11-15")).length, 1, "only the snapshot (day) extras fetch the tail");
    assert.ok(calls.every(a => !a.includes("product_summary.week")), "the open week/month tail must not reach the engine");
    assert.equal(res.excluded.length, 1);
    assert.equal(res.excluded[0].reason, "아직 마감 전이라 Product Analyzer 미제공");
    assert.equal(errs.filter(m => m.includes("안내:")).length, 1);
    assert.match(errs.join("\n"), /안내: 2026-11-15~2026-11-16 은 아직 마감 전이라 Product Analyzer 미제공 → 제외\(마감된 2026-10-18~2026-11-14 만 집계\)/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("a range with no closed week/month still fails honestly (engine exit 5)", () => {
  const root = mkdtempSync(path.join(tmpdir(), "asc-tail-"));
  const errs = [], calls = [];
  try {
    const res = dumpWindows({ account: { name: "fixture" }, configPath: "fixture.json", pages: ["analytics-product"],
      range: { start: "2026-11-15", end: "2026-11-16" }, root, opt: {}, granularity: "week", today: TODAY,
      run: (c, a) => { calls.push(a); return 5; }, err: (m) => errs.push(m) });

    assert.equal(res.code, 5);
    assert.deepEqual([res.failedWindow.start, res.failedWindow.end], ["2026-11-15", "2026-11-16"]);
    assert.equal(res.excluded.length, 0);
    assert.equal(calls.filter(a => a.includes("analytics-product")).length, 1);
    assert.equal(errs.filter(m => m.includes("아직 마감 전이라 Product Analyzer 미제공 → 제외")).length, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("range tables (ads daily) still fetch the tail dates of the same request", () => {
  const root = mkdtempSync(path.join(tmpdir(), "asc-tail-"));
  const errs = [], calls = [];
  try {
    seedWeekCache(root, "2026-10-18", "2026-11-14");
    const res = dumpWindows({ account: { name: "fixture" }, configPath: "fixture.json", pages: ["ads-report", "analytics-product"],
      range: { start: "2026-10-18", end: "2026-11-16" }, root, opt: {}, granularity: "week", today: TODAY,
      run: (c, a) => { calls.push(a); return 0; }, err: (m) => errs.push(m) });

    assert.equal(res.code, 0);
    const ads = calls.find(a => a.includes("ads-report"));
    assert.equal(ads[ads.indexOf("--start") + 1], "2026-10-18");
    assert.equal(ads[ads.indexOf("--end") + 1], "2026-11-16", "range table keeps the tail dates");
    assert.ok(calls.every(a => !a.includes("product_summary.week")), "week tail is not fetched for Product Analyzer");
    assert.equal(res.excluded.length, 1);
    assert.match(errs.join("\n"), /제외\(마감된 2026-10-18~2026-11-14 만 집계\)/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
