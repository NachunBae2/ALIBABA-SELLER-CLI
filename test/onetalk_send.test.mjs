// 테스트 전용 파일 — CLI 실행에 쓰이지 않음. 안의 ID·금액·클릭 수는 동작 확인용 예시 값이며 실제 셀러·고객 데이터가 아님.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildMessage, buildUploadData, buildUploadForm, cookieHeader, redact,
  sendText, sendProductCard, sendImage, sendFile, searchProducts, validateTarget,
  sendNameCard, uploadFile, main,
} from '../engine/onetalk_send.mjs';

const target = { buyerLevel: 'L1', conversationId: 'cid@icbu', senderAliId: '111', recipientAliId: '222', chatToken: 'secret-chat' };
const cookies = [{ name: 'sid', value: 'secret-cookie', domain: '.alibaba.com', path: '/', secure: true }];
const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

test('builds text wire params and validates L0/L1 targets', () => {
  assert.deepEqual(buildMessage(target, 1, 'hello'), {
    messageType: 1, content: 'hello', chatType: 1, extParam: {}, conversationId: 'cid',
    receiverAliId: [222, 111], chatToken: 'secret-chat',
  });
  assert.throws(() => validateTarget({ ...target, buyerLevel: 'L2' }), /REFUSED_BUYER_LEVEL/);
});

test('product card sends exact card fields and rejects server errors', async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    if (url.endsWith('/csrf/getToken.htm')) return jsonResponse({ token: 'csrf' });
    return jsonResponse({ code: 200, data: { messageID: 'msg-1' } });
  };
  const out = await sendProductCard(cookies, { target, productId: '333', country: 'US' }, { fetchImpl, now: () => 123 });
  assert.equal(out.messageType, 2000);
  const body = JSON.parse(new URLSearchParams(calls[1].init.body).get('params'));
  assert.deepEqual(body.content, { cardType: 2000, params: { type: '2000', ids: '333', ctime: '123', country: 'US' } });
  await assert.rejects(() => sendText(cookies, { target, text: 'x' }, { fetchImpl: async (u) => u.endsWith('csrf/getToken.htm') ? jsonResponse({ token: 'x' }) : jsonResponse({ code: 500 }) }), /code 500/);
});

test('name card uses type 57 and card payload', async () => {
  const calls = [];
  const fetchImpl = async (url, init) => { calls.push({ url, init }); return url.endsWith('csrf/getToken.htm') ? jsonResponse({ token: 'csrf' }) : jsonResponse({ code: 200, data: { messageID: 'name-msg' } }); };
  const out = await sendNameCard(cookies, { target }, { fetchImpl });
  assert.equal(out.messageType, 57);
  const body = JSON.parse(new URLSearchParams(calls[1].init.body).get('params'));
  assert.deepEqual(body.content, { cardType: 1, params: { type: '1', showCertification: 'true', showCompanyName: 'true', showEmailAddress: 'true' } });
});

test('upload helpers construct prepare/relation nesting and image message', async () => {
  const data = buildUploadData(target, 'pic.jpg', 3, 'a'.repeat(32));
  assert.deepEqual(JSON.parse(data.prepare.scene), { sceneType: '1', idType: '2', from: '111', to: '222' });
  assert.deepEqual(JSON.parse(data.relation.node), { nodeName: 'pic.jpg', nodeSize: 3, materialType: 'jpg', md5: 'a'.repeat(32) });
  const form = buildUploadForm({ uploadFileDir: 'dir/', policyDTO: { encodedPolicy: 'p', accessid: 'a', callbackBody: 'c', signature: 's' } }, 'a.jpg', new Uint8Array([1]), 'image/jpeg');
  assert.equal(form.get('key'), 'dir/a.jpg');
  const fetchImpl = async (url) => url.endsWith('/csrf/getToken.htm') ? jsonResponse({ token: 'csrf' }) : jsonResponse({ code: 200, data: { messageID: 'm' } });
  const out = await sendImage(cookies, { target, upload: { fileCardUrl: 'https://cdn.example/x.jpg?picWidth=10&picHeight=20', redirectFileUrl: 'https://cdn.example/x.jpg', nodeSize: 3, md5: 'a'.repeat(32), materialType: 'jpg' } }, { fetchImpl });
  assert.equal(out.messageType, 60);
});

