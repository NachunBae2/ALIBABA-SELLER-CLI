// test-only: temporary dump paths and synthetic CLI calls; no account or network access.
process.env.ALIBABA_SELLER_PYTHON = 'python3';
import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { READ_COMMANDS, main, usage } from '../lib/cli.mjs';
import { CATALOG, periodGroups, planWindows } from '../lib/periods.mjs';

const pages = CATALOG.pages.filter(p => p.adsExtra);
test('all extra ads commands register help, offline reuse, dates and output formats', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'ads-extra-fixture-'));
  mkdirSync(path.join(dir, 'raw'));
  try {
    for (const page of pages) {
      const name = page.key.slice(4), command = `ads ${name}`;
      assert.equal(READ_COMMANDS[command].report, page.key);
      assert.ok(usage('data').includes(command));
      for (const format of ['json', 'csv']) {
        const calls = [];
        const code = main(['ads', name, '--from', dir, '--start', '2026-01-01', '--end', '2026-01-07', `--${format}`], {
          run: (cmd, args) => { calls.push([cmd, args]); return 0; }, log() {}, err() {},
        });
        assert.equal(code, 0);
        assert.equal(calls.length, 1);
        assert.equal(calls[0][0], 'python3');
        const args = calls[0][1];
        assert.match(args[0], /report\.py$/);
        assert.equal(args[1], page.key);
        assert.equal(args[args.indexOf('--format') + 1], format);
        assert.equal(args[args.indexOf('--start') + 1], '2026-01-01');
        assert.equal(args[args.indexOf('--end') + 1], '2026-01-07');
      }
    }
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('dated contracts split into at most seven days; snapshots execute once', () => {
  for (const page of pages) {
    const groups = periodGroups([page.key]);
    assert.equal(groups.length, 1);
    const windows = planWindows({ start: '2026-01-01', end: '2026-01-20' }, groups[0].period);
    assert.equal(windows.length, groups[0].period.mode === 'snapshot' ? 1 : 3);
    assert.equal(windows.at(-1).end, '2026-01-20');
  }
});
