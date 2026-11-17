#!/usr/bin/env node
// OneTalk (onetalk.alibaba.com message center) without a browser tab: login cookies only.
// Three transports, all seen in the web client's own traffic:
//   mtop  GET https://acs.h.alibaba.com/h5/<api>/<v>/?...&sign=md5(token&t&appKey&data)  (token = _m_h5_tk before "_")
//   form  POST onetalk.alibaba.com|message.alibaba.com *.htm, form body params=<json> + csrf token
//   lwp   DingTalk IMPaaS JSON frames over wss://wss-imakamai.alibaba.com/ (token from mtop im.login.token.get)
// Errors are thrown (MtopError / HttpError / LwpError), never turned into empty results.
//   node engine/onetalk_api.mjs <op> [--json '{...}']   stdin {"cookies":[...]}   stdout JSON
import { createHash, randomBytes } from "node:crypto";
import { pathToFileURL } from "node:url";

export const MTOP_BASE = "https://acs.h.alibaba.com/h5";
export const APP_KEY = "24889839";            // mtop appKey of the OneTalk web client
export const IM_TOKEN_APP_KEY = "12574478";   // mtop appKey the client uses for im.login.token.get
export const IM_APP_KEY = "7594e10385fca14f1521481c62f5cfd0"; // IMPaaS app-key (public, in the web bundle) gitleaks:allow
export const LWP_URL = "wss://wss-imakamai.alibaba.com/";
export const ONETALK = "https://onetalk.alibaba.com";
export const MESSAGE = "https://message.alibaba.com";
export const PAGE = ONETALK + "/message/weblitePWA.htm";
export const BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36";
const LWP_UA = BROWSER_UA + " DingTalk(2.1.0-beta.22) OS(Mac OS/10.15.7) Browser(Chrome/140.0.0.0) DingWeb/2.1.0-beta.22 IMPaaS";
const TOKEN_RETRY = /^FAIL_SYS_TOKEN_(EMPTY|EXOIRED|EXPIRED|ILLEGAL)/;

export class MtopError extends Error {
  constructor(api, ret) { super(`${api}: ${[].concat(ret || []).join(" | ") || "no ret"}`); this.api = api; this.ret = ret; }
}
export class HttpError extends Error {
  constructor(url, status, detail = "") { super(`${url}: HTTP ${status}${detail ? " " + detail : ""}`); this.status = status; }
}
export class LwpError extends Error {
  constructor(route, code, body) { super(`${route}: code ${code} ${JSON.stringify(body ?? null).slice(0, 200)}`); this.route = route; this.code = code; }
}

export const md5 = (s) => createHash("md5").update(String(s), "utf8").digest("hex");
export const mtopSign = (token, t, appKey, data) => md5(`${token}&${t}&${appKey}&${data}`);

// ---- cookies (RAM only) ----------------------------------------------------------------------
export class CookieJar {
  constructor(cookies = []) { this.list = (cookies || []).filter((c) => c && c.name && c.value != null).map((c) => ({ ...c, domain: String(c.domain || ""), path: c.path || "/" })); }
  get(name) { const c = this.list.find((x) => x.name === name && x.value !== ""); return c ? String(c.value) : null; }
  header(url, now = Date.now() / 1000) {
    const u = new URL(url);
    return this.list.filter((c) => {
      const d = c.domain.replace(/^\./, "").toLowerCase();
      const exp = Number(c.expires ?? c.expirationDate ?? -1);
      return (u.hostname === d || u.hostname.endsWith("." + d)) && u.pathname.startsWith(c.path) && (!(exp > 0) || exp > now || exp > 1e11);
    }).map((c) => `${c.name}=${c.value}`).join("; ");
  }
  // Set-Cookie lines from a response; a later value for the same name/domain replaces the old one.
  update(lines, url) {
    const host = new URL(url).hostname;
    for (const line of lines || []) {
      const [pair, ...attrs] = String(line).split(";");
      const i = pair.indexOf("=");
      if (i <= 0) continue;
      const c = { name: pair.slice(0, i).trim(), value: pair.slice(i + 1).trim(), domain: host, path: "/" };
      for (const a of attrs) {
        const [k, v = ""] = a.split("=").map((s) => s.trim());
        if (/^domain$/i.test(k)) c.domain = v.startsWith(".") ? v : "." + v;
        else if (/^path$/i.test(k)) c.path = v || "/";
        else if (/^max-age$/i.test(k)) c.expires = Date.now() / 1000 + Number(v);
        else if (/^expires$/i.test(k) && c.expires == null) c.expires = Date.parse(v) / 1000;
      }
      const norm = (d) => d.replace(/^\./, "");
      this.list = this.list.filter((x) => !(x.name === c.name && norm(x.domain) === norm(c.domain)));
      this.list.push(c);
    }
  }
  toArray() { return this.list.map((c) => ({ ...c })); }
}
const setCookies = (res) => (typeof res.headers.getSetCookie === "function" ? res.headers.getSetCookie() : [res.headers.get("set-cookie")].filter(Boolean));