test('uploadFile performs prepare, OSS multipart upload, relation in order', async () => {
  const events = [];
  const mtopRequest = async (cookies, req) => {
    events.push(`mtop:${req.api}`);
    const result = req.api.includes('prepare')
      ? { policyDTO: { host: 'https://oss.aliyuncs.com', encodedPolicy: 'p', accessid: 'a', callbackBody: 'c', signature: 's' }, uploadFileDir: 'dir/', fileIsExist: false }
      : { fileCardUrl: 'https://cdn.example/x?ctime=1&version=2', redirectFileUrl: 'https://cdn.example/x', fileId: 'id', id: 'i', parentId: 'p', downloadUrl: 'https://cdn.example/d', nodeName: 'x.txt', nodeSize: 2, md5: 'b'.repeat(32), materialType: 'txt' };
    return { ret: ['SUCCESS::OK'], data: { code: 200, result } };
  };
  const fetchImpl = async (url, init) => {
    if (url.includes('oss.aliyuncs.com')) { events.push('oss'); const form = init.body; assert.match(form.get('key'), /^dir\/[a-f0-9]{32}\.txt$/); assert.match(form.get('name'), /^dir\/[a-f0-9]{32}\.txt$/); assert.equal(form.get('success_action_status'), '200'); assert.equal((await form.get('file').arrayBuffer()).byteLength, 2); return new Response('', { status: 200 }); }
    throw new Error(`unexpected fetch ${url}`);
  };
  const out = await uploadFile(cookies, { target, name: 'x.txt', bytes: new Uint8Array([1, 2]) }, { mtopRequest, fetchImpl });
  assert.equal(out.fileId, 'id');
  assert.deepEqual(events, ['mtop:mtop.alibaba.interaction.clouddisk.prepareSendFileWithGroup', 'oss', 'mtop:mtop.alibaba.interaction.clouddisk.buildFileRelationWithGroup']);
});

test('fileIsExist skips OSS and sendFile emits type 53 card params', async () => {
  const events = [];
  const mtopRequest = async (cookies, req) => {
    events.push(req.api);
    const result = req.api.includes('prepare') ? { policyDTO: {}, uploadFileDir: '', fileIsExist: true } : { fileCardUrl: 'https://cdn/x?ctime=1&version=2', redirectFileUrl: 'https://cdn/x', id: 'i', parentId: 'p', downloadUrl: 'https://cdn/d', nodeName: 'x.txt', nodeSize: 2, md5: 'b'.repeat(32), materialType: 'txt' };
    return { ret: ['SUCCESS::OK'], data: { code: 200, result } };
  };
  const calls = [];
  const fetchImpl = async (url, init) => { calls.push({ url, init }); return url.endsWith('csrf/getToken.htm') ? jsonResponse({ token: 'csrf' }) : jsonResponse({ code: 200, data: { messageID: 'file-msg' } }); };
  await sendFile(cookies, { target, name: 'x.txt', bytes: new Uint8Array([1, 2]) }, { mtopRequest, fetchImpl });
  assert.equal(events.length, 2);
  const body = JSON.parse(new URLSearchParams(calls[1].init.body).get('params'));
  assert.equal(body.messageType, 53); assert.equal(body.content.cardType, 12); assert.equal(body.content.params.id, 'i');
});

test('upload and send failures are fatal with no subsequent calls', async () => {
  let calls = 0;
  const mtopRequest = async () => { calls++; return { ret: ['FAIL::NO'], data: { code: 500 } }; };
  await assert.rejects(() => uploadFile(cookies, { target, name: 'x.txt', bytes: new Uint8Array([1]) }, { mtopRequest, fetchImpl: async () => { throw new Error('must not fetch'); } }), /code FAIL::NO/);
  assert.equal(calls, 1);
  await assert.rejects(() => sendText(cookies, { target, text: 'x' }, { fetchImpl: async (url) => url.endsWith('csrf/getToken.htm') ? jsonResponse({ token: 'x' }) : jsonResponse({ code: 200, data: {} }) }), /MISSING_MESSAGE_ID_AMBIGUOUS/);
});

