// Synthetic server responses go through the real Python transport and report code.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { main, READ_COMMANDS } from '../lib/cli.mjs';
import { pythonCmd } from '../lib/platform.mjs';

const harness = String.raw`
import contextlib, io, json, sys
from email.message import Message
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
sys.path.insert(0, 'engine')
from api_session import ApiSession
from extra_reads import collect
from report import emit
from types import SimpleNamespace

fixture = json.load(sys.stdin)
calls = []
responses = iter(fixture['responses'])
def cookies(refresh):
    assert not refresh, 'new reads may not refresh seller login'
    rows = {'xman_us_t': 'nick=fixture-login&ctoken=nested%2Btoken',
            'ctoken': 'fallback-token', '_tb_token_': 'fixture-tb'}
    if fixture.get('token'):
        rows['_m_h5_tk'] = fixture['token']
    return [{'domain': '.alibaba.com', 'name': k, 'value': v} for k,v in rows.items()]

class Response(io.BytesIO):
    def info(self):
        return self.headers

class Opener:
    def open(self, req, timeout):
        info = urlsplit(req.full_url)
        session.jar.add_cookie_header(req)
        headers = dict(req.header_items())
        request = dict(host=info.hostname, path=info.path, method=req.get_method(),
                       query={k: v[0] for k,v in parse_qs(info.query, keep_blank_values=True).items()},
                       headers=headers)
        if req.data:
            body = req.data.decode()
            request['body'] = (json.loads(body) if headers.get('Content-type') == 'application/json' else
                               {k: v[0] for k,v in parse_qs(body, keep_blank_values=True).items()})
        calls.append(request)
        expected, payload, *meta = next(responses)
        assert expected == info.path, (expected, info.path)
        response = Response((payload if isinstance(payload, str) else json.dumps(payload)).encode())
        meta = meta[0] if meta else {}
        response.code, response.headers = meta.get('status', 200), Message()
        if meta.get('location'):
            response.headers.add_header('Location', meta['location'])
        if expected.startswith('/h5/') and isinstance(payload, dict) and payload.get('ret', [''])[0].startswith('FAIL_SYS_TOKEN'):
            response.headers.add_header('Set-Cookie', '_m_h5_tk=fresh-token_123; Path=/; Secure')
            session.jar.extract_cookies(response, req)
        return response

args = SimpleNamespace(command=fixture['command'], start='2025-01-01', end='2025-01-04',
                       max_pages=fixture.get('maxPages'), out=fixture.get('out'), format='json')
session = ApiSession({'name':'fixture', 'loginId':'fixture-login'}, 'fixture',
                     cookie_reader=cookies, opener=Opener(), refresh_saved=False)
stdout, stderr = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), patch('api_session.time.time_ns', return_value=1700000000000000000):
    tables = collect(fixture['command'], args, session)
    emit(tables, args, raw=True)
assert next(responses, None) is None, 'fixture responses left unused'
print(json.dumps(dict(rc=tables['_exit'], result=json.loads(stdout.getvalue()), stderr=stderr.getvalue(), calls=calls)))
`;

function run(command, responses, options = {}) {
  const [exe, ...pre] = pythonCmd();
  const p = spawnSync(exe, [...pre, '-c', harness], {
    encoding: 'utf8', input: JSON.stringify({ command, responses, ...options }),
  });
  assert.equal(p.status, 0, p.stderr);
  return JSON.parse(p.stdout);
}

const csrf = ['/api/csrf', { headerName: 'X-Fixture-Csrf', parameterName: '_csrf', token: 'fixture-csrf' }];
const ads = (data, totalCount) => ({ status: 1000, ok: true, error: null, data, totalCount });
const napi = (data) => ({ status: 1000, success: true, code: 200, data });
const ok = (data) => ({ code: 200, success: true, data });
const campaign = ['/api/campaign/find/showcase', ads(123)];
const privacy = { buyerName: 'private-demo', email: 'private-demo', ownerLoginId: 'private-demo',
  customerPhone: 'private-demo', shippingAddress: 'private-demo', contact: { fullName: 'private-demo' } };

function safeResult(r) {
  assert.equal(r.rc, 0, r.stderr);
  assert.equal(r.result.period.length, 1);
  assert.doesNotMatch(JSON.stringify(r.result), /private-demo|fixture-csrf|fixture-tb|nested\+token/);
}

