// 합성 입력과 --help만 사용하며 외부 도구를 호출하지 않는다.
import assert from "node:assert/strict";
import { statSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

test("Accio entrypoint can be executed directly after npm link", { skip: process.platform === "win32" }, () => {
  const entry = fileURLToPath(new URL("../bin/alibaba-accio.mjs", import.meta.url));
  assert.ok(statSync(entry).mode & 0o111);
  const result = spawnSync(entry, ["--help"], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /alibaba-accio/);
});
