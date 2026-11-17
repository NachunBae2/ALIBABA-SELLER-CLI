// OS differences in one place: the Python launcher and how to spawn Aside.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

const WIN = process.platform === "win32";

// Python children read/write UTF-8 everywhere (Windows Python otherwise uses the locale code page,
// e.g. cp949, for files and for stdout piped back to node).
process.env.PYTHONUTF8 ??= "1";
process.env.PYTHONIOENCODING ??= "utf-8";
// The engine's CDP cookie helper (engine/cdp_cookies.mjs) runs on this same node.
process.env.ALIBABA_SELLER_NODE ??= process.execPath;

// Python 3 command as [exe, ...prefixArgs]. ALIBABA_SELLER_PYTHON overrides (e.g. "C:\\Python312\\python.exe").
// Otherwise: Windows "py -3" (python.org launcher), then "python"; elsewhere "python3".
let cached = null;
export function pythonCmd(env = process.env, probe = (cmd, args) => spawnSync(cmd, args, { encoding: "utf8", shell: false })) {
  if (env.ALIBABA_SELLER_PYTHON) return [env.ALIBABA_SELLER_PYTHON];
  if (cached) return cached;
  const candidates = WIN ? [["py", "-3"], ["python"], ["python3"]] : [["python3"], ["python"]];
  for (const c of candidates) {
    const r = probe(c[0], [...c.slice(1), "-c", "import sys; print(sys.version_info[0])"]);
    if (!r.error && r.status === 0 && String(r.stdout).trim() === "3") return (cached = c);
  }
  return (cached = candidates[0]);
}

// Windows installs npm CLIs as .cmd shims; spawn needs the shell for those.
export const SPAWN_SHELL = WIN;

// The aside command. ALIBABA_SELLER_ASIDE overrides; then PATH; then the usual install spots,
// because agent and launchd shells often start without ~/.local/bin on PATH.
const ASIDE_FALLBACKS = [path.join(homedir(), ".local", "bin", "aside"), "/opt/homebrew/bin/aside", "/usr/local/bin/aside"];
export function asideCmd(env = process.env, { exists = existsSync, probe = (cmd) => spawnSync(cmd, ["--version"], { encoding: "utf8", shell: SPAWN_SHELL }) } = {}) {
  if (env.ALIBABA_SELLER_ASIDE) return env.ALIBABA_SELLER_ASIDE;
  const onPath = probe("aside");
  if (!onPath.error) return "aside";
  if (!WIN) for (const c of ASIDE_FALLBACKS) if (exists(c)) return c;
  return "aside";
}
