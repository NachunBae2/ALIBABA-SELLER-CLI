// 테스트 전용 파일 — CLI 실행에 쓰이지 않음. 쿠키·토큰·ID 는 전부 가짜 예시 값이며 실제 셀러·바이어 데이터가 아님. 네트워크 없음(fetch·WebSocket 주입).
import assert from "node:assert/strict";
import test from "node:test";
import {
  mtopSign, md5, CookieJar, createClient, call, MtopError, HttpError, LwpError, decodeEntities, lwpMid,
  listConversations, listInquiries, getHistory, markRead, getBuyerInfo, inquiryParams, buyerInfoData, conversationRow, APP_KEY, IM_APP_KEY,
} from "../engine/onetalk_api.mjs";

const COOKIES = [
  { name: "cookie2", value: "fake-session", domain: ".alibaba.com", path: "/" },
  { name: "_m_h5_tk", value: "oldtok_1700000000000", domain: ".alibaba.com", path: "/" },
];
const json = (o, { status = 200, setCookie = [] } = {}) => ({
  status, ok: status >= 200 && status < 300,
  headers: { getSetCookie: () => setCookie, get: () => null },
  text: async () => (typeof o === "string" ? o : JSON.stringify(o)),
});
// fetch stub: handler(url, init) -> response; records every call
function stubFetch(handler) {
  const calls = [];
  const f = async (url, init = {}) => { calls.push({ url: String(url), init }); return handler(String(url), init, calls.length); };
  f.calls = calls;
  return f;
}
const q = (url) => new URL(url).searchParams;

// Fake LWP server: answers /reg and routes from a table; records frames sent by the client.
function fakeWebSocket(routes) {
  const sent = [];
  class WS {
    constructor(url, opts) { this.url = url; this.opts = opts; this.l = {}; WS.last = this; setTimeout(() => this.emit("open", {}), 0); }
    addEventListener(t, fn) { (this.l[t] ||= []).push(fn); }
    emit(t, e) { (this.l[t] || []).forEach((fn) => fn(e)); }
    send(s) {
      const m = JSON.parse(s); sent.push(m);
      if (!m.lwp) return; // ack of a push
      const reply = m.lwp === "/reg" ? { code: 200, headers: { "reg-uid": "1@icbu" }, body: {} } : routes[m.lwp] ? routes[m.lwp](m.body) : { code: 404, body: { reason: "no route" } };
      if (m.lwp === "/!") return;
      setTimeout(() => this.emit("message", { data: JSON.stringify({ ...reply, headers: { ...(reply.headers || {}), mid: m.headers.mid } }) }), 0);
    }
    push(lwp) { this.emit("message", { data: JSON.stringify({ lwp, headers: { mid: "9 0", sid: "s" }, body: {} }) }); }
    close() { this.emit("close", { code: 1000 }); }
  }
  WS.sent = sent;
  return WS;
}
const tokenFetch = (extra = () => null) => stubFetch((url, init) => {
  const r = extra(url, init);
  if (r) return r;
  if (url.includes("mtop.alibaba.icbu.im.login.token.get")) return json({ ret: ["SUCCESS::ok"], data: { object: { accessToken: "fake-access" } } });
  throw new Error("unexpected " + url);
});

test("mtop sign = md5(token&t&appKey&data)", () => {
  assert.equal(mtopSign("abc123", "1700000000000", "24889839", '{"a":1}'), "0abd12a9ad23fc6c5ed5ec75a36f057f");
  assert.equal(md5(""), "d41d8cd98f00b204e9800998ecf8427e");
});

test("mtop request: url, sign from _m_h5_tk prefix, cookies, data returned", async () => {
  const f = stubFetch(() => json({ ret: ["SUCCESS::调用成功"], data: { x: 1 } }));
  const c = createClient(COOKIES, { fetch: f, now: () => 1700000000000 });
  assert.deepEqual(await c.mtop("mtop.demo.Api", { a: 1 }, { v: "1.1" }), { x: 1 });
  const { url, init } = f.calls[0];
  assert.match(url, /^https:\/\/acs\.h\.alibaba\.com\/h5\/mtop\.demo\.api\/1\.1\/\?/);
  const p = q(url);
  assert.equal(p.get("appKey"), APP_KEY);
  assert.equal(p.get("data"), '{"a":1}');
  assert.equal(p.get("sign"), mtopSign("oldtok", "1700000000000", APP_KEY, '{"a":1}'));
  assert.equal(p.get("type"), "originaljson");
  assert.match(init.headers.cookie, /cookie2=fake-session/);
});

