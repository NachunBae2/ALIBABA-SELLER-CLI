import { readFileSync, statSync } from "node:fs";
import { loadCookies } from "./cookies.mjs";
import { EXIT_LOGIN } from "./runner.mjs";
import * as api from "../engine/onetalk_api.mjs";
import * as send from "../engine/onetalk_send.mjs";

export const API_COMMANDS = {
  list: "대화 목록 [--unread] [--limit N] | --inquiries (전체 문의)",
  open: "메시지 이력(읽음 처리 안 함) [--limit N] [--cursor N]",
  reply: "바로 답장 --text '…' | --text-file f",
  "send-card": "바로 명함 발송",
  "send-product": "바로 상품카드 발송 --product-id N | --product '검색어' (유일한 결과만)",
  "send-file": "바로 파일카드 발송 --path f [--as-image]",
  "mark-read": "읽음 처리 및 전후 안읽음 수",
  buyer: "바이어 등급·국가·API 정보",
};
const WRITES = new Set(["reply", "send-card", "send-product", "send-file", "mark-read"]);
const SELECTORS = ["cid", "buyer", "inquiry-id", "trade-id"];
const present = v => v !== undefined && v !== null && String(v) !== "";
const same = (a, b) => present(a) && present(b) && String(a) === String(b);

function positive(value, fallback, name) {
  if (value == null) return fallback;
  const n = Number(value);
  if (!Number.isSafeInteger(n) || n < 1) throw new Error(`--${name}: 양의 정수 필요`);
  return n;
}

export function validateCommand(cmd, opt) {
  if (!opt.account) throw new Error("--account <계정키> 가 필요하다");
  if (cmd !== "list" && SELECTORS.filter(k => present(opt[k])).length !== 1) throw new Error("대화 지정은 하나만: --cid | --buyer | --inquiry-id | --trade-id");
  if (opt.send || opt["allow-mark-read"]) throw new Error("API 명령은 바로 실행한다. --send/--allow-mark-read 대신 미리보기는 --dry-run");
  positive(opt.limit, 20, "limit");
  if (opt.cursor != null && (!Number.isSafeInteger(Number(opt.cursor)) || Number(opt.cursor) < 0)) throw new Error("--cursor: 0 이상의 안전한 정수 필요");
  if (cmd === "reply") {
    if (present(opt.text) === present(opt["text-file"])) throw new Error("reply: --text 또는 --text-file 하나 필요");
    if (!String(opt["text-file"] ? readFileSync(opt["text-file"], "utf8") : opt.text).trim()) throw new Error("reply: 빈 본문");
  }
  if (cmd === "send-product") {
    if (present(opt["product-id"]) === present(opt.product)) throw new Error("send-product: --product-id 또는 --product 하나 필요");
    if (opt["product-id"] && !/^\d+$/.test(opt["product-id"])) throw new Error("--product-id: 숫자 필요");
  }
  if (cmd === "send-file" && (!opt.path || !statSync(opt.path).isFile() || !statSync(opt.path).size)) throw new Error("send-file: 비어 있지 않은 --path 파일 필요");
}

export async function allConversations(client) {
  const rows = [], seen = new Set();
  let cursor;
  for (;;) {
    const page = await api.listConversations(client, { cursor, count: 100 });
    rows.push(...page.rows);
    if (!page.hasMore) return rows;
    if (!present(page.nextCursor) || seen.has(String(page.nextCursor)) || !page.rows.length) throw new Error("대화 목록 페이지 커서가 진행하지 않음");
    seen.add(String(page.nextCursor));
    cursor = page.nextCursor;
  }
}

export async function allInquiries(client) {
  const rows = [];
  for (let page = 1; ; page++) {
    const result = await api.listInquiries(client, { page, pageSize: 20 });
    if (result.total == null) throw new Error("문의 목록 totalCount 없음");
    rows.push(...result.rows);
    if (rows.length === result.total) return { rows, count: rows.length, totalCount: result.total };
    if (!result.rows.length || rows.length > result.total) throw new Error(`문의 목록 불완전: ${rows.length}/${result.total}`);
    if (page > 10000) throw new Error("문의 목록 페이지 한도 초과");
  }
}

async function buyers(client, rows) {
  const contacts = [...new Map(rows.filter(r => present(r.buyerAliId)).map(r => [String(r.buyerAliId), { aliId: r.buyerAliId, chatToken: r.chatToken }])).values()];
  return contacts.length ? (await api.getBuyerInfo(client, { contacts })).rows : [];
}

function unique(rows) {
  const choices = [...new Map(rows.map(r => [r.cid, r])).values()];
  if (choices.length !== 1 || !choices[0].cid) throw new Error(`대화 매핑 ${choices.length}개: 정확한 --cid 필요 (추측 매칭 안 함)`);
  return choices[0];
}

