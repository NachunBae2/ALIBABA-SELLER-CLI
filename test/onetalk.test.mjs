// 테스트 전용 파일 — CLI 실행에 쓰이지 않음. 안의 ID·금액·클릭 수는 동작 확인용 예시 값이며 실제 셀러·고객 데이터가 아님.
process.env.ALIBABA_SELLER_PYTHON = "python3"; // same launcher name on every OS in these offline tests
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { ONETALK, gate, findConversation, buildParams, runScript, readLookup, lookupConversation, redact, Refused, SEND_ENV } from "../lib/onetalk.mjs";
import { main } from "../lib/cli.mjs";

const SCRIPT = readFileSync(new URL("../engine/onetalk.js", import.meta.url), "utf8");
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const PRIVATE = "private-fixture-reply-text";

// Fake conversation row: attribute values as readUi collects them (synthetic ids only).
function row(y, { ali = "", cid = "", mod = "", hint = [] } = {}) {
  const attrs = { ali: ali ? [ali] : [], cid: cid ? [cid] : [], mod: mod ? [mod] : [], hint };
  return { x: 150, y, attrs: { ...attrs, all: [ali, cid, mod, ...hint].filter(Boolean) } };
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

test("open mode (read-only --allow-open) opens but never types or presses Send", async () => {
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

test("gate: --send without the owner env is refused; with it, send", () => {
  const spec = ONETALK.reply;
  assert.throws(() => gate(spec, { send: true }, {}, { unread: false }), Refused);
  assert.throws(() => gate(spec, { send: true }, { [SEND_ENV]: "yes" }, { unread: false }), /owner-approved/);
  assert.equal(gate(spec, { send: true }, { [SEND_ENV]: "owner-approved" }, { unread: false }), "send");
  assert.equal(gate(spec, {}, { [SEND_ENV]: "owner-approved" }, { unread: false }), "dry-run");
  assert.throws(() => gate(ONETALK["ui probe"], { send: true }, { [SEND_ENV]: "owner-approved" }, null), /보내는 명령/);
});

test("gate: dry-run on an unread conversation is allowed (it never opens it); open/send need --allow-mark-read", () => {
  assert.equal(gate(ONETALK.reply, {}, {}, { unread: true }), "dry-run");
  assert.equal(gate(ONETALK["send-card"], {}, {}, { unread: true }), "dry-run");
  assert.throws(() => gate(ONETALK.reply, { "allow-open": true }, {}, { unread: true }), /읽음/);
  assert.throws(() => gate(ONETALK.reply, { send: true }, { [SEND_ENV]: "owner-approved" }, { unread: true }), /읽음/);
  assert.equal(gate(ONETALK.reply, { "allow-mark-read": true }, {}, { unread: true }), "dry-run");
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
  assert.deepEqual(result.found.matchKeys, { aliIds: 1, cids: 1, times: 1, country: true });
  assert.deepEqual(calls.filter(([k]) => k !== "hover"), []);
  for (const v of ["ali-fixture-555", "3000000000099", "1789000000000", "9999999999"]) assert.ok(!JSON.stringify(result).includes(v), v);
});

test("match: no ali id on rows -> inquiry ids inside data-cid", async () => {
  const rows = [row(200, { cid: "c_1111111111" }), row(260, { cid: "c_3000000000099_x" })];
  const { result } = await runFake({ ...base, ids: [], match: { ...KEYS, aliIds: [] }, action: "reply", mode: "dry-run" }, plain(rows));
  assert.equal(result.found.match, "cid");
  assert.equal(result.found.conversation, true);
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
  assert.equal(result.found.scrolls, 1);
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

test("CLI: unread target: dry-run runs in dry-run mode (never opens it); --allow-open is refused before Aside", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "asc-cfg-"));
  const cfg = path.join(dir, "accounts.json");
  writeFileSync(cfg, JSON.stringify({ fixture: { loginId: "fixture-login" } }));
  const d = dumpWith([{ id: 5, tradeId: 77, hasUnread: true }]);
  const inputs = [];
  main(["send-card", "--account", "fixture", "--config", cfg, "--from", d, "--inquiry-id", "5"],
    { spawn: (c, a, o) => { inputs.push(o.input); return { stdout: 'RESULT {"status":"ok"}' }; }, log() {}, err() {} });
  assert.equal(inputs.length, 1);
  assert.match(inputs[0], /\\"mode\\":\\"dry-run\\"/);
  let asideCalls = 0;
  assert.throws(() => main(["send-card", "--account", "fixture", "--config", cfg, "--from", d, "--inquiry-id", "5", "--allow-open"],
    { spawn: () => { asideCalls++; return { stdout: "" }; }, log() {}, err() {} }), /읽음/);
  assert.equal(asideCalls, 0);
});

test("CLI: --send without env is refused before Aside is called; dry-run calls Aside in dry-run mode", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "asc-cfg-"));
  const cfg = path.join(dir, "accounts.json");
  writeFileSync(cfg, JSON.stringify({ fixture: { loginId: "fixture-login" } }));
  const d = dumpWith([{ id: 6, tradeId: 78, hasUnread: false }]);
  let modes = [];
  const spawn = (cmd, args, opts) => { modes.push(/"mode":"(\w[\w-]*)"/.exec(opts.input.replaceAll('\\"', '"'))[1]); return { stdout: 'RESULT {"status":"ok"}' }; };
  assert.throws(() => main(["reply", "--account", "fixture", "--config", cfg, "--from", d, "--inquiry-id", "6", "--text", "hi", "--send"], { spawn, env: {}, log() {}, err() {} }), Refused);
  assert.deepEqual(modes, []);
  assert.equal(main(["reply", "--account", "fixture", "--config", cfg, "--from", d, "--inquiry-id", "6", "--text", "hi"], { spawn, env: {}, log() {}, err() {} }), 0);
  assert.deepEqual(modes, ["dry-run"]);
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