// ---- client ----------------------------------------------------------------------------------
export function createClient(cookies, { fetch = globalThis.fetch, WebSocket = globalThis.WebSocket, now = () => Date.now(), deviceId } = {}) {
  const jar = cookies instanceof CookieJar ? cookies : new CookieJar(cookies);
  const did = deviceId || randomBytes(16).toString("hex") + "-" + now();
  const csrf = {};
  let lwp = null;

  async function request(url, init = {}) {
    const res = await fetch(url, { redirect: "manual", ...init, headers: { "user-agent": BROWSER_UA, "accept-language": "en-US,en;q=0.9", referer: PAGE, cookie: jar.header(url), ...(init.headers || {}) } });
    jar.update(setCookies(res), url);
    if (res.status >= 300 && res.status < 400) throw new HttpError(url.split("?")[0], res.status, "redirect (login expired?)");
    if (!res.ok) throw new HttpError(url.split("?")[0], res.status);
    return res.text();
  }
  const parseJson = (url, text) => {
    if (/^\s*</.test(text) && /(?:passport|login\.alibaba|sign.?in|log.?in)/i.test(text)) throw new HttpError(url.split("?")[0], 401, "login required");
    const m = /^\s*[\w$.]+\(([\s\S]*)\)\s*;?\s*$/.exec(text);
    let result;
    try { result = JSON.parse(m ? m[1] : text); } catch (e) { throw new HttpError(url.split("?")[0], 200, "not JSON: " + String(text).slice(0, 80).replace(/\s+/g, " ")); }
    if (Number(result?.code) === 501) throw new HttpError(url.split("?")[0], 401, "login required (OneTalk code 501)");
    return result;
  };

  // mtop h5 call. One retry after a token error: the error response sets a fresh _m_h5_tk.
  async function mtop(api, data = {}, { v = "1.0", appKey = APP_KEY } = {}) {
    const body = typeof data === "string" ? data : JSON.stringify(data);
    for (let attempt = 0; ; attempt++) {
      const t = String(now());
      const token = (jar.get("_m_h5_tk") || "").split("_")[0];
      const q = new URLSearchParams({ jsv: "2.7.2", appKey, t, sign: mtopSign(token, t, appKey, body), api, v, dataType: "json", ecode: "1", type: "originaljson", data: body });
      const url = `${MTOP_BASE}/${api.toLowerCase()}/${v}/?${q}`;
      const j = parseJson(url, await request(url, { headers: { accept: "application/json", "content-type": "application/x-www-form-urlencoded", origin: ONETALK } }));
      const ret = [].concat(j.ret || []);
      if (ret.some((r) => /^SUCCESS/.test(r))) return j.data;
      if (attempt === 0 && ret.some((r) => TOKEN_RETRY.test(r))) continue;
      throw new MtopError(api, ret);
    }
  }

  // Form POST to onetalk.alibaba.com (field _csrf) or message.alibaba.com (field _csrf_token_).
  async function csrfToken(host) {
    if (csrf[host]) return csrf[host];
    if (host === ONETALK) {
      const j = parseJson(ONETALK, await request(ONETALK + "/csrf/getToken.htm", { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded; charset=UTF-8", origin: ONETALK } }));
      csrf[host] = j.token || j.data?.token;
    } else {
      const j = parseJson(MESSAGE, await request(MESSAGE + "/message/ajax/csrftoken.htm", { headers: { "x-requested-with": "XMLHttpRequest" } }));
      csrf[host] = j.data?.token || j.token;
    }
    if (!csrf[host]) throw new HttpError(host, 200, "no csrf token in response");
    return csrf[host];
  }
  async function form(url, params = {}, { extra = {} } = {}) {
    const host = new URL(url).origin;
    const tok = await csrfToken(host);
    const f = new URLSearchParams({ params: JSON.stringify(params), ...extra });
    f.set(host === MESSAGE ? "_csrf_token_" : "_csrf", tok);
    const text = await request(url, { method: "POST", body: f.toString(), headers: { "content-type": "application/x-www-form-urlencoded; charset=UTF-8", "x-requested-with": "XMLHttpRequest", origin: host, accept: "application/json, text/plain, */*" } });
    if (host !== MESSAGE) return parseJson(url, text);
    // message.alibaba.com: either the whole body or the string values are HTML-entity encoded
    let decoded;
    try { decoded = decodeDeep(JSON.parse(text)); } catch { return parseJson(url, decodeEntities(text)); }
    if (Number(decoded?.code) === 501) throw new HttpError(url.split("?")[0], 401, "login required (OneTalk code 501)");
    return decoded;
  }

  // LWP: one websocket per client, opened on first use.
  async function lwpCall(route, body, { timeoutMs = 20000 } = {}) {
    if (!lwp) lwp = await connectLwp({ mtop, WebSocket, did, now });
    return lwp.call(route, body, timeoutMs);
  }
  return {
    jar, mtop, form, lwp: lwpCall, request,
    get deviceId() { return did; },
    close() { if (lwp) { lwp.close(); lwp = null; } },
  };
}

export function decodeEntities(s) {
  return String(s).replace(/&(quot|amp|lt|gt|#39|#x27|#34);/g, (m, k) => ({ quot: '"', amp: "&", lt: "<", gt: ">", "#39": "'", "#x27": "'", "#34": '"' })[k]);
}

export function decodeDeep(v) {
  if (typeof v === "string") return decodeEntities(v);
  if (Array.isArray(v)) return v.map(decodeDeep);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, decodeDeep(x)]));
  return v;
}

export const lwpMid = (rand = Math.random, now = Date.now) => `${Math.floor(1000 * rand())}${now()} 0`;

export async function connectLwp({ mtop, WebSocket, did, now = Date.now, url = LWP_URL }) {
  if (typeof WebSocket !== "function") throw new Error("WebSocket is not available (Node 22+ needed)");
  const tok = await mtop("mtop.alibaba.icbu.im.login.token.get", { deviceId: did, appKey: IM_APP_KEY }, { appKey: IM_TOKEN_APP_KEY });
  const accessToken = tok?.object?.accessToken;
  if (!accessToken) throw new MtopError("mtop.alibaba.icbu.im.login.token.get", ["NO_ACCESS_TOKEN"]);
  const ws = new WebSocket(url, { headers: { origin: ONETALK, "user-agent": BROWSER_UA } });
  const pending = new Map();
  let closed = null;
  const send = (o) => ws.send(JSON.stringify(o));
  ws.addEventListener("message", (e) => {
    let m; try { m = JSON.parse(typeof e.data === "string" ? e.data : Buffer.from(e.data).toString("utf8")); } catch { return; }
    if (m.lwp) { send({ code: 200, headers: { ...(m.headers || {}) } }); return; } // server push: ack it
    const p = pending.get(m.headers?.mid);
    if (p) { pending.delete(m.headers.mid); clearTimeout(p.timer); p.resolve(m); }
  });
  ws.addEventListener("close", (e) => { closed = new Error(`lwp socket closed (${e.code})`); for (const p of pending.values()) { clearTimeout(p.timer); p.reject(closed); } pending.clear(); });
  await new Promise((resolve, reject) => { ws.addEventListener("open", resolve, { once: true }); ws.addEventListener("error", () => reject(new Error("lwp websocket failed to open")), { once: true }); });
  const raw = (route, headers, body, timeoutMs) => new Promise((resolve, reject) => {
    if (closed) return reject(closed);
    const mid = lwpMid(Math.random, now);
    const timer = setTimeout(() => { pending.delete(mid); reject(new LwpError(route, "timeout", null)); }, timeoutMs);
    pending.set(mid, { resolve, reject, timer });
    send({ lwp: route, headers: { ...headers, mid }, ...(body === undefined ? {} : { body }) });
  });
  const reg = await raw("/reg", { "cache-header": "app-key token ua wv", "app-key": IM_APP_KEY, token: accessToken, ua: LWP_UA, dt: "j", wv: "im:3,au:3,sy:6", sync: "0,0;0;0;", did }, undefined, 20000);
  if (reg.code !== 200) { ws.close(); throw new LwpError("/reg", reg.code, reg.body); }
  const hb = setInterval(() => raw("/!", {}, undefined, 15000).catch(() => {}), 25000);
  hb.unref?.();
  return {
    uid: reg.headers?.["reg-uid"],
    async call(route, body, timeoutMs = 20000) {
      const r = await raw(route, {}, body, timeoutMs);
      if (r.code !== 200) throw new LwpError(route, r.code, r.body);
      return r.body;
    },
    close() { clearInterval(hb); try { ws.close(); } catch { /* already closed */ } },
  };
}

// One-shot form of the common requester (for sibling modules):
//   call(cookies, {kind:"mtop", api, data, v?, appKey?}) | {kind:"form", url, params, extra?} | {kind:"lwp", route, body}
// cookies = array of {name,value,domain,path,...} or a client from createClient (kept open then).
export async function call(cookies, spec, deps) {
  return withClient(cookies, deps, (c) => {
    if (spec.kind === "mtop") return c.mtop(spec.api, spec.data, { v: spec.v, appKey: spec.appKey });
    if (spec.kind === "form") return c.form(spec.url, spec.params, { extra: spec.extra });
    if (spec.kind === "lwp") return c.lwp(spec.route, spec.body, { timeoutMs: spec.timeoutMs });
    throw new Error(`unknown spec.kind ${spec.kind}`);
  });
}
async function withClient(cookiesOrClient, deps, fn) {
  if (cookiesOrClient && typeof cookiesOrClient.mtop === "function") return fn(cookiesOrClient);
  const c = createClient(cookiesOrClient, deps);
  try { return await fn(c); } finally { c.close(); }
}

// ---- read operations -------------------------------------------------------------------------
const CURSOR_NEWEST = Number.MAX_SAFE_INTEGER;

// One flat row per conversation, from a Conversation/listNewestPagination entry.
export function conversationRow(uc) {
  const u = uc.singleChatUserConversation || uc.groupChatUserConversation || uc;
  const conv = u.singleChatConversation || u.groupChatConversation || uc.singleChatConversation || uc.groupChatConversation || {};
  const last = u.lastMessage?.message || {};
  const parse = (s) => { try { return JSON.parse(s || "{}"); } catch { return {}; } };
  const custom = parse(u.user_extension?.custom);
  const target = parse(u.user_extension?.target);
  return {
    cid: conv.cid || last.cid, type: uc.type, bizType: conv.bizType, redPoint: u.redPoint ?? 0, visible: u.visible,
    modifyTime: u.modifyTime, lastMessageAt: last.createAt, lastMessageId: last.messageId, lastContentType: last.content?.contentType,
    lastSenderUid: last.sender?.uid, country: conv.extension?.country, chatToken: custom.chatToken || conv.extension?.chatToken,
    buyerAliId: target.id, // the other party (contactAliIds for getBuyerInfo)
    toAliIdE: custom.toAliIdE, toAccIdE: custom.toAccIdE, toAccId: custom.toAccId, fromAccId: custom.fromAccId,
    pair: [conv.pairFirst, conv.pairSecond].filter(Boolean),
  };
}

// ① conversation list (newest first). cursor = nextCursor of the previous page.
export async function listConversations(cookies, { cursor = CURSOR_NEWEST, count = 100, pages = 1 } = {}, deps) {
  return withClient(cookies, deps, async (c) => {
    const rows = [];
    let next = cursor, hasMore = 0;
    for (let i = 0; i < pages; i++) {
      const body = await c.lwp("/r/Conversation/listNewestPagination", [next, count]);
      if (!body || !Array.isArray(body.userConvs)) throw new LwpError("/r/Conversation/listNewestPagination", 200, "no userConvs");
      rows.push(...body.userConvs.map(conversationRow));
      next = body.nextCursor; hasMore = body.hasMore;
      if (!hasMore) break;
    }
    return { rows, count: rows.length, nextCursor: next, hasMore: !!hasMore };
  });
}

// ② inquiry list (message.alibaba.com, "All Inquiries"). Page size 20 like the web page.
export function inquiryParams({ page = 1, pageSize = 20, listType = "all" } = {}) {
  return { listType, pagination: { nextPage: page, pageSize }, filter: { isShowAtm: false }, order: { order: "desc", orderBy: "latest_contact_time" }, search: {} };
}
export async function listInquiries(cookies, { page = 1, pageSize = 20, pages = 1, listType = "all" } = {}, deps) {
  return withClient(cookies, deps, async (c) => {
    const rows = [];
    let total = null;
    for (let p = page; p < page + pages; p++) {
      const j = await c.form(MESSAGE + "/message/ajax/feedback/subjectList.htm", inquiryParams({ page: p, pageSize, listType }), { extra: { postId: String(Date.now()) } });
      if (j.code != null && Number(j.code) !== 200) throw new HttpError("subjectList.htm", 200, `API code ${j.code}`);
      const data = j.data ?? j;
      const list = findArray(data, ["list", "subjectList", "dataList", "result"]);
      if (!list) throw new HttpError("subjectList.htm", 200, `unexpected shape: ${Object.keys(data || {}).join(",").slice(0, 120)}`);
      total = findNumber(data, ["totalCount", "total", "totalNum"]) ?? total;
      rows.push(...list.flat()); // the page answers one array per subject group
      if (list.length < pageSize) break;
    }
    return { rows, count: rows.length, total };
  });
}

// ③ message history of one conversation, newest first. cursor = nextCursor of the previous page.
export async function getHistory(cookies, { cid, cursor = CURSOR_NEWEST, count = 20, pages = 1 } = {}, deps) {
  if (!cid) throw new Error("getHistory: cid required");
  return withClient(cookies, deps, async (c) => {
    const rows = [];
    let next = cursor, hasMore = false;
    for (let i = 0; i < pages; i++) {
      const body = await c.lwp("/r/MessageManager/listUserMessages", [cid, false, next, count, false]);
      const list = body?.userMessageModels;
      if (!Array.isArray(list)) throw new LwpError("/r/MessageManager/listUserMessages", 200, "no userMessageModels");
      rows.push(...list.map(messageRow));
      hasMore = list.length >= count && body.nextCursor != null && body.nextCursor !== next;
      next = body.nextCursor;
      if (!hasMore) break;
    }
    return { rows, count: rows.length, nextCursor: next, hasMore };
  });
}
export function messageRow(m) {
  const msg = m.message || {};
  const ct = msg.content?.contentType;
  return {
    messageId: msg.messageId, cid: msg.cid, createAt: msg.createAt, contentType: ct, customType: msg.content?.custom?.type,
    senderUid: msg.sender?.uid, readStatus: m.readStatus, unreadCount: msg.unreadCount,
    text: ct === 1 ? msg.content?.text?.content : undefined, custom: ct === 101 ? msg.content?.custom : undefined,
  };
}

// ④ mark read: clear the red point up to messageId (what the web client sends when a chat is viewed).
export async function markRead(cookies, { cid, messageId } = {}, deps) {
  if (!cid || !messageId) throw new Error("markRead: cid and messageId required");
  return withClient(cookies, deps, async (c) => {
    const body = await c.lwp("/r/Conversation/clearRedPoint", [[{ cid, messageId }]]);
    return { ok: true, body };
  });
}
export async function getConversations(cookies, { cids } = {}, deps) {
  return withClient(cookies, deps, async (c) => {
    const body = await c.lwp("/r/Conversation/getByCids", [[].concat(cids)]);
    if (!Array.isArray(body)) throw new LwpError("/r/Conversation/getByCids", 200, "not an array");
    return { rows: body.map(conversationRow) };
  });
}

const BUYER_BATCH = 20;
export function buyerInfoData(contacts) {
  return {
    contactAliIds: JSON.stringify(contacts.map((x) => Number(x.aliId))),
    queryType: "get_buyer_member_info",
    queryParams: JSON.stringify(["buyerNewFlag", "countryCode", "replyCountdown"]),
    chatTokens: contacts.map((x) => x.chatToken || "").join(","),
  };
}
// ⑨ buyer info (member level L0..L4 = userNewLevel; absent field -> level null) for rows from listConversations.
export async function getBuyerInfo(cookies, { contacts } = {}, deps) {
  const list = [].concat(contacts || []).filter(Boolean);
  if (!list.length) throw new Error("getBuyerInfo: contacts [{aliId, chatToken}] required");
  return withClient(cookies, deps, async (c) => {
    const rows = [];
    for (let i = 0; i < list.length; i += BUYER_BATCH) { // the API answers at most 20 contacts per call
      const part = list.slice(i, i + BUYER_BATCH);
      const data = await c.mtop("mtop.alibaba.icbu.im.getUserInfoByParams", buyerInfoData(part), { v: "1.1" });
      if (!Array.isArray(data?.object)) throw new MtopError("mtop.alibaba.icbu.im.getUserInfoByParams", ["NO_OBJECT_ARRAY"]);
      rows.push(...data.object);
    }
    return { rows: rows.map((r) => ({ ...r, level: r.userNewLevel ?? null })), count: rows.length };
  });
}

function findArray(o, keys, depth = 0) {
  if (!o || typeof o !== "object" || depth > 4) return null;
  for (const k of keys) if (Array.isArray(o[k])) return o[k];
  for (const v of Object.values(o)) { const a = findArray(v, keys, depth + 1); if (a) return a; }
  return null;
}
function findNumber(o, keys, depth = 0) {
  if (!o || typeof o !== "object" || depth > 4) return null;
  for (const k of keys) if (o[k] != null && Number.isFinite(Number(o[k]))) return Number(o[k]);
  for (const v of Object.values(o)) { const n = findNumber(v, keys, depth + 1); if (n != null) return n; }
  return null;
}

// ---- CLI -------------------------------------------------------------------------------------
export const OPS = { listConversations, listInquiries, getHistory, markRead, getConversations, getBuyerInfo };

export async function main(argv = process.argv.slice(2), { stdin = process.stdin, out = (s) => process.stdout.write(s + "\n"), deps } = {}) {
  const [op, ...rest] = argv;
  if (!OPS[op]) throw new Error(`usage: onetalk_api.mjs <${Object.keys(OPS).join("|")}> [--json '{...}']  (stdin {"cookies":[...]})`);
  const i = rest.indexOf("--json");
  const args = i >= 0 ? JSON.parse(rest[i + 1]) : {};
  let input = "";
  for await (const chunk of stdin) input += chunk;
  const { cookies } = JSON.parse(input || "{}");
  if (!Array.isArray(cookies) || !cookies.length) throw new Error("stdin needs {\"cookies\":[...]}");
  out(JSON.stringify(await OPS[op](cookies, args, deps)));
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) {
  main().catch((e) => { process.stderr.write(`onetalk_api: ${e.message}\n`); process.exit(1); });
}
