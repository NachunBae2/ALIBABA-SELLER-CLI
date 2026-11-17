// Test-only: fixture accounts and local temporary dumps; no live data or network.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { main, READ_COMMANDS, usage } from '../lib/cli.mjs';
import { periodGroups } from '../lib/periods.mjs';

const commands = ['inquiries folders', 'products risk-counts', 'products power-score', 'products risk-tasks',
  'rfq saved-searches', 'orders list', 'knowledge metrics', 'knowledge storage'];

test('eight misc read commands support offline output formats and snapshot planning', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'misc-fixture-'));
  mkdirSync(path.join(dir, 'raw'));
  for (const command of commands) {
    assert.ok(usage('data').includes(command));
    assert.equal(periodGroups(READ_COMMANDS[command].pages)[0].period.mode, 'snapshot');
    for (const format of ['json', 'csv']) {
      const calls = [];
      const code = main([...command.split(' '), '--from', dir, '--days', '90', '--' + format], {
        run: (cmd, args) => { calls.push(args); return 0; }, log() {}, err() {},
      });
      assert.equal(code, 0);
      assert.equal(calls.length, 1);
      // Windows runs python as "py -3": find report.py instead of a fixed position
      const at = calls[0].findIndex((a) => String(a).endsWith('report.py'));
      assert.ok(at >= 0);
      assert.equal(calls[0][at + 1], READ_COMMANDS[command].report);
      assert.ok(calls[0].includes(format));
    }
  }
});

test('misc live collection stays in one snapshot and propagates failures', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'misc-fixture-'));
  const config = path.join(dir, 'accounts.json');
  writeFileSync(config, JSON.stringify({ fixture: { loginId: 'fixture-login', outDir: dir } }));
  for (const command of commands) {
    const calls = [];
    const code = main([...command.split(' '), '--account', 'fixture', '--config', config, '--transport', 'api', '--fresh', '--days', '365'], {
      run: (cmd, args) => { calls.push(args); return 5; }, log() {}, err() {},
    });
    assert.equal(code, 5);
    assert.equal(calls.length, 1);
    assert.ok(calls[0].includes('--transport'));
    assert.ok(calls[0].includes('api'));
    assert.ok(calls[0].includes(READ_COMMANDS[command].pages[0]));
  }
});
