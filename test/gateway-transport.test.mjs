import test from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { readPath, parseArgs } from "../lib/cli.mjs";
import { read, gatewayInfo, gatewayPassword, SERVER_NAME, RUN_LIMIT_MS } from "../lib/accio_data.mjs";

// The sleeps and the clock belong to the test: nothing here waits out a real 30 minutes.
let nowMs = 0;
const waited = [];
const rio = () => { nowMs = 0; waited.length = 0; return { now: () => nowMs, sleep: async (ms) => { waited.push(ms); nowMs += ms; } }; };


// A fake Accio app home: CI has no app and the real keychain must never be read by a test.
function fakeEnv() {
  const home = mkdtempSync(path.join(tmpdir(), "accio-home-"));
  const rt = path.join(home, "accounts", "fixture", ".accio", "runtime");
  mkdirSync(rt, { recursive: true });
  writeFileSync(path.join(rt, "gateway-cli.json"), JSON.stringify({ url: "http://127.0.0.1:4097", username: "fixture", password: "fixture-pw", createdAt: "2026-01-01" }));
  return { ACCIO_HOME: home, ALIOPS_ACCIO_SECRET_SERVICE: "no-such-service-gateway-test" };
}

// ---- alibaba-data transport: gateway first, direct api only when the gateway cannot serve ----

function harness(gatewayRead) {
  const said = [], written = [];
  const orig = process.stdout.write.bind(process.stdout);
  process.stdout.write = (s) => { written.push(String(s)); return true; };
  const out = { said, written, called: null };
  out.code = readPath({ transport: "accio", cmd: "ads report", account: { name: "acct" }, start: "2026-07-01", end: "2026-07-31",
    err: (m) => said.push(m), gatewayRead: gatewayRead || (() => { throw new Error("off"); }),
    api: (t) => { out.called = t; return t === "api" ? 0 : 0; } });
  process.stdout.write = orig;
  return out;
}

test("gateway answer is printed as the CLI printed it, rc kept", () => {
  const h = harness(() => ({ rc: 0, stdout: '{"account_day":[]}\n' }));
  assert.equal(h.called, null); // no direct call
  assert.equal(h.written.join(""), '{"account_day":[]}\n');
  assert.deepEqual(h.said, []);
  assert.equal(h.code, 0);
  assert.equal(parseArgs(["ads", "report"]).opt.transport, "accio");
});

test("a failing command's rc is passed through, not retried", () => {
  const h = harness(() => ({ rc: 64, stdout: "", stderr: "옵션 오류" }));
  assert.equal(h.code, 64);
  assert.equal(h.called, null);
});

test("gateway unreachable -> one 안내 line, then the direct api path", () => {
  const h = harness(() => { throw new Error("fetch failed ECONNREFUSED 127.0.0.1:4097"); });
  assert.equal(h.called, "api");
  assert.equal(h.said.length, 1);
  assert.match(h.said[0], /^안내: 게이트웨이 경유 실패\(fetch failed ECONNREFUSED 127\.0\.0\.1:4097\) → 직결\(api\)로 받음$/);
});

test("--transport api skips the gateway entirely", () => {
  let asked = false;
  const code = readPath({ transport: "api", cmd: "ads report", account: { name: "acct" }, err: () => {},
    gatewayRead: () => { asked = true; return { rc: 0 }; }, api: () => 0 });
  assert.equal(asked, false);
  assert.equal(code, 0);
});

test("default transport is accio, --transport api still parses", () => {
  assert.equal(parseArgs(["ads", "report"]).opt.transport, "accio");
  assert.equal(parseArgs(["ads", "report", "--transport", "api"]).opt.transport, "api");
});

test("the real client is async: readPath awaits it before printing", async () => {
  const said = [], written = [];
  let seen = null;
  const code = await readPath({ transport: "accio", cmd: "ads report", account: { name: "acct" },
    start: "2026-07-01", end: "2026-07-31", out: null, format: "json", err: (m) => said.push(m),
    write: (s) => written.push(String(s)),
    gatewayRead: async (args) => { seen = args; await new Promise((r) => setTimeout(r, 5)); return { rc: 5, stdout: '{"a":1}\n' }; },
    api: () => { throw new Error("must not fall back"); } });
  assert.equal(code, 5);
  assert.equal(written.join(""), '{"a":1}\n');
  assert.deepEqual(said, []);
  assert.deepEqual(seen, { command: "ads report", account: "acct", start: "2026-07-01", end: "2026-07-31", out: null, format: "json" });
});

test("the gateway's stderr is passed through to stderr, not swallowed", () => {
  const h = harness(() => ({ rc: 3, stdout: "", stderr: "안내: 저장된 로그인 없음\n" }));
  assert.deepEqual(h.said, ["안내: 저장된 로그인 없음"]);
  assert.equal(h.code, 3);
});

// ---- the gateway client against a fake gateway its own server registers ----

function fakeFetch(routes) {
  const calls = [];
  const f = async (url, init = {}) => {
    const p = new URL(url).pathname;
    calls.push([init.method || "GET", p, init.body ? JSON.parse(init.body) : null]);
    const hit = routes[p];
    if (!hit) return { ok: false, status: 404, text: async () => `no ${p}` };
    const body = hit(calls.length, init);
    return { ok: true, status: 200, text: async () => JSON.stringify(body) };
  };
  f.calls = calls;
  return f;
}

