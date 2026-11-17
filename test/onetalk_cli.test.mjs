// 테스트 전용 파일 — CLI 실행에 쓰이지 않음. 안의 ID·이름·연락처는 동작 확인용 예시 값이며 실제 셀러·고객 데이터가 아님.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { brief, convBrief, main, mask, reader, run, templateText, TYPES } from "../engine/onetalk_cli.mjs";
import { ONETALK, MESSAGE, HttpError, MtopError } from "../engine/onetalk_api.mjs";
import { ONETALK as COMMANDS, apiRequest, onetalkMain } from "../lib/onetalk.mjs";

const SELF = "1000000000001";
const COOKIES = [{ name: "fixture_session", value: "fixture-cookie-value", domain: ".alibaba.com", path: "/" }];

// In-memory OneTalk + message center behind the requester's form(); records every call.
function fakeWorld() {
  const w = {
    convs: [
      { cid: "c1", chatToken: "chat-token-a", selfAliId: SELF, contactAliId: "4000000000001", contactAccountId: "5000000000001", unreadCount: 0, modifyTime: 1, chatType: 1, latestMessage: { messageType: 1 } },
      { cid: "c2", chatToken: "chat-token-b", selfAliId: SELF, contactAliId: "4000000000002", contactAccountId: "5000000000002", unreadCount: 1, modifyTime: 2, chatType: 1, contactName: "Fixture Buyer" },
    ],
    msgs: { "4000000000001": [{ messageId: "m0", messageType: 1, messageSendType: "receive", sendTime: 1, content: "hi, mail me at fixture@example.com or +1 555 010 0000" }] },
    inquiries: [{ id: "900000001", tradeId: "800000001", secTradeId: "sec-1", hasUnread: true, sender: { accountId: "5000000000002", name: "Fixture Buyer" } }],
    calls: [],
  };
  w.tagDefs = [{ tagId: "T1", tagName: "VIP", tagType: 2, tagColor: "#f00", status: 1 }, { tagId: "T2", tagName: "Sample sent", tagType: 2, tagColor: "#0f0", status: 1 },
    { tagId: "T9", tagName: "Auto reception", tagType: 9, tagColor: "#00f", status: 1 }];
  w.contactTags = { "4000000000001": [{ tagId: "T2", tagType: 2 }] };
  w.templates = [{ id: 7001, templateName: "Fixture Brand is a sample line", content: "Hello,<br>Fixture Brand price list &amp; samples are ready." },
    { id: 7002, templateName: "Welcome note", content: "Thanks for your message." }, { id: 7003, templateName: "Welcome back", content: "Good to hear from you again." }];
  w.client = {
    async mtop(api, data, opts = {}) {
      w.calls.push([api, data, opts]);
      if (api === "mtop.alibaba.icbu.tag.feature.query") return { code: "0", data: { protocolList: w.tagDefs } };
      if (api === "mtop.alibaba.icbu.contact.tag.batch.query") {
        const id = JSON.parse(data.targetIds)[0];
        return { code: "0", data: (w.contactTags[id] || []).map((t) => ({ ...t, aliId: id, tagTargetId: id, status: 1 })) };
      }
      if (api === "mtop.alibaba.icbu.tag.relation.save") { w.contactTags[data.targetId] = JSON.parse(data.tagRelReqList); return { code: "0", data: true }; }
      assert.equal(api, "mtop.alibaba.icbu.im.getUserInfoByParams");
      assert.equal(opts.v, "1.1");
      assert.equal(data.queryType, "get_buyer_member_info");
      assert.ok(JSON.parse(data.contactAliIds).length <= 20);
      return { object: JSON.parse(data.contactAliIds).map((aliId) => ({
        aliId, ...(aliId % 2 ? { userNewLevel: "L2" } : {}), countryCode: "US", joiningYears: 3, potentialScore: 7,
        name: "Fixture Buyer", email: "fixture@example.com", phone: "+1 555 010 0000", loginId: "fixture-login", chatToken: "fixture-token",
      })).reverse() };
    },
    async form(url, params, opts = {}) {
      w.calls.push([url.replace(ONETALK, "OT").replace(MESSAGE, "MC"), params, opts]);
      if (url.endsWith("/pageListRecentConversation.htm")) return { code: 200, data: { dataList: w.convs, hasMore: false } };
      if (url.endsWith("/listRecentMessage.htm")) return { code: 200, data: { messageList: (w.msgs[params.contactAliId] || []).slice(0, params.count) } };
      if (url.endsWith("/subjectList.htm")) return { code: 200, data: { list: w.inquiries.map((x) => [x]), pagination: { totalCount: w.inquiries.length } } };
      if (url.endsWith("/markRead.htm")) {
        for (const x of w.inquiries) if (x.secTradeId === params.data[0].secTradeId) x.hasUnread = params.readStatus === "unread";
        return { code: 200, data: "ALL_SUCCESS" };
      }
      if (url.endsWith("/template/index.htm")) {
        const { cmd, params: p } = JSON.parse(opts.extra.json);
        if (cmd === "list") return { code: 200, data: { list: w.templates.map(({ content, ...t }) => ({ ...t, content: "" })) } };
        if (cmd === "find") return { code: 200, data: { list: w.templates.filter((t) => t.id === p.id) } };
      }
      throw new Error("unexpected " + url);
    },
  };
  // send module stand-in: the message shows up in the conversation like the real one
  let n = 0;
  const post = (type, extra = {}) => (cookies, { target }) => {
    assert.deepEqual(cookies, COOKIES);
    assert.equal(target.senderAliId, SELF);
    const m = { messageId: "s" + ++n, messageType: type, messageSendType: "send", sendTime: 10 + n, ...extra };
    if (w.dropSends) return { messageId: m.messageId };
    (w.msgs[target.recipientAliId] ||= []).unshift(m);
    w.sent.push([type, target]);
    return { messageId: m.messageId, messageType: type };
  };
  w.sent = [];
  w.send = {
    sendText: (c, a) => post(TYPES.text, { content: a.text })(c, a),
    sendNameCard: post(TYPES.card),
    sendProductCard: (c, a) => post(TYPES.product, { content: JSON.stringify({ cardType: 2000, ids: a.productId }) })(c, a),
    uploadFile: async (c, a) => { w.uploaded = { name: path.basename(a.path), contentType: a.contentType, size: a.bytes.byteLength }; return { fileCardUrl: "https://example.invalid/card", redirectFileUrl: "https://example.invalid/f" }; },
    sendImage: post(TYPES.image),
    sendFile: post(TYPES.file),
  };
  w.deps = { cookies: COOKIES, send: w.send, sleep: async () => {} };
  w.r = reader(w.client, { now: () => 1789000000000 });
  return w;
}

