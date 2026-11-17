// Runs the Python unit tests with the same Python launcher the CLI uses (python3, or py -3 on Windows).
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { pythonCmd } from "../lib/platform.mjs";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const [exe, ...pre] = pythonCmd();
const r = spawnSync(exe, [...pre, "-m", "unittest", "discover", "-s", "test", "-p", "test_*.py"], { cwd: root, stdio: "inherit" });
process.exitCode = r.error ? 1 : r.status ?? 1;
