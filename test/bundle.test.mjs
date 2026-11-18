// 번들 정의·실행기 테스트 — 네트워크·계정 없음. 자식 명령은 deps.run 가짜로 대신한다.
// 시작 명령(bundle) 자신이 자식으로 다시 불릴 때는 test/fixtures/bundle-child.mjs 가 그 역할을 한다.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync, existsSync, mkdirSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { main, READ_COMMANDS, usage } from "../lib/cli.mjs";
import { CATALOG } from "../lib/periods.mjs";
import { loadBundles, pickBundles, commandsOf, slugOf } from "../lib/bundles.mjs";
import { runBundles, periodArgs, redactText, bundleDirName, stampOf, firstPeriodRow, commandOfArgs, bundleSpecs } from "../lib/bundle.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.dirname(here);
const def = loadBundles();                       // 정본 (감독이 넣은 lib/bundles.json)
const SPECS = bundleSpecs(READ_COMMANDS, CATALOG.pages);   // 실행기가 쓰는 것과 같은 스펙(pages.json 허용 days)
const fixture = JSON.parse(readFileSync(path.join(here, "fixtures", "bundles.json"), "utf8"));
const account = { name: "fixture", loginId: "fixture_login" };
const now = new Date(2026, 10, 18, 7, 16, 31);   // 2026-11-18 07:16:31 (로컬 시각)
const names = (bundle) => def.bundles.find((b) => b.name === bundle).commands;

const tmp = (t) => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "bundle-test-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
};

// 가짜 자식: stdout = result.json 이 될 --json 출력, fail 목록은 rc 3.
function fakeRun({ fail = [], calls = [], stdout = () => JSON.stringify({ rows: [{ period: "P", n: 1 }] }), stderr = "ok line\nlast line\n" } = {}) {
  return async (args) => {
    calls.push(args);
    const command = commandOfArgs(args, SPECS);
    if (fail.includes(command)) return { rc: 3, stdout: "", stderr: "boom\nfailed tail\n", timedOut: false };
    return { rc: 0, stdout: stdout(command), stderr, timedOut: false };
  };
}
// 가짜 자식이 --out 폴더에 CSV 를 남기는 것까지 흉내낸다.
const withCsv = (run) => async (args) => {
  const r = await run(args);
  const dir = args[args.indexOf("--out") + 1];
  mkdirSync(dir, { recursive: true });
  writeFileSync(path.join(dir, "ads_report.csv"), "a,b\n1,2\n");
  return r;
};

test("bundle definition: every READ_COMMANDS name is in a bundle or excluded, no overlap, no unknown", () => {
  const known = new Set(Object.keys(READ_COMMANDS));
  const included = def.bundles.flatMap((b) => b.commands);
  const excluded = def.excluded.map((e) => e.command);
  assert.deepEqual(included.filter((c) => excluded.includes(c)), [], "번들과 제외 목록에 겹친 명령");
  assert.deepEqual(included.filter((c) => !known.has(c)), [], "READ_COMMANDS 에 없는 번들 명령");
  // 제거한 명령은 READ_COMMANDS 에 없어도 excluded 에 사유와 함께 남긴다(커버리지 표의 '제외+이유' 근거).
  for (const e of def.excluded.filter((e) => !known.has(e.command)))
    assert.ok(e.reason.trim(), `없는 명령인데 사유 없음: ${e.command}`);
  assert.deepEqual([...known].filter((c) => !included.includes(c) && !excluded.includes(c)), [], "번들에도 제외에도 없는 명령");
  assert.deepEqual(included.filter((c, i) => included.indexOf(c) !== i), [], "두 번 들어간 명령");
  for (const e of def.excluded) assert.ok(e.reason.trim(), `제외 사유 없음: ${e.command}`);
  for (const b of def.bundles) assert.ok(b.ko && b.when, `ko/when 없음: ${b.name}`);
});

test("fixture mounts the same commands (definition is the single source)", () => {
  assert.deepEqual(Object.fromEntries(fixture.bundles.map((b) => [b.name, b.commands])),
    Object.fromEntries(def.bundles.map((b) => [b.name, b.commands])));
  assert.deepEqual(fixture.excluded, def.excluded);
});

