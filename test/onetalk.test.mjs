// 테스트 전용 파일 — CLI 실행에 쓰이지 않음. 안의 ID·금액·클릭 수는 동작 확인용 예시 값이며 실제 셀러·고객 데이터가 아님.
process.env.ALIBABA_SELLER_PYTHON = "python3"; // same launcher name on every OS in these offline tests
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { ONETALK, gate, findConversation, buildParams, runScript, readLookup, lookupConversation, redact } from "../lib/onetalk.mjs";
import { readSends, sendLogPath, flowAlreadySent } from "../lib/sendlog.mjs";
import { whyNot, fill, runFlowOn, loadFlows } from "../lib/flow.mjs";
import { main } from "../lib/cli.mjs";

const SCRIPT = readFileSync(new URL("../engine/onetalk.js", import.meta.url), "utf8");
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const PRIVATE = "private-fixture-reply-text";

// Fake conversation row: attribute values as readUi collects them (synthetic ids only).
function row(y, { ali = "", cid = "", mod = "", hint = [], name = "" } = {}) {
  const attrs = { ali: ali ? [ali] : [], cid: cid ? [cid] : [], mod: mod ? [mod] : [], name: name ? [name] : [], hint };
  return { x: 150, y, attrs: { ...attrs, all: [ali, cid, mod, name, ...hint].filter(Boolean) } };
}
const TARGET = "1000000000077";  // inquiry id of the fixture target

// Fake OneTalk screen: popup open, one conversation row matching the target id, toolbar icons, input and Send.
function screen({ open = false, rows } = {}) {
  return {
    popup: { closeX: { x: 900, y: 120 }, byElement: true },
    rows: rows || [row(200, { cid: "cid-" + TARGET }), row(260, { cid: "cid-2000000000088" })],
    rowAttrNames: ["class", "data-id"],
    input: { x: 600, y: 700, placeholder: 'Enter "/" to call up Smart Assistant', length: 0 },
    send: { x: 1100, y: 760 },
    toolbar: [
      { x: 505, y: 740, cls: "send-tool-icon", label: "", title: "Image", svg: "" },
      { x: 537, y: 740, cls: "send-tool-icon", label: "", title: "", svg: "#icon-file" },
      { x: 569, y: 740, cls: "send-tool-icon icon-tmpl", label: "", title: "", svg: "" },
      { x: 601, y: 740, cls: "send-tool-icon", label: "Business card", title: "", svg: "" },
      { x: 633, y: 740, cls: "send-tool-icon", label: "", title: "More", svg: "" },
    ],
    aiButtons: [{ x: 560, y: 640, text: "Copilot" }],
    tooltips: open ? ["Business card"] : [],
    menuItems: [{ x: 640, y: 600, text: "Quote" }, { x: 640, y: 620, text: "Product" }, { x: 640, y: 640, text: "Welcome template" }],
    dialogButtons: [{ x: 700, y: 500, text: "Send" }],
    tags: [{ text: "VIP", color: "rgb(255, 0, 0)", x: 50, y: 50 }],
  };
}

async function runFake(params, screens = null) {
  const calls = [];
  let n = 0;
  const next = () => (screens ? screens[Math.min(n++, screens.length - 1)] : screen());
  const page = {
    evaluate: async (fn) => (fn.name === "readUi" ? next() : fn.name === "readDraft" ? "draft" : null),
    mouse: { click: async (x, y) => calls.push(["click", x, y]), move: async (x, y) => calls.push(["hover", x, y]), wheel: async (dx, dy) => calls.push(["wheel", dx, dy]) },
    keyboard: { type: async (t) => calls.push(["type", t]), press: async (k) => calls.push(["press", k]) },
    locator: () => ({ setInputFiles: async (f) => calls.push(["upload", f]) }),
  };
  let printed = "";
  const fn = new AsyncFunction("openTab", "sleep", "closeTab", "console", SCRIPT.replace("const P = __PARAMS__;", "const P = " + JSON.stringify(params) + ";"));
  await fn(async () => page, async () => {}, async () => {}, { log: (s) => { printed += s; } });
  return { calls, result: JSON.parse(printed.replace(/^RESULT /, "")) };
}