test("send-card by buyer ali id posts a type 57 card and confirms it in the read-back", async () => {
  const w = fakeWorld();
  const res = await run("send", { buyerAliId: "4000000000001", kind: "card" }, w.r, w.deps);
  assert.equal(res.status, "ok");
  assert.deepEqual(res.readback, { messageType: 57, seen: true });
  assert.deepEqual(w.sent.map(([t, tg]) => [t, tg.conversationId, tg.recipientAliId, tg.chatToken]), [[57, "c1", "4000000000001", "chat-token-a"]]);
});

test("text and product are each confirmed by their own message in the read-back", async () => {
  const w = fakeWorld();
  const t = await run("send", { buyerAliId: "4000000000001", kind: "text", text: "Thanks for your message." }, w.r, w.deps);
  assert.equal(t.status, "ok");
  assert.equal(t.readback.textSeen, true);
  assert.ok(!JSON.stringify(t).includes("Thanks for your message"));
  const p = await run("send", { buyerAliId: "4000000000001", kind: "product", productId: "11000000000001" }, w.r, w.deps);
  assert.equal(p.status, "ok");
  assert.equal(p.readback.productSeen, true);
});

test("a send the conversation does not show is sent_not_confirmed", async () => {
  const w = fakeWorld();
  w.dropSends = true;
  const res = await run("send", { buyerAliId: "4000000000001", kind: "card" }, w.r, w.deps);
  assert.equal(res.status, "sent_not_confirmed");
  assert.equal(res.sentMessage, true);
  assert.equal(res.readback.seen, false);
});