test('ads adgroups: runtime campaign, all pages, both summaries, exact form and PII removal', () => {
  const base = '/api/campaign/123/adgroup';
  const r = run('ads-adgroups', [csrf, campaign,
    [base, ads([{ adGroupId: 1, impr: 3, buyer: 1, ...privacy }], 2)], [base, ads([{ adGroupId: 2 }], 2)],
    [base + '/summary', ads({ adgroupNum: 2 })], [base + '/summary', ads({ adgroupNum: 4 })]]);
  safeResult(r);
  assert.deepEqual(r.result.adgroups, [{ adGroupId: 1, impr: 3, buyer: 1 }, { adGroupId: 2 }]);
  assert.deepEqual(r.result.adgroup_summary.map(row => row.adgroupOnlineStatus), [1, 0]);
  assert.deepEqual(r.calls.slice(2, 4).map(c => JSON.parse(c.body.data).page), [1, 2]);
  for (const c of r.calls.slice(1)) {
    assert.equal(c.method, 'POST');
    assert.equal(c.body.ads, '{"productLineId":110101,"productId":110101009}');
    assert.equal(c.body._csrf, 'fixture-csrf');
    assert.equal(c.body.type, 'normal');
    assert.equal(c.headers['X-fixture-csrf'], 'fixture-csrf');
  }
});

test('ads showcase: four order states and bound overview ID', () => {
  const r = run('ads-showcase', [csrf, ['/api/showcase/order/count', ads({ execute: 1 })],
    ...[1, 2, 3, 4].map(orderId => ['/api/showcase/order/list', ads([{ orderId, ...privacy }])]),
    campaign, ['/api/showcase/overview', ads({ star: 1 })]]);
  safeResult(r);
  assert.deepEqual(r.result.showcase_orders.map(x => x.type), ['execute', 'issue_ready', 'service', 'suspend']);
  assert.deepEqual(JSON.parse(r.calls.at(-1).body.data), { campaignId: 123 });
});

test('ads brand: all six tables, three paginated responses, explicit dates', () => {
  const r = run('ads-brand', [csrf,
    ['/api/brand/campaign/getFlowCampaign', ads([{ id: 1 }])],
    ...[1, 2].map(id => ['/api/brand/campaign/listSupremeWordPackage', ads([{ id }], 2)]),
    ['/api/brand/supreme/flow/black/getBlackProductList', ads([{ productId: 1 }])],
    ['/api/brand/supreme/flow/black/getBlackWordsList', ads({ blackWordList: ['sample'] })],
    ...[1, 2].map(id => ['/brand/napi/creative/getCreativeList', napi({ result: [{ id }], totalCount: 2, code: '200', success: true })]),
    ...[1, 2].map(id => ['/brand/napi/dataReport/findKeywordEffect', napi({ list: [{ id }], totalCount: 2 })])]);
  safeResult(r);
  assert.deepEqual(Object.values(r.result).map(x => x.length), [1, 2, 1, 1, 2, 2, 1]);
  for (const c of r.calls.slice(-2)) {
    assert.deepEqual(JSON.parse(c.body.data).date, ['2025-01-01', '2025-01-04']);
    assert.equal(c.body.ads, '{"productLineId":110101}');
  }
});

test('ads brand: null package total only counts as empty with an explicit zero page count', () => {
  const responses = [csrf, ['/api/brand/campaign/getFlowCampaign', ads([])],
    ['/api/brand/campaign/listSupremeWordPackage', { ...ads([], null), totalPages: 0 }],
    ['/api/brand/supreme/flow/black/getBlackProductList', ads([])],
    ['/api/brand/supreme/flow/black/getBlackWordsList', ads({ blackWordList: [] })],
    ['/brand/napi/creative/getCreativeList', napi({ result: [], totalCount: 0, code: '200', success: true })],
    ['/brand/napi/dataReport/findKeywordEffect', napi({ list: [], totalCount: 0 })]];
  safeResult(run('ads-brand', responses));
  responses[2][1].totalPages = null;
  assert.equal(run('ads-brand', responses).rc, 3);
});

test('ads creatives: creative host CSRF and all pages', () => {
  const r = run('ads-creatives', [csrf, ['/api/creative/getSummaryInfo', ads({ allNum: 2 })],
    ...[1, 2].map(id => ['/api/creative/listCreative',
      ads([{ id, name: 'creative fixture', owner: { name: 'private-demo' } }], 2)])]);
  safeResult(r);
  assert.equal(r.result.creatives.length, 2);
  assert.equal(r.result.creatives[0].name, 'creative fixture');
  assert.deepEqual(r.result.creatives[0].owner, {});
  assert.equal(r.calls[0].host, 'creative.alibaba.com');
  assert.equal(r.calls[1].headers.Referer, 'https://www2.alibaba.com/supereme/index.htm');
  assert.equal(r.calls[1].headers.Origin, 'https://www2.alibaba.com');
  assert.equal(r.calls[1].body.ads, '{"productLineId":110102,"productId":110102001}');
});

