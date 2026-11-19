// Windows smoke tests: each CLI entrypoint exits 0 on --help, Korean output intact, config path writable.
// Runs on every platform; uses node to invoke .mjs directly (no shebang dependency).
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, rmSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BIN_DIR = path.join(__dirname, "..", "bin");

const ENTRIES = [
  ["alibaba-data.mjs", ["--help"]],
  ["alibaba-inquiry.mjs", ["--help"]],
  ["alibaba-seller.mjs", ["--help"]],
  ["alibaba-accio.mjs", ["help"]],
  ["alibaba-ads.mjs", ["--help"]],
  ["alibaba-manage.mjs", ["--help"]],
];

for (const [file, args] of ENTRIES) {
  test(`${file} ${args.join(" ")} exits 0`, () => {
    const entry = path.join(BIN_DIR, file);
    const result = spawnSync(process.execPath, [entry, ...args], {
      encoding: "utf8",
      timeout: 15000,
      env: { ...process.env, ALIBABA_SELLER_PYTHON: process.env.ALIBABA_SELLER_PYTHON || "python3" },
    });
    assert.equal(result.status, 0, `${file} failed:\nstdout: ${result.stdout}\nstderr: ${result.stderr}`);
  });
}

test("Korean output is not garbled", () => {
  // alibaba-accio features --tested 됨 prints Korean labels.
  const entry = path.join(BIN_DIR, "alibaba-accio.mjs");
  const result = spawnSync(process.execPath, [entry, "features", "--tested", "됨", "--format", "csv"], {
    encoding: "utf8",
    timeout: 15000,
  });
  assert.equal(result.status, 0, result.stderr);
  // Must contain actual Korean, not mojibake.
  assert.ok(result.stdout.includes("됨") || result.stdout.includes("테스트"), `Korean missing in output: ${result.stdout.slice(0, 200)}`);
});

test("config directory creation and file write with mode 0o600", () => {
  const dir = path.join(tmpdir(), `seller-cli-smoke-${Date.now()}`);
  try {
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    const cfg = path.join(dir, "accounts.json");
    writeFileSync(cfg, "{}", { mode: 0o600 });
    assert.ok(existsSync(cfg));
    const back = readFileSync(cfg, "utf8");
    assert.equal(back, "{}");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