test('cookie scope, redaction, product errors, and empty response are strict', async () => {
  assert.equal(cookieHeader(cookies, 'https://onetalk.alibaba.com/message/sendMessage.htm'), 'sid=secret-cookie');
  assert.equal(cookieHeader(cookies, 'https://evil.example/'), '');
  assert.equal(redact({ token: 'secret-cookie', text: 'secret-cookie' }, cookies).token, '***');
  await assert.rejects(() => searchProducts(cookies, {}, { fetchImpl: async () => jsonResponse({ success: false, code: 'NO' }) }), /code NO/);
  assert.throws(() => validateTarget({ ...target, buyerLevel: 'L3' }), /REFUSED_BUYER_LEVEL/);
});

test('product search accepts verified result envelope', async () => {
  let request;
  const out = await searchProducts(cookies, { query: 'blue mug' }, { fetchImpl: async (url, init) => {
    request = { url: new URL(url), init };
    return jsonResponse({ result: true, count: 1, currentPage: 1, products: [{ id: '333', subject: 'Blue Mug' }] });
  } });
  assert.deepEqual(out.products, [{ id: '333', subject: 'Blue Mug' }]);
  assert.equal(request.url.searchParams.get('subject'), 'blue mug');
  assert.equal(request.url.searchParams.get('displayStatus'), 'on');
  assert.equal(request.url.searchParams.get('status'), 'approved');
  assert.equal(request.url.searchParams.get('marketType'), 'all');
  assert.equal(request.url.searchParams.get('imageType'), 'all');
  assert.equal(request.url.searchParams.get('page'), '1');
  assert.equal(request.url.searchParams.get('size'), '15');
  assert.equal(request.url.searchParams.get('gmtModified'), 'desc');
  assert.equal(request.url.searchParams.get('statisticsType'), 'month');
});

test('product search rejects invalid pagination before network', async () => {
  let calls = 0;
  const fetchImpl = async () => { calls++; return jsonResponse({ result: true, products: [] }); };
  await assert.rejects(() => searchProducts(cookies, { page: 0 }, { fetchImpl }), /Invalid pagination/);
  await assert.rejects(() => searchProducts(cookies, { size: 101 }, { fetchImpl }), /Invalid pagination/);
  assert.equal(calls, 0);
});

test('createClient fetch path uploads and links image before sending message', async () => {
  const events = [];
  let sendBody;
  const fetchImpl = async (url, init = {}) => {
    const parsed = new URL(url);
    if (parsed.hostname === 'acs.h.alibaba.com') {
      const api = parsed.pathname.split('/')[2];
      events.push(api);
      const result = api.includes('prepare')
        ? { policyDTO: { host: 'https://oss.aliyuncs.com', encodedPolicy: 'p', accessid: 'a', callbackBody: 'c', signature: 's' }, uploadFileDir: 'dir/', fileIsExist: false }
        : { fileCardUrl: 'https://cdn.example/x.jpg?picWidth=10&picHeight=20', redirectFileUrl: 'https://cdn.example/x.jpg', nodeSize: 2, md5: 'b'.repeat(32), materialType: 'jpg', fileId: 'f' };
      return jsonResponse({ ret: ['SUCCESS::OK'], data: { code: 200, result } });
    }
    if (parsed.hostname === 'oss.aliyuncs.com') { events.push('oss'); return new Response('', { status: 200 }); }
    if (url.endsWith('/csrf/getToken.htm')) return jsonResponse({ token: 'csrf' });
    events.push('send');
    sendBody = JSON.parse(new URLSearchParams(init.body).get('params'));
    return jsonResponse({ code: 200, data: { messageID: 'image-msg' } });
  };
  const out = await sendImage(cookies, { target, name: 'x.jpg', bytes: new Uint8Array([1, 2]) }, { fetchImpl });
  assert.equal(out.messageType, 60);
  assert.deepEqual(sendBody.content, {
    height: 20, width: 10, url: 'https://cdn.example/x.jpg', isOriginal: 1,
    size: 2, md5: 'b'.repeat(32), fileId: 'f', suffix: 'jpg',
  });
  assert.deepEqual(events, [
    'mtop.alibaba.interaction.clouddisk.preparesendfilewithgroup', 'oss',
    'mtop.alibaba.interaction.clouddisk.buildfilerelationwithgroup', 'send',
  ]);
});

