#!/usr/bin/env node
// alibaba-inquiry's API commands on top of the shared OneTalk requester (onetalk_api.mjs) and the
// send module (onetalk_send.mjs). No browser, no clicks.
//   node engine/onetalk_cli.mjs <op>   stdin {"cookies":[...], "request":{...}}   stdout one JSON line
//   ops: conversations | messages | send | mark-read | mark-unread | tags | tag-add | tag-remove
//   target: request.buyerAliId (OneTalk contact id) or request.inquiryId / request.tradeId (inquiry
//   list row; its sender's account id is the conversation's contactAccountId)
// Endpoints: onetalk.alibaba.com/message/pageListRecentConversation.htm (list),
//   /message/listRecentMessage.htm (history, does not mark read), /message/sendMessage.htm (via
//   onetalk_send.mjs), message.alibaba.com/message/ajax/feedback/subjectList.htm (inquiry list),
//   /message/ajax/feedback/markRead.htm (read | unread). Tags (mtop, appKey 24889839): tag.feature.query 1.1
//   (the account's tags), contact.tag.batch.query 1.0 (one contact's tags), tag.relation.save 1.1 (the contact's
//   whole manual tag set: star 1 + manual 2, as the web client saves it).
// Cookies, csrf and chat tokens stay in this process: the result carries types, times, ids of the
// conversation only, never names, contact details, tokens or the sent text.
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { basename, extname, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { createClient, ONETALK, MESSAGE } from "./onetalk_api.mjs";
import * as sender from "./onetalk_send.mjs";

export const TYPES = { text: 1, card: 57, product: 2000, file: 53, image: 60 };
const MIME = { pdf: "application/pdf", jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", gif: "image/gif", webp: "image/webp",
  xls: "application/vnd.ms-excel", xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", doc: "application/msword",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ppt: "application/vnd.ms-powerpoint",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation", txt: "text/plain", csv: "text/csv", zip: "application/zip", mp4: "video/mp4" };
const SENDER_IDS = ["accountId", "alitalkId", "vaccountId"];
const MANUAL_TAG_TYPES = [1, 2];  // seller star + seller manual: the only tags a seller sets by hand

export class CliError extends Error {
  constructor(message, code = 1) { super(message); this.code = code; }
}

const EMAIL = /[\w.+-]+@[\w-]+(\.[\w-]+)+/g;
const PHONE = /\+?\d[\d\s().-]{6,}\d/g;
export const mask = (t) => String(t || "").replace(EMAIL, "[email]").replace(PHONE, "[phone]");

// A message: type, direction, time, id; the text only on request, masked and cut to 300 characters.
export function brief(m, withText = false) {
  const out = { messageId: m.messageId, messageType: m.messageType, sendType: m.messageSendType, sendTime: m.sendTime, read: m.contactRead };
  if (withText && m.messageType === TYPES.text && typeof m.content === "string") out.text = mask(m.content).slice(0, 300);
  return out;
}

// A conversation row without names or tokens. buyerAliId is what --buyer-ali-id takes.
export function convBrief(c) {
  const lm = c.latestMessage || {};
  return { buyerAliId: String(c.contactAliId ?? ""), unread: c.unreadCount, modifyTime: c.modifyTime, chatType: c.chatType,
    lastType: lm.messageType, lastSendType: lm.messageSendType, lastTime: lm.sendTime };
}

export function targetOf(conv) {
  return { conversationId: conv.cid, senderAliId: String(conv.selfAliId), recipientAliId: String(conv.contactAliId), chatToken: conv.chatToken };
}

// Reads over the shared requester. form() answers the full JSON; message.alibaba.com strings come decoded.
export function reader(client, { now = Date.now } = {}) {
  const ok = (path, j) => {
    if (String(j?.code) !== "200") throw new CliError(`${path}: ${j?.code} ${j?.message || ""}`.trim());
    return j.data;
  };
  const ot = async (path, params) => ok(path, await client.form(ONETALK + path, params)) || {};
  const mc = async (path, params) => ok(path, await client.form(MESSAGE + path, params, { extra: { postId: String(now()) } }));
  const r = {
    async conversations(count = 50, cursor = null) {
      const d = await ot("/message/pageListRecentConversation.htm", { count, limitTimeStamp: cursor || now() });
      return { rows: d.dataList || [], cursor: d.nextTimeStamp ?? null, more: !!d.hasMore };
    },
    async find(ids, key = "contactAliId", maxPages = 40) {
      const want = new Set([].concat(ids).filter((v) => v != null && v !== "").map(String));
      let cursor = null;
      for (let i = 0; i < maxPages; i++) {
        const page = await r.conversations(50, cursor);
        const hit = page.rows.find((c) => want.has(String(c[key])));
        if (hit) return hit;
        if (!page.more || !page.cursor) return null;
        cursor = page.cursor;
      }
      return null;
    },
    async messages(conv, count = 20) {
      const d = await ot("/message/listRecentMessage.htm", { contactAliId: conv.contactAliId, pointTimeStamp: now(), forward: false, count });
      return d.messageList || [];
    },
    async inquiries(page = 1, size = 20) {
      const d = (await mc("/message/ajax/feedback/subjectList.htm", { listType: "all", pagination: { nextPage: page, pageSize: size },
        filter: { isShowAtm: false }, order: { order: "desc", orderBy: "latest_contact_time" }, search: {} })) || {};
      const items = (d.list || []).flatMap((g) => (Array.isArray(g) ? g : [g])).filter((x) => x && typeof x === "object");
      return { items, total: d.pagination?.totalCount ?? d.totalCount ?? null };
    },
    async findInquiry(inquiryId, tradeId, maxPages = 30) {
      for (let page = 1; page <= maxPages; page++) {
        const { items, total } = await r.inquiries(page);
        const hit = items.find((it) => {
          const ids = ["id", "tradeId", "secTradeId", "feedbackId"].map((k) => it[k]).filter((v) => v != null && v !== "").map(String);
          return (inquiryId && ids.includes(String(inquiryId))) || (tradeId && [String(it.tradeId), String(it.secTradeId)].includes(String(tradeId)));
        });
        if (hit) return hit;
        if (!items.length || (total && page * 20 >= Number(total))) return null;
      }
      return null;
    },
    markInquiry: (secTradeId, status) => mc("/message/ajax/feedback/markRead.htm", { readStatus: status, data: [{ secTradeId }], listMode: "REC_AGGREGATED" }),
    // tag definitions of the account: [{tagId, tagName, tagType, tagColor, status}]
    async tagDefs() {
      const d = await client.mtop("mtop.alibaba.icbu.tag.feature.query", { language: "en", userRole: "seller" }, { v: "1.1" });
      if (!["0", "200"].includes(String(d?.code))) throw new CliError("tag.feature.query: " + d?.code);
      return d.data?.protocolList || [];
    },
    // live manual tags on one contact: [{tagId, tagType}]
    async contactTags(aliId) {
      const d = await client.mtop("mtop.alibaba.icbu.contact.tag.batch.query", { targetIds: JSON.stringify([String(aliId)]), tagObjType: 1, tagTypeList: JSON.stringify(MANUAL_TAG_TYPES) }, { v: "1.0" });
      if (String(d?.code) !== "0") throw new CliError("contact.tag.batch.query: " + d?.code);
      return (d.data || []).filter((t) => t.status !== -1 && String(t.tagTargetId ?? t.aliId) === String(aliId)).map((t) => ({ tagId: t.tagId, tagType: t.tagType }));
    },
    async saveContactTags(aliId, tags) {
      const d = await client.mtop("mtop.alibaba.icbu.tag.relation.save", { tagObjType: 1, targetId: String(aliId), tagTypeList: JSON.stringify(MANUAL_TAG_TYPES),
        tagRelReqList: JSON.stringify(tags.map(({ tagId, tagType }) => ({ tagId, tagType }))), userRole: "seller" }, { v: "1.1" });
      if (String(d?.code) !== "0" || !d.data) throw new CliError("tag.relation.save: " + (d?.code ?? "no code"));
    },
  };
  return r;
}

export async function resolveTarget(r, req) {
  let inq = null;
  if (req.inquiryId || req.tradeId) {
    inq = await r.findInquiry(req.inquiryId, req.tradeId, Number(req.maxPages) || 30);
    if (!inq) return { conv: null, inq: null };
  }
  if (req.buyerAliId) return { conv: await r.find(req.buyerAliId), inq };
  if (inq) return { conv: await r.find(SENDER_IDS.map((k) => inq.sender?.[k]), "contactAccountId"), inq };
  throw new CliError("대화 지정: --inquiry-id N | --trade-id N | --buyer-ali-id N", 64);
}

// One upload, then the image bubble (images) or the file card (other files).
async function sendUpload(send, cookies, target, path) {
  const bytes = await readFile(path);
  const name = basename(path), ext = extname(name).slice(1).toLowerCase();
  const contentType = MIME[ext] || "application/octet-stream";
  const up = await send.uploadFile(cookies, { target, path, bytes, contentType });
  const upload = { nodeName: name, nodeSize: bytes.byteLength, materialType: ext, md5: createHash("md5").update(bytes).digest("hex"), ...up };
  const image = contentType.startsWith("image/");
  return { mtype: image ? TYPES.image : TYPES.file, res: await (image ? send.sendImage : send.sendFile)(cookies, { target, upload }) };
}

// Send one message, then read the conversation back: the new message of that type above the old top.
export async function sendAndConfirm(r, conv, req, { cookies, send = sender, sleep = (ms) => new Promise((ok) => setTimeout(ok, ms)), tries = 4 } = {}) {
  const before = await r.messages(conv, 5);
  const top0 = before[0]?.messageId;
  const target = targetOf(conv);
  let mtype, res;
  if (req.kind === "text") {
    if (!String(req.text || "").trim()) throw new CliError("보낼 글이 비었다", 64);
    [mtype, res] = [TYPES.text, await send.sendText(cookies, { target, text: req.text })];
  } else if (req.kind === "card") [mtype, res] = [TYPES.card, await send.sendNameCard(cookies, { target })];
  else if (req.kind === "product") {
    if (!req.productId) throw new CliError("--product-id 필요", 64);
    [mtype, res] = [TYPES.product, await send.sendProductCard(cookies, { target, productId: String(req.productId) })];
  } else if (req.kind === "file") ({ mtype, res } = await sendUpload(send, cookies, target, req.path));
  else throw new CliError("모르는 종류: " + req.kind, 64);
  let found = null;
  for (let i = 0; i < tries && !found; i++) {
    await sleep(1500);
    const after = await r.messages(conv, 5);
    const ids = after.map((m) => m.messageId);
    const fresh = top0 != null && ids.includes(top0) ? after.slice(0, ids.indexOf(top0)) : after;
    found = fresh.find((m) => m.messageType === mtype && m.messageSendType === "send") || null;
  }
  const rb = { messageType: mtype, seen: !!found };
  if (found && req.kind === "text") rb.textSeen = String(found.content || "").includes(String(req.text).trim().slice(0, 60));
  if (found && req.kind === "product") rb.productSeen = JSON.stringify(found).includes(String(req.productId));
  const ok = found && rb.textSeen !== false && rb.productSeen !== false;
  return { status: ok ? "ok" : "sent_not_confirmed", sentMessage: !!res?.messageId, readback: rb, message: found ? brief(found) : null };
}

// Mark the inquiry read or unread (the message center button), then read its list row back.
export async function markRead(r, inq, status) {
  if (!inq?.secTradeId) throw new CliError("읽음 처리는 문의 지정이 필요하다: --inquiry-id N | --trade-id N", 64);
  const before = !!inq.hasUnread;
  // already in that state: the endpoint refuses a no-op (400 "can't edit other's inquiries status"), so skip it
  if (before === (status === "unread")) return { status: "ok", unchanged: true, readback: { hasUnreadBefore: before, hasUnreadAfter: before } };
  await r.markInquiry(inq.secTradeId, status);
  const after = await r.findInquiry(inq.id);
  const unreadAfter = !!after?.hasUnread;
  return { status: after && unreadAfter === (status === "unread") ? "ok" : "sent_not_confirmed", readback: { hasUnreadBefore: before, hasUnreadAfter: unreadAfter } };
}

// Add or remove one manual tag: read the contact's manual set, save the set with/without it, read it back.
export async function changeTag(r, conv, name, add) {
  if (!String(name || "").trim()) throw new CliError("--tag '이름' 필요", 64);
  const def = (await r.tagDefs()).find((t) => t.tagName === name && MANUAL_TAG_TYPES.includes(t.tagType));
  if (!def) return { status: "tag_not_found", tag: name };
  const aliId = conv.contactAliId;
  const before = await r.contactTags(aliId);
  const has = before.some((t) => t.tagId === def.tagId);
  if (has === add) return { status: "ok", unchanged: true, tag: name, readback: { tagged: has } };
  await r.saveContactTags(aliId, add ? [...before, { tagId: def.tagId, tagType: def.tagType }] : before.filter((t) => t.tagId !== def.tagId));
  const after = await r.contactTags(aliId);
  const tagged = after.some((t) => t.tagId === def.tagId);
  // the other manual tags must still be there
  const kept = before.filter((t) => t.tagId !== def.tagId).every((t) => after.some((x) => x.tagId === t.tagId));
  return { status: tagged === add && kept ? "ok" : "sent_not_confirmed", tag: name, readback: { tagged, otherTagsKept: kept, countBefore: before.length, countAfter: after.length } };
}

export async function run(op, req, r, deps = {}) {
  if (op === "tags") {
    const defs = await r.tagDefs();
    return { status: "ok", count: defs.length, tags: defs.map((t) => ({ name: t.tagName, type: t.tagType, manual: MANUAL_TAG_TYPES.includes(t.tagType), color: t.tagColor })) };
  }
  if (op === "conversations") {
    const page = await r.conversations(Number(req.count) || 50, req.cursor || null);
    return { status: "ok", count: page.rows.length, hasMore: page.more, nextCursor: page.cursor, conversations: page.rows.map(convBrief) };
  }
  if (!["messages", "send", "mark-read", "mark-unread", "tag-add", "tag-remove"].includes(op)) throw new CliError("모르는 명령: " + op, 64);
  if (op.startsWith("mark-") && !req.inquiryId && !req.tradeId) throw new CliError("읽음 처리는 문의 지정이 필요하다: --inquiry-id N | --trade-id N", 64);
  const { conv, inq } = await resolveTarget(r, req);
  if ((req.inquiryId || req.tradeId) && !inq) return { status: "inquiry_not_found" };
  if (op.startsWith("mark-")) return markRead(r, inq, op === "mark-read" ? "read" : "unread");
  if (!conv) return { status: "conversation_not_found" };
  if (op === "tag-add" || op === "tag-remove") return changeTag(r, conv, req.tag, op === "tag-add");
  if (op === "messages") {
    const ms = await r.messages(conv, Number(req.count) || 20);
    return { status: "ok", count: ms.length, conversation: convBrief(conv), messages: ms.map((m) => brief(m, !!req.withText)) };
  }
  return sendAndConfirm(r, conv, req, deps);
}

// -> exit code. 2 = login missing or expired (a redirect), 64 = bad argument, 1 = anything else.
export async function main(argv, stdin, { output = (s) => process.stdout.write(s + "\n"), client: makeClient = createClient, ...deps } = {}) {
  let client = null;
  try {
    const [op] = argv;
    const { cookies, request = {} } = JSON.parse(stdin || "{}");
    if (!Array.isArray(cookies) || !cookies.length) throw new CliError("저장된 로그인 쿠키 없음: alibaba-data login", 2);
    client = makeClient(cookies);
    output(JSON.stringify(await run(op, request, reader(client), { cookies, ...deps })));
    return 0;
  } catch (e) {
    const login = e.status >= 300 && e.status < 400 || e.httpStatus >= 300 && e.httpStatus < 400;
    const code = login ? 2 : e.code === 64 || e.code === 2 ? e.code : 1;
    // one line, no request data; transport errors name the endpoint and status only
    output(JSON.stringify({ status: "error", error: login ? "로그인 필요(쿠키 만료): alibaba-data login" : String(e.message || e).replace(/\?.*$/s, "").slice(0, 200) }));
    return code;
  } finally {
    client?.close?.();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  process.exitCode = await main(process.argv.slice(2), Buffer.concat(chunks).toString("utf8"));
}
