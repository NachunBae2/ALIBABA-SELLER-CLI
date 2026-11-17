// OS differences in one place: the Python launcher and how to spawn Aside.
import { spawnSync } from "node:child_process";

const WIN = process.platform === "win32";

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
