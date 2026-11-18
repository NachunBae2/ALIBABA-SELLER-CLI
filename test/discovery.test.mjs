// test-only — fake account keys and category IDs; no customer data.
process.env.ALIBABA_SELLER_PYTHON = 'python3';
import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { main, READ_COMMANDS } from '../lib/cli.mjs';

function fixture() {
  const root = mkdtempSync(path.join(tmpdir(), 'discovery-test-'));
  const config = path.join(root, 'accounts.json');
  writeFileSync(config, JSON.stringify({ fixture: { loginId: 'fixture-login', outDir: root } }));
  const calls = [];
  const deps = { run: (exe, args) => { calls.push({ exe, args }); return 0; }, err() {}, log() {}, gatewayRead() { throw new Error('off'); } };
  return { root, config, calls, deps };
}
const arg = (args, name) => args[args.indexOf(name)+1];

test('every discovery command registers and runs API collector plus report', () => {
  for (const [command, spec] of Object.entries(READ_COMMANDS).filter(([,s]) => s.discovery)) {
    const f = fixture();
    const argv = [...command.split(' '), '--account', 'fixture', '--config', f.config, '--fresh', '--json'];
    if (command === 'keywords index-detail') argv.push('--keyword', 'test widget');
    assert.equal(main(argv, f.deps), 0);
    assert.equal(f.calls.length, 2);
    assert.equal(arg(f.calls[0].args, '--transport'), 'api');
    assert.equal(arg(f.calls[0].args, '--pages'), spec.pages[0]);
    assert.equal(f.calls[1].args[1], spec.report);
    assert.equal(arg(f.calls[1].args, '--format'), 'json');
    assert.ok(f.calls.every(c => c.exe === 'python3'));
  }
});

test('keyword, category and days get distinct cache folders and are forwarded intact', () => {
  const f = fixture();
  const run = (extra) => main(['keywords', 'index-detail', '--account', 'fixture', '--config', f.config, ...extra], f.deps);
  run(['--keyword', 'test widget', '--days', '7']);
  run(['--keyword', 'other widget', '--days', '7']);
  run(['--keyword', 'test widget', '--days', '30']);
  const dumps = f.calls.filter(c => c.args[0].endsWith('seller_central_raw.py'));
  assert.equal(new Set(dumps.map(c => arg(c.args, '--out'))).size, 3);
  assert.deepEqual(JSON.parse(arg(dumps[0].args, '--discovery-options')), { days: 7, keyword: 'test widget' });
  main(['analytics', 'market-keywords', '--account', 'fixture', '--config', f.config, '--category-id', '90003'], f.deps);
  assert.equal(JSON.parse(arg(f.calls.at(-2).args, '--discovery-options')).categoryId, '90003');
});

test('unsupported windows, historical dates, input filters and browser transports fail before collection', () => {
  for (const argv of [
    ['keywords','breakout','--days','7'],
    ['analytics','visitor-products','--days','180'], ['analytics','market-summary','--days','30'],
    ['analytics','market-keywords','--start','2026-01-01'], ['analytics','market-keywords','--end','2026-01-01'],
    ['analytics','market-keywords','--nd','30d'], ['analytics','market-keywords','--category-id','90001 > 90003'],
    ['analytics','visitor-products','--transport','repl'], ['analytics','visitor-products','--keyword','test'],
  ]) {
    const f=fixture();
    assert.throws(() => main([...argv,'--account','fixture','--config',f.config], f.deps));
    assert.equal(f.calls.length,0);
  }
});

test('offline replay uses report only, CSV and limit work, conflicting filters are rejected', () => {
  const f=fixture();mkdirSync(path.join(f.root,'raw'));
  assert.equal(main(['keywords','index-detail','--from',f.root,'--csv','--limit','9'],f.deps),0);
  assert.equal(f.calls.length,1);
  assert.equal(arg(f.calls[0].args,'--format'),'csv');
  assert.equal(arg(f.calls[0].args,'--limit'),'9');
  assert.ok(!f.calls[0].args.includes('--start'));
  assert.throws(() => main(['keywords','index-detail','--from',f.root,'--keyword','ignored'],f.deps),/--from/);
});

test('collection errors stop reporting and retain nonzero exit', () => {
  const f=fixture();
  f.deps.run=(exe,args)=>{f.calls.push({exe,args});return 5;};
  assert.equal(main(['analytics','visitor-groups','--account','fixture','--config',f.config],f.deps),5);
  assert.equal(f.calls.length,1);
});

test('same-query current cache is reused while fresh forces collection', () => {
  const f=fixture();
  const argv=['analytics','visitor-products','--account','fixture','--config',f.config,'--days','7'];
  main(argv,f.deps);
  const a=f.calls[0].args;const dir=arg(a,'--out');mkdirSync(path.join(dir,'raw'),{recursive:true});
  writeFileSync(path.join(dir,'raw','visitor-products.jsonl'),'');
  writeFileSync(path.join(dir,'manifest.json'),JSON.stringify({ start:arg(a,'--start'),end:arg(a,'--end'),generatedAt:new Date().toISOString(),pages:[{key:'visitor-products',status:'ok'}] }));
  f.calls.length=0;main(argv,f.deps);
  assert.equal(f.calls.length,1);
  f.calls.length=0;main([...argv,'--fresh'],f.deps);
  assert.equal(f.calls.length,2);
});

test('generic dump excludes parameterized discovery pages and directs explicit selection to commands', () => {
  const f=fixture();
  main(['dump','--account','fixture','--config',f.config,'--fresh'],f.deps);
  const pages=f.calls.filter(c=>c.args.includes('--pages')).flatMap(c=>arg(c.args,'--pages').split(','));
  const discoveryPages=Object.values(READ_COMMANDS).filter(s=>s.discovery).flatMap(s=>s.pages);
  assert.ok(pages.length>0);
  assert.ok(discoveryPages.every(p=>!pages.includes(p)));
  f.calls.length=0;
  assert.throws(()=>main(['dump','--account','fixture','--config',f.config,'--pages','keyword-index-detail'],f.deps),/전용 명령/);
  assert.equal(f.calls.length,0);
});
