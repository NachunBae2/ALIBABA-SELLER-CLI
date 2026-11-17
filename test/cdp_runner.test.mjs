process.env.ALIBABA_SELLER_PYTHON = "python3";
process.env.ALIBABA_SELLER_ASIDE = "aside-fixture"; // never probe or start a real Aside
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  axTreeToText, botBlockReason, cookieParams, findBrowser, keyEvents, launchArgs, main, makeApi, parseArgv, run, runScript, uaOverride,
} from "../engine/cdp_runner.mjs";
import { loadCookies } from "../lib/cookies.mjs";
import { runRepl } from "../lib/runner.mjs";
import { main as cliMain } from "../lib/cli.mjs";

const SECRET = "cookie-value-fixture-9f3k"; // synthetic, never a real cookie
const COOKIES = [{ name: "fixture_sid", value: SECRET, domain: ".alibaba.com", path: "/", secure: true, httpOnly: true, expires: 13400000000000000, sameSite: "lax" }];

// A login form as Chrome's Accessibility.getFullAXTree returns it (wrapper div ignored, root transparent).
const AX = [
  { nodeId: "1", role: { value: "RootWebArea" }, name: { value: "Sign in" }, childIds: ["2"], backendDOMNodeId: 1 },
  { nodeId: "2", parentId: "1", ignored: true, role: { value: "generic" }, childIds: ["3", "4", "5", "6"], backendDOMNodeId: 2 },
  { nodeId: "3", parentId: "2", role: { value: "heading" }, name: { value: "Sign  in" }, childIds: ["7"], backendDOMNodeId: 10 },
  { nodeId: "7", parentId: "3", role: { value: "StaticText" }, name: { value: "Sign in" }, childIds: [] },
  { nodeId: "4", parentId: "2", role: { value: "textbox" }, name: { value: "Email" }, childIds: [], backendDOMNodeId: 11 },
  { nodeId: "5", parentId: "2", role: { value: "textbox" }, name: { value: "Password" }, childIds: [], backendDOMNodeId: 12 },
  { nodeId: "6", parentId: "2", role: { value: "button" }, name: { value: "Sign in" }, childIds: [], backendDOMNodeId: 13 },
];

test("AX tree -> Aside-shaped text with refs to DOM nodes", () => {
  const { tree, refs } = axTreeToText(AX);
  assert.equal(tree, [
    '- heading "Sign in" [ref=e1]',
    '  - text "Sign in"',
    '- textbox "Email" [ref=e2]',
    '- textbox "Password" [ref=e3]',
    '- button "Sign in" [ref=e4]',
  ].join("\n"));
  assert.equal(refs.get("e3"), 12);
  const inter = axTreeToText(AX, { interactive: true }).tree;
  assert.equal(inter, '- textbox "Email" [ref=e1]\n- textbox "Password" [ref=e2]\n- button "Sign in" [ref=e3]');
});

// Fake CDP: canned answers, every call recorded.
// probe / ax may be lists: one entry per call, the last one repeats.
function fakeCdp({ probe = { url: "https://www.alibaba.com/", title: "Alibaba", slider: false, text: "" }, onLogin = [true, false], ax = [AX] } = {}) {
  const calls = [];
  let states = 0, probes = 0, trees = 0;
  const nth = (list, i) => list[Math.min(i, list.length - 1)];
  return {
    calls,
    waitEvent: async () => ({}),
    send: async (method, params = {}) => {
      calls.push([method, params]);
      if (method === "Target.createTarget") return { targetId: "t1" };
      if (method === "Target.attachToTarget") return { sessionId: "s1" };
      if (method === "Browser.getVersion") return { userAgent: "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 HeadlessChrome/140.0.1.2 Safari/537.36" };
      if (method === "Accessibility.getFullAXTree") return { nodes: nth(ax, trees++) };
      if (method === "Storage.getCookies") return { cookies: [...COOKIES, { ...COOKIES[0], domain: ".example.com" }] };
      if (method === "DOM.resolveNode") return { object: { objectId: "o1" } };
      if (method === "DOM.getBoxModel") return { model: { content: [100, 200, 140, 200, 140, 220, 100, 220] } };
      if (method === "Runtime.evaluate") {
        if (params.expression.includes("innerText")) return { result: { value: Array.isArray(probe) ? nth(probe, probes++) : probe } };
        if (params.expression.includes("onLogin")) return { result: { value: { onLogin: onLogin[Math.min(states++, onLogin.length - 1)], matches: true } } };
        if (params.expression.includes("document.title")) return { result: { value: "Alibaba" } };
        return { result: {} };
      }
      return {};
    },
  };
}

