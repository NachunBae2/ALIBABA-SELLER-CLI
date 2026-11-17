import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { resolveRange, splitRange } from '../lib/dates.mjs';
import { periodGroups, planWindows, resolveQuery, readAdsBounds, completeAdsCache } from '../lib/periods.mjs';

test('default is inclusive 30 calendar days across leap day', () => {
  assert.deepEqual(resolveRange({}, new Date('2024-03-01')), { start:'2024-02-01', end:'2024-03-01', warnings:[] });
  for (const n of [0, -1, 1.5, NaN]) assert.throws(() => splitRange('2024-01-01','2024-03-01', n));
});
test('max and all use the verified earliest bound and reject conflicting presets', () => {
  const now = new Date('2026-11-17');
  const catalog = { periodDefaults: {maxDays:31}, pages:[{key:'x',period:{mode:'range',status:'verified',earliestDate:'2025-01-01'}}] };
  for (const opt of [{max:true},{all:true},{max:true,all:true}]) assert.equal(resolveQuery(opt,['x'],null,now,catalog).start,'2025-01-01');
  assert.throws(() => resolveQuery({max:true}, ['ads-report']), /미확인/);
  assert.throws(() => resolveRange({max:true,weeks:2}, now), /하나만/);
});
test('each table and extra preserves its own period limit', () => {
  const catalog = {periodDefaults:{maxDays:31},pages:[{key:'ads',rangeTables:[{key:'a',period:{maxDays:7}},{key:'b',period:{maxDays:30}}]}, {key:'analytics',extra:[{tag:'weekly',period:{mode:'week',earliestDate:'2026-01-01'}}]}]};
  const groups = periodGroups(null,null,catalog);
  assert.deepEqual(groups.map(g=>g.period.maxDays),[7,30,31]);
  assert.deepEqual(groups[2].extras,['weekly']);
  const range = {start:'2026-01-01',end:'2026-02-02'};
  assert.equal(planWindows(range,groups[0].period).length,5);
  assert.equal(planWindows(range,groups[1].period).length,2);
});
test('reuse old windows and fetch only nonoverlapping gaps', () => {
  const w = planWindows({start:'2026-01-01',end:'2026-04-15'}, {maxDays:31}, [
    {start:'2026-01-10',end:'2026-04-09',dir:'old91'},
    {start:'2026-02-01',end:'2026-02-28',dir:'overlap'}]);
  assert.deepEqual(w,[{start:'2026-01-01',end:'2026-01-09'}, {start:'2026-01-10',end:'2026-04-09',dir:'old91'}, {start:'2026-04-10',end:'2026-04-15'}]);
});
test('max bounds come only from the actual offline report date filter', () => {
  const dir=mkdtempSync(path.join(tmpdir(),'bounds-'));mkdirSync(path.join(dir,'raw'));
  const file=path.join(dir,'raw','ads-report.jsonl');
  writeFileSync(file,JSON.stringify({url:'https://fixture/api/data/dynamic/filter/data',json:{data:{queryResult:[{sceneType:'offline_effect_report',beginDateTime:'2025-12-01 00:00:00',endDateTime:'2026-11-17 00:00:00'}]}}}));
  assert.deepEqual(readAdsBounds(dir),{start:'2025-12-01',end:'2026-11-17'});
  assert.equal(completeAdsCache(dir,['ai.product'],'2026-01-01','2026-01-31'),false);
});
