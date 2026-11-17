#!/usr/bin/env node
// Runs an Aside repl script (capture.js, onetalk.js, login.js) in a throwaway Chrome/Edge over CDP,
// so the CLI works without Aside, from login cookies read out of the user's own browser.
//   stdin : {"cookies": [{name, value, domain, path, secure, httpOnly, expires, sameSite}], "script": "...", "userAgent"?: "..."}
//   argv  : [--browser chrome|edge] [--headed] [--timeout ms] [--profile-dir DIR]
//           (env ALIBABA_SELLER_BROWSER_EXE, ALIBABA_SELLER_HEADED=1)
//   stdout: the script's console.log lines, plus "BOTBLOCK {json}" when Alibaba answers with a bot check.
// Cookies live in this process's RAM and the browser's temp profile only: never in argv, files or output.
// The browser profile is a fresh temp folder (never the user's own profile), removed on every exit path.
// --profile-dir DIR instead keeps a dedicated persistent profile (login --sign-in): always an off-screen
// window and the OS keychain, so its cookie file stays readable by engine/browser_cookies.py afterwards.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, promises as fsp, rmSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { format } from "node:util";

export const EXIT = { ok: 0, script: 1, blocked: 5, usage: 64, noBrowser: 69, timeout: 124 };

// ---- browser executable -----------------------------------------------------------------------
const MAC_APP = { chrome: "Google Chrome.app/Contents/MacOS/Google Chrome", edge: "Microsoft Edge.app/Contents/MacOS/Microsoft Edge" };
const WIN_EXE = { chrome: "Google\\Chrome\\Application\\chrome.exe", edge: "Microsoft\\Edge\\Application\\msedge.exe" };
const LINUX_EXE = { chrome: ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser"], edge: ["/usr/bin/microsoft-edge"] };

export function browserCandidates(kind, { platform = process.platform, env = process.env, home = homedir() } = {}) {
  if (platform === "darwin") return ["/Applications", path.posix.join(home, "Applications")].map((d) => path.posix.join(d, MAC_APP[kind]));
  if (platform === "win32") return ["ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"].filter((k) => env[k]).map((k) => path.win32.join(env[k], WIN_EXE[kind]));
  return LINUX_EXE[kind];
}

// The requested browser first, then the other one (cookies are injected, so either works).
export function findBrowser(kind, { env = process.env, platform = process.platform, home, exists = existsSync } = {}) {
  const forced = env.ALIBABA_SELLER_BROWSER_EXE;
  if (forced) return exists(forced) ? forced : null;
  const order = kind === "edge" ? ["edge", "chrome"] : ["chrome", "edge"];
  return order.flatMap((k) => browserCandidates(k, { platform, env, home })).find((p) => exists(p)) || null;
}

export function launchArgs({ dir, headed = false, platform = process.platform, realKeychain = false }) {
  return [
    headed ? "--window-position=-2400,-2400" : "--headless=new",
    "--remote-debugging-port=0", `--user-data-dir=${dir}`, "--no-first-run", "--no-default-browser-check",
    "--window-size=1440,900", "--lang=en-US", "--disable-blink-features=AutomationControlled",
    "--disable-sync", "--disable-features=Translate,MediaRouter", "--mute-audio",
    ...(platform === "darwin" && !realKeychain ? ["--use-mock-keychain"] : platform === "linux" ? ["--password-store=basic"] : []),
    "about:blank",
  ];
}

// ---- cookies ------------------------------------------------------------------------------------
const SAME_SITE = { strict: "Strict", lax: "Lax", none: "None", no_restriction: "None", 2: "Strict", 1: "Lax", 0: "None" };

// Seconds since 1970 from seconds, milliseconds or Chrome's microseconds since 1601. Session cookie -> null.
export function epochSeconds(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return null;
  if (n > 1e14) return n / 1e6 - 11644473600;
  if (n > 1e11) return n / 1000;
  return n;
}

export function cookieParams(cookies) {
  return (cookies || []).filter((c) => c && c.name && c.value != null && c.domain).map((c) => {
    const p = { name: String(c.name), value: String(c.value), path: c.path || "/", secure: !!c.secure, httpOnly: !!c.httpOnly };
    if (p.name.startsWith("__Host-")) p.url = `https://${String(c.domain).replace(/^\./, "")}/`;
    else p.domain = String(c.domain);
    const sameSite = SAME_SITE[String(c.sameSite ?? "").toLowerCase()];
    if (sameSite) p.sameSite = sameSite;
    if (sameSite === "None") p.secure = true; // Chrome drops SameSite=None without Secure
    const expires = epochSeconds(c.expires);
    if (expires) p.expires = expires;
    return p;
  });
}

// Any cookie value (4+ chars) that shows up in output becomes ***.
export function redactor(cookies) {
  const values = (cookies || []).map((c) => String(c?.value ?? "")).filter((v) => v.length >= 4).sort((a, b) => b.length - a.length);
  return (text) => values.reduce((t, v) => t.replaceAll(v, "***"), String(text));
}

// ---- headless tells -----------------------------------------------------------------------------
export function uaOverride(browserUA, { userAgent, platform = process.platform, arch = process.arch } = {}) {
  const ua = userAgent || String(browserUA).replace(/HeadlessChrome/g, "Chrome");
  const full = (/Chrome\/([\d.]+)/.exec(ua) || [])[1] || "0";
  const major = full.split(".")[0];
  const brands = [{ brand: "Not_A Brand", version: "8" }, { brand: "Chromium", version: major }, { brand: /Edg\//.test(ua) ? "Microsoft Edge" : "Google Chrome", version: major }];
  const os = platform === "win32" ? ["Win32", "Windows", "10.0.0"] : platform === "darwin" ? ["MacIntel", "macOS", "14.0.0"] : ["Linux x86_64", "Linux", ""];
  return {
    userAgent: ua, acceptLanguage: "en-US,en", platform: os[0],
    userAgentMetadata: { brands, fullVersionList: brands.map((b) => ({ brand: b.brand, version: b.brand === "Not_A Brand" ? "8.0.0.0" : full })),
      fullVersion: full, platform: os[1], platformVersion: os[2], architecture: arch === "arm64" ? "arm" : "x86", bitness: "64", model: "", mobile: false },
  };
}

const STEALTH = "Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => false, configurable: true });";

// Alibaba's bot checks: x5sec / punish redirect, the slider captcha (nc_*, baxia) or its text.
const BLOCK_PROBE = () => ({
  url: location.href, title: document.title,
  slider: !!document.querySelector("#nc_1_n1z, .nc_iconfont, #baxia-dialog-content, iframe[src*='punish'], iframe[src*='captcha']"),
  text: ((document.body && document.body.innerText) || "").slice(0, 2000),
});

export function botBlockReason({ url = "", title = "", slider = false, text = "" } = {}) {
  if (/\/_____tmd_____\/|\/punish\b|x5secdata|[/.?&]captcha/i.test(url)) return "url";
  if (slider) return "slider";
  if (/captcha interception|unusual traffic|slide to verify|please slide|验证码|滑动验证|访问被拒绝/i.test(title + "\n" + text)) return "text";
  return null;
}

export class BotBlocked extends Error {
  constructor(info) { super(`bot check (${info.reason}) at ${info.url}`); this.info = info; }
}

// ---- accessibility snapshot -------------------------------------------------------------------
const INTERACTIVE = new Set(["button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox", "listbox", "option",
  "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "switch", "slider", "spinbutton", "treeitem"]);
const TRANSPARENT = new Set(["generic", "none", "presentation", "InlineTextBox", "LineBreak", "RootWebArea", "WebArea"]);
const clean = (s) => String(s ?? "").replace(/\s+/g, " ").trim().slice(0, 200);

// Accessibility.getFullAXTree nodes -> Aside-shaped text ("- textbox \"Password\" [ref=e3]") and ref -> backendDOMNodeId.
// interactive: only controls, one per line. Otherwise the outline, two spaces per level.
export function axTreeToText(nodes, { interactive = false } = {}) {
  const byId = new Map(nodes.map((n) => [n.nodeId, n]));
  const lines = [];
  const refs = new Map();
  const walk = (n, depth) => {
    const role = n.role?.value || "";
    const name = clean(n.name?.value);
    const show = !n.ignored && (interactive ? INTERACTIVE.has(role) : !(TRANSPARENT.has(role) || (role === "StaticText" && !name)));
    if (show) {
      const shown = role === "StaticText" ? "text" : role;
      let line = `${"  ".repeat(interactive ? 0 : depth)}- ${shown}${name ? " " + JSON.stringify(name) : ""}`;
      if (shown !== "text" && n.backendDOMNodeId) {
        const ref = "e" + (refs.size + 1);
        refs.set(ref, n.backendDOMNodeId);
        line += ` [ref=${ref}]`;
      }
      lines.push(line);
    }
    for (const id of n.childIds || []) if (byId.has(id)) walk(byId.get(id), show ? depth + 1 : depth);
  };
  for (const n of nodes) if (!n.parentId || !byId.has(n.parentId)) walk(n, 0);
  return { tree: lines.join("\n"), refs };
}

// ---- keys -------------------------------------------------------------------------------------
const KEYS = {
  Enter: ["Enter", 13, "\r"], Tab: ["Tab", 9], Backspace: ["Backspace", 8], Delete: ["Delete", 46], Escape: ["Escape", 27],
  ArrowUp: ["ArrowUp", 38], ArrowDown: ["ArrowDown", 40], ArrowLeft: ["ArrowLeft", 37], ArrowRight: ["ArrowRight", 39],
  Home: ["Home", 36], End: ["End", 35], PageUp: ["PageUp", 33], PageDown: ["PageDown", 34], " ": ["Space", 32, " "],
  Shift: ["ShiftLeft", 16], Control: ["ControlLeft", 17], Alt: ["AltLeft", 18], Meta: ["MetaLeft", 91],
};
const MODIFIER = { Alt: 1, Control: 2, Meta: 4, Shift: 8 };
// macOS Chrome ignores Cmd shortcuts from synthetic events unless the editing command is named.
const COMMANDS = { a: "selectAll", c: "copy", v: "paste", x: "cut", z: "undo" };

function keyDef(key) {
  if (KEYS[key]) { const [code, keyCode, text] = KEYS[key]; return { code, keyCode, text }; }
  if (key.length !== 1) throw new Error(`unknown key: ${key}`);
  const up = key.toUpperCase();
  const code = /[A-Z]/.test(up) ? "Key" + up : /\d/.test(key) ? "Digit" + key : "";
  return { code, keyCode: /[A-Z0-9]/.test(up) ? up.charCodeAt(0) : 0, text: key };
}

// "Enter", "Meta+a", "Control+Shift+ArrowLeft" -> Input.dispatchKeyEvent params, in order.
export function keyEvents(combo) {
  const parts = String(combo).split(/\+(?!$)/);
  const key = parts.pop();
  for (const m of parts) if (!MODIFIER[m]) throw new Error(`unknown modifier: ${m}`);
  const events = [];
  let modifiers = 0;
  const ev = (type, k, d, extra = {}) => ({ type, key: k, code: d.code, windowsVirtualKeyCode: d.keyCode, modifiers, ...extra });
  for (const m of parts) { modifiers |= MODIFIER[m]; events.push(ev("rawKeyDown", m, keyDef(m))); }
  const d = keyDef(key);
  const text = modifiers & ~MODIFIER.Shift ? null : d.text;
  const command = modifiers & (MODIFIER.Meta | MODIFIER.Control) ? COMMANDS[key.toLowerCase()] : null;
  events.push(ev(text ? "keyDown" : "rawKeyDown", key, d, { ...(text ? { text, unmodifiedText: text } : {}), ...(command ? { commands: [command] } : {}) }));
  events.push(ev("keyUp", key, d));
  for (const m of parts.reverse()) { modifiers &= ~MODIFIER[m]; events.push(ev("keyUp", m, keyDef(m))); }
  return events;
}

// ---- CDP connection -----------------------------------------------------------------------------
export class Cdp {
  #ws; #id = 0; #pending = new Map(); #waiters = new Set();
  constructor(ws) {
    this.#ws = ws;
    ws.addEventListener("message", (e) => this.#onMessage(String(e.data)));
    ws.addEventListener("close", () => { for (const p of this.#pending.values()) p.reject(new Error("browser connection closed")); this.#pending.clear(); });
  }
  send(method, params = {}, sessionId) {
    const id = ++this.#id;
    return new Promise((resolve, reject) => {
      this.#pending.set(id, { resolve, reject, method });
      this.#ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }
  waitEvent(method, sessionId, ms) {
    return new Promise((resolve, reject) => {
      const w = { method, sessionId, resolve: (p) => { clearTimeout(timer); resolve(p); } };
      const timer = setTimeout(() => { this.#waiters.delete(w); reject(new Error(`${method}: no event in ${ms} ms`)); }, ms);
      this.#waiters.add(w);
    });
  }
  #onMessage(text) {
    const m = JSON.parse(text);
    if (m.id) {
      const p = this.#pending.get(m.id);
      if (!p) return;
      this.#pending.delete(m.id);
      if (m.error) p.reject(new Error(`${p.method}: ${m.error.message}`)); else p.resolve(m.result || {});
      return;
    }
    for (const w of this.#waiters) if (w.method === m.method && w.sessionId === m.sessionId) { this.#waiters.delete(w); w.resolve(m.params); }
  }
}

// ---- the Aside repl globals over CDP ------------------------------------------------------------
const LOAD_MS = 45000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const center = (q) => ({ x: (q[0] + q[2] + q[4] + q[6]) / 4, y: (q[1] + q[3] + q[5] + q[7]) / 4 });

function makePage(cdp, targetId, sessionId) {
  const send = (method, params) => cdp.send(method, params, sessionId);
  let refs = new Map();
  let at = { x: 0, y: 0 };
  const evaluate = async (fn, arg) => {
    const expression = typeof fn === "function" ? `(${fn})(${arg === undefined ? "" : JSON.stringify(arg)})` : String(fn);
    const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, userGesture: true });
    if (r.exceptionDetails) throw new Error("evaluate: " + String(r.exceptionDetails.exception?.description || r.exceptionDetails.text).slice(0, 300));
    return r.result?.value;
  };
  const mouse = {
    move: async (x, y) => { at = { x, y }; await send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y }); },
    down: () => send("Input.dispatchMouseEvent", { type: "mousePressed", ...at, button: "left", buttons: 1, clickCount: 1 }),
    up: () => send("Input.dispatchMouseEvent", { type: "mouseReleased", ...at, button: "left", buttons: 0, clickCount: 1 }),
    click: async (x, y) => {
      await mouse.move(x, y);
      await send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", buttons: 1, clickCount: 1 });
      await send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", buttons: 0, clickCount: 1 });
    },
    wheel: (deltaX, deltaY) => send("Input.dispatchMouseEvent", { type: "mouseWheel", x: at.x, y: at.y, deltaX, deltaY }),
  };
  const keyboard = {
    type: (text) => send("Input.insertText", { text: String(text) }),
    press: async (combo) => { for (const e of keyEvents(combo)) await send("Input.dispatchKeyEvent", e); },
  };
  // "e12" = a ref from the last snapshot, anything else = a CSS selector.
  const node = async (sel) => {
    if (/^e\d+$/.test(sel)) {
      const backendNodeId = refs.get(sel);
      if (!backendNodeId) throw new Error(`unknown ref ${sel} (take a snapshot first)`);
      return { backendNodeId };
    }
    const r = await send("Runtime.evaluate", { expression: `document.querySelector(${JSON.stringify(sel)})` });
    if (!r.result?.objectId) throw new Error(`no element matches ${sel}`);
    return { backendNodeId: (await send("DOM.describeNode", { objectId: r.result.objectId })).node.backendNodeId };
  };
  const locator = (sel) => ({
    click: async () => {
      const n = await node(sel);
      await send("DOM.scrollIntoViewIfNeeded", n);
      const { x, y } = center((await send("DOM.getBoxModel", n)).model.content);
      await mouse.click(x, y);
    },
    fill: async (text) => {
      const n = await node(sel);
      await send("DOM.focus", n);
      const { object } = await send("DOM.resolveNode", n);
      await send("Runtime.callFunctionOn", { objectId: object.objectId, functionDeclaration:
        "function () { if (typeof this.select === 'function') this.select(); else { const r = document.createRange(); r.selectNodeContents(this); const s = getSelection(); s.removeAllRanges(); s.addRange(r); } }" });
      await keyboard.press("Backspace");
      if (String(text)) await send("Input.insertText", { text: String(text) });
    },
    setInputFiles: async (files) => send("DOM.setFileInputFiles", { files: [].concat(files), ...(await node(sel)) }),
  });
  return {
    targetId, sessionId, evaluate, mouse, keyboard, locator,
    _send: send, _setRefs: (m) => { refs = m; },
  };
}

// api = the globals a script sees. state.acted counts what the script did besides opening a tab:
// a bot check on the very first openTab (nothing done yet) is safe to retry in a headed window.
export function makeApi(cdp, { ua, log, err = () => {}, wait = sleep }) {
  const state = { acted: 0, opened: 0 };
  const openTab = async (url) => {
    const first = state.opened++ === 0 && state.acted === 0;
    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
    const page = makePage(cdp, targetId, sessionId);
    await page._send("Page.enable");
    await page._send("Emulation.setUserAgentOverride", ua);
    await page._send("Page.addScriptToEvaluateOnNewDocument", { source: STEALTH });
    const loaded = cdp.waitEvent("Page.loadEventFired", sessionId, LOAD_MS);
    const nav = await page._send("Page.navigate", { url });
    if (nav.errorText) { loaded.catch(() => {}); throw new Error(`openTab: ${nav.errorText}`); }
    await loaded.catch(() => err("cdp_runner: load event not seen in 45 s, continuing"));
    const probe = await page.evaluate(BLOCK_PROBE).catch(() => null);
    const reason = probe && botBlockReason(probe);
    if (reason) throw Object.assign(new BotBlocked({ reason, url: String(probe.url).split("?")[0] }), { first });
    return wrap(page);
  };
  // Every page call except opening counts as the script having acted.
  const wrap = (page) => {
    const count = (f) => (...a) => { state.acted++; return f(...a); };
    return {
      ...page, evaluate: count(page.evaluate),
      mouse: { move: count(page.mouse.move), down: count(page.mouse.down), up: count(page.mouse.up), click: count(page.mouse.click), wheel: count(page.mouse.wheel) },
      keyboard: { type: count(page.keyboard.type), press: count(page.keyboard.press) },
      locator: (sel) => { const l = page.locator(sel); return { click: count(l.click), fill: count(l.fill), setInputFiles: count(l.setInputFiles) }; },
    };
  };
  const snapshot = async (page, opts = {}) => {
    const { nodes } = await page._send("Accessibility.getFullAXTree", {});
    const { tree, refs } = axTreeToText(nodes, opts);
    page._setRefs(refs);
    return { tree };
  };
  const api = {
    openTab, snapshot, sleep: wait,
    closeTab: (page) => cdp.send("Target.closeTarget", { targetId: page.targetId }),
    fs: { writeFile: (p, text) => fsp.writeFile(p, text), readFile: (p) => fsp.readFile(p, "utf8") },
    console: { log: (...a) => log(format(...a)), info: (...a) => log(format(...a)), error: (...a) => err(format(...a)), warn: (...a) => err(format(...a)) },
  };
  return { api, state };
}

const AsyncFunction = (async () => {}).constructor;

// The script body runs inside its own async arrow, so its top-level const/let may reuse a global's name.
// Both input shapes work: a raw script (capture.js) and Aside's stdin form `await eval("(async () => {…})()")`.
export function runScript(script, api) {
  const names = Object.keys(api);
  return new AsyncFunction(...names, "return await (async () => {\n" + script + "\n})();")(...names.map((k) => api[k]));
}

// ---- browser process --------------------------------------------------------------------------
const OPEN = new Set(); // cleanups of running browsers, for a kill signal mid-run
async function waitFile(file, ms, proc) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (proc.exitCode !== null) throw new Error(`browser exited (code ${proc.exitCode})`);
    try { const t = await fsp.readFile(file, "utf8"); if (t.includes("\n")) return t; } catch (e) { /* not written yet */ }
    await sleep(100);
  }
  throw new Error("browser did not open a DevTools port in time");
}

export async function launchBrowser({ exe, headed = false, platform = process.platform, profileDir = null }) {
  const dir = profileDir || mkdtempSync(path.join(tmpdir(), "alibaba-seller-cdp-"));
  const proc = spawn(exe, launchArgs({ dir, headed: headed || !!profileDir, platform, realKeychain: !!profileDir }), { stdio: "ignore" });
  const exited = new Promise((r) => proc.once("exit", r));
  proc.once("error", () => {});
  const cleanup = async () => {
    if (proc.exitCode === null && proc.signalCode === null) {
      proc.kill();
      await Promise.race([exited, sleep(5000)]);
      if (proc.exitCode === null && proc.signalCode === null) proc.kill("SIGKILL");
    }
    if (!profileDir) rmSync(dir, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
    OPEN.delete(cleanup);
  };
  OPEN.add(cleanup);
  try {
    const [port, wsPath] = (await waitFile(path.join(dir, "DevToolsActivePort"), 20000, proc)).trim().split("\n");
    const ws = new WebSocket(`ws://127.0.0.1:${port}${wsPath}`);
    await new Promise((resolve, reject) => { ws.addEventListener("open", resolve, { once: true }); ws.addEventListener("error", () => reject(new Error("CDP websocket failed")), { once: true }); });
    const cdp = new Cdp(ws);
    return {
      cdp,
      close: async () => {
        await Promise.race([cdp.send("Browser.close").catch(() => {}), sleep(3000)]);
        try { ws.close(); } catch (e) { /* already closed */ }
        // A kept profile is read from disk next: let the browser finish writing its cookie file.
        if (profileDir) await Promise.race([exited, sleep(15000)]);
        await cleanup();
      },
    };
  } catch (e) {
    await cleanup();
    throw e;
  }
}

async function setCookies(cdp, params, err) {
  if (!params.length) return;
  try {
    await cdp.send("Storage.setCookies", { cookies: params });
  } catch (e) {
    let ok = 0;
    for (const c of params) await cdp.send("Storage.setCookies", { cookies: [c] }).then(() => ok++, () => {});
    err(`cdp_runner: cookies set ${ok}/${params.length} (the rest were refused by the browser)`);
  }
}

// One script run. Returns an EXIT code. Headless first; a bot check on the first page load retries
// once in an off-screen window (or starts there with headed).
export async function run({ input, exe, headed = false, timeoutMs = 160000, out, err, launch = launchBrowser, platform = process.platform, profileDir = null }) {
  const hide = redactor(input.cookies);
  const log = (s) => out(hide(s));
  const warn = (s) => err(hide(s));
  const params = cookieParams(input.cookies);
  for (const offscreen of headed || profileDir ? [true] : [false, true]) {
    let b = null;
    let timer;
    try {
      const work = (async () => {
        b = await launch({ exe, headed: offscreen, platform, profileDir });
        await setCookies(b.cdp, params, warn);
        const { userAgent } = await b.cdp.send("Browser.getVersion");
        const { api } = makeApi(b.cdp, { ua: uaOverride(userAgent, { userAgent: input.userAgent, platform }), log, err: warn });
        await runScript(input.script, api);
        return EXIT.ok;
      })();
      const timeout = new Promise((r) => { timer = setTimeout(() => r(EXIT.timeout), timeoutMs); });
      const code = await Promise.race([work, timeout]);
      if (code === EXIT.timeout) { work.catch(() => {}); warn(`cdp_runner: timeout after ${timeoutMs} ms`); }
      return code;
    } catch (e) {
      if (e instanceof BotBlocked) {
        log("BOTBLOCK " + JSON.stringify({ ...e.info, headed: offscreen }));
        if (e.first && !offscreen) { warn("cdp_runner: bot check in headless mode, retrying once in an off-screen window"); continue; }
        return EXIT.blocked;
      }
      warn("cdp_runner: " + String(e?.stack || e).split("\n").slice(0, 3).join(" | "));
      return EXIT.script;
    } finally {
      clearTimeout(timer);
      if (b) await b.close().catch(() => {});
    }
  }
  return EXIT.blocked;
}

export function parseArgv(argv) {
  const o = { browser: "chrome", headed: false, timeoutMs: 160000, profileDir: null };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--browser") o.browser = argv[++i];
    else if (argv[i] === "--headed") o.headed = true;
    else if (argv[i] === "--timeout") o.timeoutMs = Number(argv[++i]);
    else if (argv[i] === "--profile-dir") o.profileDir = argv[++i];
    else throw new Error(`unknown argument ${argv[i]}`);
  }
  if (!["chrome", "edge"].includes(o.browser)) throw new Error("--browser chrome|edge");
  if (!(o.timeoutMs > 0)) throw new Error("--timeout <ms>");
  if (o.profileDir !== null && !o.profileDir) throw new Error("--profile-dir <folder>");
  return o;
}

export async function main(argv = process.argv.slice(2), { stdin = process.stdin, env = process.env, out = (s) => process.stdout.write(s + "\n"), err = (s) => process.stderr.write(s + "\n"), launch } = {}) {
  let o, input;
  try {
    o = parseArgv(argv);
    let text = "";
    for await (const chunk of stdin) text += chunk;
    input = JSON.parse(text);
    if (typeof input?.script !== "string" || !Array.isArray(input.cookies ?? [])) throw new Error("stdin must be {cookies: [...], script: \"...\"}");
  } catch (e) {
    err("cdp_runner: " + (e instanceof SyntaxError ? "stdin is not JSON" : e.message));
    return EXIT.usage;
  }
  const exe = findBrowser(o.browser, { env });
  if (!exe) { err("cdp_runner: Chrome/Edge not found (set ALIBABA_SELLER_BROWSER_EXE to the browser executable)"); return EXIT.noBrowser; }
  return run({ input, exe, headed: o.headed || env.ALIBABA_SELLER_HEADED === "1", timeoutMs: o.timeoutMs, profileDir: o.profileDir, out, err, ...(launch ? { launch } : {}) });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  for (const sig of ["SIGTERM", "SIGINT", "SIGHUP"]) {
    process.on(sig, () => { Promise.allSettled([...OPEN].map((f) => f())).finally(() => process.exit(130)); });
  }
  main().then((code) => {
    process.exitCode = code;
    setTimeout(() => process.exit(code), 3000).unref(); // a script's stray timers must not hold the exit
  });
}
