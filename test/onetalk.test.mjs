// 테스트 전용 파일 — CLI 실행에 쓰이지 않음. 안의 ID·금액·클릭 수는 동작 확인용 예시 값이며 실제 셀러·고객 데이터가 아님.
process.env.ALIBABA_SELLER_PYTHON = "python3"; // same launcher name on every OS in these offline tests
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { ONETALK, apiRequest, apiPlan, runApi, Refused } from "../lib/onetalk.mjs";
import { readSends, sendLogPath, flowAlreadySent } from "../lib/sendlog.mjs";
import { whyNot, fill, runFlowOn, loadFlows } from "../lib/flow.mjs";
import { main } from "../lib/cli.mjs";

const PRIVATE = "private-fixture-reply-text";

// --- API commands (engine/onetalk_cli.mjs): saved cookies + request on stdin, one JSON line back ---
const API = "onetalk_cli.mjs";
const STORE = "cookie_store.py";
const FAKE_COOKIES = [{ name: "fixture_session", value: "fixture-cookie-value", domain: ".alibaba.com", path: "/" }];
const isApi = (args) => (args || []).some((a) => String(a).endsWith(API));
const apiOp = (args) => args[args.findIndex((a) => String(a).endsWith(API)) + 1];
const reqOf = (o) => JSON.parse(o.input).request;
// The cookie store answers a fake cookie list; every other spawn goes to `spawn`.
const withStore = (spawn) => (c, a, o) => (a || []).some((x) => String(x).endsWith(STORE))
  ? { status: 0, stdout: JSON.stringify(FAKE_COOKIES) + "\n" } : spawn(c, a, o);

test("API: each send command maps to one request kind; reply text and file path ride on stdin, never in args", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "asc-ot-"));
  const f = path.join(dir, "t.txt");
  writeFileSync(f, PRIVATE);
  assert.deepEqual(apiRequest(ONETALK.reply, { "text-file": f, "inquiry-id": "5" }), { inquiryId: "5", kind: "text", text: PRIVATE });
  assert.deepEqual(apiRequest(ONETALK["send-card"], { "buyer-ali-id": "4000000000001" }), { buyerAliId: "4000000000001", kind: "card" });
  assert.deepEqual(apiRequest(ONETALK["send-product"], { "trade-id": "77", "product-id": "900000001" }), { tradeId: "77", kind: "product", productId: "900000001" });
  assert.equal(apiRequest(ONETALK["send-file"], { "inquiry-id": "5", path: f }).path, path.resolve(f));
  assert.deepEqual(apiRequest(ONETALK.messages, { "inquiry-id": "5", count: "3", "with-text": true }), { inquiryId: "5", count: 3, withText: true });
  let seen;
  const res = runApi("send", apiRequest(ONETALK.reply, { "text-file": f, "inquiry-id": "5" }), { name: "fixture" }, "/x/accounts.json",
    { spawn: withStore((cmd, args, opts) => { seen = { cmd, args, opts }; return { stdout: 'noise\n{"status":"ok"}\n', stderr: "" }; }) });
  assert.equal(res.status, "ok");
  assert.equal(seen.cmd, process.execPath);
  assert.equal(apiOp(seen.args), "send");
  assert.ok(!seen.args.join(" ").includes(PRIVATE) && !seen.args.join(" ").includes("fixture-cookie-value"));
  assert.equal(reqOf(seen.opts).text, PRIVATE);
  assert.deepEqual(JSON.parse(seen.opts.input).cookies, FAKE_COOKIES);
});

test("API: no saved login -> exit 2 with the login hint, and the engine is never started", () => {
  let engine = 0;
  const spawn = (c, a) => { if (isApi(a)) engine++; return a.some((x) => String(x).endsWith(STORE)) ? { status: 2, stdout: "" } : { stdout: '{"status":"ok"}' }; };
  const res = runApi("conversations", {}, { name: "fixture" }, "/x/a.json", { spawn });
  assert.equal(res.status, "error");
  assert.equal(res.exitCode, 2);
  assert.match(res.error, /alibaba-data login --account fixture/);
  assert.equal(engine, 0);
});