const base = { ids: [TARGET], match: null, text: null, textLength: 12, filePath: "/tmp/x.pdf", productId: "123", template: "Welcome template", tag: "VIP", draftButton: null };
const WRITES = ["reply", "send-card", "send-file", "send-product", "template-send", "tag-add", "tag-remove"];

for (const action of WRITES) {
  test(`dry-run ${action}: zero clicks, typing or uploads; the steps are listed in the plan`, async () => {
    const { calls, result } = await runFake({ ...base, action, mode: "dry-run" });
    assert.deepEqual(calls.filter(([k]) => k !== "hover"), []);
    assert.ok(result.plan.length > 0);
    assert.equal(result.found.conversation, true);
  });
}

test("open mode (read commands) opens but never types or presses Send", async () => {
  const { calls, result } = await runFake({ ...base, action: "reply", mode: "open" });
  assert.deepEqual(calls.map(([k]) => k), ["click", "click"]); // popup X, conversation row
  assert.ok(result.plan.some((p) => p.what === "press Send"));
});

test("send mode reply clicks the input, types the text and presses Send", async () => {
  const { calls, result } = await runFake({ ...base, action: "reply", mode: "send", text: PRIVATE });
  assert.deepEqual(calls, [["click", 900, 120], ["click", 150, 200], ["click", 600, 700], ["type", PRIVATE], ["click", 1100, 760]]);
  assert.ok(!JSON.stringify(result).includes(PRIVATE));
});

test("send-card finds the icon by its label, not by position", async () => {
  const { calls } = await runFake({ ...base, action: "send-card", mode: "send" });
  assert.ok(calls.some(([k, x]) => k === "click" && x === 601));
});

test("ui probe hovers every toolbar icon and names roles", async () => {
  const { calls, result } = await runFake({ ...base, ids: [], action: "ui-probe", mode: "dry-run" });
  assert.equal(calls.filter(([k]) => k === "hover").length, 5);
  assert.equal(calls.filter(([k]) => k !== "hover").length, 0);
  assert.deepEqual(result.found.toolbar.map((t) => t.role), ["image", "file", "template", "card", "more"]);
});

test("gate: a send command sends right away (no flag, no env, unread or not); --dry-run previews", () => {
  for (const k of ["reply", "send-card", "send-file", "send-product", "template send"]) {
    assert.equal(gate(ONETALK[k], {}), "send");
    assert.equal(gate(ONETALK[k], { "dry-run": true }), "dry-run");
  }
  assert.equal(gate(ONETALK.inspect, {}), "open");
  assert.equal(gate(ONETALK["ui probe"], { "dry-run": true }), "dry-run");
  assert.equal(gate(ONETALK["tags list"], {}), "dry-run");
});

test("send readback: the sent text showing up in the chat pane confirms it; a pane that does not change -> sent_not_confirmed", async () => {
  const chats = [{ n: 10, media: 0, tail: "older lines", cls: "x" }, { n: 14, media: 0, tail: "older lines " + PRIVATE, cls: "x" }];
  const run = async (after) => {
    let i = 0;
    const page = {
      evaluate: async (fn) => (fn.name === "readUi" ? screen() : fn.name === "readChat" ? (i++ === 0 ? chats[0] : after) : null),
      mouse: { click: async () => {}, move: async () => {}, wheel: async () => {} },
      keyboard: { type: async () => {}, press: async () => {} }, locator: () => ({ setInputFiles: async () => {} }),
    };
    let printed = "";
    const fn = new AsyncFunction("openTab", "sleep", "closeTab", "console", SCRIPT.replace("const P = __PARAMS__;", "const P = " + JSON.stringify({ ...base, action: "reply", mode: "send", text: PRIVATE }) + ";"));
    await fn(async () => page, async () => {}, async () => {}, { log: (x) => { printed += x; } });
    return JSON.parse(printed.replace(/^RESULT /, ""));
  };
  const ok = await run(chats[1]);
  assert.equal(ok.status, "ok");
  assert.equal(ok.readback.textSeen, true);
  assert.ok(!JSON.stringify(ok).includes(PRIVATE));
  const no = await run(chats[0]);
  assert.equal(no.status, "sent_not_confirmed");
});

