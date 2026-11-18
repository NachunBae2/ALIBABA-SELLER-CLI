// alibaba-manage wiring: the command lives in engine/manage_write.py (dry-run by default, writes only --yes).
// Kept separate from the bin file so tests can build the exact command without spawning anything.
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { pythonCmd } from "./platform.mjs";

export const ENGINE = path.join(path.dirname(path.dirname(fileURLToPath(import.meta.url))), "engine", "manage_write.py");

// argv goes through untouched: the engine parses --account/--config/--yes itself.
export function manageCommand(argv, env = process.env) {
  const [exe, ...pre] = pythonCmd(env);
  return { exe, args: [...pre, ENGINE, ...argv] };
}

export function runManage(argv, { spawn = spawnSync, env = process.env } = {}) {
  const { exe, args } = manageCommand(argv, env);
  const result = spawn(exe, args, { stdio: "inherit" });
  if (result.error) {
    console.error("오류: Python 3 실행 안 됨(" + exe + "): " + result.error.message);
    return 1;
  }
  return result.status ?? 1;
}