test("slug: words with _, hyphen kept", () => {
  assert.equal(slugOf("ads report"), "ads_report");
  assert.equal(slugOf("store country-rules"), "store_country-rules");
  assert.equal(slugOf("keywords"), "keywords");
});

test("bundle pick: ads only, all, unknown", () => {
  assert.deepEqual(pickBundles(def, ["ads"]).selected, ["ads"]);
  const all = def.bundles.map((b) => b.name);
  assert.deepEqual(pickBundles(def, ["all"]).selected, all);
  assert.deepEqual(pickBundles(def, []).selected, all);
  assert.deepEqual(pickBundles(def, ["xads"]).unknown, ["xads"]);
  assert.equal(commandsOf(def, ["ads"]).length, names("ads").length);
  assert.equal(commandsOf(def, ["ads"]).every((c) => c.bundle === "ads"), true);
  const total = all.reduce((n, b) => n + names(b).length, 0);
  const listed = commandsOf(def, ["all"]);
  const unique = new Set(listed.map((c) => c.command));
  assert.equal(unique.size, listed.length, "같은 명령이 두 번 들어갔다");
  assert.equal(listed.length, total, "all 은 정의된 모든 명령(중복 없이)");
});

test("discovery period: allowed value passes, other values are not passed and leave a note", () => {
  const spec = { discovery: { days: [7, 30, 90], defaultDays: 30 } };
  assert.deepEqual(periodArgs(spec, { days: 30 }), { args: ["--days", "30"], note: null });
  assert.deepEqual(periodArgs(spec, { days: 45 }), { args: [], note: "화면 선택지 7/30/90 만 지원 → 화면 기본값" });
  assert.deepEqual(periodArgs(spec, { start: "2026-11-01", end: "2026-11-10" }).args, []);
  assert.deepEqual(periodArgs({}, { days: 45 }), { args: ["--days", "45"], note: null });
  assert.deepEqual(periodArgs({}, { start: "2026-11-01", end: "2026-11-10" }), { args: ["--start", "2026-11-01", "--end", "2026-11-10"], note: null });
  assert.deepEqual(periodArgs({}, { max: true }), { args: ["--max"], note: null });
});

