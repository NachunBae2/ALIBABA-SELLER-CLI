// All identities, cookies and messages in this fixture are synthetic. No network.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { main, parseArgs, LEGACY, SCOPES } from "../lib/cli.mjs";
import { messageKind } from "../lib/inquiry-api.mjs";

const CID = "10001-20002#11011@icbu";
const cookies = [{ name: "cookie2", value: "fixture-cookie-only", domain: ".alibaba.com", path: "/" }];
const json = (body, status = 200) => new Response(JSON.stringify(body), { status });
function fixture({ missing = false, level = "L1", count = 1, fail, historyCount = 1, historyError = false } = {}) {
  const http = [], frames = [], output = [], errors = [], loads = [];
  let unread = 2, sent;
  const conversation = (i = 0) => ({ type: 1, singleChatUserConversation: {
    redPoint: unread, user_extension: { custom: JSON.stringify({ toAccId: String(30003 + i), chatToken: "fixture-chat-token" }), target: JSON.stringify({ id: String(10001 + i) }) },
    singleChatConversation: { cid: i ? `other-${i}` : CID, pairFirst: `${10001 + i}@icbu`, pairSecond: "20002@icbu" },
    lastMessage: { message: { messageId: "old-message", cid: CID, createAt: 123 } },
  } });
  class WS {
    constructor() { this.events = {}; queueMicrotask(() => this.emit("open", {})); }
    addEventListener(k, fn) { (this.events[k] ||= []).push(fn); }
    emit(k, value) { for (const fn of this.events[k] || []) fn(value); }
    close() { this.emit("close", { code: 1000 }); }
    send(raw) {
      const m = JSON.parse(raw); frames.push(m);
      let body = {};
      if (m.lwp === "/r/Conversation/listNewestPagination") body = { userConvs: Array.from({ length: count }, (_, i) => conversation(i)), hasMore: 0, nextCursor: 1 };
      else if (m.lwp === "/r/Conversation/getByCids") body = [conversation()];
      else if (m.lwp === "/r/Conversation/clearRedPoint") unread = 0;
      else if (m.lwp === "/r/MessageManager/listUserMessages") {
        const n = Math.min(historyCount, m.body[3]);
        const type = sent?.messageType;
        const content = !sent || type === 1 ? { contentType: 1, text: { content: "fixture" } } : {
          contentType: 101, custom: { type: type === 60 ? 7 : 10010, data: Buffer.from(JSON.stringify(sent.content)).toString("base64") },
        };
        body = { nextCursor: m.body[2] - n, userMessageModels: Array.from({ length: n }, () => ({ message: { messageId: missing || !sent ? "old-message" : "sent-message", cid: CID, content } })) };
      }
      queueMicrotask(() => this.emit("message", { data: JSON.stringify({ code: historyError && m.lwp.includes("listUserMessages") ? 500 : 200, headers: { mid: m.headers.mid }, body }) }));
    }
  }
  const fetch = async (url, init = {}) => {
    url = String(url); http.push({ url, init });
    const failed = fail?.(url, init);
    if (failed) return failed;
    if (url.includes("login.token.get")) return json({ ret: ["SUCCESS::ok"], data: { object: { accessToken: "fixture-access" } } });
    if (url.includes("getuserinfobyparams")) {
      const ids = JSON.parse(JSON.parse(new URL(url).searchParams.get("data")).contactAliIds);
      return json({ ret: ["SUCCESS::ok"], data: { object: ids.map(aliId => ({ aliId, loginId: `fixture-${aliId}`, countryCode: "ZZ", ...(level ? { userNewLevel: level } : {}) })) } });
    }
    if (url.endsWith("getToken.htm")) return json({ token: "fixture-csrf" });
    if (url.endsWith("csrftoken.htm")) return json({ data: { token: "fixture-csrf" } });
    if (url.endsWith("subjectList.htm")) {
      const page = JSON.parse(new URLSearchParams(init.body).get("params")).pagination.nextPage;
      return json({ data: { totalCount: 2, list: page === 1 ? [[{ id: 41, feedbackId: 42, tradeId: 43, sender: { accountId: 30003 } }]] : [[{ id: 51, sender: { accountId: 90009 } }]] } });
    }
    if (url.endsWith("sendMessage.htm")) {
      sent = JSON.parse(new URLSearchParams(init.body).get("params"));
      return json({ code: 200, data: { messageID: "sent-message" } });
    }
    if (url.includes("asyQueryProductsList.do")) return json({ result: true, count: 1, products: [{ id: "55555" }] });
    if (url.includes("preparesendfilewithgroup")) return json({ ret: ["SUCCESS::ok"], data: { code: 200, result: { fileIsExist: true } } });
    if (url.includes("buildfilerelationwithgroup")) return json({ ret: ["SUCCESS::ok"], data: { code: 200, result: {
      fileCardUrl: "https://example.invalid/file?ctime=123&version=1&picWidth=10&picHeight=10", redirectFileUrl: "https://example.invalid/image.jpg",
      materialType: "jpg", id: "1", parentId: "2", md5: "fixture-hash", nodeName: "fixture.jpg", nodeSize: 7,
    } } });
    throw new Error(`Unexpected fixture endpoint: ${url}`);
  };
  const deps = { scope: "inquiry", fetch, WebSocket: WS, sleep: async () => {}, loadCookies: (account, opts) => { loads.push({ account, opts }); return cookies; }, log: x => output.push(x), err: x => errors.push(x) };
  const run = (args, extra = {}) => main([...args, "--account", "fixture", "--json"], { ...deps, ...extra });
  return { run, output, errors, http, frames, loads, result: () => JSON.parse(output.at(-1)), sent: () => sent };
}

