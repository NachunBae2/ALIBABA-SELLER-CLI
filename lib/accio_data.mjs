// alibaba-data reads through the Accio Work gateway: the custom MCP server `seller-gw`
// (accio/gateway-server/mcp_server.py, attached by `alibaba-accio attach`) runs the CLI's own read
// command and hands back its JSON. The gateway cuts a tool call at 30 s, so the server answers
// {state:"running", job_id} until the run ends; seller_central_result continues from there.
//
// Everything here is plain fetch + the gateway password from the same secret store the engine uses
// (`alibaba-accio login` copies it), so this path needs no Python of its own. Only the command name,
// account and dates are sent; the server-side command list refuses writes.
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

export const SERVER_NAME = "seller-gw";
export const RUN_LIMIT_MS = 30 * 60 * 1000; // a read that runs longer is given up on (30 min)
const CALL_WAIT_MS = 25 * 1000; // the gateway cuts at 30 s; leave room for the round trip
const SECRET_TIMEOUT_MS = 5000;
const POLL_MIN_MS = 500, POLL_MAX_MS = 3000;

export function accioHome(env = process.env) {
  return env.ACCIO_HOME || path.join(homedir(), ".accio");
}

// Newest Accio Work app start first: its gateway-cli.json carries url/username and the password
// the app wrote at that start. -> null when the app never ran on this machine.
export function gatewayInfo(env = process.env) {
  const dir = path.join(accioHome(env), "accounts");
  if (!existsSync(dir)) return null;
  const found = [];
  for (const name of readdirSync(dir)) {
    try {
      const g = JSON.parse(readFileSync(path.join(dir, name, ".accio", "runtime", "gateway-cli.json"), "utf8"));
      found.push({ accountDir: path.join(dir, name),
        url: String(env.ACCIO_GATEWAY_URL || g.url || "http://127.0.0.1:4097").replace(/\/+$/, ""),
        username: g.username || "phoenix", password: g.password, createdAt: g.createdAt || "" });
    } catch { /* not an app account */ }
  }
  found.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
  return found[0] || null;
}

const quiet = { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: SECRET_TIMEOUT_MS };

function keychainPassword(service, account) {
  return execFileSync("security", ["find-generic-password", "-s", service, "-a", account, "-w"], quiet).trim() || null;
}

function windowsPassword(service, account) {
  const ps = `$s=Get-Content -Raw "$env:LOCALAPPDATA\\aliops-accio-gateway\\secrets.json"|ConvertFrom-Json;$s.'${service}:${account}'.value`;
  return execFileSync("powershell", ["-NoProfile", "-Command", ps], quiet).trim() || null;
}

// The secret engine/secret_store.py keeps (macOS keychain, or the DPAPI file on Windows); the app's
// own file is the last resort, the same password it wrote at its last start. -> {password, source}
export function gatewayPassword(info, env = process.env) {
  const service = env.ALIOPS_ACCIO_SECRET_SERVICE || "aliops-accio-gateway";
  for (const [read, source] of [[keychainPassword, "keychain"], [windowsPassword, "dpapi"]]) {
    try {
      const password = read(service, info.username);
      if (password) return { password, source };
    } catch { /* not stored there */ }
  }
  if (info.password) return { password: info.password, source: "app file" };
  throw new Error("게이트웨이 비밀번호 없음: alibaba-accio login");
}

