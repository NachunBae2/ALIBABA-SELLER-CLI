// test-only: all IDs, names and secrets are synthetic; no network or sends.
import test from 'node:test';
import assert from 'node:assert/strict';
import { generateDraft, parseDraftStream } from '../engine/onetalk_draft.mjs';
const event = (data) => 'data:' + JSON.stringify(data) + '\n\n';
const chunk = (modelRspContent, thought = '') => event({ code: 200, data: JSON.stringify({ modelRspContent, thought }) });
const end = event({ code: 200, message: 'end', data: 'end' });

test('SSE requires completion, handles CRLF, and only returns generated content', () => {
  assert.deepEqual(parseDraftStream((chunk('hello', 'private reasoning') + chunk(' there') + end).replaceAll('\n', '\r\n')),
    { draft: 'hello there', ended: true, events: 3 });
  assert.equal(parseDraftStream(chunk('partial')).ended, false);
  assert.equal(parseDraftStream('').draft, '');
  assert.throws(() => parseDraftStream(event({ code: 403, message: 'private error text' })), /response code 403/);
  assert.throws(() => parseDraftStream('data:invalid\n\n'), /invalid SSE/);
  assert.throws(() => parseDraftStream(event({ code: 200, data: 'bad' })), /invalid content/);
});

test('draft generation only calls summary and the measured GET SSE endpoint', async () => {
  const calls = [];
  let response = chunk('Fixture reply', 'omit this') + end;
  const client = {
    deviceId: 'fixture-device',
    form: async (url, params) => { calls.push({ url, params }); return { code: 200, data: { contact: { loginId: 'fixture-buyer' } } }; },
    request: async (url, options) => { calls.push({ url, options }); return response; },
  };
  const conv = { cid: 'fixture-conversation', contactAliId: '4000000000001' };
  const inquiry = { secTradeId: 'fixture-sec' };
  const messages = [{ messageId: 'fixture-new.PNM', sendTime: 20 }, { messageId: 'fixture-old', sendTime: 10 }];
  const result = await generateDraft(client, conv, inquiry, messages, 'reply', { now: () => 123 });
  assert.equal(result.sent, false);
  assert.equal(result.draft, 'Fixture reply');
  assert.equal(calls.length, 2);
  assert.match(calls[0].url, /querySummary.htm$/);
  const url = new URL(calls[1].url);
  assert.equal(url.pathname, '/reception/aiCopilot');
  const protocol = JSON.parse(url.searchParams.get('protocolParam'));
  const context = JSON.parse(protocol.context);
  assert.equal(context.questionDesc.endMsgId, 'fixture-new.PNM');
  assert.equal(context.questionDesc.userContent, 'replySug');
  assert.equal(calls[1].options.method, undefined); // GET only
  assert.ok(!JSON.stringify(result).includes('fixture-buyer'));
  assert.ok(!JSON.stringify(result).includes('omit this'));
  response = chunk('partial');
  assert.equal((await generateDraft(client, conv, inquiry, messages)).status, 'draft_incomplete');
  response = end;
  assert.equal((await generateDraft(client, conv, inquiry, messages, 'summary')).status, 'draft_empty');
  await assert.rejects(generateDraft(client, conv, inquiry, messages, 'enhance'), /draft-kind/);
  assert.equal((await generateDraft(client, conv, null, messages)).status, 'draft_unavailable');
});