test("file: a PDF goes out as a file card (53), an image as an image message (60)", async () => {
  const w = fakeWorld();
  const dir = mkdtempSync(path.join(tmpdir(), "asc-otc-"));
  for (const f of ["catalog.pdf", "photo.jpg"]) writeFileSync(path.join(dir, f), "fixture bytes");
  const pdf = await run("send", { buyerAliId: "4000000000001", kind: "file", path: path.join(dir, "catalog.pdf") }, w.r, w.deps);
  assert.equal(pdf.status, "ok");
  assert.equal(pdf.readback.messageType, 53);
  assert.deepEqual(w.uploaded, { name: "catalog.pdf", contentType: "application/pdf", size: 13 });
  const jpg = await run("send", { buyerAliId: "4000000000001", kind: "file", path: path.join(dir, "photo.jpg") }, w.r, w.deps);
  assert.equal(jpg.readback.messageType, 60);
  assert.equal(w.uploaded.contentType, "image/jpeg");
});

test("inquiry id resolves the conversation through the sender's account id (= contactAccountId)", async () => {
  const w = fakeWorld();
  const res = await run("messages", { inquiryId: "900000001" }, w.r, w.deps);
  assert.equal(res.status, "ok");
  assert.equal(res.conversation.buyerAliId, "4000000000002");
  const mc = w.calls.find(([u]) => u.endsWith("subjectList.htm"));
  assert.ok(mc[2].extra.postId, "message center posts carry a postId");
});

test("unknown inquiry and unknown buyer are reported, nothing is sent", async () => {
  const w = fakeWorld();
  assert.equal((await run("send", { inquiryId: "1", kind: "card" }, w.r, w.deps)).status, "inquiry_not_found");
  assert.equal((await run("send", { buyerAliId: "4999999999999", kind: "card" }, w.r, w.deps)).status, "conversation_not_found");
  assert.deepEqual(w.sent, []);
  await assert.rejects(run("send", { kind: "card" }, w.r, w.deps), (e) => e.code === 64);
  await assert.rejects(run("mark-read", { buyerAliId: "4000000000001" }, w.r, w.deps), (e) => e.code === 64);
});

test("mark-read / mark-unread flip the inquiry's unread flag and read its row back", async () => {
  const w = fakeWorld();
  const a = await run("mark-read", { inquiryId: "900000001" }, w.r, w.deps);
  assert.deepEqual(a, { status: "ok", readback: { hasUnreadBefore: true, hasUnreadAfter: false } });
  const b = await run("mark-unread", { tradeId: "800000001" }, w.r, w.deps);
  assert.deepEqual(b, { status: "ok", readback: { hasUnreadBefore: false, hasUnreadAfter: true } });
  // already unread: no call (the endpoint answers 400 to a no-op), same answer
  const n = w.calls.filter(([u]) => u.endsWith("markRead.htm")).length;
  const c = await run("mark-unread", { inquiryId: "900000001" }, w.r, w.deps);
  assert.deepEqual(c, { status: "ok", unchanged: true, readback: { hasUnreadBefore: true, hasUnreadAfter: true } });
  assert.equal(w.calls.filter(([u]) => u.endsWith("markRead.htm")).length, n);
  const mark = w.calls.find(([u]) => u.endsWith("markRead.htm"));
  assert.deepEqual(mark[1], { readStatus: "read", data: [{ secTradeId: "sec-1" }], listMode: "REC_AGGREGATED" });
});

test("conversation list and messages carry no names, tokens or contact details", async () => {
  const w = fakeWorld();
  const list = await run("conversations", { count: 5 }, w.r, w.deps);
  assert.equal(list.count, 2);
  assert.deepEqual(Object.keys(list.conversations[0]).sort(), ["buyerAliId", "chatType", "lastSendType", "lastTime", "lastType", "level", "levelSource", "modifyTime", "unread"]);
  assert.equal(list.conversations[0].levelSource, "userNewLevel");
  assert.equal(list.conversations[1].levelSource, "no_level_field");
  for (const v of ["Fixture Buyer", "chat-token", "c1"]) assert.ok(!JSON.stringify(list).includes(v), v);
  const ms = await run("messages", { buyerAliId: "4000000000001", withText: true }, w.r, w.deps);
  assert.equal(ms.messages[0].text, "hi, mail me at [email] or [phone]");
  assert.equal(brief({ messageType: 1, content: "x" }).text, undefined);
  assert.equal(mask("a.b@c.io"), "[email]");
  assert.equal(convBrief({ contactAliId: 7 }).buyerAliId, "7");
});