test("API: dry-run plan shows the text length and file name, not the text; an unreadable engine answer becomes api_failed without the text", () => {
  const plan = apiPlan(ONETALK.reply, { inquiryId: "5", kind: "text", text: PRIVATE });
  assert.deepEqual(plan, { status: "dry_run", op: "send", inquiryId: "5", kind: "text", textLength: PRIVATE.length });
  assert.deepEqual(apiPlan(ONETALK["send-file"], { kind: "file", path: "/a/b/catalog.pdf" }), { status: "dry_run", op: "send", kind: "file", file: "catalog.pdf" });
  const bad = runApi("send", { text: PRIVATE, buyerAliId: "4000000000001" }, { name: "fixture" }, "/x/a.json", { spawn: withStore(() => ({ stdout: "", stderr: "Error " + PRIVATE + " 4000000000001" })) });
  assert.equal(bad.status, "api_failed");
  assert.ok(!bad.detail.includes(PRIVATE) && !bad.detail.includes("4000000000001"));
});

test("mark-read needs an inquiry (--inquiry-id | --trade-id); conversations needs no target", () => {
  const { cfg } = cliFixture([]);
  assert.throws(() => main(["mark-read", "--account", "fixture", "--config", cfg, "--buyer-ali-id", "4000000000001"], { spawn: () => ({}), log() {}, err() {}, gatewayRead() { throw new Error("off"); } }), /문의 지정/);
  const ops = [];
  const spawn = withStore((c, a, o) => { ops.push([apiOp(a), reqOf(o)]); return { stdout: '{"status":"ok","count":0,"conversations":[]}' }; });
  assert.equal(main(["conversations", "--account", "fixture", "--config", cfg, "--count", "5"], { spawn, log() {}, err() {} }), 0);
  assert.equal(main(["mark-read", "--account", "fixture", "--config", cfg, "--inquiry-id", "5"], { spawn, log() {}, err() {} }), 0);
  assert.deepEqual(ops, [["conversations", { count: 5 }], ["mark-read", { inquiryId: "5" }]]);
  // the engine's own exit code for an expired login (2) or a bad argument (64) comes through; other failures are 1
  const fail = (status) => ({ spawn: withStore(() => ({ status, stdout: '{"status":"error","error":"x"}' })), log() {}, err() {} });
  assert.equal(main(["conversations", "--account", "fixture", "--config", cfg], fail(2)), 2);
  assert.equal(main(["conversations", "--account", "fixture", "--config", cfg], fail(64)), 64);
  assert.equal(main(["conversations", "--account", "fixture", "--config", cfg], fail(3)), 1);
});

test("template send --dry-run only looks the template up (read-only op); template list and failed tag changes exit by status", () => {
  const { cfg } = cliFixture([]);
  const ops = [];
  const answer = (out, status = 0) => withStore((c, a, o) => { ops.push([apiOp(a), reqOf(o)]); return { status, stdout: JSON.stringify(out) }; });
  const logs = [];
  const io = (spawn) => ({ spawn, log: (s) => logs.push(s), err() {} });
  const send = ["template", "send", "--account", "fixture", "--config", cfg, "--buyer-ali-id", "4000000000001", "--template", "Fixture Brand", "--dry-run"];
  assert.equal(main(send, io(answer({ status: "ok", template: "Fixture Brand line", id: "7001", textLength: 42 }))), 0);
  assert.deepEqual(ops, [["template-find", { buyerAliId: "4000000000001", kind: "template", template: "Fixture Brand" }]]);
  assert.equal(JSON.parse(logs[0]).lookup.textLength, 42);
  assert.equal(main(send, io(answer({ status: "template_not_found", template: "Fixture Brand" }))), 1);
  ops.length = 0;
  assert.equal(main(["template", "list", "--account", "fixture", "--config", cfg], io(answer({ status: "ok", count: 0, templates: [] }))), 0);
  assert.equal(ops[0][0], "templates");
  // a tag the account does not have is a failure, not a quiet ok
  assert.equal(main(["tag", "add", "--account", "fixture", "--config", cfg, "--buyer-ali-id", "4000000000001", "--tag", "Nope"], io(answer({ status: "tag_not_found", tag: "Nope" }))), 1);
});

function dumpWith(items) {
  const d = mkdtempSync(path.join(tmpdir(), "asc-inq-"));
  mkdirSync(path.join(d, "raw"));
  writeFileSync(path.join(d, "raw", "inquiries.jsonl"), JSON.stringify({ replay: { kind: "inquiry" }, json: { list: items } }) + "\n");
  return d;
}

