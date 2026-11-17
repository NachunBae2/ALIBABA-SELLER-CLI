// test-only: subprocess guard; no account, cookies, or network.
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import test from 'node:test';

test('explicit API transport never probes Aside at startup', () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'fixture-api-startup-'));
  try {
    const guard = path.join(directory, 'guard.mjs');
    writeFileSync(guard, `import cp from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
cp.spawnSync = () => { throw Error('fixture: unexpected child process'); };
syncBuiltinESMExports();`);
    const entry = fileURLToPath(new URL('../bin/alibaba-data.mjs', import.meta.url));
    const result = spawnSync(process.execPath, ['--import', pathToFileURL(guard).href, entry, '--transport', 'api', '--help'], {
      encoding: 'utf8', env: { ...process.env, ALIBABA_SELLER_ASIDE: '' },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /alibaba-data/);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