test("reply text with new lines goes in as Shift+Enter, never a bare Enter", async () => {
  const { calls } = await runFake({ ...base, action: "reply", mode: "send", text: "line one\nline two" });
  assert.deepEqual(calls.filter(([k]) => k === "type" || k === "press"), [["type", "line one"], ["press", "Shift+Enter"], ["type", "line two"]]);
});

test("reply text reaches Aside only in send mode, and only on stdin", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "asc-ot-"));
  const f = path.join(dir, "t.txt");
  writeFileSync(f, PRIVATE);
  assert.equal(buildParams(ONETALK.reply, { "text-file": f }, "dry-run", { ids: ["1"] }).text, null);
  const p = buildParams(ONETALK.reply, { "text-file": f }, "send", { ids: ["1"] });
  assert.equal(p.text, PRIVATE);
  let seen;
  runScript("u0", p, { spawn: (cmd, args, opts) => { seen = { cmd, args, opts }; return { stdout: 'RESULT {"status":"ok"}', stderr: "" }; } });
  assert.equal(seen.cmd, "aside");
  assert.ok(!seen.args.join(" ").includes(PRIVATE));
  assert.ok(seen.opts.input.includes(PRIVATE));
});

function dumpWith(items) {
  const d = mkdtempSync(path.join(tmpdir(), "asc-inq-"));
  mkdirSync(path.join(d, "raw"));
  writeFileSync(path.join(d, "raw", "inquiries.jsonl"), JSON.stringify({ replay: { kind: "inquiry" }, json: { list: items } }) + "\n");
  return d;
}

// --- conversation row matching (values only in RAM; the result names the method) ---
const KEYS = { aliIds: ["ali-fixture-555"], cids: ["3000000000099"], times: [1789000000000], country: ["US"] };
const plain = (rows) => [screen({ rows })];

test("match: sender ali id = row data-ali-id wins, and no id value reaches the result", async () => {
  const rows = [row(200, { ali: "ali-fixture-444" }), row(260, { ali: "ali-fixture-555" })];
  const { calls, result } = await runFake({ ...base, ids: ["9999999999"], match: KEYS, action: "reply", mode: "dry-run" }, plain(rows));
  assert.equal(result.status, "ok");
  assert.equal(result.found.match, "ali-id");
  assert.deepEqual(result.found.matchKeys, { aliIds: 1, cids: 1, times: 1, country: true, names: 0 });
  assert.deepEqual(calls.filter(([k]) => k !== "hover"), []);
  for (const v of ["ali-fixture-555", "3000000000099", "1789000000000", "9999999999"]) assert.ok(!JSON.stringify(result).includes(v), v);
});

test("match: no ali id on rows -> inquiry ids inside data-cid", async () => {
  const rows = [row(200, { cid: "c_1111111111" }), row(260, { cid: "c_3000000000099_x" })];
  const { result } = await runFake({ ...base, ids: [], match: { ...KEYS, aliIds: [] }, action: "reply", mode: "dry-run" }, plain(rows));
  assert.equal(result.found.match, "cid");
  assert.equal(result.found.conversation, true);
});

test("match: list-API ids differ from row ids -> buyer display name + country (RAM only, never in the result)", async () => {
  const rows = [row(200, { ali: "1234567890123", name: "Fixture Buyer", hint: ["DE"] }), row(260, { ali: "2234567890123", name: "fixture  buyer", hint: ["US"] })];
  const { result } = await runFake({ ...base, ids: [], match: { ...KEYS, aliIds: ["99999"], cids: [], names: ["Fixture Buyer"] }, action: "reply", mode: "dry-run" }, plain(rows));
  assert.equal(result.found.match, "name+country");
  assert.ok(!JSON.stringify(result).toLowerCase().includes("fixture buyer"));
});

test("match: fallback on data-modify-time near the last reply time + country", async () => {
  const rows = [row(200, { mod: "1789000030000", hint: ["DE"] }), row(260, { mod: "1789000045000", hint: ["US"] }), row(320, { mod: "1700000000000", hint: ["US"] })];
  const { result } = await runFake({ ...base, ids: [], match: { ...KEYS, aliIds: [], cids: [] }, action: "reply", mode: "dry-run" }, plain(rows));
  assert.equal(result.found.match, "modify-time+country");
});