async function req(fetchImpl, info, pw, method, p, body) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), CALL_WAIT_MS + 5000);
  let r;
  try {
    r = await fetchImpl(info.url + p, { method, signal: ctrl.signal,
      headers: { Authorization: "Basic " + Buffer.from(`${info.username}:${pw}`).toString("base64"),
        "Content-Type": "application/json", Accept: "application/json" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  } finally { clearTimeout(timer); }
  const text = await r.text();
  if (!r.ok) throw new Error(`게이트웨이 HTTP ${r.status} ${method} ${p}: ${text.slice(0, 200)}`);
  try { return JSON.parse(text); } catch { throw new Error(`게이트웨이 응답이 JSON 아님 ${method} ${p}: ${text.slice(0, 120)}`); }
}

// The registered server's tool names as the gateway reports them (it may prefix them with the
// server name). -> {read, result}; throws when the server is missing or switched off.
export async function toolNames(fetchImpl, info, pw) {
  const d = await req(fetchImpl, info, pw, "GET", "/mcp/custom/servers");
  const data = d?.data;
  const list = Array.isArray(data) ? data : (data?.servers || data?.list || data?.items || []);
  const mine = list.find((s) => (s?.name || s?.serverName) === SERVER_NAME);
  if (!mine) throw new Error(`${SERVER_NAME} 미등록: alibaba-accio attach`);
  if (mine.enabled === false) throw new Error(`${SERVER_NAME} 꺼짐: alibaba-accio attach`);
  const flat = (mine.tools || mine.toolNames || []).map((t) => (typeof t === "string" ? t : t?.name)).filter(Boolean);
  const pick = (tool) => flat.find((n) => n === tool) || flat.find((n) => n.endsWith(tool)) || tool;
  return { read: pick("seller_central_read"), result: pick("seller_central_result") };
}

// One gateway tool call -> the result object (the server puts JSON text in content[0].text).
// `server` pins the call to our registration: the prototype supervisor attached earlier exposes the
// same tool names, and without it the gateway answers from whichever entry it listed first (its
// shadowedCount then hides ours).
export async function callTool(fetchImpl, info, pw, name, args) {
  const d = await req(fetchImpl, info, pw, "POST", "/mcp/proxy", { name, arguments: args, server: SERVER_NAME });
  const content = d?.data?.content || d?.data?.result?.content || d?.result?.content || d?.content;
  const text = Array.isArray(content) ? content.find((c) => c?.type === "text")?.text : null;
  if (!text) throw new Error(`게이트웨이 응답 해석 실패 ${name}: ${JSON.stringify(d).slice(0, 200)}`);
  let inner;
  try { inner = JSON.parse(text); } catch { throw new Error(`게이트웨이 결과가 JSON 아님 ${name}: ${text.slice(0, 120)}`); }
  if (inner?.reason) {
    // A refused account is not a gateway outage: the caller must stop, not retry on the direct path
    // (that would run the very account the server refused).
    const e = new Error(`${name}: ${inner.reason}${inner.field ? " (" + inner.field + ")" : ""}`);
    if (inner.reason === "account_refused") e.code = 77;
    throw e;
  }
  return inner;
}

// A big answer comes back as runs/<job>.json (the gateway cuts a tool result near 400k chars); read it.
function withResultFile(got) {
  if (!got?.result_file) return got;
  const full = JSON.parse(readFileSync(path.resolve(String(got.result_file)), "utf8"));
  return { ...full, ...got, stdout: full.stdout, data: full.data };
}

// -> {rc, stdout, stderr, data, started, job} for one read command; throws when the gateway cannot
// run it (the caller then takes the direct api path). stdout/stderr are the CLI's own, so then
// --json / --out / rc come out exactly as the engine prints them.
export async function read({ command, account, start, end, out, format, fetchImpl = fetch, env = process.env, now = Date.now, sleep }) {
  const pause = sleep || ((ms) => new Promise((res) => setTimeout(res, ms)));
  const info = gatewayInfo(env);
  if (!info) throw new Error(`Accio Work 앱 게이트웨이 정보 없음(${accioHome(env)}/accounts/*/.accio/runtime/gateway-cli.json): 앱 켜고 alibaba-accio login`);
  const { password } = gatewayPassword(info, env);
  const names = await toolNames(fetchImpl, info, password);
  const args = { command, account, ...(start ? { start } : {}), ...(end ? { end } : {}),
    ...(out ? { out } : {}), ...(format ? { format } : {}) };
  let got = withResultFile(await callTool(fetchImpl, info, password, names.read, args));
  const started = now();
  const deadline = started + RUN_LIMIT_MS;
  let wait = POLL_MIN_MS;
  while (got.state === "running") {
    if (now() > deadline) throw new Error(`게이트웨이 경유 ${Math.round(RUN_LIMIT_MS / 60000)}분 넘김(job ${got.job_id})`);
    await pause(wait);
    wait = Math.min(POLL_MAX_MS, Math.round(wait * 1.5));
    got = withResultFile(await callTool(fetchImpl, info, password, names.result, { job_id: got.job_id }));
    if (got.state === "unknown") throw new Error(`게이트웨이 작업 없음(job ${got.job_id})`);
  }
  if (got.state !== "done") throw new Error(`게이트웨이 상태 ${got.state || "없음"}`);
  return { rc: got.rc ?? 0, stdout: got.stdout || "", stderr: got.stderr || "", data: got.data, started, job: got.job_id };
}