test("mtop token expired: Set-Cookie applied in RAM and the call is re-signed once", async () => {
  const f = stubFetch((url, init, n) => n === 1
    ? json({ ret: ["FAIL_SYS_TOKEN_EXOIRED::令牌过期"] }, { setCookie: ["_m_h5_tk=newtok_1700000009999; Domain=.alibaba.com; Path=/", "_m_h5_tk_enc=e1; Domain=.alibaba.com; Path=/"] })
    : json({ ret: ["SUCCESS::ok"], data: { ok: true } }));
  const c = createClient(COOKIES, { fetch: f, now: () => 1700000000001 });
  assert.deepEqual(await c.mtop("mtop.demo.api", {}), { ok: true });
  assert.equal(f.calls.length, 2);
  assert.equal(q(f.calls[1].url).get("sign"), mtopSign("newtok", "1700000000001", APP_KEY, "{}"));
  assert.match(f.calls[1].init.headers.cookie, /_m_h5_tk=newtok_1700000009999/);
  assert.equal(c.jar.get("_m_h5_tk"), "newtok_1700000009999");
  assert.equal(c.jar.toArray().filter((x) => x.name === "_m_h5_tk").length, 1);
});

test("mtop errors are thrown, not swallowed (token error twice, business error)", async () => {
  const twice = stubFetch(() => json({ ret: ["FAIL_SYS_TOKEN_EMPTY::empty"] }));
  await assert.rejects(createClient(COOKIES, { fetch: twice }).mtop("mtop.demo.api", {}), (e) => e instanceof MtopError && /TOKEN_EMPTY/.test(e.message));
  assert.equal(twice.calls.length, 2);
  const biz = stubFetch(() => json({ ret: ["FAIL_BIZ_PARAM::bad"] }));
  await assert.rejects(createClient(COOKIES, { fetch: biz }).mtop("mtop.demo.api", {}), MtopError);
  assert.equal(biz.calls.length, 1);
});

test("http: redirect (login page) and 5xx throw HttpError; non-JSON throws", async () => {
  await assert.rejects(createClient(COOKIES, { fetch: stubFetch(() => json("", { status: 302 })) }).mtop("mtop.demo.api", {}), (e) => e instanceof HttpError && e.status === 302);
  await assert.rejects(createClient(COOKIES, { fetch: stubFetch(() => json("", { status: 503 })) }).mtop("mtop.demo.api", {}), HttpError);
  await assert.rejects(createClient(COOKIES, { fetch: stubFetch(() => json("<html>login</html>")) }).mtop("mtop.demo.api", {}), /not JSON/);
});

test("form: onetalk gets _csrf from csrf/getToken.htm, message.alibaba.com gets _csrf_token_ and entity decoding", async () => {
  const f = stubFetch((url) => {
    if (url.endsWith("/csrf/getToken.htm")) return json({ token: "csrf-one" });
    if (url.endsWith("/message/ajax/csrftoken.htm")) return json({ data: { token: "csrf-msg" } });
    if (url.includes("onetalk.alibaba.com/message/x.htm")) return json({ code: "200", data: { ok: 1 } });
    if (url.includes("message.alibaba.com/message/ajax/y.htm")) return json('{"code":200,"data":{"s":"a &quot;b&quot; &amp; c"}}');
    throw new Error(url);
  });
  const c = createClient(COOKIES, { fetch: f });
  assert.deepEqual(await c.form("https://onetalk.alibaba.com/message/x.htm", { k: 1 }), { code: "200", data: { ok: 1 } });
  const b1 = new URLSearchParams(f.calls[1].init.body);
  assert.equal(b1.get("_csrf"), "csrf-one");
  assert.equal(b1.get("params"), '{"k":1}');
  const j = await c.form("https://message.alibaba.com/message/ajax/y.htm", { k: 2 }, { extra: { postId: "1" } });
  assert.equal(j.data.s, 'a "b" & c');
  const b2 = new URLSearchParams(f.calls[3].init.body);
  assert.equal(b2.get("_csrf_token_"), "csrf-msg");
  assert.equal(b2.get("postId"), "1");
  await c.form("https://onetalk.alibaba.com/message/x.htm", {}); // token cached
  assert.equal(f.calls.filter((x) => x.url.endsWith("/csrf/getToken.htm")).length, 1);
  assert.equal(decodeEntities("&lt;&#39;&gt;"), "<'>");
});