test("engine/login.js runs unchanged: fills the password box found in the snapshot and clicks Sign in", async () => {
  const cdp = fakeCdp();
  const out = [];
  const { api } = makeApi(cdp, { ua: uaOverride("HeadlessChrome/140.0.0.0"), log: (s) => out.push(s), wait: async () => {} });
  const src = readFileSync(new URL("../engine/login.js", import.meta.url), "utf8")
    .replaceAll("__PARAMS__", JSON.stringify({ email: "fixture@example.com", pw: "fixture-pw", loginId: "" }));
  await runScript("await eval(" + JSON.stringify("(async () => {\n" + src + "\n})()") + ")\n", api);
  assert.deepEqual(out, ["STATUS signed_in"]);
  const i = cdp.calls.findIndex(([m, p]) => m === "DOM.focus" && p.backendNodeId === 12);
  assert.ok(i > 0, "password box focused by its ref");
  assert.deepEqual(cdp.calls.slice(i).find(([m]) => m === "Input.insertText"), ["Input.insertText", { text: "fixture-pw" }]);
  const press = cdp.calls.find(([m, p]) => m === "Input.dispatchMouseEvent" && p.type === "mousePressed");
  assert.deepEqual([press[1].x, press[1].y], [120, 210]);
});

// The current form: email + Continue first, the password box only on the next step.
const AX_EMAIL = [
  { nodeId: "1", role: { value: "RootWebArea" }, name: { value: "Sign in" }, childIds: ["4", "6"], backendDOMNodeId: 1 },
  { nodeId: "4", parentId: "1", role: { value: "textbox" }, name: { value: "Email" }, childIds: [], backendDOMNodeId: 11 },
  { nodeId: "6", parentId: "1", role: { value: "button" }, name: { value: "Continue" }, childIds: [], backendDOMNodeId: 14 },
];
const runLogin = async (cdp, extra = {}) => {
  const out = [];
  const { api } = makeApi(cdp, { ua: uaOverride("HeadlessChrome/140.0.0.0"), log: (s) => out.push(s), wait: async () => {} });
  const src = readFileSync(new URL("../engine/login.js", import.meta.url), "utf8")
    .replaceAll("__PARAMS__", JSON.stringify({ email: "fixture@example.com", pw: "fixture-pw", loginId: "", ...extra }));
  await runScript("await eval(" + JSON.stringify("(async () => {\n" + src + "\n})()") + ")\n", api);
  return out;
};
const NORMAL = { url: "https://login.alibaba.com/", title: "Sign in", slider: false, text: "" };

test("engine/login.js two-step form: email + Continue, then the password box that appears", async () => {
  const cdp = fakeCdp({ ax: [AX_EMAIL, AX_EMAIL, AX] });
  assert.deepEqual(await runLogin(cdp), ["STATUS signed_in"]);
  const inserted = cdp.calls.filter(([m]) => m === "Input.insertText").map(([, p]) => p.text);
  assert.deepEqual(inserted, ["fixture@example.com", "fixture-pw"]);
  const focusPw = cdp.calls.findIndex(([m, p]) => m === "DOM.focus" && p.backendNodeId === 12);
  const continueBox = cdp.calls.findIndex(([m, p]) => m === "DOM.getBoxModel" && p.backendNodeId === 14);
  assert.ok(continueBox > 0 && continueBox < focusPw, "Continue clicked before the password box is filled");
});

test("engine/login.js dumpCookies (login --sign-in): the signed-in browser's alibaba.com cookies, then the status", async () => {
  const out = await runLogin(fakeCdp(), { dumpCookies: true });
  assert.equal(out.length, 2);
  assert.deepEqual(JSON.parse(out[0].replace(/^COOKIES /, "")).map((c) => c.domain), [".alibaba.com"]);
  assert.equal(out[1], "STATUS signed_in");
  assert.deepEqual(await runLogin(fakeCdp({ ax: [AX_EMAIL] }), { dumpCookies: true }), ["STATUS password_box_not_found"]);
});