export async function resolveConversation(client, opt) {
  if (opt.cid) return unique((await api.getConversations(client, { cids: [opt.cid] })).rows.filter(r => same(r.cid, opt.cid)));
  const rows = await allConversations(client);
  if (opt.buyer) {
    const direct = rows.filter(r => same(r.buyerAliId, opt.buyer));
    if (direct.length) return unique(direct);
    const info = await buyers(client, rows);
    const ids = info.filter(r => same(r.loginId, opt.buyer)).map(r => r.aliId);
    return unique(rows.filter(r => ids.some(id => same(id, r.buyerAliId))));
  }
  const inquiries = (await allInquiries(client)).rows;
  const matching = inquiries.filter(r => opt["trade-id"] ? same(r.tradeId, opt["trade-id"]) || same(r.secTradeId, opt["trade-id"]) : same(r.id, opt["inquiry-id"]) || same(r.feedbackId, opt["inquiry-id"]));
  // Verified on subjectList + listNewestPagination: sender.accountId equals custom.toAccId.
  return unique(rows.filter(r => matching.some(i => same(i.sender?.accountId, r.toAccId))));
}

export function messageKind(row) {
  let data = row.custom?.data;
  if (typeof data === "string") {
    try { data = JSON.parse(Buffer.from(data, "base64").toString("utf8")); }
    catch { try { data = JSON.parse(data); } catch { data = null; } }
  }
  return { contentType: row.contentType, customType: row.customType, cardType: data?.cardType, paramsKeys: Object.keys(data?.params || {}) };
}

export function matchesReadback(row, result, cmd, asImage) {
  if (!same(row.messageId, result.messageId)) return false;
  const k = messageKind(row);
  if (cmd === "reply") return k.contentType === 1;
  if (cmd === "send-file" && asImage) return k.contentType === 101 && k.customType === 7;
  return k.contentType === 101 && k.customType === 10010 && Number(k.cardType) === ({ "send-card": 1, "send-product": 2000, "send-file": 12 }[cmd]);
}

function preview(cmd, cid, opt) {
  if (cmd === "mark-read") return { dryRun: true, cid, requests: [{ endpoint: "/r/Conversation/clearRedPoint", fields: ["cid", "messageId"] }] };
  const requests = [];
  if (cmd === "send-file") requests.push(
    { endpoint: send.ENDPOINTS.prepare, fields: ["appKey", "fileName", "scene", "isToken", "chatToken"] },
    { endpoint: "OSS policyDTO.host (prepare 응답)", fields: ["key", "policy", "OSSAccessKeyId", "success_action_status", "callback", "signature", "name", "file"] },
    { endpoint: send.ENDPOINTS.relation, fields: ["appKey", "scene", "node", "params", "chatToken"] });
  requests.push({ endpoint: send.ENDPOINTS.send, fields: ["params.messageType", "params.content", "params.chatType", "params.extParam", "params.conversationId", "params.receiverAliId", "params.chatToken", "_csrf"],
    messageType: ({ reply: 1, "send-card": 57, "send-product": 2000, "send-file": opt["as-image"] ? 60 : 53 })[cmd] });
  return { dryRun: true, cid, requests };
}

function printResult(result, opt, log) {
  if (opt.json || !result.rows) { log(JSON.stringify(result, null, 2)); return; }
  const rows = result.rows;
  const columns = [...new Set(rows.flatMap(Object.keys))];
  const cell = v => v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
  const escape = v => opt.csv ? '"' + cell(v).replaceAll('"', '""') + '"' : cell(v).replace(/[\t\r\n]/g, " ");
  log(columns.map(escape).join(opt.csv ? "," : "\t"));
  for (const row of rows) log(columns.map(k => escape(row[k])).join(opt.csv ? "," : "\t"));
  log(JSON.stringify(Object.fromEntries(Object.entries(result).filter(([k]) => k !== "rows"))));
}

export function loginError(error) {
  return [error.status, error.httpStatus, error.code].some(s => [301, 302, 303, 307, 308, 401].includes(Number(s))) ||
    /FAIL_SYS_(?:SESSION_EXPIRED|SESSION_INVALID|USER_VALIDATE|TOKEN_)|NO_ACCESS_TOKEN|LOGIN_REQUIRED|NOT_LOGIN|SESSION_INVALID|signed.out|login.*expired|login redirect/i.test(error.message);
}