test("main: one JSON line; a login redirect is exit 2; the cookie never reaches the output", async () => {
  const w = fakeWorld();
  let out = [];
  const code = await main(["conversations"], JSON.stringify({ cookies: COOKIES, request: { count: 2 } }), { output: (s) => out.push(s), client: () => w.client });
  assert.equal(code, 0);
  assert.equal(out.length, 1);
  assert.equal(JSON.parse(out[0]).status, "ok");
  out = [];
  const redirect = { form: async () => { const e = new Error("https://onetalk.alibaba.com/x?token=fixture-cookie-value: HTTP 302"); e.status = 302; throw e; } };
  assert.equal(await main(["conversations"], JSON.stringify({ cookies: COOKIES }), { output: (s) => out.push(s), client: () => redirect }), 2);
  assert.match(JSON.parse(out[0]).error, /alibaba-data login/);
  assert.ok(!out.join("").includes("fixture-cookie-value"));
  out = [];
  assert.equal(await main(["conversations"], "{}", { output: (s) => out.push(s), client: () => w.client }), 2);
  assert.equal(await main(["bogus"], JSON.stringify({ cookies: COOKIES }), { output: (s) => out.push(s), client: () => w.client }), 64);
});

test("buyer uses all existing target forms and returns only non-identifying fields", async () => {
  const w = fakeWorld();
  const first = await run("buyer", { buyerAliId: w.convs[0].contactAliId }, w.r);
  assert.deepEqual(first, { status: "ok", buyer: { buyerAliId: w.convs[0].contactAliId, level: "L2", levelSource: "userNewLevel", countryCode: "US", joiningYears: 3, potentialScore: 7 } });
  for (const req of [{ inquiryId: "900000001" }, { tradeId: "800000001" }]) {
    const result = await run("buyer", req, w.r);
    assert.deepEqual(result.buyer, { ...first.buyer, buyerAliId: w.convs[1].contactAliId, level: null, levelSource: "no_level_field" });
  }
  assert.equal((await run("buyer", { inquiryId: "missing" }, w.r)).status, "inquiry_not_found");
  assert.equal((await run("buyer", { buyerAliId: "missing" }, w.r)).status, "conversation_not_found");
  await assert.rejects(run("buyer", {}, w.r), (e) => e.code === 64);
  assert.ok(!w.calls.some(([url]) => /markRead|listRecentMessage|sendMessage/.test(url)));
  assert.deepEqual(w.sent, []);
});

test("conversations batches buyer lookup in twenties and maps unordered responses by id", async () => {
  const w = fakeWorld();
  w.convs = Array.from({ length: 41 }, (_, i) => ({ ...w.convs[0], contactAliId: String(4000000000001 + i), chatToken: `fixture-token-${i}` }));
  const result = await run("conversations", { count: 41, cursor: 123 }, w.r);
  assert.equal(result.count, 41);
  assert.deepEqual(result.conversations.map((c) => c.level), w.convs.map((_, i) => i % 2 ? null : "L2"));
  const calls = w.calls.filter(([api]) => api.startsWith("mtop."));
  assert.deepEqual(calls.map(([, params]) => JSON.parse(params.contactAliIds).length), [20, 20, 1]);
  assert.deepEqual(calls.map(([, params]) => params.chatTokens.split(",")), [w.convs.slice(0, 20), w.convs.slice(20, 40), w.convs.slice(40)].map((part) => part.map((c) => c.chatToken)));
  assert.equal(w.calls[0][1].limitTimeStamp, 123);
  w.convs = [];
  w.calls = [];
  assert.deepEqual(await run("conversations", {}, w.r), { status: "ok", count: 0, hasMore: false, nextCursor: null, conversations: [] });
  assert.equal(w.calls.length, 1);
});

test("buyer lookup failures stay errors, while missing records are reported", async () => {
  for (const op of ["conversations", "buyer"]) {
    const w = fakeWorld();
    const req = { buyerAliId: w.convs[0].contactAliId };
    for (const error of [new HttpError("https://fixture.invalid/buyer", 503), new MtopError("fixture.buyer", ["FAIL_FIXTURE"])]) {
      w.client.mtop = async () => { throw error; };
      await assert.rejects(run(op, req, w.r), (e) => e === error);
      const output = [];
      assert.equal(await main([op], JSON.stringify({ cookies: COOKIES, request: req }), { client: () => w.client, output: (line) => output.push(JSON.parse(line)) }), 1);
      assert.equal(output[0].status, "error");
    }
    w.client.mtop = async () => ({ object: [] });
    const output = [];
    assert.equal(await main([op], JSON.stringify({ cookies: COOKIES, request: req }), { client: () => w.client, output: (line) => output.push(JSON.parse(line)) }), 0);
    const result = output[0];
    assert.equal(result.status, op === "conversations" ? "ok" : "no_buyer_info");
    const buyers = op === "conversations" ? result.conversations : [result.buyer];
    assert.ok(buyers.length > 0);
    for (const buyer of buyers) {
      assert.equal(buyer.level, null);
      assert.equal(buyer.levelSource, "no_buyer_info");
    }
    w.client.mtop = async () => ({});
    await assert.rejects(run(op, req, w.r), /NO_OBJECT_ARRAY/);
  }
});