test("engine/login.js: no password box after Continue, a code prompt, or a slider that stays = clear statuses", async () => {
  assert.deepEqual(await runLogin(fakeCdp({ ax: [AX_EMAIL] })), ["STATUS password_box_not_found"]);
  const code = { ...NORMAL, code: true }; // the fake answers checks() with its result shape
  assert.deepEqual(await runLogin(fakeCdp({ probe: [NORMAL, code], onLogin: [true] })), ["STATUS two_factor"]);
  const slider = { ...NORMAL, slider: true, drag: { x: 10, y: 20, to: 300 } };
  const cdp = fakeCdp({ probe: [NORMAL, slider], onLogin: [true] });
  assert.deepEqual(await runLogin(cdp), ["STATUS captcha"]);
  const presses = cdp.calls.filter(([m, p]) => m === "Input.dispatchMouseEvent" && p.type === "mousePressed" && p.x === 10 && p.y === 20);
  assert.equal(presses.length, 1, "the slider is dragged once");
  const release = cdp.calls.filter(([m, p]) => m === "Input.dispatchMouseEvent" && p.type === "mouseReleased").pop();
  assert.equal(release[1].x, 300);
});

test("--profile-dir: kept folder, real keychain, always an off-screen window; parse errors", async () => {
  assert.equal(parseArgv(["--profile-dir", "/tmp/p"]).profileDir, "/tmp/p");
  assert.throws(() => parseArgv(["--profile-dir", ""]), /--profile-dir/);
  assert.ok(launchArgs({ dir: "/d", platform: "darwin" }).includes("--use-mock-keychain"));
  const real = launchArgs({ dir: "/d", headed: true, platform: "darwin", realKeychain: true });
  assert.ok(!real.includes("--use-mock-keychain") && !real.includes("--headless=new"));
  assert.ok(real.includes("--user-data-dir=/d"));
  const seen = [];
  const launch = async (o) => { seen.push(o); const cdp = fakeCdp(); return { cdp, close: async () => {} }; };
  const code = await run({ input: { cookies: [], script: 'console.log("STATUS ok")' }, exe: "c", out() {}, err() {}, launch, profileDir: "/tmp/p" });
  assert.equal(code, 0);
  assert.deepEqual(seen.map((o) => [o.headed, o.profileDir]), [[true, "/tmp/p"]]);
});

test("openTab: UA without HeadlessChrome, webdriver hidden, then navigate", async () => {
  const cdp = fakeCdp();
  const { api } = makeApi(cdp, { ua: uaOverride("Mozilla/5.0 HeadlessChrome/140.0.1.2 Safari/537.36", { platform: "win32" }), log() {} });
  await runScript('const page = await openTab("https://www.alibaba.com/"); console.log(await page.evaluate(() => document.title));', api);
  const methods = cdp.calls.map(([m]) => m);
  assert.ok(methods.indexOf("Emulation.setUserAgentOverride") < methods.indexOf("Page.navigate"));
  assert.ok(methods.indexOf("Page.addScriptToEvaluateOnNewDocument") < methods.indexOf("Page.navigate"));
  const ua = cdp.calls.find(([m]) => m === "Emulation.setUserAgentOverride")[1];
  assert.ok(!ua.userAgent.includes("Headless"));
  assert.equal(ua.userAgentMetadata.platform, "Windows");
  assert.ok(ua.userAgentMetadata.brands.some((b) => b.brand === "Google Chrome" && b.version === "140"));
  assert.match(cdp.calls.find(([m]) => m === "Page.addScriptToEvaluateOnNewDocument")[1].source, /webdriver/);
  assert.ok(uaOverride("x Chrome/141.0.0.0 Edg/141.0.0.0").userAgentMetadata.brands.some((b) => b.brand === "Microsoft Edge"));
});

test("keys: Enter types a newline, Meta+a selects all with the modifier held", () => {
  assert.deepEqual(keyEvents("Enter"), [
    { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, modifiers: 0, text: "\r", unmodifiedText: "\r" },
    { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, modifiers: 0 },
  ]);
  assert.deepEqual(keyEvents("Meta+a").map((e) => [e.type, e.key, e.modifiers, e.commands?.[0] ?? null, e.text ?? null]), [
    ["rawKeyDown", "Meta", 4, null, null], ["rawKeyDown", "a", 4, "selectAll", null], ["keyUp", "a", 4, null, null], ["keyUp", "Meta", 0, null, null],
  ]);
  assert.equal(keyEvents("Backspace")[0].windowsVirtualKeyCode, 8);
  assert.equal(keyEvents("Escape")[0].key, "Escape");
  assert.equal(keyEvents("Tab")[0].code, "Tab");
  assert.throws(() => keyEvents("Hyper+x"), /modifier/);
  assert.throws(() => keyEvents("NoSuchKey"), /unknown key/);
});

