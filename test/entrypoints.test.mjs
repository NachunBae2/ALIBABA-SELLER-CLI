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

// test-only: a process guard catches even an accidental Aside version probe.
test("default help never probes Aside or loads the optional gateway", () => {
  const cli = new URL("../lib/cli.mjs", import.meta.url).href;
  const code = `
    import cp from 'node:child_process';
    import { syncBuiltinESMExports } from 'node:module';
    cp.spawnSync = () => { throw Error('unexpected child process'); };
    syncBuiltinESMExports();
    const { main, parseArgs } = await import(${JSON.stringify(cli)});
    if (parseArgs([]).opt.transport !== 'accio') throw Error('wrong default');
    for (const scope of ['data', 'inquiry', 'all']) {
      if (main(['--help'], { scope, log() {} }) !== 0) throw Error('help failed');
    }
  `;
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", code], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
});