test("missing one buyer row preserves the other rows and group chats skip lookup", async () => {
  const w = fakeWorld();
  w.client.mtop = async (...args) => {
    const ids = JSON.parse(args[1].contactAliIds);
    return { object: ids.filter((id) => String(id) !== w.convs[0].contactAliId).map((aliId) => ({ aliId, userNewLevel: "L3" })) };
  };
  const result = await run("conversations", {}, w.r);
  assert.equal(result.conversations[0].level, null);
  assert.equal(result.conversations[0].levelSource, "no_buyer_info");
  assert.equal(result.conversations[1].level, "L3");
  assert.equal(result.conversations[1].levelSource, "userNewLevel");
  w.convs[0].chatType = 2;
  w.client.mtop = async (...args) => {
    w.calls.push(args);
    return { object: JSON.parse(args[1].contactAliIds).map((aliId) => ({ aliId, userNewLevel: "L4" })) };
  };
  w.calls = [];
  const group = await run("buyer", { buyerAliId: w.convs[0].contactAliId }, w.r);
  assert.deepEqual(group, { status: "ok", buyer: { buyerAliId: w.convs[0].contactAliId, level: null, levelSource: "not_one_to_one" } });
  assert.equal(w.calls.filter(([api]) => api.startsWith("mtop.")).length, 0);
  w.calls = [];
  const mixed = await run("conversations", {}, w.r);
  assert.deepEqual(mixed.conversations.map((c) => [c.level, c.levelSource]), [[null, "not_one_to_one"], ["L4", "userNewLevel"]]);
  const mixedLookup = w.calls.find(([api]) => api.startsWith("mtop."));
  assert.deepEqual(JSON.parse(mixedLookup[1].contactAliIds), [Number(w.convs[1].contactAliId)]);
  w.convs[1].chatType = 2;
  w.calls = [];
  const allGroups = await run("conversations", {}, w.r);
  assert.deepEqual(allGroups.conversations.map((c) => [c.level, c.levelSource]), [[null, "not_one_to_one"], [null, "not_one_to_one"]]);
  assert.equal(w.calls.filter(([api]) => api.startsWith("mtop.")).length, 0);
});

test("send-product searches once, sends the sole result, and confirms its resolved id", async () => {
  const w = fakeWorld();
  const queries = [];
  w.send.searchProducts = async (cookies, args) => {
    assert.deepEqual(cookies, COOKIES);
    queries.push(args);
    return { products: [{ id: "11000000000001" }], count: "1" };
  };
  const req = { buyerAliId: w.convs[0].contactAliId, kind: "product", product: " Fixture Tea " };
  const result = await run("send", req, w.r, w.deps);
  assert.equal(result.status, "ok");
  assert.equal(result.readback.productSeen, true);
  assert.deepEqual(queries, [{ query: "Fixture Tea" }]);
  assert.equal(w.sent.length, 1);
  assert.equal(req.productId, undefined);
  assert.equal(req.productIdResolved, undefined);
  const explicit = await run("send", { ...req, productId: "11000000000002" }, w.r, w.deps);
  assert.equal(explicit.readback.productSeen, true);
  assert.equal(queries.length, 1, "explicit product id does not search");
});