const info = { url: "http://127.0.0.1:4097", username: "phoenix", password: "pw" };
const registered = { data: { servers: [{ name: SERVER_NAME, enabled: true, tools: [{ name: "seller_central_read" }, { name: "seller_central_result" }] }] } };
const toolResult = (inner) => ({ data: { result: { content: [{ type: "text", text: JSON.stringify(inner) }] } } });

test("read follows running -> result and keeps it under the 30 min cap", async () => {
  let polled = 0;
  const fetchImpl = fakeFetch({
    "/mcp/custom/servers": () => registered,
    "/mcp/proxy": (_n, init) => {
      const { name } = JSON.parse(init.body);
      if (name === "seller_central_read") return toolResult({ state: "running", job_id: "abc123" });
      polled++;
      return polled < 3 ? toolResult({ state: "running", job_id: "abc123" })
        : toolResult({ state: "done", job_id: "abc123", rc: 0, stdout: '{"ok":1}\n', data: { ok: 1 } });
    },
  });
  const got = await read({ command: "ads report", account: "acct", start: "2026-07-01", end: "2026-07-31", fetchImpl, env: fakeEnv(), ...rio() });
  assert.equal(got.rc, 0);
  assert.equal(got.stdout, '{"ok":1}\n');
  assert.equal(got.job, "abc123");
  assert.equal(polled, 3);
  assert.deepEqual(waited.slice(0, 3), [500, 750, 1125]); // it backs off instead of hammering
  const [, , first] = fetchImpl.calls.find(([m, p]) => m === "POST" && p === "/mcp/proxy");
  assert.deepEqual(first.arguments, { command: "ads report", account: "acct", start: "2026-07-01", end: "2026-07-31" });
});

test("read gives up after 30 minutes and says which job", async () => {
  const fetchImpl = fakeFetch({
    "/mcp/custom/servers": () => registered,
    "/mcp/proxy": () => toolResult({ state: "running", job_id: "slowjob" }),
  });
  await assert.rejects(read({ command: "ads report", account: "acct", fetchImpl, env: fakeEnv(), ...rio() }),
    /게이트웨이 경유 30분 넘김\(job slowjob\)/);
  assert.ok(waited.reduce((a, b) => a + b, 0) >= RUN_LIMIT_MS);
});

test("a command that ran and failed comes back with its rc and stderr, not as a connection error", async () => {
  const fetchImpl = fakeFetch({
    "/mcp/custom/servers": () => registered,
    "/mcp/proxy": () => toolResult({ state: "done", rc: 3, stdout: "", stderr: "안내: 저장된 로그인 없음" }),
  });
  const got = await read({ command: "ads report", account: "acct", fetchImpl, env: fakeEnv(), ...rio() });
  assert.equal(got.rc, 3);
  assert.equal(got.stderr, "안내: 저장된 로그인 없음");
});

test("unregistered or switched-off server -> attach 안내 (a server-side refusal stays rc)", async () => {
  const missing = fakeFetch({ "/mcp/custom/servers": () => ({ data: { servers: [] } }) });
  await assert.rejects(read({ command: "ads report", account: "acct", fetchImpl: missing, env: fakeEnv(), ...rio() }), /seller-gw 미등록: alibaba-accio attach/);
  const off = fakeFetch({ "/mcp/custom/servers": () => ({ data: { servers: [{ name: SERVER_NAME, enabled: false }] } }) });
  await assert.rejects(read({ command: "ads report", account: "acct", fetchImpl: off, env: fakeEnv(), ...rio() }), /seller-gw 꺼짐/);
});

test("a result_file answer is read from disk (the gateway cuts big strings)", async () => {
  const dir = mkdtempSync(path.join(tmpdir(), "gw-res-"));
  const file = path.join(dir, "job.json");
  writeFileSync(file, JSON.stringify({ state: "done", rc: 0, stdout: '{"big":1}\n', data: { big: 1 } }));
  const fetchImpl = fakeFetch({
    "/mcp/custom/servers": () => registered,
    "/mcp/proxy": () => toolResult({ state: "done", rc: 0, result_file: file }),
  });
  const got = await read({ command: "ads report", account: "acct", fetchImpl, env: fakeEnv(), ...rio() });
  assert.equal(got.stdout, '{"big":1}\n');
  assert.deepEqual(got.data, { big: 1 });
});

test("a refused account stops with 77 instead of retrying on the direct path", () => {
  const h = harness(() => { const e = new Error("seller_central_read: account_refused"); e.code = 77; throw e; });
  assert.equal(h.code, 77);
  assert.equal(h.called, null);
  assert.match(h.said[0], /^거부: seller_central_read: account_refused$/);
});

test("gateway password comes from the app's own runtime file when the store has none", () => {
  const info = { username: "phoenix", password: "from-app" };
  const env = { ALIOPS_ACCIO_SECRET_SERVICE: "no-such-service-hermes-test" };
  const got = gatewayPassword(info, env);
  assert.equal(got.password, "from-app");
  assert.equal(got.source, "app file");
  assert.equal(gatewayInfo({ ACCIO_HOME: "/nonexistent-account-dir" }), null);
});