function cliFixture(items) {
  const dir = mkdtempSync(path.join(tmpdir(), "asc-cfg-"));
  const cfg = path.join(dir, "accounts.json");
  writeFileSync(cfg, JSON.stringify({ fixture: { loginId: "fixture-login", outDir: path.join(dir, "out") } }));
  return { cfg, d: dumpWith(items), account: { name: "fixture", outDir: path.join(dir, "out") } };
}


test("CLI: send-card goes to the API at once (no browser, no lookup dump); --dry-run spawns nothing", () => {
  const { cfg } = cliFixture([]);
  const calls = [];
  const spawn = withStore((c, a, o) => { calls.push([isApi(a), apiOp(a), reqOf(o)]); return { stdout: '{"status":"ok"}' }; });
  assert.equal(main(["send-card", "--account", "fixture", "--config", cfg, "--inquiry-id", "5"], { spawn, log() {}, err() {} }), 0);
  assert.equal(main(["send-card", "--account", "fixture", "--config", cfg, "--inquiry-id", "5", "--dry-run"], { spawn, log() {}, err() {} }), 0);
  assert.deepEqual(calls, [[true, "send", { inquiryId: "5", kind: "card" }]]);
});

test("CLI: every send is logged (reply body, no buyer contact); dry-run is not; `log --days` reads it back", () => {
  const { cfg, account } = cliFixture([]);
  const deps = (status) => ({ spawn: withStore(() => ({ stdout: JSON.stringify({ status, readback: { messageType: 1, seen: status === "ok", textSeen: status === "ok" } }) })), log() {}, err() {}, gatewayRead() { throw new Error("off"); } });
  main(["reply", "--account", "fixture", "--config", cfg, "--inquiry-id", "6", "--text", "Hello there"], deps("ok"));
  main(["send-card", "--account", "fixture", "--config", cfg, "--inquiry-id", "6"], deps("sent_not_confirmed"));
  main(["reply", "--account", "fixture", "--config", cfg, "--inquiry-id", "6", "--text", "preview", "--dry-run"], deps("ok"));
  const rows = readSends(account);
  assert.deepEqual(rows.map((r) => [r.inquiryId, r.item, r.result, r.text]), [["6", "reply", "success", "Hello there"], ["6", "card", "fail", null]]);
  assert.equal(rows[0].account, "fixture");
  assert.equal(rows[0].readback.textSeen, true);
  assert.ok(existsSync(sendLogPath(account)));
  let out = "";
  assert.equal(main(["log", "--account", "fixture", "--config", cfg, "--days", "1"], { log: (x) => { out += x; }, err() {} }), 0);
  assert.match(out, /reply\tsuccess/);
});

test("flow: conditions (spam skipped by default, country, level, first inquiry, viewed product) and placeholders", () => {
  const flow = { name: "f", when: { country: ["US"], buyerLevel: ["L2"], firstInquiry: true, viewedProduct: "any" } };
  const row = { buyer_country: "us", buyer_level: "L2", spam: false, history_inquiry: false, inquiry_count: "1" };
  assert.equal(whyNot(flow, row, { products: [{ id: "1" }] }), null);
  assert.match(whyNot(flow, { ...row, spam: true }, {}), /스팸/);
  assert.equal(whyNot(flow, { ...row, spam: true }, { products: [{ id: "1" }] }, { includeSpam: true }), null);
  assert.match(whyNot(flow, { ...row, buyer_country: "DE" }, {}), /국가/);
  assert.match(whyNot(flow, { ...row, buyer_level: "L0" }, {}), /등급/);
  assert.match(whyNot(flow, { ...row, history_inquiry: true }, {}), /첫 문의/);
  assert.match(whyNot(flow, row, { products: [] }), /본 상품/);
  assert.deepEqual(fill("Hi {product_title}", { product_title: "Tea" }), { value: "Hi Tea", missing: null });
  assert.equal(fill("Hi {product_title}", {}).missing, "product_title");
  assert.ok(loadFlows().some((f) => f.name === "first-reply-product"));
});