test('OSS failure stops relation and message send', async () => {
  const events = [];
  const fetchImpl = async (url) => {
    const parsed = new URL(url);
    if (parsed.hostname === 'acs.h.alibaba.com') {
      events.push('prepare');
      return jsonResponse({ ret: ['SUCCESS::OK'], data: { code: 200, result: {
        policyDTO: { host: 'https://oss.aliyuncs.com', encodedPolicy: 'p', accessid: 'a', callbackBody: 'c', signature: 's' }, uploadFileDir: 'dir/', fileIsExist: false,
      } } });
    }
    if (parsed.hostname === 'oss.aliyuncs.com') { events.push('oss'); return new Response('', { status: 503 }); }
    events.push('unexpected');
    return jsonResponse({ code: 200, data: { messageID: 'must-not-send' } });
  };
  await assert.rejects(() => sendImage(cookies, { target, name: 'x.jpg', bytes: new Uint8Array([1]) }, { fetchImpl }), /OSS upload: HTTP 503/);
  assert.deepEqual(events, ['prepare', 'oss']);
});

test('relation failure stops image message send', async () => {
  const events = [];
  const fetchImpl = async (url) => {
    const parsed = new URL(url);
    if (parsed.hostname === 'acs.h.alibaba.com') {
      const api = parsed.pathname.split('/')[2];
      events.push(api);
      const result = api.includes('prepare') ? { policyDTO: {}, uploadFileDir: '', fileIsExist: true } : {};
      return jsonResponse({ ret: ['SUCCESS::OK'], data: { code: 200, result } });
    }
    events.push('unexpected');
    return jsonResponse({ code: 200, data: { messageID: 'must-not-send' } });
  };
  await assert.rejects(() => sendImage(cookies, { target, name: 'x.jpg', bytes: new Uint8Array([1]) }, { fetchImpl }), /Missing fileCardUrl/);
  assert.deepEqual(events, [
    'mtop.alibaba.interaction.clouddisk.preparesendfilewithgroup',
    'mtop.alibaba.interaction.clouddisk.buildfilerelationwithgroup',
  ]);
});

test('CLI rejects malformed args and missing cookies without throwing while reporting', async () => {
  for (const [args, input] of [['null', '{}'], ['{}', '{}'], ['{}', '{"cookies":null}']]) {
    const lines = [];
    assert.equal(await main(['sendText', '--json', args], input, line => lines.push(line)), 1);
    assert.equal(typeof JSON.parse(lines[0]).error, 'string');
  }
});

test('shared transport failures omit server response text and token details', async () => {
  const bytes = new Uint8Array([1]);
  await assert.rejects(() => uploadFile(cookies, { target, name: 'x.jpg', bytes }, {
    fetchImpl: async () => jsonResponse({ ret: ['FAIL_SYS_DENIED::refreshed-private-token'] }),
  }), error => error.code === 'FAIL_SYS_DENIED' && !error.message.includes('refreshed-private-token'));
  await assert.rejects(() => sendText(cookies, { target, text: 'x' }, {
    fetchImpl: async () => new Response('refreshed-private-token', { status: 200 }),
  }), error => error.code === 'HTTP_ERROR' && !error.message.includes('refreshed-private-token'));
  assert.equal(redact('https://cdn.example/file?accessToken=private-value&x=1'), 'https://cdn.example/file?accessToken=***&x=1');
});
