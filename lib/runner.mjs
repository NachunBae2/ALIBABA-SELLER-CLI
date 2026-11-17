// Where a repl script runs: the Aside browser, or a throwaway Chrome/Edge (engine/cdp_runner.mjs)
// with the saved login cookies. auto = chrome when saved cookies exist, else aside.
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadCookies as savedCookies } from "./cookies.mjs";
import { asideCmd, SPAWN_SHELL } from "./platform.mjs";

export const RUNNERS = ["auto", "aside", "chrome", "edge"];
export const EXIT_LOGIN = 3;
const CDP_RUNNER = path.join(path.dirname(path.dirname(fileURLToPath(import.meta.url))), "engine", "cdp_runner.mjs");

// input = the script, raw or in Aside's stdin form; it reaches the child on stdin only, cookies too.
export function runRepl({ asideAccount, input, runner = "aside", account, configPath, spawn = spawnSync, loadCookies = savedCookies, timeout = 170000 }) {
  if (!RUNNERS.includes(runner)) throw new Error(`--runner 는 ${RUNNERS.join("|")}`);
  let cookies = null, note = "";
  if (runner !== "aside") {
    try { cookies = loadCookies(account, { configPath }); }
    catch (e) { if (runner !== "auto") throw e; note = `runner auto: ${e.message} -> aside\n`; }
  }
  const use = runner === "auto" ? (cookies?.length ? "chrome" : "aside") : runner;
  if (use === "aside") {
    const r = spawn(asideCmd(), ["--host", "local", "--account", asideAccount, "repl"], { input, encoding: "utf8", timeout, shell: SPAWN_SHELL });
    return { stdout: r.stdout || "", stderr: note + (r.stderr || ""), status: r.status, runner: use };
  }
  if (!cookies?.length) {
    return { stdout: "", stderr: `저장된 로그인 쿠키 없음: alibaba-data login --account ${account} --browser ${use} 를 먼저`, status: EXIT_LOGIN, runner: use };
  }
  const r = spawn(process.execPath, [CDP_RUNNER, "--browser", use], { input: JSON.stringify({ cookies, script: input }), encoding: "utf8", timeout, maxBuffer: 64 * 1024 * 1024 });
  return { stdout: r.stdout || "", stderr: r.stderr || "", status: r.status, runner: use };
}