test("flow run: API check, then reply / card / product in order, PDF skipped when missing; the same flow never twice", () => {
  const { account, cfg } = cliFixture([]);
  const flow = { ...loadFlows().find((f) => f.name === "first-reply-product") };
  const actions = [];
  const spawn = withStore((c, a, o) => {
    assert.ok(isApi(a));
    if (apiOp(a) === "inspect") {
      actions.push(["api", "inspect"]);
      return { stdout: JSON.stringify({ status: "ok", transport: "api", found: { products: [{ id: "900000001", title: "Fixture Tea" }] } }) };
    }
    actions.push(["api", reqOf(o).kind]); return { stdout: '{"status":"ok"}' };
  });
  const ctx = { account, configPath: cfg, log() {}, spawn, quiet: true };
  const row = { inquiry_id: "11", buyer_country: "US", spam: false, reply_status: "0" };
  const r = runFlowOn(flow, row, {}, ctx);
  assert.deepEqual(actions, [["api", "inspect"], ["api", "text"], ["api", "card"], ["api", "product"]]);
  assert.deepEqual(r.steps.map((s) => s.do), ["check", "reply", "card", "product", "file"]);
  assert.equal(r.steps.at(-1).skipped, "파일 없음");
  assert.equal(readSends(account).find((e) => e.item === "reply").text.includes("Fixture Tea"), true);
  assert.equal(readSends(account).find((e) => e.item === "product").productId, "900000001");
  assert.ok(flowAlreadySent(account, flow.name, "11"));
  actions.length = 0;
  assert.match(runFlowOn(flow, row, {}, ctx).skipped, /이미/);
  assert.deepEqual(actions, []);
  assert.match(runFlowOn(flow, { ...row, inquiry_id: "12", spam: true }, {}, ctx).skipped, /스팸/);
});

test('flow dry-run checks live API facts but never sends; missing panel cannot satisfy viewedProduct none', () => {
  const { account, cfg } = cliFixture([]);
  const flow = { name: 'fixture-check', when: { viewedProduct: 'any' }, steps: [{ do: 'check' }, { do: 'reply', text: '{product_title}' }] };
  const row = { inquiry_id: '11' }, ops = [];
  const ctx = { account, configPath: cfg, log() {}, quiet: true,
    spawn: withStore((c, a) => { ops.push(apiOp(a)); return { stdout: JSON.stringify({ status: 'ok', transport: 'api', found: { products: [{ id: '900000001', title: 'Fixture Tea' }] } }) }; }) };
  const result = runFlowOn(flow, row, { 'dry-run': true }, ctx);
  assert.deepEqual(ops, ['inspect']);
  assert.equal(result.steps[0].products.length, 1);
  assert.equal(readSends(account).length, 0);
  ctx.inspect = () => ({ status: 'ok', found: { products: null, productsReason: 'missing' } });
  const unknown = runFlowOn({ ...flow, when: { viewedProduct: 'none' } }, row, { 'dry-run': true }, ctx);
  assert.equal(unknown.stopped, undefined);
  assert.equal(unknown.skipped, '고객 패널 방문 상품 필드 없음(API 응답에 productVisitHistory 없음)');
});