test("send-product refuses zero, multiple, paginated and invalid results without sending", async () => {
  for (const [products, count] of [[[], 0], [[{ id: "11000000000001" }, { id: "11000000000002" }], 2], [[{ id: "11000000000001" }], 20]]) {
    const w = fakeWorld();
    w.send.searchProducts = async () => ({ products, count });
    await assert.rejects(run("send", { buyerAliId: w.convs[0].contactAliId, kind: "product", product: "Fixture" }, w.r, w.deps), (e) => e.code === 64 && e.message.includes(`${count}개`));
    assert.deepEqual(w.sent, []);
  }
  {
    const w = fakeWorld();
    const queries = [];
    w.send.searchProducts = async (cookies, args) => {
      queries.push(args);
      return args.query ? { products: [], count: 0 } : { products: [{ id: "11000000000001", subject: "Fixture Mug" }], count: 1 };
    };
    await assert.rejects(run("send", { buyerAliId: w.convs[0].contactAliId, kind: "product", product: "Other Brand" }, w.r, w.deps),
      (e) => e.code === 64 && e.message.includes('"Other Brand"') && e.message.includes("승인 상품 1개"));
    assert.deepEqual(queries, [{ query: "Other Brand" }, {}]);
    w.send.searchProducts = async () => ({ products: [{ id: "11000000000001", subject: "Fixture Mug" }, { id: "11000000000002", subject: "Fixture Cup" }], count: 2 });
    await assert.rejects(run("send", { buyerAliId: w.convs[0].contactAliId, kind: "product", product: "Fixture" }, w.r, w.deps),
      (e) => e.code === 64 && e.message.includes("11000000000001 Fixture Mug | 11000000000002 Fixture Cup"));
    assert.deepEqual(w.sent, []);
  }
  const w = fakeWorld();
  const req = { buyerAliId: w.convs[0].contactAliId, kind: "product", product: "Fixture" };
  w.send.searchProducts = async () => ({ products: [{}], count: 1 });
  await assert.rejects(run("send", req, w.r, w.deps), (e) => e.code === 64);
  w.send.searchProducts = async () => { throw new Error("fixture search failure"); };
  await assert.rejects(run("send", req, w.r, w.deps), /fixture search failure/);
  await assert.rejects(run("send", { ...req, product: "   " }, w.r, w.deps), (e) => e.code === 64);
  assert.deepEqual(w.sent, []);
});

test("public buyer CLI treats missing info as success without hiding engine errors", () => {
  for (const [status, engineExit, expected] of [["no_buyer_info", 0, 0], ["no_buyer_info", 1, 1], ["error", 1, 1], ["error", 2, 2]]) {
    const output = [];
    const code = onetalkMain(COMMANDS.buyer, { "buyer-ali-id": "4000000000001" }, {
      account: { name: "fixture" }, configPath: "/fixture/accounts.json", log: (line) => output.push(JSON.parse(line)),
      spawn: (cmd, args, opts) => {
        if (args.some((arg) => String(arg).endsWith("cookie_store.py"))) return { status: 0, stdout: JSON.stringify(COOKIES) };
        assert.equal(args.at(-1), "buyer");
        assert.deepEqual(JSON.parse(opts.input).request, { buyerAliId: "4000000000001" });
        return { status: engineExit, stdout: JSON.stringify({ status }) };
      },
    });
    assert.equal(code, expected);
    assert.deepEqual(output, [{ status }]);
  }
});

test("buyer and product search use the existing public API command wiring", () => {
  assert.equal(COMMANDS.buyer.write, false);
  assert.equal(COMMANDS.buyer.conversation, true);
  assert.deepEqual(apiRequest(COMMANDS.buyer, { "trade-id": "800000001" }), { tradeId: "800000001" });
  const opt = { "buyer-ali-id": "4000000000001", product: "Fixture Tea", "dry-run": true };
  assert.deepEqual(apiRequest(COMMANDS["send-product"], opt), { buyerAliId: "4000000000001", kind: "product", product: "Fixture Tea" });
  const log = [];
  const code = onetalkMain(COMMANDS["send-product"], opt, { account: { name: "fixture" }, log: (line) => log.push(line), spawn: () => assert.fail("dry-run must not spawn") });
  assert.equal(code, 0);
  assert.equal(JSON.parse(log[0]).product, "Fixture Tea");
});

