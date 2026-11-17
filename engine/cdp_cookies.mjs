// node cdp_cookies.mjs <browser executable> <user data dir>
// Starts the browser headless on <user data dir> (a temp copy of one profile), asks it for every
// cookie over CDP (Storage.getCookies: the browser decrypts its own store, Windows v20 included)
// and prints {"total": <all cookies>, "cookies": [alibaba.com ones]} as one JSON line on stdout.
// Only a parent pipe reads it.
import { spawn } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";

const [exe, dir] = process.argv.slice(2);
if (!exe || !dir) { console.error("usage: cdp_cookies.mjs <browser exe> <user data dir>"); process.exit(64); }

const child = spawn(exe, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${dir}`, "--profile-directory=Default",
  "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync", "--disable-gpu", "about:blank"],
  { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const stop = () => { try { child.kill(); } catch {} };

try {
  const portFile = path.join(dir, "DevToolsActivePort");
  let lines = null;
  for (let i = 0; i < 100 && !lines; i++) {
    if (child.exitCode !== null) throw new Error(`browser exited (${child.exitCode})`);
    if (existsSync(portFile)) { const t = readFileSync(portFile, "utf8").trim().split("\n"); if (t.length >= 2) lines = t; }
    if (!lines) await sleep(200);
  }
  if (!lines) throw new Error("no DevToolsActivePort after 20s");
  const ws = new WebSocket(`ws://127.0.0.1:${lines[0]}${lines[1]}`);
  await new Promise((ok, bad) => { ws.onopen = ok; ws.onerror = () => bad(new Error("CDP websocket error")); });
  const cookies = await new Promise((ok, bad) => {
    const t = setTimeout(() => bad(new Error("Storage.getCookies timeout")), 20000);
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id !== 1) return;
      clearTimeout(t);
      m.error ? bad(new Error(m.error.message)) : ok(m.result.cookies);
    };
    ws.send(JSON.stringify({ id: 1, method: "Storage.getCookies" }));
  });
  ws.send(JSON.stringify({ id: 2, method: "Browser.close" }));
  const ali = cookies.filter((c) => /(^|\.)alibaba\.com$/i.test(c.domain.replace(/^\./, "")));
  process.stdout.write(JSON.stringify({ total: cookies.length, cookies: ali }) + "\n");
  await Promise.race([new Promise((r) => child.once("exit", r)), sleep(5000)]);
} catch (e) {
  console.error(e.message);
  process.exitCode = 1;
} finally {
  stop();
}