test("secret redaction follows engine/http_session.py rules", () => {
  const text = [
    "GET /x?csrf_token=abc123&a=1",
    "cookie: SESSION=zzz",
    '{"accessToken":"t-1","list":{"_m_h5_tk":"mval"},"token":"tok"}',
    "Authorization: Bearer bval",
  ].join("\n");
  const out = redactText(text);
  for (const secret of ["abc123", "zzz", "t-1", "mval", "bval"]) assert.equal(out.includes(secret), false, `${secret} — ${out}`);
  assert.equal(/"token":\s*"?\*\*\*/.test(out), true, `token 값이 지워지지 않았다: ${out}`);
  assert.match(out, /csrf_token=\*\*\*/);
});

test("folder name and stamp", () => {
  assert.equal(stampOf(now), "20261118071631");
  assert.equal(bundleDirName("fixture", { start: "2026-11-09", end: "2026-11-17" }, now), "bundle-fixture-2026-11-09_2026-11-17-20261118071631");
});

test("run writes the contract folder, manifest, result.json and CSVs; exit 0", async (t) => {
  const out = tmp(t);
  const calls = [];
  const res = await runBundles({
    def, selected: ["ads"], specs: SPECS, opt: { out, days: 7 }, account, configPath: "/tmp/cfg.json",
    deps: { run: withCsv(fakeRun({ calls })), now: () => now, log: () => {}, err: () => {} },
  });
  assert.equal(res.code, 0);
  const folder = path.join(out, "bundle-fixture-2026-11-11_2026-11-17-20261118071631");
  assert.equal(res.folder, folder);
  const manifest = JSON.parse(readFileSync(path.join(folder, "manifest.json"), "utf8"));
  assert.equal(manifest.schema, "alibaba-seller-bundle/1");
  assert.equal(manifest.account, "fixture");
  assert.deepEqual(manifest.period, { start: "2026-11-11", end: "2026-11-17", days: 7, mode: "days" });
  assert.deepEqual(manifest.bundles, ["ads"]);
  assert.deepEqual(manifest.commands.map((c) => c.command), names("ads"));
  assert.ok(manifest.started_at && manifest.finished_at);
  assert.equal(manifest.cli_version, JSON.parse(readFileSync(path.join(root, "package.json"), "utf8")).version);
  const first = manifest.commands[0];
  assert.equal(first.command, "ads daily");
  assert.equal(first.slug, "ads_daily");
  assert.equal(first.bundle, "ads");
  assert.equal(first.status, "ok");
  assert.equal(first.rc, 0);
  assert.deepEqual(first.tables, { rows: 1 });
  assert.equal(first.period, "P");
  assert.equal(first.error, null);
  assert.ok(existsSync(path.join(folder, "ads_daily", "result.json")));
  assert.ok(existsSync(path.join(folder, "ads_daily", "ads_report.csv")));
  assert.ok(existsSync(path.join(folder, "ads_daily", "stderr.txt")));
  assert.deepEqual(first.files, ["ads_daily/result.json", "ads_daily/ads_report.csv", "ads_daily/stderr.txt"]);
  assert.deepEqual(manifest.totals, { ok: names("ads").length, failed: 0, skipped: 0 });
  // 인자: 명령어 --account A --json --out <폴더>/<slug> <기간> --config, 정의 파일 순서대로 하나씩
  const argv = calls[0];
  assert.deepEqual(argv.slice(0, 2), ["ads", "daily"]);
  assert.equal(argv[argv.indexOf("--account") + 1], "fixture");
  assert.ok(argv.includes("--json"));
  assert.equal(argv[argv.indexOf("--out") + 1], path.join(folder, "ads_daily"));
  assert.deepEqual(argv.slice(argv.indexOf("--days"), argv.indexOf("--days") + 2), ["--days", "7"]);
  assert.equal(argv[argv.indexOf("--config") + 1], "/tmp/cfg.json");
  assert.deepEqual(calls.map((a) => commandOfArgs(a, SPECS)), names("ads"));
});

test("run: one failure continues, exit 3, later commands still run", async (t) => {
  const out = tmp(t);
  const calls = [];
  const res = await runBundles({
    def, selected: ["products"], specs: SPECS, opt: { out, days: 30 }, account,
    deps: { run: fakeRun({ calls, fail: ["products risk-counts"] }), now: () => now, log: () => {}, err: () => {} },
  });
  assert.equal(res.code, 3);
  const by = Object.fromEntries(res.manifest.commands.map((c) => [c.command, c]));
  assert.equal(by["products risk-counts"].status, "failed");
  assert.equal(by["products risk-counts"].rc, 3);
  assert.equal(by["products risk-counts"].error, "boom\nfailed tail");
  assert.equal(calls.length, names("products").length);
  assert.equal(by["products risk-tasks"].status, "ok");
  assert.deepEqual(res.manifest.totals, { ok: names("products").length - 1, failed: 1, skipped: 0 });
});

test("run: every command fails, exit 5", async (t) => {
  const out = tmp(t);
  const res = await runBundles({
    def, selected: ["products"], specs: SPECS, opt: { out }, account,
    deps: { run: fakeRun({ fail: names("products") }), now: () => now, log: () => {}, err: () => {} },
  });
  assert.equal(res.code, 5);
  assert.deepEqual(res.manifest.totals, { ok: 0, failed: names("products").length, skipped: 0 });
});

test("run: command needing an argument is skipped (not run), exit 3", async (t) => {
  const out = tmp(t);
  const calls = [];
  const res = await runBundles({
    def, selected: ["market"], specs: SPECS, opt: { out, days: 30 }, account,
    deps: { run: fakeRun({ calls }), now: () => now, log: () => {}, err: () => {} },
  });
  assert.equal(res.code, 3);
  const entry = res.manifest.commands.find((c) => c.command === "keywords index-detail");
  assert.equal(entry.status, "skipped");
  assert.equal(entry.error, "--keyword 필요");
  assert.equal(calls.some((a) => a[0] === "keywords" && a[1] === "index-detail"), false);
  assert.equal(res.manifest.totals.skipped, 1);
});

test("run: discovery out-of-range --days is not passed and leaves period_note", async (t) => {
  const out = tmp(t);
  const calls = [];
  const res = await runBundles({
    def, selected: ["market"], specs: SPECS, opt: { out, days: 45 }, account,
    deps: { run: fakeRun({ calls }), now: () => now, log: () => {}, err: () => {} },
  });
  const by = Object.fromEntries(res.manifest.commands.map((c) => [c.command, c]));
  assert.equal(res.code, 3); // index-detail 은 필수 인자 없음 → skipped
  assert.equal(by["keywords breakout"].period_arg, null);
  assert.equal(by["keywords breakout"].period_note, "화면 선택지 90/180 만 지원 → 화면 기본값");
  // 허용 값이 정의되지 않은 discovery 명령은 기간 인자를 받지 않는다(기간 선택 API 아님).
  assert.equal(by["analytics market-summary"].period_note, "기간 선택 API 아님(서버 상대 기간·스냅샷) → 기간 인자 미전달");
  assert.equal(by["analytics market-summary"].period_arg, null);
  const argv = calls.find((a) => a[0] === "keywords" && a[1] === "breakout");
  assert.equal(argv.includes("--days"), false);
  assert.equal(by["keywords"].period_arg, "--days 45"); // 기간 명령은 그대로 받는다
});

test("run: keyword given → index-detail runs with --keyword", async (t) => {
  const out = tmp(t);
  const calls = [];
  await runBundles({
    def, selected: ["market"], specs: SPECS, opt: { out, days: 30, keyword: "serum" }, account,
    deps: { run: fakeRun({ calls }), now: () => now, log: () => {}, err: () => {} },
  });
  const argv = calls.find((a) => a[0] === "keywords" && a[1] === "index-detail");
  assert.ok(argv);
  assert.equal(argv[argv.indexOf("--keyword") + 1], "serum");
});

test("--resume reruns only commands that are not ok", async (t) => {
  const out = tmp(t);
  const first = await runBundles({
    def, selected: ["store"], specs: SPECS, opt: { out, days: 7 }, account,
    deps: { run: fakeRun({ fail: ["store rating"] }), now: () => now, log: () => {}, err: () => {} },
  });
  assert.equal(first.code, 3);
  const calls = [];
  const second = await runBundles({
    def, selected: [], specs: SPECS, opt: { resume: first.folder }, account,
    deps: { run: fakeRun({ calls }), now: () => new Date(now.getTime() + 60_000), log: () => {}, err: () => {} },
  });
  assert.deepEqual(calls.map((a) => commandOfArgs(a, SPECS)), ["store rating"]);
  assert.equal(second.code, 0);
  assert.equal(second.folder, first.folder);
  const manifest = JSON.parse(readFileSync(path.join(first.folder, "manifest.json"), "utf8"));
  assert.deepEqual(manifest.commands.map((c) => c.command), names("store"));
  assert.deepEqual(manifest.totals, { ok: names("store").length, failed: 0, skipped: 0 });
});

test("cli bundle list prints names, when, commands and excluded reasons; --json one object", () => {
  const lines = [];
  assert.equal(main(["bundle", "list"], { scope: "data", log: (s) => lines.push(s), err: () => {} }), 0);
  const text = lines.join("\n");
  for (const b of def.bundles) assert.ok(text.includes(b.name) && text.includes(b.ko) && text.includes(b.when), b.name);
  for (const e of def.excluded) assert.ok(text.includes(e.command) && text.includes(e.reason), e.command);
  const json = [];
  assert.equal(main(["bundle", "list", "--json"], { scope: "data", log: (s) => json.push(s), err: () => {} }), 0);
  const payload = JSON.parse(json.join(""));
  assert.deepEqual(payload.bundles.map((b) => [b.name, b.commands]), def.bundles.map((b) => [b.name, b.commands]));
  assert.deepEqual(payload.excluded, def.excluded);
  assert.equal(usage("data").includes("bundle"), true);
});

test("cli: unknown bundle name exits 64, bundle without name prints usage", () => {
  const errs = [];
  assert.equal(main(["bundle", "nope", "--account", "fixture"], { scope: "data", log: () => {}, err: (s) => errs.push(s) }), 64);
  assert.match(errs.join("\n"), /ads, products/);
  assert.equal(main(["bundle"], { scope: "data", log: () => {}, err: () => {} }), 0);
});

test("cli end to end: bundle ads --account fixture runs a child process for each command", (t) => {
  const out = tmp(t);
  const cfg = path.join(out, "config.json");
  writeFileSync(cfg, JSON.stringify({ fixture: { loginId: "fixture_login", outDir: out } }));
  // 비동기 실행이라 프로세스 안에서 main() 반환값을 기다릴 수 없다 → 실제 CLI 를 자식으로 돌린다.
  const env = { ...process.env, ALIBABA_SELLER_BUNDLE_BIN: path.join(here, "fixtures", "bundle-child.mjs"), ALIBABA_SELLER_BUNDLE_DEPTH: "1" };
  const result = spawnSync(process.execPath, [path.join(root, "bin", "alibaba-data.mjs"),
    "bundle", "ads", "--account", "fixture", "--config", cfg, "--days", "7"], { encoding: "utf8", env, timeout: 120_000 });
  assert.equal(result.status, 0, result.stderr);
  const summary = JSON.parse(result.stdout.trim());
  assert.equal(summary.totals.ok, names("ads").length);
  assert.ok(summary.folder.startsWith(path.join(out, "bundles", "bundle-fixture-")));
  assert.ok(existsSync(path.join(summary.folder, "manifest.json")));
});

test("cli end to end: one failed command → bundle exits 3 (not 0)", (t) => {
  const out = tmp(t);
  const cfg = path.join(out, "config.json");
  writeFileSync(cfg, JSON.stringify({ fixture: { loginId: "fixture_login", outDir: out } }));
  const env = { ...process.env, ALIBABA_SELLER_BUNDLE_BIN: path.join(here, "fixtures", "bundle-child.mjs"),
    ALIBABA_SELLER_BUNDLE_DEPTH: "1", BUNDLE_CHILD_FAIL: "ads report" };
  const result = spawnSync(process.execPath, [path.join(root, "bin", "alibaba-data.mjs"),
    "bundle", "ads", "--account", "fixture", "--config", cfg, "--days", "7"], { encoding: "utf8", env, timeout: 120_000 });
  assert.equal(result.status, 3, result.stderr);
});

test("firstPeriodRow reads the first period of the first table", () => {
  assert.equal(firstPeriodRow(JSON.stringify({ a: [], b: [{ period: "2026-11-09~2026-11-15", x: 1 }] })), "2026-11-09~2026-11-15");
  assert.equal(firstPeriodRow("{}"), null);
  assert.equal(firstPeriodRow("not json"), null);
});

test("run: --start/--end without --days is a range, not a preset conflict", async (t) => {
  const out = tmp(t);
  const calls = [];
  const res = await runBundles({
    def, selected: ["ads"], specs: SPECS, opt: { out, start: "2026-07-01", end: "2026-07-31" }, account, configPath: "/tmp/cfg.json",
    deps: { run: withCsv(fakeRun({ calls })), now: () => now, log: () => {}, err: () => {} },
  });
  assert.equal(res.code, 0);
  const manifest = JSON.parse(readFileSync(path.join(res.folder, "manifest.json"), "utf8"));
  assert.equal(manifest.period.start, "2026-07-01");
  assert.equal(manifest.period.end, "2026-07-31");
  assert.ok(calls.length > 0);
  for (const c of calls) assert.ok(!JSON.stringify(c).includes("--days"), "start/end 와 --days 를 같이 넘기면 안 됨");
});