test("browser search: env override, Chrome then Edge, Windows install folders, none", () => {
  const has = (...p) => (f) => p.includes(f);
  const mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  const macEdge = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge";
  assert.equal(findBrowser("chrome", { platform: "darwin", env: {}, home: "/Users/x", exists: has(mac, macEdge) }), mac);
  assert.equal(findBrowser("edge", { platform: "darwin", env: {}, home: "/Users/x", exists: has(mac, macEdge) }), macEdge);
  assert.equal(findBrowser("chrome", { platform: "darwin", env: {}, home: "/Users/x", exists: has(macEdge) }), macEdge);
  assert.equal(findBrowser("chrome", { platform: "darwin", env: {}, home: "/Users/x", exists: has("/Users/x/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") }),
    "/Users/x/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
  const winEnv = { ProgramFiles: "C:\\Program Files", "ProgramFiles(x86)": "C:\\Program Files (x86)", LOCALAPPDATA: "C:\\Users\\x\\AppData\\Local" };
  assert.equal(findBrowser("chrome", { platform: "win32", env: winEnv, exists: has("C:\\Users\\x\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe") }),
    "C:\\Users\\x\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe");
  assert.equal(findBrowser("chrome", { platform: "win32", env: winEnv, exists: has("C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe") }),
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe");
  assert.equal(findBrowser("chrome", { platform: "win32", env: { ...winEnv, ALIBABA_SELLER_BROWSER_EXE: "D:\\c.exe" }, exists: has("D:\\c.exe", mac) }), "D:\\c.exe");
  assert.equal(findBrowser("chrome", { platform: "win32", env: winEnv, exists: () => false }), null);
});

test("cookies -> CDP params: sameSite names, Chrome-epoch expiry, __Host- by url, SameSite=None is Secure", () => {
  const [c] = cookieParams(COOKIES);
  assert.equal(c.sameSite, "Lax");
  assert.equal(Math.round(c.expires), 13400000000 - 11644473600);
  assert.equal(c.domain, ".alibaba.com");
  const [h, n, s] = cookieParams([
    { name: "__Host-x", value: "1", domain: "www.alibaba.com", secure: true },
    { name: "n", value: "1", domain: ".alibaba.com", sameSite: "no_restriction", expires: 1900000000000 },
    { name: "s", value: "1", domain: ".alibaba.com", expires: -1 },
  ]);
  assert.deepEqual([h.url, h.domain], ["https://www.alibaba.com/", undefined]);
  assert.deepEqual([n.sameSite, n.secure, n.expires], ["None", true, 1900000000]);
  assert.equal(s.expires, undefined);
});

test("bot check detection: punish redirect, slider, captcha text; a normal page is not one", () => {
  assert.equal(botBlockReason({ url: "https://www.alibaba.com//_____tmd_____/punish?x5secdata=a" }), "url");
  assert.equal(botBlockReason({ url: "https://www.alibaba.com/", slider: true }), "slider");
  assert.equal(botBlockReason({ url: "https://www.alibaba.com/", text: "Sorry, we have detected unusual traffic from your network. Please slide to verify." }), "text");
  assert.equal(botBlockReason({ url: "https://www.alibaba.com/", title: "Alibaba.com: Manufacturers", text: "Sign in Join free" }), null);
});

// run() with a fake browser: cookies go to Storage.setCookies, never to output.
function fakeLaunch(probes) {
  const launches = [];
  const launch = async ({ headed }) => {
    const cdp = fakeCdp({ probe: probes[Math.min(launches.length, probes.length - 1)] });
    const b = { headed, cdp, closed: false, close: async () => { b.closed = true; } };
    launches.push(b);
    return b;
  };
  return { launch, launches };
}

test("cookie values never reach stdout or stderr, even from a script that prints them or fails with them", async () => {
  const { launch, launches } = fakeLaunch([{ url: "https://www.alibaba.com/", title: "", slider: false, text: "" }]);
  const out = [], err = [];
  const script = `const page = await openTab("https://www.alibaba.com/"); console.log("RESULT " + ${JSON.stringify(SECRET)}); throw new Error("boom ${SECRET}");`;
  const code = await run({ input: { cookies: COOKIES, script }, exe: "chrome", out: (s) => out.push(s), err: (s) => err.push(s), launch });
  assert.equal(code, 1);
  assert.ok(!out.join("\n").includes(SECRET) && !err.join("\n").includes(SECRET));
  assert.deepEqual(out, ["RESULT ***"]);
  assert.match(err.join("\n"), /boom \*\*\*/);
  const set = launches[0].cdp.calls.find(([m]) => m === "Storage.setCookies")[1];
  assert.equal(set.cookies[0].value, SECRET);
  assert.ok(launches[0].closed);
});

test("bot check on the first page: BOTBLOCK line, one retry in an off-screen window, then the result", async () => {
  const blocked = { url: "https://www.alibaba.com/punish?x5secdata=zz", title: "", slider: true, text: "" };
  const ok = { url: "https://www.alibaba.com/", title: "", slider: false, text: "" };
  const script = 'const page = await openTab("https://www.alibaba.com/"); console.log("STATUS ok");';
  let r = fakeLaunch([blocked, ok]);
  let out = [];
  assert.equal(await run({ input: { cookies: [], script }, exe: "c", out: (s) => out.push(s), err() {}, launch: r.launch }), 0);
  assert.deepEqual(r.launches.map((b) => [b.headed, b.closed]), [[false, true], [true, true]]);
  assert.deepEqual(out, ['BOTBLOCK {"reason":"url","url":"https://www.alibaba.com/punish","headed":false}', "STATUS ok"]);
  r = fakeLaunch([blocked]);
  out = [];
  assert.equal(await run({ input: { cookies: [], script }, exe: "c", out: (s) => out.push(s), err() {}, launch: r.launch }), 5);
  assert.equal(r.launches.length, 2);
  assert.equal(out.filter((l) => l.startsWith("BOTBLOCK ")).length, 2);
});

test("cdp_runner main: bad stdin = 64, no browser = 69", async () => {
  const stdin = (t) => [Buffer.from(t)];
  assert.equal(await main([], { stdin: stdin("not json"), env: {}, out() {}, err() {} }), 64);
  assert.equal(await main(["--browser", "safari"], { stdin: stdin("{}"), env: {}, out() {}, err() {} }), 64);
  const err = [];
  assert.equal(await main([], { stdin: stdin(JSON.stringify({ cookies: COOKIES, script: "" })), env: { ALIBABA_SELLER_BROWSER_EXE: "/no/such/browser" }, out() {}, err: (s) => err.push(s) }), 69);
  assert.ok(!err.join("").includes(SECRET));
});

// ---- lib/runner.mjs, lib/cookies.mjs, CLI wiring --------------------------------------------------
function recorder(result = { stdout: 'RESULT {"status":"ok"}', stderr: "", status: 0 }) {
  const calls = [];
  return { calls, spawn: (cmd, args, opts) => { calls.push({ cmd, args, opts }); return result; } };
}

test("runRepl chrome: cdp_runner gets script and cookies on stdin only", () => {
  const r = recorder();
  const res = runRepl({ asideAccount: "u0", input: "console.log(1)", runner: "chrome", account: "fixture", spawn: r.spawn, loadCookies: () => COOKIES });
  assert.equal(res.runner, "chrome");
  const [c] = r.calls;
  assert.equal(c.cmd, process.execPath);
  assert.match(c.args[0], /cdp_runner\.mjs$/);
  assert.deepEqual(c.args.slice(1), ["--browser", "chrome"]);
  assert.ok(!c.args.join(" ").includes(SECRET));
  assert.deepEqual(JSON.parse(c.opts.input), { cookies: COOKIES, script: "console.log(1)" });
});

test("runRepl auto: saved cookies -> chrome, none or unreadable -> aside; explicit chrome without cookies = login needed", () => {
  let r = recorder();
  assert.equal(runRepl({ asideAccount: "u1", input: "x", runner: "auto", account: "a", spawn: r.spawn, loadCookies: () => COOKIES }).runner, "chrome");
  r = recorder();
  assert.equal(runRepl({ asideAccount: "u1", input: "x", runner: "auto", account: "a", spawn: r.spawn, loadCookies: () => null }).runner, "aside");
  assert.equal(r.calls[0].cmd, "aside-fixture");
  assert.deepEqual(r.calls[0].args, ["--host", "local", "--account", "u1", "repl"]);
  r = recorder();
  const res = runRepl({ asideAccount: "u1", input: "x", runner: "auto", account: "a", spawn: r.spawn, loadCookies: () => { throw new Error("store locked"); } });
  assert.equal(res.runner, "aside");
  assert.match(res.stderr, /store locked -> aside/);
  assert.throws(() => runRepl({ asideAccount: "u1", input: "x", runner: "edge", account: "a", spawn: r.spawn, loadCookies: () => { throw new Error("store locked"); } }), /store locked/);
  r = recorder();
  const none = runRepl({ asideAccount: "u1", input: "x", runner: "edge", account: "a", spawn: r.spawn, loadCookies: () => null });
  assert.deepEqual([none.status, r.calls.length], [3, 0]);
  assert.match(none.stderr, /login --account a --browser edge/);
  assert.throws(() => runRepl({ input: "x", runner: "firefox" }), /--runner/);
});

test("loadCookies: cookie_store.py load|refresh <account> --config on a pipe; exit 2 = none", () => {
  let r = recorder({ stdout: JSON.stringify(COOKIES) + "\n", stderr: "", status: 0 });
  assert.deepEqual(loadCookies("fixture", { configPath: "/cfg.json", spawn: r.spawn }), COOKIES);
  assert.match(r.calls[0].args.join(" "), /cookie_store\.py load fixture --config \/cfg\.json$/);
  assert.deepEqual(r.calls[0].opts.stdio, ["ignore", "pipe", "pipe"]);
  r = recorder({ stdout: "[]", stderr: "", status: 0 });
  loadCookies("fixture", { refresh: true, spawn: r.spawn });
  assert.match(r.calls[0].args.join(" "), /cookie_store\.py refresh fixture$/);
  assert.equal(loadCookies("fixture", { spawn: recorder({ stdout: "", stderr: "", status: 2 }).spawn }), null);
  assert.throws(() => loadCookies("fixture", { spawn: recorder({ stdout: "", stderr: "boom", status: 1 }).spawn }), /종료 코드 1/);
});

test("--runner reaches the data dump engine run; bad value refused", () => {
  const calls = [];
  const dir = mkdtempSync(path.join(tmpdir(), "asc-runner-"));
  const cfg = path.join(dir, "accounts.json");
  writeFileSync(cfg, JSON.stringify({ fixture: { loginId: "fixture" } }));
  cliMain(["dump", "--account", "fixture", "--config", cfg, "--start", "2026-11-01", "--end", "2026-11-02", "--out", path.join(dir, "out"), "--runner", "chrome"],
    { run: (c, a) => { calls.push(a); return 0; }, log() {}, err() {} });
  assert.deepEqual(calls[0].slice(-2), ["--runner", "chrome"]);
  assert.throws(() => cliMain(["dump", "--runner", "firefox"], { run: () => 0, log() {}, err() {} }), /--runner/);
});

test("e2e (ALIBABA_SELLER_E2E=1): a real headless browser fills a password box found by its ref", { skip: process.env.ALIBABA_SELLER_E2E !== "1" }, () => {
  const html = "<title>fixture</title><label>Password <input type=password id=p></label><button>Sign in</button>";
  const script = `const page = await openTab(${JSON.stringify("data:text/html," + encodeURIComponent(html))});
const snap = await snapshot(page, { interactive: true });
const ref = /textbox "Password" \\[ref=(e\\d+)\\]/.exec(snap.tree)[1];
await page.locator(ref).fill("abc");
await page.keyboard.press("Backspace");
console.log("VALUE " + await page.evaluate(() => document.getElementById("p").value));
console.log("TITLE " + await page.evaluate(() => document.title));`;
  const r = spawnSync(process.execPath, [fileURLToPath(new URL("../engine/cdp_runner.mjs", import.meta.url))],
    { input: JSON.stringify({ cookies: COOKIES, script }), encoding: "utf8", timeout: 120000 });
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /VALUE ab\n/);
  assert.match(r.stdout, /TITLE fixture/);
  assert.ok(!(r.stdout + r.stderr).includes(SECRET));
});