test("match: two candidates at one level -> refused as ambiguous, nothing opened", async () => {
  const rows = [row(200, { mod: "1789000030", hint: ["US"] }), row(260, { mod: "1789000045000", hint: ["US"] })];  // seconds and ms
  const { calls, result } = await runFake({ ...base, ids: [], match: { ...KEYS, aliIds: [], cids: [] }, action: "reply", mode: "open" }, plain(rows));
  assert.equal(result.status, "conversation_ambiguous");
  assert.equal(result.found.candidates, 2);
  assert.equal(result.found.match, null);
  assert.ok(!calls.some(([k, x, y]) => k === "click" && x === 150));
});

test("scroll: target below the first 25 rows is found by wheel scrolling only (no click in dry-run)", async () => {
  const first = Array.from({ length: 25 }, (_, i) => row(100 + i * 20, { ali: "ali-other-" + i }));
  const second = [...first.slice(5), row(700, { ali: "ali-fixture-555" })];
  const { calls, result } = await runFake({ ...base, ids: [], match: KEYS, action: "reply", mode: "dry-run" }, [screen({ rows: first }), screen({ rows: second })]);
  assert.equal(result.found.match, "ali-id");
  assert.ok(result.found.scrolls >= 1);
  assert.ok(calls.some(([k]) => k === "wheel"));
  assert.deepEqual(calls.filter(([k]) => k === "click" || k === "type" || k === "press" || k === "upload"), []);
});

test("scroll stops when the list stops changing, then reports not located", async () => {
  const rows = [row(200, { ali: "ali-other-1" })];
  const { result } = await runFake({ ...base, ids: [], match: KEYS, action: "reply", mode: "dry-run" }, plain(rows));
  assert.equal(result.status, "conversation_not_located");
  assert.equal(result.found.scrolls, 3);
});

test("lookup keys come back on the engine stdout pipe only; the temp dump never holds them", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "asc-cfg-"));
  let seenArgs, dumpText = "";
  const spawn = (cmd, args, opts) => {
    seenArgs = { cmd, args, opts };
    const out = args[args.indexOf("--out") + 1];
    mkdirSync(path.join(out, "raw"), { recursive: true });
    dumpText = JSON.stringify({ replay: { kind: "inquiry" }, json: { list: [{ id: TARGET, tradeId: 7000000001, hasUnread: false }] } });
    writeFileSync(path.join(out, "raw", "inquiries.jsonl"), dumpText + "\n");
    return { status: 0, stdout: "inquiries ok\nLOOKUP " + JSON.stringify(KEYS) + "\n" };
  };
  const conv = lookupConversation({ opt: { "inquiry-id": TARGET }, account: { name: "fixture" }, configPath: path.join(dir, "a.json"), enginePath: "engine.py", spawn });
  assert.deepEqual(conv.keys, KEYS);
  assert.equal(conv.unread, false);
  assert.ok(seenArgs.args.includes("--lookup-inquiry-id"));
  assert.deepEqual(seenArgs.opts.stdio, ["ignore", "pipe", "inherit"]);
  assert.ok(!dumpText.includes("ali-fixture-555"));
  assert.equal(readLookup("x\nno lookup"), null);
  const p = buildParams(ONETALK["send-card"], {}, "dry-run", conv);
  assert.deepEqual(p.match, KEYS);
  assert.equal(redact("err ali-fixture-555 3000000000099 1789000000000", p), "err *** *** ***");
});

test("conversation lookup by inquiry id or trade id carries the unread flag", () => {
  const d = dumpWith([{ id: 5, tradeId: 77, hasUnread: true }, { id: 6, tradeId: 78, unreadCount: 0 }]);
  assert.deepEqual(findConversation(d, { inquiryId: "5" }), { ids: ["5", "77"], unread: true });
  assert.equal(findConversation(d, { tradeId: "78" }).unread, false);
  assert.equal(findConversation(d, { inquiryId: "999" }), null);
});