test("parse direct command flags, require account/one selector, remove only list aliases", async () => {
  assert.deepEqual(parseArgs(["open", "--cid=x", "--cursor", "15", "--limit", "5"]).opt.cid, "x");
  assert.throws(() => parseArgs(["reply", "--cid", "--text", "x"]), /값/);
  assert.equal(LEGACY["inquiry list"], undefined);
  assert.deepEqual(SCOPES.inquiry.shorthand, ["stats", "by-product", "fields"]);
  await assert.rejects(main(["list"], { scope: "inquiry" }), /account/);
  const f = fixture();
  await assert.rejects(f.run(["open", "--cid", CID, "--buyer", "10001"]), /하나/);
  await assert.rejects(f.run(["open", "--cid", CID, "--limit", "0"]), /정수/);
  await assert.rejects(f.run(["reply", "--cid", CID]), /text/);
});

test("list conversations batches buyer info at 20 and filters unread/limit", async () => {
  const f = fixture({ count: 25 });
  assert.equal(await f.run(["list", "--unread", "--limit", "23"]), 0);
  assert.equal(f.result().count, 23);
  assert.equal(f.result().rows[0].cid, CID);
  assert.equal(f.result().rows[0].level, "L1");
  assert.equal(f.http.filter(x => x.url.includes("getuserinfobyparams")).length, 2);
  assert.equal(f.loads[0].opts.refresh, false);
});

test("All Inquiries fetches all pages and reports totalCount", async () => {
  const f = fixture();
  assert.equal(await f.run(["list", "--inquiries"]), 0);
  assert.equal(f.result().count, 2);
  assert.equal(f.result().totalCount, 2);
});

test("selectors map cid, aliId, loginId, inquiry/feedback ID and trade ID without guesswork", async () => {
  for (const [key, value] of [["cid", CID], ["buyer", "10001"], ["buyer", "fixture-10001"], ["inquiry-id", "41"], ["inquiry-id", "42"], ["trade-id", "43"]]) {
    const f = fixture();
    assert.equal(await f.run(["buyer", `--${key}`, value]), 0, key);
    assert.equal(f.result().cid, CID);
    assert.equal(f.result().level, "L1");
  }
  const f = fixture();
  assert.equal(await f.run(["open", "--inquiry-id", "404"]), 1);
  assert.match(f.errors[0], /매핑 0개/);
  assert.equal(f.output.length, 0);
});

test("open paginates exactly to limit with cursor, never clears unread", async () => {
  const f = fixture({ historyCount: 100 });
  assert.equal(await f.run(["open", "--cid", CID, "--limit", "101", "--cursor", "1000"]), 0);
  assert.equal(f.result().count, 101);
  assert.equal(f.result().nextCursor, 899);
  assert.deepEqual(f.frames.filter(x => x.lwp.includes("listUserMessages")).map(x => x.body), [[CID, false, 1000, 100, false], [CID, false, 900, 1, false]]);
  assert.ok(!f.frames.some(x => /clearRedPoint|MessageStatus\/read/.test(x.lwp)));
});

test("mark-read writes directly and prints before/after", async () => {
  const f = fixture();
  assert.equal(await f.run(["mark-read", "--cid", CID]), 0);
  assert.deepEqual(f.result(), { cid: CID, code: 200, before: 2, after: 0, ok: true });
});

