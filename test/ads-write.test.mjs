// alibaba-ads entrypoint: no network, no account, no token — --help and usage only.
import assert from "node:assert/strict";
import { statSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { adsCommand, runAds, ENGINE } from "../lib/ads_write.mjs";

const entry = fileURLToPath(new URL("../bin/alibaba-ads.mjs", import.meta.url));

test("alibaba-ads is executable and its help says when to use it", { skip: process.platform === "win32" }, () => {
  assert.ok(statSync(entry).mode & 0o111, "bin/alibaba-ads.mjs must stay executable");
  const result = spawnSync(process.execPath, [entry, "--help"], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /alibaba-ads/);
  // 첫 줄 = 잠금 표시 (2026-11-19)
  assert.match(result.stdout, /이용 불가/);
  assert.match(result.stdout, /잠금/);
  // 명령은 숨기지 않고 그대로 목록에 보인다
  for (const group of ["campaign", "products", "keywords", "restore"]) {
    assert.match(result.stdout, new RegExp(group), `help must still list ${group}`);
  }
});

test("no command exits 64 with usage on stderr and sends nothing", () => {
  const result = spawnSync(process.execPath, [entry], { encoding: "utf8" });  // node: Windows cannot run a .mjs directly
  assert.equal(result.status, 64, result.stdout);
  assert.match(result.stderr, /사용법: 명령 필요/);
  assert.doesNotMatch(result.stdout + result.stderr, /_csrf|cookie|token/i);
});

test("adsCommand runs the engine with the arguments untouched", () => {
  const { exe, args } = adsCommand(["campaign", "set", "1", "--name", "x", "--yes"], {});
  assert.ok(exe.length > 0);
  assert.equal(args[args.length - 7], ENGINE);
  assert.deepEqual(args.slice(-6), ["campaign", "set", "1", "--name", "x", "--yes"]);
});

test("runAds passes the child exit code through and reports a missing interpreter", () => {
  const ok = runAds(["backups"], { spawn: () => ({ status: 77 }) });
  assert.equal(ok, 77);
  const broken = runAds(["backups"], { spawn: () => ({ error: new Error("nope") }) });
  assert.equal(broken, 1);
});

// 잠금 대상 12개 쓰기 명령. 계정 파일·네트워크 접근 이전에 거절해야 한다.
const LOCK_MESSAGE = "이용 불가: 오너 지시로 광고 조작 잠금 (2026-11-19)";
const LOCKED_COMMANDS = [
  ["campaign", "set", "1", "--name", "x"],
  ["campaign", "create", "--name", "x", "--budget", "30", "--bid", "0.5", "--products", "1", "--start-billing"],
  ["products", "add", "1", "2"],
  ["products", "remove", "1", "2"],
  ["products", "pause", "1", "2"],
  ["products", "resume", "1", "2"],
  ["keywords", "add", "1", "word"],
  ["keywords", "remove", "1", "word"],
  ["keywords", "pause", "1", "word"],
  ["keywords", "resume", "1", "word"],
  ["keywords", "bid", "1", "word", "--price", "1.5"],
  ["restore", "/nonexistent/b.json"],
];

function runLocked(argv, extraEnv = {}) {
  const env = { ...process.env, ...extraEnv };
  delete env.ALIBABA_ADS_UNLOCK;
  delete env.ADS_WRITE_LOCKED;
  return spawnSync(process.execPath, [entry, ...argv, "--account", "thedozers", "--config", "/nonexistent/config.json"], {
    encoding: "utf8",
    env: { ...env, ...extraEnv },
  });
}

function assertLocked(argv, result) {
  const label = argv.slice(0, 2).join(" ");
  assert.equal(result.status, 69, `${label}: ${result.stdout}${result.stderr}`);
  assert.match(result.stderr, /이용 불가: 오너 지시로 광고 조작 잠금 \(2026-11-19\)/, label);
  // 계획 출력(dry-run 표·요청 계획)이 없어야 한다
  assert.doesNotMatch(result.stdout, /요청|POST|dry-run/, `${label} must print no plan`);
}

test("locked ads write commands are refused with exit 69 even with --yes", () => {
  for (const argv of LOCKED_COMMANDS) assertLocked(argv, runLocked([...argv, "--yes"]));
});

test("locked ads write commands are refused with exit 69 without --yes too", () => {
  for (const argv of LOCKED_COMMANDS) assertLocked(argv, runLocked(argv));
  // --yes 가 --account/--config 뒤에 와도 같다 (인자 해석 이전 거절 확인)
  const trailingYes = spawnSync(process.execPath,
    [entry, "campaign", "set", "1", "--name", "x", "--account", "thedozers", "--config", "/nonexistent/config.json", "--yes"],
    { encoding: "utf8" });
  assertLocked(["campaign", "set"], trailingYes);
});

test("environment variables cannot unlock ads writes", () => {
  const result = runLocked(["campaign", "create", "--name", "x", "--budget", "30", "--bid", "0.5", "--yes", "--start-billing", "--keep-on"],
    { ALIBABA_ADS_UNLOCK: "1", ADS_WRITE_LOCKED: "0" });
  assertLocked(["campaign", "create"], result);
});

test("잠금이 차단 계정 검사보다 먼저 (exit 69)", () => {
  // 계정 파일이 없어도, 차단 계정이어도 잠금이 먼저라 네트워크·파일 접근 없이 69.
  const result = spawnSync(process.execPath, [entry, "campaign", "set", "1", "--name", "x", "--account", "linea",
    "--config", "/nonexistent/config.json"], { encoding: "utf8" });
  assert.equal(result.status, 69, result.stdout + result.stderr);
  assert.match(result.stderr, new RegExp(LOCK_MESSAGE.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
});