test("tags: the account's tags; tag add/remove save the contact's whole manual set and keep the other tags", async () => {
  const w = fakeWorld();
  const list = await run("tags", {}, w.r, w.deps);
  assert.deepEqual(list.tags.map((t) => [t.name, t.manual]), [["VIP", true], ["Sample sent", true], ["Auto reception", false]]);
  const add = await run("tag-add", { buyerAliId: "4000000000001", tag: "VIP" }, w.r, w.deps);
  assert.equal(add.status, "ok");
  assert.deepEqual(add.readback, { tagged: true, otherTagsKept: true, countBefore: 1, countAfter: 2 });
  const save = w.calls.find(([api]) => api === "mtop.alibaba.icbu.tag.relation.save");
  assert.deepEqual(save[1], { tagObjType: 1, targetId: "4000000000001", tagTypeList: "[1,2]", tagRelReqList: JSON.stringify([{ tagId: "T2", tagType: 2 }, { tagId: "T1", tagType: 2 }]), userRole: "seller" });
  assert.equal(save[2].v, "1.1");
  const again = await run("tag-add", { buyerAliId: "4000000000001", tag: "VIP" }, w.r, w.deps);
  assert.equal(again.unchanged, true);
  const rm = await run("tag-remove", { buyerAliId: "4000000000001", tag: "VIP" }, w.r, w.deps);
  assert.deepEqual([rm.status, rm.readback.tagged, rm.readback.otherTagsKept], ["ok", false, true]);
  assert.deepEqual(w.contactTags["4000000000001"], [{ tagId: "T2", tagType: 2 }]);
  // system tags are not set by hand; unknown names are reported
  assert.equal((await run("tag-add", { buyerAliId: "4000000000001", tag: "Auto reception" }, w.r, w.deps)).status, "tag_not_found");
  assert.equal((await run("tag-add", { buyerAliId: "4000000000001", tag: "Nope" }, w.r, w.deps)).status, "tag_not_found");
  await assert.rejects(run("tag-add", { buyerAliId: "4000000000001" }, w.r, w.deps), (e) => e.code === 64);
});

test("templates: list over the template API; send finds the one matching body and sends it as text with read-back", async () => {
  const w = fakeWorld();
  const list = await run("templates", {}, w.r, w.deps);
  assert.deepEqual(list, { status: "ok", count: 3, templates: [{ id: "7001", name: "Fixture Brand is a sample line" }, { id: "7002", name: "Welcome note" }, { id: "7003", name: "Welcome back" }] });
  const tplCall = w.calls.find(([u]) => u === "MC/template/index.htm");
  assert.deepEqual(JSON.parse(tplCall[2].extra.json), { params: {}, cmd: "list" });
  // dry-run lookup: read-only, the body length but not the body
  const find = await run("template-find", { template: "fixture brand" }, w.r, w.deps);
  assert.deepEqual(find, { status: "ok", template: "Fixture Brand is a sample line", id: "7001", textLength: "Hello,\nFixture Brand price list & samples are ready.".length });
  assert.equal(w.sent.length, 0);
  const sent = await run("send", { buyerAliId: "4000000000001", kind: "template", template: "Fixture Brand" }, w.r, w.deps);
  assert.equal(sent.status, "ok");
  assert.equal(sent.template, "Fixture Brand is a sample line");
  assert.equal(sent.readback.textSeen, true);
  assert.equal(w.sent.length, 1);
  assert.equal(w.msgs["4000000000001"][0].content, "Hello,\nFixture Brand price list & samples are ready.");
  // no match -> template_not_found; two matches -> refused; nothing sent either way
  assert.equal((await run("send", { buyerAliId: "4000000000001", kind: "template", template: "Nope" }, w.r, w.deps)).status, "template_not_found");
  await assert.rejects(run("send", { buyerAliId: "4000000000001", kind: "template", template: "Welcome" }, w.r, w.deps), (e) => e.code === 64 && e.message.includes("Welcome note | Welcome back"));
  await assert.rejects(run("template-find", { template: " " }, w.r, w.deps), (e) => e.code === 64);
  assert.equal(w.sent.length, 1);
});

test("templateText turns the stored HTML into the send box text", () => {
  assert.equal(templateText("Hi&nbsp;there<br/>Line two↵<b>bold</b> &#39;q&#39; &lt;3"), "Hi there\nLine two\nbold 'q' <3");
});

test("tag save that the read-back does not show is sent_not_confirmed", async () => {
  const w = fakeWorld();
  const real = w.client.mtop;
  w.client.mtop = async (api, data, opts) => (api === "mtop.alibaba.icbu.tag.relation.save" ? { code: "0", data: true } : real(api, data, opts));
  const res = await run("tag-add", { buyerAliId: "4000000000001", tag: "VIP" }, w.r, w.deps);
  assert.equal(res.status, "sent_not_confirmed");
  assert.equal(res.readback.tagged, false);
});
