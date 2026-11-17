// 테스트 전용 파일 — CLI 실행에 쓰이지 않음. 안의 ID·이름·연락처는 동작 확인용 예시 값이며 실제 셀러·고객 데이터가 아님.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { brief, convBrief, main, mask, reader, run, TYPES } from "../engine/onetalk_cli.mjs";
import { ONETALK, MESSAGE } from "../engine/onetalk_api.mjs";

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
  w.client = {
    async mtop(api, data, opts = {}) {
      w.calls.push([api, data, opts]);
      if (api === "mtop.alibaba.icbu.tag.feature.query") return { code: "0", data: { protocolList: w.tagDefs } };
      if (api === "mtop.alibaba.icbu.contact.tag.batch.query") {
        const id = JSON.parse(data.targetIds)[0];
        return { code: "0", data: (w.contactTags[id] || []).map((t) => ({ ...t, aliId: id, tagTargetId: id, status: 1 })) };
      }
      if (api === "mtop.alibaba.icbu.tag.relation.save") { w.contactTags[data.targetId] = JSON.parse(data.tagRelReqList); return { code: "0", data: true }; }
      throw new Error("unexpected " + api);
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
  assert.deepEqual(Object.keys(list.conversations[0]).sort(), ["buyerAliId", "chatType", "lastSendType", "lastTime", "lastType", "modifyTime", "unread"]);
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

test("tag save that the read-back does not show is sent_not_confirmed", async () => {
  const w = fakeWorld();
  const real = w.client.mtop;
  w.client.mtop = async (api, data, opts) => (api === "mtop.alibaba.icbu.tag.relation.save" ? { code: "0", data: true } : real(api, data, opts));
  const res = await run("tag-add", { buyerAliId: "4000000000001", tag: "VIP" }, w.r, w.deps);
  assert.equal(res.status, "sent_not_confirmed");
  assert.equal(res.readback.tagged, false);
});