test('products effect: paginated product source, batched IDs, month effects and JSONP radar', () => {
  const base = '/product/managementproducts/asyQuery';
  const products = Array.from({ length: 51 }, (_, i) => ({ id: 1000 + i }));
  const r = run('products-effect', [
    [base + 'ProductsList.do', { result: true, products: products.slice(0, 50), count: 51 }],
    [base + 'ProductsList.do', { result: true, products: products.slice(50), count: 51 }],
    [base + 'ProductEffect.do', { productEffect: { 1000: { clicks: 3 } } }],
    [base + 'ProductLock.do', ok([{ productId: 1000 }])],
    [base + 'ProductEffect.do', { productEffect: { 1050: { clicks: 4 } } }],
    [base + 'ProductLock.do', ok([{ productId: 1050 }])],
    ['/product/product_radar/radar_summary.do', '/**/dashboardCallback({"result":"success","todoDetail":{"totalNum":9}});'],
  ]);
  safeResult(r);
  assert.deepEqual(r.result.product_effect.map(x => x.productId), ['1000', '1050']);
  assert.deepEqual(r.result.product_radar, [{ totalNum: 9 }]);
  assert.equal(r.calls[2].query.productIds.split(',').length, 50);
  assert.equal(r.calls[2].query.statisticsType, 'month');
  assert.equal(r.calls[2].query.ctoken, 'nested+token');
  assert.equal(r.calls[4].query.productIds, '1050');
  assert.equal(r.calls[5].query.productIds, '1050');
  assert.equal(r.calls.at(-1).headers.Origin, undefined);
});

test('products growth: paginated JSON body with cookie tokens in query', () => {
  const url = '/product/management/query-incubation-product.json';
  const r = run('products-growth', [1, 2].map(id => [url, {
    code: 200, success: true, content: { totalCount: 2, contents: [{ id }] },
  }]));
  safeResult(r);
  assert.deepEqual(r.calls.map(c => c.body.page), [1, 2]);
  assert.equal(r.calls[0].query.ctoken, 'nested+token');
  assert.equal(r.calls[0].query._tb_token_, 'fixture-tb');
});

test('funds balance: fee rows and currency balances, no wallet tokens', () => {
  const r = run('funds-balance', [
    ['/ggs/web/fee/getFeeStatusView.json', ok({ statusItemList: [{ label: 'fee', value: 1 }] })],
    ['/ggs/web/funds/queryWalletList.json', ok([{ status: 'open', walletPermToken: 'private-demo', panMask: 'private-demo' }])],
    ['/ggs/web/withdraw/queryBalance.json', ok([{ currency: 'USD', balance: 123 }])],
    ['/seller/apollo/forex/queryWaitRelateAmountEveryCcy.json', ok([{ currency: 'USD', amount: 12 }])],
  ]);
  safeResult(r);
  assert.deepEqual(r.result.funds_wallets, [{ status: 'open' }]);
  assert.equal(r.result.funds_pending[0].amount, 12);
  for (const c of r.calls) {
    assert.equal(c.query.ctoken, 'nested+token');
    assert.equal(c.query._tb_token_, 'fixture-tb');
    assert.equal(c.headers.Origin, 'https://i.alibaba.com');
  }
});

test('promotions events: current topics and all enrollments, mismatch is exit 3', () => {
  const available = ['/enroll/GGSAcitities.do', { code: 200, topicDatas: [{ topicId: 1 }] }];
  const enrolled = (rows, total) => ['/enroll/myEnrollActivityList.do', { code: 200, enrolledActivityList: rows, total }];
  const r = run('promotions-events', [available, enrolled([{ id: 1 }], '2'), enrolled([{ id: 2 }], 2)]);
  safeResult(r);
  assert.deepEqual(r.calls.slice(1).map(c => c.query.pageNo), ['1', '2']);
  const incomplete = run('promotions-events', [available, enrolled([{ id: 1 }], 2), enrolled([], 2)]);
  assert.equal(incomplete.rc, 3);
  assert.equal(incomplete.result.promotion_enrolled.length, 1);
  assert.match(incomplete.stderr, /received 1 rows; total=2/);
});