export async function inquiryApiMain(cmd, opt, deps = {}) {
  const { log = console.log, err = console.error, configPath } = deps;
  validateCommand(cmd, opt);
  let client, cookies = [], target, accepted;
  try {
    cookies = await (deps.loadCookies || loadCookies)(opt.account, { configPath, refresh: false });
    if (!cookies?.length) { err("로그인 쿠키 없음: alibaba-seller login --account <계정키>"); return EXIT_LOGIN; }
    client = api.createClient(cookies, { fetch: deps.fetch, WebSocket: deps.WebSocket });
    let result;
    if (cmd === "list") {
      if (opt.inquiries) result = await allInquiries(client);
      else {
        let rows = await allConversations(client);
        if (opt.unread) rows = rows.filter(r => r.redPoint > 0);
        if (opt.limit) rows = rows.slice(0, positive(opt.limit, 20, "limit"));
        const info = await buyers(client, rows);
        rows = rows.map(r => { const buyer = info.find(b => same(b.aliId, r.buyerAliId)); return {
          cid: r.cid, lastMessageAt: r.lastMessageAt ?? null, unread: r.redPoint, buyerAliId: r.buyerAliId ?? null,
          loginId: buyer?.loginId ?? null, level: buyer?.level ?? null, country: buyer?.countryCode ?? null,
        }; });
        result = { rows, count: rows.length };
      }
    } else if (opt["dry-run"] && WRITES.has(cmd) && opt.cid) result = preview(cmd, opt.cid, opt);
    else {
      const conv = await resolveConversation(client, opt);
      const cid = conv.cid;
      if (opt["dry-run"] && WRITES.has(cmd)) result = preview(cmd, cid, opt);
      else if (cmd === "open") {
        const rows = [], limit = positive(opt.limit, 20, "limit"), seen = new Set();
        let cursor = opt.cursor == null ? undefined : Number(opt.cursor);
        for (;;) {
          const page = await api.getHistory(client, { cid, count: Math.min(limit - rows.length, 100), cursor });
          rows.push(...page.rows);
          result = { ...page, rows, count: rows.length };
          if (!page.hasMore || rows.length >= limit) break;
          if (seen.has(String(page.nextCursor))) throw new Error("이력 페이지 커서가 진행하지 않음");
          seen.add(String(page.nextCursor));
          cursor = page.nextCursor;
        }
      }
      else if (cmd === "mark-read") {
        const before = conv.redPoint;
        const marked = await api.markRead(client, { cid, messageId: conv.lastMessageId });
        const after = unique((await api.getConversations(client, { cids: [cid] })).rows).redPoint;
        result = { cid, code: 200, before, after, ok: marked.ok };
      } else {
        const info = (await buyers(client, [conv])).find(b => same(b.aliId, conv.buyerAliId));
        if (cmd === "buyer") {
          if (!info) throw new Error("바이어 정보 응답 없음");
          result = { cid, ...info };
        } else {
          const sender = conv.pair.map(p => String(p).replace(/@icbu$/, "")).filter(p => !same(p, conv.buyerAliId));
          if (sender.length !== 1 || !conv.pair.some(p => same(String(p).replace(/@icbu$/, ""), conv.buyerAliId))) throw new Error("1:1 대화 송수신자 매핑 실패");
          target = { conversationId: cid, recipientAliId: conv.buyerAliId, senderAliId: sender[0], chatToken: conv.chatToken, buyerLevel: info?.level };
          send.validateTarget(target);
          const options = { fetchImpl: deps.fetch };
          const jar = () => client.jar.toArray();
          if (cmd === "reply") result = await send.sendText(jar(), { target, text: opt["text-file"] ? readFileSync(opt["text-file"], "utf8") : opt.text }, options);
          if (cmd === "send-card") result = await send.sendNameCard(jar(), { target }, options);
          if (cmd === "send-product") {
            let productId = opt["product-id"];
            if (!productId) {
              const found = await send.searchProducts(jar(), { query: opt.product, size: 100 }, options);
              if (Number(found.count) !== 1 || found.products.length !== 1) throw new Error(`상품 검색 ${found.count}개: --product-id 로 하나를 지정`);
              productId = found.products[0].id;
            }
            result = await send.sendProductCard(jar(), { target, productId, country: info?.countryCode }, options);
          }
          if (cmd === "send-file") {
            const upload = await send.uploadFile(jar(), { target, path: opt.path }, options);
            result = await (opt["as-image"] ? send.sendImage : send.sendFile)(jar(), { target, upload }, options);
          }
          const sent = result;
          accepted = { ...sent, cid, readback: "missing" };
          let match;
          for (let attempt = 0; attempt < 3 && !match; attempt++) {
            if (attempt) await (deps.sleep || (ms => new Promise(resolve => setTimeout(resolve, ms))))(500);
            const history = await api.getHistory(client, { cid, count: 50 });
            match = history.rows.find(r => matchesReadback(r, sent, cmd, opt["as-image"]));
          }
          result = { ...sent, cid, readback: match ? "ok" : "missing", ...(match ? { kind: messageKind(match) } : {}) };
        }
      }
    }
    if (cmd === "open") { result.rows = result.rows.slice(0, positive(opt.limit, 20, "limit")); result.count = result.rows.length; }
    printResult(send.redact(result, client.jar.toArray(), [target?.chatToken]), opt, log);
    return result.readback === "missing" ? 1 : 0;
  } catch (error) {
    const expired = loginError(error);
    if (accepted) printResult(send.redact(accepted, client.jar.toArray(), [target?.chatToken]), opt, log);
    err(send.redact(error.message, [...(cookies || []), ...(client?.jar.toArray() || [])], [target?.chatToken]));
    if (expired) err("로그인 만료: alibaba-seller login --account <계정키>");
    return expired ? EXIT_LOGIN : 1;
  } finally { client?.close(); }
}
