// alibaba-manage entrypoint: no network, no account, no token — --help and usage only.
import assert from "node:assert/strict";
import { statSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { manageCommand, runManage, ENGINE } from "../lib/manage_write.mjs";

const entry = fileURLToPath(new URL("../bin/alibaba-manage.mjs", import.meta.url));

test("alibaba-manage is executable and its help says when to use it", { skip: process.platform === "win32" }, () => {
  assert.ok(statSync(entry).mode & 0o111, "bin/alibaba-manage.mjs must stay executable");
  const result = spawnSync(entry, ["--help"], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /alibaba-manage/);
  // 첫 줄 = 언제 쓰는지
  assert.match(result.stdout, /광고 밖 셀러센트럴 조작/);
  for (const group of ["products", "showcase", "rfq", "promo", "shipping", "store", "settings", "reviews"]) {
    assert.match(result.stdout, new RegExp(group));
  }
});

test("no command exits 64 with usage on stderr and sends nothing", () => {
  const result = spawnSync(process.execPath, [entry], { encoding: "utf8" });  // node: Windows cannot run a .mjs directly
  assert.equal(result.status, 64, result.stdout);
  assert.match(result.stderr, /사용법: 명령 필요/);
  assert.doesNotMatch(result.stdout + result.stderr, /_csrf|cookie|token/i);
});

test("manageCommand runs the engine with the arguments untouched", () => {
  const { exe, args } = manageCommand(["products", "offline", "--ids", "1", "--yes"], {});
  assert.ok(exe.length > 0);
  assert.equal(args[args.length - 6], ENGINE);
  assert.deepEqual(args.slice(-5), ["products", "offline", "--ids", "1", "--yes"]);
});

test("runManage passes the child exit code through and reports a missing interpreter", () => {
  const ok = runManage(["backups"], { spawn: () => ({ status: 77 }) });
  assert.equal(ok, 77);
  const broken = runManage(["backups"], { spawn: () => ({ error: new Error("nope") }) });
  assert.equal(broken, 1);
});

test("blocked accounts are refused by the engine before any request (exit 77)", () => {
  // 계정 파일이 없어도 차단 계정 검사가 먼저라 네트워크·파일 접근 없이 77.
  const result = spawnSync(process.execPath, [entry, "products", "offline", "--ids", "1", "--account", "linea",
    "--config", "/nonexistent/config.json"], { encoding: "utf8" });
  assert.equal(result.status, 77, result.stdout + result.stderr);
  assert.match(result.stderr, /거부/);
});

test("a dry-run needs an account before it reads anything (exit 64, no request)", () => {
  const result = spawnSync(process.execPath, [entry, "products", "offline", "--ids", "1"], { encoding: "utf8" });
  assert.equal(result.status, 64, result.stdout);
  assert.match(result.stderr, /--account/);
});