// test-only: fabricated rows and API responses; no live account or network access.
for (const scenario of [
  { name: 'unknown panel + row product satisfies any', panel: null, rowProduct: true, viewed: 'any', plans: true },
  { name: 'unknown panel + no row product cannot satisfy any', panel: null, viewed: 'any', unknown: true },
  { name: 'unknown panel + no row product cannot satisfy none', panel: null, viewed: 'none', unknown: true },
  { name: 'unknown panel + row product disproves none', panel: null, rowProduct: true, viewed: 'none', skipped: '본 상품 있음' },
  { name: 'unknown panel + matching row product satisfies list', panel: null, rowProduct: true, viewed: ['900000001'], plans: true },
  { name: 'unknown panel + nonmatching row product leaves list unknown', panel: null, rowProduct: true, viewed: ['900000002'], unknown: true },
  { name: 'unknown panel + no row product leaves list unknown', panel: null, viewed: ['900000001'], unknown: true },
  { name: 'unknown panel without a product condition proceeds', panel: null, rowProduct: true, plans: true },
  { name: 'inspect failure stops even with a row product', panel: null, rowProduct: true, viewed: 'any', status: 'error', stopped: true },
  { name: 'inspect failure stops even with known panel products', panel: [], rowProduct: true, viewed: 'any', status: 'error', stopped: true },
  { name: 'malformed panel stops', panel: {}, rowProduct: true, viewed: 'any', stopped: true },
  { name: 'known empty panel + no row product disproves any', panel: [], viewed: 'any', skipped: '본 상품 없음' },
  { name: 'known empty panel + no row product satisfies none', panel: [], viewed: 'none', plans: true },
  { name: 'known empty panel + row product satisfies any', panel: [], rowProduct: true, viewed: 'any', plans: true },
]) {
  test(`flow dry-run (test-only): ${scenario.name}`, () => {
    const { account, cfg } = cliFixture([]);
    const reason = '고객 패널 방문 상품 필드 없음(API 응답에 productVisitHistory 없음)';
    const row = { inquiry_id: '11', ...(scenario.rowProduct ? { product_id: '900000001', viewed_products: '900000001 Fixture Tea' } : {}) };
    const flow = { name: 'fixture-check', when: { viewedProduct: scenario.viewed },
      steps: [{ do: 'check' }, { do: 'reply', text: scenario.rowProduct ? 'Hi {product_title} {product_id}' : 'Fixture reply' },
        { do: 'product', productId: '{product_id}' }] };
    const logs = [];
    let inspections = 0;
    const result = runFlowOn(flow, row, { 'dry-run': true }, {
      account, configPath: cfg, log: (line) => logs.push(line),
      spawn: () => { assert.fail('test-only dry-run must not spawn or send'); },
      inspect: (op, req) => {
        inspections++;
        assert.equal(op, 'inspect');
        assert.equal(req.inquiryId, row.inquiry_id);
        return { status: scenario.status || 'ok', transport: 'api',
          found: { products: scenario.panel, productsReason: 'fixture panel reason' } };
      },
    });
    assert.equal(inspections, 1);
    assert.equal(result.steps[0].panelKnown, Array.isArray(scenario.panel));
    assert.deepEqual(result.steps[0].products, Array.isArray(scenario.panel) ? [] : null);
    assert.equal(result.steps[0].reason, scenario.panel === null && !scenario.status ? reason : 'fixture panel reason');
    assert.equal(result.stopped, scenario.stopped ? '고객 패널 상품 조회 실패' : undefined);
    assert.equal(result.skipped, scenario.unknown ? reason : scenario.skipped);
    const plans = logs.filter((line) => line.startsWith('{')).map((line) => JSON.parse(line));
    assert.equal(plans.length, scenario.plans ? (scenario.rowProduct ? 2 : 1) : 0);
    assert.ok(plans.every((plan) => plan.status === 'dry_run'));
    if (scenario.plans && scenario.rowProduct) {
      assert.equal(plans.find((plan) => plan.kind === 'product').productId, row.product_id);
      assert.equal(plans.find((plan) => plan.kind === 'text').textLength, 'Hi Fixture Tea 900000001'.length);
    }
    if (!scenario.plans) assert.equal(result.steps.length, 1);
    assert.equal(readSends(account).length, 0);
  });
}

test('inspect and draft route to HTTP; dry-run spawns nothing; UI command and options are rejected', () => {
  const { cfg } = cliFixture([]), ops = [];
  const spawn = withStore((c, a, o) => { assert.ok(isApi(a)); ops.push([apiOp(a), reqOf(o)]); return { stdout: '{"status":"ok"}' }; });
  const context = { spawn, log() {}, err() {} };
  const args = ['--account', 'fixture', '--config', cfg, '--buyer-ali-id', '4000000000001'];
  assert.equal(main(['inspect', ...args], context), 0);
  assert.equal(main(['draft', ...args, '--draft-kind', 'summary'], context), 0);
  assert.equal(main(['draft', ...args, '--dry-run'], context), 0);
  assert.deepEqual(ops, [['inspect', { buyerAliId: '4000000000001' }], ['draft', { buyerAliId: '4000000000001', draftKind: 'summary' }]]);
  for (const option of ['runner', 'shot', 'button']) assert.throws(() => main(['inspect', ...args, '--'+option, 'x'], context), /삭제된 UI 옵션/);
  assert.equal(main(['ui', 'probe', ...args], context), 64);
});

test("CLI: --send without the env is refused before the inquiry lookup (no engine, no Aside) with code 77", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "asc-cfg-"));
  const cfg = path.join(dir, "accounts.json");
  writeFileSync(cfg, JSON.stringify({ fixture: { loginId: "fixture-login" } }));
  let runs = 0, spawns = 0;
  const deps = { run: () => { runs++; return 0; }, spawn: () => { spawns++; return { stdout: "" }; }, env: {}, log() {}, err() {} };
  for (const argv of [["reply", "--text", "x"], ["send-card"], ["tag", "add", "--tag", "VIP"]]) {
    assert.throws(() => main([...argv, "--account", "fixture", "--config", cfg, "--inquiry-id", "123", "--send"], deps), (e) => e instanceof Refused && e.code === 77);
  }
  assert.equal(runs, 0);
  assert.equal(spawns, 0);
});