test("all send commands have no send/env gate and verify exact message id AND type", async t => {
  const dir = mkdtempSync(path.join(tmpdir(), "inquiry-api-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, "fixture.jpg"); writeFileSync(file, "fixture");
  const commands = [["reply", "--text", "fixture"], ["reply", "--text-file", file], ["send-card"], ["send-product", "--product-id", "55555"], ["send-product", "--product", "fixture"], ["send-file", "--path", file], ["send-file", "--path", file, "--as-image"]];
  for (const args of commands) {
    const f = fixture();
    assert.equal(await f.run([...args, "--cid", CID], { env: {} }), 0, args[0]);
    assert.equal(f.result().readback, "ok");
    assert.equal(f.http.filter(x => x.url.endsWith("sendMessage.htm")).length, 1);
    if (args[0] === "send-file") {
      assert.equal(f.sent().messageType, args.includes("--as-image") ? 60 : 53);
      if (!args.includes("--as-image")) assert.equal(f.sent().content.params.downloadUrl, "");
    }
  }
});

test("dry-run sends/uploads/marks nothing, even jpg paths and resolved inquiry selectors", async t => {
  const dir = mkdtempSync(path.join(tmpdir(), "inquiry-api-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, "fixture.jpg"); writeFileSync(file, "fixture");
  for (const args of [["reply", "--text", "fixture"], ["send-card"], ["send-product", "--product", "fixture"], ["send-file", "--path", file], ["mark-read"]]) {
    const f = fixture();
    assert.equal(await f.run([...args, "--cid", CID, "--dry-run"]), 0);
    assert.equal(f.result().cid, CID);
    assert.equal(f.http.length, 0);
    assert.equal(f.frames.length, 0);
  }
  const f = fixture();
  assert.equal(await f.run(["reply", "--text", "fixture", "--inquiry-id", "41", "--dry-run"]), 0);
  assert.equal(f.result().dryRun, true);
  assert.ok(!f.http.some(x => /sendMessage|clouddisk/.test(x.url)));
  assert.ok(!f.frames.some(x => /clearRedPoint/.test(x.lwp)));
});

test("readback missing exits 1, never retries sending", async () => {
  const f = fixture({ missing: true });
  assert.equal(await f.run(["send-card", "--cid", CID]), 1);
  assert.equal(f.result().readback, "missing");
  assert.equal(f.http.filter(x => x.url.endsWith("sendMessage.htm")).length, 1);
});

test("readback transport failure preserves accepted message ID and missing status", async () => {
  const f = fixture({ historyError: true });
  assert.equal(await f.run(["send-card", "--cid", CID]), 1);
  assert.equal(f.result().readback, "missing");
  assert.equal(f.result().messageId, "sent-message");
  assert.match(f.errors[0], /code 500/);
  assert.equal(f.http.filter(x => x.url.endsWith("sendMessage.htm")).length, 1);
});

test("no cookies, expired mtop session, redirect and login HTML exit 3 without browser", async () => {
  const empty = fixture();
  assert.equal(await empty.run(["list"], { loadCookies: () => null }), 3);
  assert.equal(empty.http.length, 0);
  for (const fail of [() => json({ ret: ["FAIL_SYS_SESSION_EXPIRED::expired"] }), () => json({ code: 501 }), () => new Response(null, { status: 302 }), () => new Response("<html>login.alibaba.com</html>")]) {
    const f = fixture({ fail });
    assert.equal(await f.run(["list"]), 3);
    assert.match(f.errors.at(-1), /alibaba-seller login/);
    assert.equal(f.output.length, 0);
  }
});

test("unknown/high buyer grades refuse real sends, API errors do not become empty success", async () => {
  for (const level of [null, "L2", "L4"]) {
    const f = fixture({ level });
    assert.equal(await f.run(["send-card", "--cid", CID]), 1);
    assert.ok(!f.http.some(x => x.url.endsWith("sendMessage.htm")));
  }
  const f = fixture({ fail: () => json({}, 500) });
  assert.equal(await f.run(["list"]), 1);
  assert.equal(f.output.length, 0);
});

test("form sends and product search preserve login HTML/501 as exit 3", async () => {
  for (const endpoint of ["getToken.htm", "sendMessage.htm", "asyQueryProductsList.do", "subjectList.htm"]) {
    for (const response of [() => new Response("<html>login.alibaba.com</html>"), () => json({ code: 501 })]) {
      const f = fixture({ fail: url => url.includes(endpoint) ? response() : null });
      const args = endpoint === "subjectList.htm" ? ["list", "--inquiries"] : endpoint === "asyQueryProductsList.do" ? ["send-product", "--product", "fixture", "--cid", CID] : ["send-card", "--cid", CID];
      assert.equal(await f.run(args), 3, endpoint);
      assert.match(f.errors.at(-1), /alibaba-seller login/);
    }
  }
});

test("inquiry business error with empty rows is not success", async () => {
  const f = fixture({ fail: url => url.endsWith("subjectList.htm") ? json({ code: 500, data: { list: [], totalCount: 0 } }) : null });
  assert.equal(await f.run(["list", "--inquiries"]), 1);
  assert.equal(f.output.length, 0);
  assert.match(f.errors[0], /API code 500/);
});

test("file card decoding includes cardType and params keys", () => {
  const custom = { data: Buffer.from(JSON.stringify({ cardType: 12, params: { type: "12", name: "fixture.jpg" } })).toString("base64") };
  assert.deepEqual(messageKind({ contentType: 101, customType: 10010, custom }), { contentType: 101, customType: 10010, cardType: 12, paramsKeys: ["type", "name"] });
});
