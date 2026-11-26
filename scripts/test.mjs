// Test-only: isolate optional app configuration for this process and all test children.
import { spawnSync } from "node:child_process";
import { mkdtempSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const home = mkdtempSync(path.join(tmpdir(), "seller-test-accio-"));
const env = { ...process.env, ACCIO_HOME: home, ACCIO_GATEWAY_URL: "http://gateway.test.invalid" };
try {
  // `node --test` discovers this runner too. In that context the individual
  // test files are already being run; spawning another `node --test` would recurse.
  if (process.env.NODE_TEST_CONTEXT) process.exitCode = 0;
  else {
  const tests = readdirSync(path.join(root, "test")).filter(f => f.endsWith(".test.mjs"))
    .sort().map(f => path.join("test", f));
  for (const args of [["--test", ...tests], ["scripts/test-py.mjs"]]) {
    const result = spawnSync(process.execPath, args, { cwd: root, env, stdio: "inherit" });
    if (result.error || result.status !== 0) {
      process.exitCode = result.status || 1;
      break;
    }
  }
  }
} finally {
  rmSync(home, { recursive: true, force: true });
}