function cliFixture(items) {
  const dir = mkdtempSync(path.join(tmpdir(), "asc-cfg-"));
  const cfg = path.join(dir, "accounts.json");
  writeFileSync(cfg, JSON.stringify({ fixture: { loginId: "fixture-login", outDir: path.join(dir, "out") } }));
  return { cfg, d: dumpWith(items), account: { name: "fixture", outDir: path.join(dir, "out") } };
}
const modeOf = (input) => /"mode":"(\w[\w-]*)"/.exec(input.replaceAll('\\"', '"'))[1];

test("CLI: send-card on an unread conversation opens it and sends at once; --dry-run previews only", () => {
  const { cfg, d } = cliFixture([{ id: 5, tradeId: 77, hasUnread: true }]);
  const modes = [];
  const spawn = (c, a, o) => { modes.push(modeOf(o.input)); return { stdout: 'RESULT {"status":"ok"}' }; };
  assert.equal(main(["send-card", "--account", "fixture", "--config", cfg, "--from", d, "--inquiry-id", "5"], { spawn, log() {}, err() {} }), 0);
  assert.equal(main(["send-card", "--account", "fixture", "--config", cfg, "--from", d, "--inquiry-id", "5", "--dry-run"], { spawn, log() {}, err() {} }), 0);
  assert.deepEqual(modes, ["send", "dry-run"]);
});

test("CLI: every send is logged (reply body, no buyer contact); dry-run is not; `log --days` reads it back", () => {
  const { cfg, d, account } = cliFixture([{ id: 6, tradeId: 78, hasUnread: false }]);
  const deps = (status) => ({ spawn: () => ({ stdout: 'RESULT {"status":"' + status + '","readback":{"textSeen":true}}' }), log() {}, err() {} });
  main(["reply", "--account", "fixture", "--config", cfg, "--from", d, "--inquiry-id", "6", "--text", "Hello there"], deps("ok"));
  main(["send-card", "--account", "fixture", "--config", cfg, "--from", d, "--inquiry-id", "6"], deps("card_tool_not_located"));
  main(["reply", "--account", "fixture", "--config", cfg, "--from", d, "--inquiry-id", "6", "--text", "preview", "--dry-run"], deps("ok"));
  const rows = readSends(account);
  assert.deepEqual(rows.map((r) => [r.inquiryId, r.item, r.result, r.text]), [["6", "reply", "success", "Hello there"], ["6", "card", "fail", null]]);
  assert.equal(rows[0].account, "fixture");
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

test("flow run: open+check, then reply / card / product in order, PDF skipped when missing; the same flow never twice", () => {
  const { account } = cliFixture([]);
  const flow = { ...loadFlows().find((f) => f.name === "first-reply-product") };
  const actions = [];
  const spawn = (c, a, o) => {
    const p = JSON.parse(/const P = (\{.*?\});/s.exec(JSON.parse(o.input.slice("await eval(".length, -2)))[1]);
    actions.push([p.action, p.mode]);
    return { stdout: "RESULT " + JSON.stringify({ status: "ok", found: { products: [{ id: "900000001", title: "Fixture Tea" }] } }) };
  };
  const ctx = { account, log() {}, spawn, lookup: () => ({ ids: ["11"], unread: true, keys: null }), quiet: true };
  const row = { inquiry_id: "11", buyer_country: "US", spam: false, reply_status: "0" };
  const r = runFlowOn(flow, row, {}, ctx);
  assert.deepEqual(actions, [["inspect", "open"], ["reply", "send"], ["send-card", "send"], ["send-product", "send"]]);
  assert.deepEqual(r.steps.map((s) => s.do), ["open+check", "reply", "card", "product", "file"]);
  assert.equal(r.steps.at(-1).skipped, "파일 없음");
  assert.equal(readSends(account).find((e) => e.item === "reply").text.includes("Fixture Tea"), true);
  assert.equal(readSends(account).find((e) => e.item === "product").productId, "900000001");
  assert.ok(flowAlreadySent(account, flow.name, "11"));
  actions.length = 0;
  assert.match(runFlowOn(flow, row, {}, ctx).skipped, /이미/);
  assert.deepEqual(actions, []);
  assert.match(runFlowOn(flow, { ...row, inquiry_id: "12", spam: true }, {}, ctx).skipped, /스팸/);
});
