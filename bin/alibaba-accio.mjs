#!/usr/bin/env node
// alibaba-accio: Accio Work gateway CLI (read tools only). The work is in engine/accio_gateway.py.
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { pythonCmd } from "../lib/platform.mjs";

const engine = path.join(path.dirname(path.dirname(fileURLToPath(import.meta.url))), "engine", "accio_gateway.py");
const [exe, ...pre] = pythonCmd();
const r = spawnSync(exe, [...pre, engine, ...process.argv.slice(2)], { stdio: "inherit" });
if (r.error) { console.error("오류: Python 3 실행 안 됨(" + exe + "): " + r.error.message); process.exitCode = 1; }
else process.exitCode = r.status ?? 1;
