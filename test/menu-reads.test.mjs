// test-only: synthetic configuration and dumps; no network or real customer data.
import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { main, READ_COMMANDS, usage } from '../lib/cli.mjs';

// report.py's first argument; Windows runs python as "py -3", so the position is not fixed.
const reportName = (argv) => argv[argv.findIndex((a) => /report\.py$/.test(String(a))) + 1];

for (const [command, spec] of Object.entries(READ_COMMANDS).filter(([, spec]) => spec.menuRead)) {
  test(`${command} registers help, forces API, preserves dates, and reuses dumps`, (t) => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'menu-cli-'));
    t.after(() => rmSync(dir, { recursive: true, force: true }));
    mkdirSync(path.join(dir, 'raw'));
    const cfg = path.join(dir, 'config.json');
    writeFileSync(cfg, JSON.stringify({ fixture: { loginId: 'fixture_login', outDir: dir } }));
    assert.ok(usage('data').includes(command));
    for (const period of [['--days', '3'], ['--start', '2026-01-02', '--end', '2026-01-03']]) {
      const calls = [];
      const deps = { scope: 'data', run: (exe, argv) => { calls.push(argv); return 0; }, err() {}, log() {} };
      assert.equal(main([...command.split(' '), '--account', 'fixture', '--config', cfg, '--fresh', ...period], deps), 0);
      assert.equal(calls.length, 2);
      assert.equal(calls[0][calls[0].indexOf('--transport') + 1], 'api');
      assert.equal(calls[0][calls[0].indexOf('--pages') + 1], spec.pages[0]);
      assert.equal(reportName(calls[1]), spec.report);
      calls.length = 0;
      assert.equal(main([...command.split(' '), '--from', dir, ...period, '--json'], deps), 0);
      assert.equal(calls.length, 1);
      assert.equal(reportName(calls[0]), spec.report);
      calls.length = 0;
      assert.equal(main([...command.split(' '), '--from', dir, '--transport', 'repl'], deps), 64);
      assert.equal(calls.length, 0);
    }
  });
}

test('menu snapshots never reuse yesterday even for settled historical request dates', async (t) => {
  const { cachedPages } = await import('../lib/cli.mjs');
  const dir = mkdtempSync(path.join(os.tmpdir(), 'store-cache-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  mkdirSync(path.join(dir, 'raw'));
  writeFileSync(path.join(dir, 'raw', 'content-status.jsonl'), '');
  const w = { start: '2025-01-01', end: '2025-01-02' };
  const now = new Date(2026, 0, 4, 12);
  const manifest = { ...w, pages: [{ key: 'content-status', status: 'ok', fetchedAt: new Date(2026, 0, 3, 12).toISOString() }] };
  writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(manifest));
  assert.deepEqual(cachedPages(dir, w, ['content-status'], { now }), []);
  manifest.pages[0].fetchedAt = now.toISOString();
  writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(manifest));
  assert.deepEqual(cachedPages(dir, w, ['content-status'], { now }), ['content-status']);
});