test("lwp: token -> /reg headers -> request frame; push acked; non-200 throws LwpError", async () => {
  const WS = fakeWebSocket({
    "/r/Conversation/listNewestPagination": () => ({ code: 200, body: { userConvs: [], hasMore: 0, nextCursor: 5 } }),
    "/r/Bad/route": () => ({ code: 500, body: { reason: "x" } }),
  });
  const f = tokenFetch();
  const c = createClient(COOKIES, { fetch: f, WebSocket: WS, deviceId: "dev-1" });
  const body = await c.lwp("/r/Conversation/listNewestPagination", [9, 100]);
  assert.deepEqual(body, { userConvs: [], hasMore: 0, nextCursor: 5 });
  const tokenCall = JSON.parse(q(f.calls[0].url).get("data"));
  assert.deepEqual(tokenCall, { deviceId: "dev-1", appKey: IM_APP_KEY });
  assert.equal(q(f.calls[0].url).get("appKey"), "12574478");
  const reg = WS.sent[0];
  assert.equal(reg.lwp, "/reg");
  assert.equal(reg.headers.token, "fake-access");
  assert.equal(reg.headers["app-key"], IM_APP_KEY);
  assert.equal(reg.headers.did, "dev-1");
  assert.match(reg.headers.mid, /^\d+ 0$/);
  assert.equal(WS.last.opts.headers.origin, "https://onetalk.alibaba.com");
  WS.last.push("/s/sync");
  assert.deepEqual(WS.sent.at(-1), { code: 200, headers: { mid: "9 0", sid: "s" } });
  await assert.rejects(c.lwp("/r/Bad/route", []), (e) => e instanceof LwpError && e.code === 500);
  c.close();
  assert.match(lwpMid(() => 0.5, () => 1700000000000), /^5001700000000000 0$/);
});

test("lwp: missing accessToken throws before opening a socket", async () => {
  const f = stubFetch(() => json({ ret: ["SUCCESS::ok"], data: { object: {} } }));
  let opened = 0;
  class WS { constructor() { opened++; } }
  await assert.rejects(createClient(COOKIES, { fetch: f, WebSocket: WS }).lwp("/r/x", []), MtopError);
  assert.equal(opened, 0);
});

test("request bodies: listConversations, getHistory, markRead", async () => {
  const conv = {
    type: 1,
    singleChatUserConversation: {
      redPoint: 2, visible: 1, modifyTime: 3, user_extension: { custom: JSON.stringify({ chatToken: "ct-1", toAliIdE: "E1" }), target: JSON.stringify({ id: "2000000000001", domain: "icbu" }) },
      lastMessage: { message: { messageId: "m1", cid: "c1@icbu", createAt: 10, content: { contentType: 1 }, sender: { uid: "u@icbu" } } },
      singleChatConversation: { cid: "c1@icbu", bizType: "11011", pairFirst: "a", pairSecond: "b", extension: { country: "ZZ" } },
    },
  };
  const WS = fakeWebSocket({
    "/r/Conversation/listNewestPagination": (b) => ({ code: 200, body: { userConvs: b[0] === 99 ? [conv] : [conv, conv], hasMore: b[0] === 99 ? 1 : 0, nextCursor: 7 } }),
    "/r/MessageManager/listUserMessages": () => ({ code: 200, body: { nextCursor: 1, userMessageModels: [{ readStatus: 2, message: { messageId: "m1", cid: "c1@icbu", createAt: 10, content: { contentType: 1, text: { content: "fixture" } }, sender: { uid: "u" } } }] } }),
    "/r/Conversation/clearRedPoint": () => ({ code: 200, body: {} }),
  });
  const c = createClient(COOKIES, { fetch: tokenFetch(), WebSocket: WS });
  const r = await listConversations(c, { cursor: 99, count: 100, pages: 2 });
  assert.equal(r.count, 3);
  assert.deepEqual(WS.sent.filter((m) => m.lwp === "/r/Conversation/listNewestPagination").map((m) => m.body), [[99, 100], [7, 100]]);
  assert.deepEqual({ cid: r.rows[0].cid, buyerAliId: r.rows[0].buyerAliId, chatToken: r.rows[0].chatToken, redPoint: r.rows[0].redPoint }, { cid: "c1@icbu", buyerAliId: "2000000000001", chatToken: "ct-1", redPoint: 2 });
  const h = await getHistory(c, { cid: "c1@icbu", cursor: 50, count: 20 });
  assert.deepEqual(WS.sent.find((m) => m.lwp === "/r/MessageManager/listUserMessages").body, ["c1@icbu", false, 50, 20, false]);
  assert.equal(h.rows[0].contentType, 1);
  await markRead(c, { cid: "c1@icbu", messageId: "m1" });
  assert.deepEqual(WS.sent.find((m) => m.lwp === "/r/Conversation/clearRedPoint").body, [[{ cid: "c1@icbu", messageId: "m1" }]]);
  await assert.rejects(getHistory(c, {}), /cid required/);
  await assert.rejects(markRead(c, { cid: "c1@icbu" }), /messageId required/);
  c.close();
});

