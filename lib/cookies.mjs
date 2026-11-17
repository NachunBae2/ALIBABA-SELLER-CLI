// Saved seller-center login cookies, read through engine/cookie_store.py on a pipe (never a file).
// Contract: `cookie_store.py load|refresh <account> [--config PATH]` prints one JSON array line; exit 2 = none saved.
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { pythonCmd } from "./platform.mjs";

const COOKIE_STORE = path.join(path.dirname(path.dirname(fileURLToPath(import.meta.url))), "engine", "cookie_store.py");

// -> cookie list, or null when nothing is saved. refresh: read the source browser again first.
export function loadCookies(account, { configPath, refresh = false, spawn = spawnSync } = {}) {
  const [exe, ...pre] = pythonCmd();
  const args = [...pre, COOKIE_STORE, refresh ? "refresh" : "load", account, ...(configPath ? ["--config", configPath] : [])];
  const r = spawn(exe, args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 120000, maxBuffer: 16 * 1024 * 1024 });
  if (r.status === 2) return null;
  if (r.error || r.status !== 0) throw new Error(`저장 쿠키 읽기 실패(종료 코드 ${r.status}): ${String(r.stderr || r.error || "").trim().slice(-200)}`);
  const list = JSON.parse(String(r.stdout).trim().split("\n").pop());
  if (!Array.isArray(list)) throw new Error("저장 쿠키 읽기 실패: 배열이 아님");
  return list;
}