test('store tasks: JSONP and mtop retries once with Set-Cookie, ID never emitted', () => {
  const url = '/h5/mtop.alibaba.camel.antioffline.ggs.punish.get/1.0/';
  for (const [token, condition] of [[undefined, 'EMPTY'], ['stale-token_111', 'EXOIRED']]) {
    const r = run('store-tasks', [
      ['/ajax/ggsTaskProgress.do', { result: 'success', data: { taskList: [{ taskId: 1 }] } }],
      ['/task/ggsFetchStarTaskV2.jsonp', 'dashboardCallback({"success":true,"values":[{"taskId":2}]});'],
      ['/message/weblitePWA.htm', '<script>window.aliId = "987654321";</script>'],
      [url, { ret: ['FAIL_SYS_TOKEN_' + condition + '::test'] }],
      [url, { ret: ['SUCCESS::test'], data: { code: 0, data: { punishEnabled: true, punishResult0Score: 4, punishResult3Score: 1, aliId: '987654321' } } }],
    ], { token });
    safeResult(r);
    assert.deepEqual(r.result.star_tasks, [{ taskId: 2 }]);
    assert.deepEqual(r.result.punishment_scores, [{ punishEnabled: true, punishResult0Score: 4, punishResult3Score: 1 }]);
    const signed = r.calls.slice(-2).map(c => c.query);
    const data = '{"aliId":"987654321"}';
    for (const [i, key] of [(token ?? '').split('_')[0], 'fresh-token'].entries()) {
      assert.equal(signed[i].data, data);
      assert.equal(signed[i].sign, createHash('md5').update(key + '&1700000000000&24889839&' + data).digest('hex'));
    }
    assert.doesNotMatch(JSON.stringify(r.result), /987654321/);
  }
});

test('knowledge files: all metadata pages and CSV per table, never requests file body', (t) => {
  const dir = mkdtempSync(path.join(tmpdir(), 'extra-reads-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const r = run('knowledge-files', [1, 2].map(id => [
    '/ai/knowledgeBaseQuery/queryKnowledgeFoldersOrFiles.do',
    ok({ totalCount: 2, dataList: [{ type: id, knowledgeBaseDTO: {
      id, name: 'test-file', aliId: 'private-demo', content: 'private-demo', downloadUrl: 'private-demo',
    } }] }),
  ]), { out: dir });
  safeResult(r);
  assert.deepEqual(r.calls.map(c => c.query.page), ['1', '2']);
  assert.equal(r.result.knowledge_files.length, 2);
  assert.match(readFileSync(path.join(dir, 'knowledge_files.csv'), 'utf8'), /test-file/);
  assert.match(readFileSync(path.join(dir, 'period.csv'), 'utf8'), /snapshot/);
});

test('business error remains verbatim on stderr, no refresh, exit 5', () => {
  const error = '{ "code" : 500, "message" : "fixture server error" }';
  const r = run('funds-balance', [['/ggs/web/fee/getFeeStatusView.json', error, { status: 503 }]]);
  assert.equal(r.rc, 5);
  assert.equal(r.stderr, error + '\n');
  assert.deepEqual(r.result.funds_fee_status, []);
});

test('saved-cookie authentication redirect stops immediately, without refresh or redirect', () => {
  const r = run('store-tasks', [['/ajax/ggsTaskProgress.do', '<html>private-demo</html>', {
    status: 302, location: 'https://login.alibaba.com/verify',
  }]]);
  assert.equal(r.rc, 5);
  assert.equal(r.stderr, 'session_expired_or_captcha\n');
  assert.equal(r.calls.length, 1);
});

test('all new Node commands route to the saved-cookie collector and put use case first in help', (t) => {
  const dir = mkdtempSync(path.join(tmpdir(), 'extra-cli-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const config = path.join(dir, 'config.json');
  writeFileSync(config, JSON.stringify({ fixture: { loginId: 'fixture-login' } }));
  const entries = Object.entries(READ_COMMANDS).filter(([, spec]) => spec.liveRead);
  assert.equal(entries.length, 10);
  for (const [command, spec] of entries) {
    let help, call;
    const deps = { scope: 'data', err() {}, log(s) { help = s; }, run(_exe, argv) { call = argv; return 3; } };
    const cmd = command.split(' ');
    assert.equal(main([...cmd, '--help'], deps), 0);
    assert.equal(help.split('\n')[0], spec.help);
    assert.equal(main([...cmd, '--transport', 'repl'], deps), 64);
    assert.equal(main([...cmd, '--transport', 'accio'], deps), 64);
    assert.equal(main([...cmd, '--transport', 'api', '--config', config, '--account', 'fixture',
      '--start', '2025-01-01', '--end', '2025-01-04', '--json', '--out', dir], deps), 3);
    assert.equal(call[call.findIndex(s => s.endsWith('extra_reads.py')) + 1], spec.liveRead);
    assert.equal(call[call.indexOf('--format') + 1], 'json');
    assert.equal(call[call.indexOf('--out') + 1], dir);
    assert.equal(call.includes('--start'), command === 'ads brand');
  }
});