test("lwp shape errors are thrown (no empty-list fallback)", async () => {
  const WS = fakeWebSocket({ "/r/Conversation/listNewestPagination": () => ({ code: 200, body: {} }) });
  await assert.rejects(listConversations(COOKIES, {}, { fetch: tokenFetch(), WebSocket: WS }), LwpError);
});

test("listInquiries: params, flattening, total, and unexpected shape throws", async () => {
  assert.deepEqual(inquiryParams({ page: 2 }), { listType: "all", pagination: { nextPage: 2, pageSize: 20 }, filter: { isShowAtm: false }, order: { order: "desc", orderBy: "latest_contact_time" }, search: {} });
  const f = stubFetch((url, init) => {
    if (url.endsWith("csrftoken.htm")) return json({ data: { token: "t" } });
    const p = JSON.parse(new URLSearchParams(init.body).get("params"));
    return json({ code: 200, data: { totalCount: 3, list: p.pagination.nextPage === 1 ? [[{ tradeId: 1 }], [{ tradeId: 2 }]] : [[{ tradeId: 3 }]] } });
  });
  const r = await listInquiries(COOKIES, { pageSize: 2, pages: 3 }, { fetch: f });
  assert.deepEqual(r.rows.map((x) => x.tradeId), [1, 2, 3]);
  assert.equal(r.total, 3);
  const bad = stubFetch((url) => (url.endsWith("csrftoken.htm") ? json({ data: { token: "t" } }) : json({ code: 200, data: { nothing: true } })));
  await assert.rejects(listInquiries(COOKIES, {}, { fetch: bad }), /unexpected shape/);
});

test("getBuyerInfo: data fields, batches of 20, level from userNewLevel", async () => {
  const contacts = Array.from({ length: 25 }, (_, i) => ({ aliId: String(3000000000000 + i), chatToken: "ct" + i }));
  assert.deepEqual(buyerInfoData(contacts.slice(0, 2)), { contactAliIds: "[3000000000000,3000000000001]", queryType: "get_buyer_member_info", queryParams: '["buyerNewFlag","countryCode","replyCountdown"]', chatTokens: "ct0,ct1" });
  const f = stubFetch((url) => {
    const d = JSON.parse(q(url).get("data"));
    return json({ ret: ["SUCCESS::ok"], data: { object: JSON.parse(d.contactAliIds).map((id, i) => (i === 0 ? { aliId: id } : { aliId: id, userNewLevel: "L1" })) } });
  });
  const r = await getBuyerInfo(COOKIES, { contacts }, { fetch: f });
  assert.equal(f.calls.length, 2);
  assert.equal(q(f.calls[0].url).get("v"), "1.1");
  assert.equal(r.count, 25);
  assert.equal(r.rows[0].level, null);
  assert.equal(r.rows[1].level, "L1");
  await assert.rejects(getBuyerInfo(COOKIES, { contacts: [] }), /required/);
  const bad = stubFetch(() => json({ ret: ["SUCCESS::ok"], data: {} }));
  await assert.rejects(getBuyerInfo(COOKIES, { contacts }, { fetch: bad }), MtopError);
});

test("call(): one-shot spec dispatch and unknown kind", async () => {
  const f = stubFetch(() => json({ ret: ["SUCCESS::ok"], data: { y: 2 } }));
  assert.deepEqual(await call(COOKIES, { kind: "mtop", api: "mtop.demo.api", data: {} }, { fetch: f }), { y: 2 });
  await assert.rejects(call(COOKIES, { kind: "nope" }, { fetch: f }), /unknown spec.kind/);
});

test("CookieJar: domain/path matching and conversationRow tolerates missing parts", () => {
  const jar = new CookieJar([{ name: "a", value: "1", domain: ".alibaba.com", path: "/" }, { name: "b", value: "2", domain: "login.alibaba.com", path: "/x" }]);
  assert.equal(jar.header("https://acs.h.alibaba.com/h5/"), "a=1");
  assert.equal(jar.header("https://login.alibaba.com/x/y"), "a=1; b=2");
  assert.equal(conversationRow({ type: 2 }).cid, undefined);
});
