// test-only: all IDs, contact fields and credentials below are synthetic fixtures.
import test from 'node:test';
import assert from 'node:assert/strict';
import { customerProducts, parseProfile } from '../engine/onetalk_inspect.mjs';

test('customer panel uses summary, HTML token and guarded JSONP without executing HTML', async () => {
  const calls = [];
  const client = {
    jar: { get: (name) => name === 'ctoken' ? 'fixture-csrf' : null },
    form: async (url, params) => { calls.push([url, params]); return { code: 200, data: { contact: { accountIdEncrypt: 'fixture-buyer' }, secTradeId: 'fixture-sec' } }; },
    request: async (url, init) => {
      calls.push([url, init]);
      if (url.includes('maDetail.htm')) return '<script>window.KHTAccessToken = "fixture-secret";</script>';
      return '/**/ typeof profileRead === \'function\' && profileRead(' + JSON.stringify({ data: { data: { buyerInfo: {
        name: 'private-fixture-name', phone: 'private-fixture-phone', buyerShopBehaviorInfo: { productVisitHistory: [{ productId: '900000001', productName: 'Fixture Tea', private: 'omit' }] }
      } } } }) + ');';
    },
  };
  const result = await customerProducts(client, { tradeId: '800000001', secTradeId: 'fixture-sec' });
  assert.deepEqual(result, { products: [{ id: '900000001', title: 'Fixture Tea' }], reason: null });
  assert.equal(calls.length, 3);
  assert.match(calls[1][0], /maDetail.htm\?imInquiryId=800000001$/);
  const q = new URL(calls[2][0]).searchParams;
  assert.equal(q.get('secReqToken'), 'fixture-secret');
  assert.equal(q.get('ctoken'), 'fixture-csrf');
  assert.ok(!JSON.stringify(result).includes('private'));
  assert.ok(!JSON.stringify(result).includes('fixture-secret'));
});

test('unknown products stay null with a reason; explicit empty history is an empty list', async () => {
  const client = { form: async () => ({ code: 200, data: {} }) };
  assert.match((await customerProducts(client, null)).reason, /tradeId/);
  const inquiry = { tradeId: '800000001', secTradeId: 'fixture-sec' };
  assert.deepEqual(await customerProducts(client, inquiry), { products: null, reason: 'querySummary contact 없음' });
  client.form = async () => ({ code: 200, data: { contact: { accountIdEncrypt: 'fixture' } } });
  client.request = async () => '<html>no token</html>';
  assert.match((await customerProducts(client, inquiry)).reason, /KHTAccessToken/);
  let buyer = {};
  client.request = async (url) => url.includes('maDetail') ? 'window.KHTAccessToken="fixture"' : JSON.stringify({ data: { data: { buyerInfo: buyer } } });
  assert.equal((await customerProducts(client, inquiry)).products, null);
  buyer = { buyerShopBehaviorInfo: { productVisitHistory: [] } };
  assert.deepEqual((await customerProducts(client, inquiry)).products, []);
  client.form = async () => ({ code: 500 });
  await assert.rejects(customerProducts(client, inquiry), /querySummary: 500/);
  assert.throws(() => parseProfile('profileRead({});evil()'), /invalid JSON/);
});
