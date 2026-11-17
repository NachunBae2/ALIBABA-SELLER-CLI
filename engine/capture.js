// Aside repl script: capture every XHR/fetch request + response one seller-center page makes.
// seller_central_raw.py replaces __PARAMS__ with
//   {key, url, outFile, loginId, start, end, maxPages, rangeReplay, rangeTables, extra, inquiryList, lookup, budgetMs}.
// lookup {inquiryId, tradeId} (OneTalk conversation lookup): the target item's sender ids, conversation
// ids, times and country are printed once on stdout as a "LOOKUP {json}" line, after the file is written.
// They never enter out (the temp file); the caller keeps them in RAM only.
// How: open the page, re-fetch its HTML and document.write it back with a fetch/XHR hook
// placed first in <head>, so every request the page makes on load is recorded with its body
// and response. Then (read-only) replays:
//   - pagination of list/query endpoints (same request, next page numbers)
//   - rangeReplay: /api/data/dynamic/component/data with the --start/--end date range
// Nothing that looks like a write endpoint is replayed. Token/csrf query params and form
// fields are stripped before the file is written; csrf endpoints are not saved.
const P = __PARAMS__;
const T0 = Date.now();
let BASE = P.url;
const left = () => P.budgetMs - (Date.now() - T0);
const out = { key: P.key, url: P.url, status: "ok", finalUrl: null, records: [], notes: [] };
let LOOKUP = null;  // OneTalk lookup keys: RAM and stdout only, never in out
const save = async () => { await fs.writeFile(P.outFile, JSON.stringify(out)); };

const HOOK = `(() => { if (window.__rawHook) return; window.__rawHook = 1; window.__raw = [];
  const keep = (r) => { try { if (r.text && r.text.length > 8e6) { r.text = r.text.slice(0, 8e6); r.truncated = true; } window.__raw.push(r); } catch (e) {} };
  const hdr = (h) => { try { if (!h) return {}; if (h instanceof Headers) return Object.fromEntries(h.entries()); if (Array.isArray(h)) return Object.fromEntries(h); return Object.assign({}, h); } catch (e) { return {}; } };
  const of = window.fetch;
  window.fetch = async function (u, o) {
    const url = typeof u === 'string' ? u : (u && u.url) || String(u);
    const method = ((o && o.method) || (u && u.method) || 'GET').toUpperCase();
    const body = o && typeof o.body === 'string' ? o.body : (o && o.body instanceof URLSearchParams ? o.body.toString() : null);
    const headers = hdr(o && o.headers);
    const res = await of.apply(this, arguments);
    try { res.clone().text().then((t) => keep({ via: 'fetch', method, url, body, headers, status: res.status, ct: res.headers.get('content-type'), text: t })).catch(() => {}); } catch (e) {}
    return res;
  };
  const XO = XMLHttpRequest.prototype.open, XS = XMLHttpRequest.prototype.send, XH = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function (m, u) { this.__m = String(m).toUpperCase(); this.__u = String(u); this.__h = {}; return XO.apply(this, arguments); };
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) { try { this.__h[k] = v; } catch (e) {} return XH.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (b) {
    const x = this;
    x.addEventListener('loadend', () => { try {
      const rt = x.responseType;
      const text = (rt === '' || rt === 'text') ? x.responseText : (rt === 'json' ? JSON.stringify(x.response) : null);
      keep({ via: 'xhr', method: x.__m, url: x.__u, body: typeof b === 'string' ? b : (b instanceof URLSearchParams ? b.toString() : null), headers: x.__h, status: x.status, ct: x.getResponseHeader('content-type'), text });
    } catch (e) {} });
    return XS.apply(this, arguments);
  };
})();`;

const SECRET_KEY = /(csrf|xsrf|token|^sign$|^_m_h5_tk(_enc)?$|^ua$|^bx-|^x5sec$|^cookie$)/i;
const SKIP_URL = /(\/csrf\b|\/api\/csrf|um\.json|\/log\b|mmstat|arms-retcode|sentry|\.(js|css|png|jpg|gif|svg|woff2?)(\?|$)|onetalk\.alibaba\.com|message\.alibaba\.com)/i;
const WRITE_PATH = /(save|update|delete|remove|submit|create|\badd|send|mark|read(?!y)|\bset[A-Z_]|apply|enroll|withdraw|confirm|cancel|close|bind|publish|edit|modify|upload|import|\bpay|accept|reject|agree|offline|online|launch|start|stop|pause|resume|reply|popup|trial)/i;
const QUERY_PATH = /(query|list|data|search|get|report|summary|stat|overview|detail|info|count|trend|index|page)/i;
const PAGE_KEYS = ["pageNo", "pageNum", "pageIndex", "currentPage", "curPage", "current", "page", "pageNow"];
const TOTAL_KEYS = ["totalRows", "totalCount", "total", "totalNum", "totalSize", "totalItem", "totalItems", "totalRecord", "recordCount"];

const stripUrl = (u) => { try { const x = new URL(u, BASE); for (const k of [...x.searchParams.keys()]) if (SECRET_KEY.test(k)) x.searchParams.delete(k); return x.toString(); } catch (e) { return String(u).split("?")[0]; } };
const scrubObj = (o) => { if (Array.isArray(o)) return o.map(scrubObj); if (o && typeof o === "object") { const r = {}; for (const [k, v] of Object.entries(o)) if (!SECRET_KEY.test(k)) r[k] = scrubObj(v); return r; } return o; };
const stripBody = (b) => {
  if (b == null) return null;
  try { return JSON.stringify(scrubObj(JSON.parse(b))); } catch (e) {}
  try { const sp = new URLSearchParams(b); if ([...sp.keys()].length) { for (const k of [...sp.keys()]) if (SECRET_KEY.test(k)) sp.delete(k); return sp.toString(); } } catch (e) {}
  return b;
};
const scrubText = (t) => t == null ? t : String(t).replace(/("[\w-]*(?:csrf|xsrf|token)[\w-]*"\s*:\s*")[^"]*(")/gi, "$1***$2");
const jparse = (t) => { try { return JSON.parse(t); } catch (e) { const m = /^[\w$.]+\(([\s\S]*)\)\s*;?\s*$/.exec(t || ""); if (m) { try { return JSON.parse(m[1]); } catch (e2) {} } return null; } };
const findNum = (o, keys, depth = 0) => { if (!o || typeof o !== "object" || depth > 6) return null; for (const k of keys) if (typeof o[k] === "number" || (typeof o[k] === "string" && /^\d+$/.test(o[k]))) return Number(o[k]); for (const v of Object.values(o)) { const r = findNum(v, keys, depth + 1); if (r != null) return r; } return null; };
const biggestArray = (o, depth = 0) => { let best = 0; if (Array.isArray(o)) return o.length; if (o && typeof o === "object" && depth < 6) for (const v of Object.values(o)) best = Math.max(best, biggestArray(v, depth + 1)); return best; };

const sanitizeInquiryResponse = (input) => {
  const text = typeof input === "string" ? input : input && input.text;
  const lookup = typeof input === "string" ? null : input && input.lookup;
  const keep = ["id", "tradeId", "feedbackId", "secTradeId", "createTime", "lastestReplyTime", "modifiedTime", "source", "feedbackType", "targetIds", "appFrom", "inquiryCount", "replyStatus", "hasUnread", "unreadCount", "process", "imInquiry", "historyInquiry", "spam"];
  let value = String(text), parsed = null, success = false;
  for (let attempt = 0; attempt < 5000; attempt++) {
    try {
      parsed = JSON.parse(value, (key, number, context) => Number.isInteger(number) && !Number.isSafeInteger(number) && context?.source ? context.source : number);
      success = true;
      break;
    }
    catch (e) {
      const position = /\bposition\s+(\d+)\b/.exec(String(e.message));
      if (!position || Number(position[1]) === 0) break;
      const quote = value.lastIndexOf('"', Number(position[1]) - 1);
      if (quote < 0) break;
      value = value.slice(0, quote) + "\\" + value.slice(quote);
    }
  }
  // Buyer level: the field name is not documented, so any short scalar under a level/grade-named key
  // of the item or its sender is kept (value must look like a code such as "L3", never free text).
  // fieldNames records key names only (no values) so the real name can be read off one live run.
  const LEVEL_KEY = /(level|lvl|grade)/i, LEVEL_VALUE = /^[A-Za-z0-9_.+-]{1,12}$/;
  const fieldNames = new Set();
  const items = [];
  let found = null;
  const list = parsed?.data?.list;
  for (const group of Array.isArray(list) ? list : []) {
    for (const item of Array.isArray(group) ? group : [group]) {
      if (!item || typeof item !== "object" || !(Number.isSafeInteger(item.id) || typeof item.id === "string" && /^\d+$/.test(item.id))) continue;
      const entry = {};
      for (const key of keep) if (Object.prototype.hasOwnProperty.call(item, key)) entry[key] = item[key];
      entry.buyerCountry = item.sender?.countryCode ?? null;
      const buyerAttrs = {};
      for (const [prefix, obj] of [["", item], ["sender.", item.sender], ["buyer.", item.buyer]]) {
        if (!obj || typeof obj !== "object") continue;
        for (const [k, v] of Object.entries(obj)) {
          fieldNames.add(prefix + k);
          if (LEVEL_KEY.test(k) && (typeof v === "number" || typeof v === "string" && LEVEL_VALUE.test(v))) buyerAttrs[prefix + k] = v;
        }
      }
      if (Object.keys(buyerAttrs).length) entry.buyerAttrs = buyerAttrs;
      items.push(entry);
      const ids = (keys) => keys.map((k) => item[k]).filter((v) => v != null && v !== "" && v !== 0 && v !== "0").map(String);
      if (lookup && !found && ((lookup.inquiryId && ids(["id", "tradeId", "secTradeId", "feedbackId"]).includes(String(lookup.inquiryId))) ||
          (lookup.tradeId && ids(["tradeId", "secTradeId"]).includes(String(lookup.tradeId))))) {
        const s = item.sender && typeof item.sender === "object" ? item.sender : {};
        const sv = (keys) => keys.map((k) => s[k]).filter((v) => v != null && v !== "" && v !== 0 && v !== "0").map(String);
        found = { aliIds: sv(["accountId", "alitalkId", "vaccountId", "secAccountId", "contactId", "secContactId"]),
          cids: ids(["secTradeId", "messageId", "tradeId", "id", "feedbackId", "secFeedbackId", "requestNo"]),
          times: ["lastestReplyTime", "modifiedTime", "createTime"].map((k) => Number(item[k])).filter((v) => v > 0),
          country: sv(["countryCode", "countryName"]) };
      }
    }
  }
  const res = { code: parsed?.code ?? null, pagination: parsed?.data?.pagination ?? null, list: items, parsed: success, fieldNames: [...fieldNames].sort() };
  if (lookup) res.lookup = found;
  return res;
};

// Additional list information contains messages too; persist only buyer metadata, keyed by tradeId.
const sanitizeInquiryExtra = (text, allow) => {
  let j, value = String(text);
  for (let attempt = 0; attempt < 5000; attempt++) {
    try { j = JSON.parse(value); break; }
    catch (e) {
      const position = /\bposition\s+(\d+)\b/.exec(String(e.message));
      if (!position || Number(position[1]) === 0) break;
      const quote = value.lastIndexOf('"', Number(position[1]) - 1);
      if (quote < 0) break;
      value = value.slice(0, quote) + "\\" + value.slice(quote);
    }
  }
  if (!j) return {parsed: false, list: []};
  return { code: j.code, parsed: true, list: Object.entries(j.data?.list || {}).map(([tradeId, x]) => ({
    tradeId, ...Object.fromEntries((allow || []).filter(k => Object.hasOwn(x.sender || {}, k)).map(k => [k, x.sender[k]]))
  })) };
};

// Customer activity: only explicit fields from the panel, never names/contact details or tokens.
const sanitizeBuyerProfile = (input) => {
  const b = input?.data?.data?.buyerInfo;
  if (!b) return { available: false, reason: "queryCustomerInfo buyerInfo 없음" };
  const keep = ["country", "productViewCount", "validInquiryCount", "validRfqCount", "loginDays", "spamInquiryMarkedBySupplierCount", "addedToBlacklistCount"];
  const visits = b.buyerShopBehaviorInfo?.productVisitHistory;
  return { available: true, ...Object.fromEntries(keep.filter(k => Object.hasOwn(b, k)).map(k => [k, b[k]])),
    growthLevel: b.growthLevelInfo?.growthLevel ?? null,
    productVisitHistory: Array.isArray(visits) ? visits.map(p => Object.fromEntries(["productId", "productName"].filter(k => Object.hasOwn(p, k)).map(k => [k, p[k]]))) : null,
    viewedProductsReason: Array.isArray(visits) ? (visits.length ? "" : "productVisitHistory 0행") : "queryCustomerInfo buyerShopBehaviorInfo/productVisitHistory 없음" };
};

// Replays the panel GET without navigating to a conversation or executing its read-status scripts.
const fetchBuyerProfile = async (a) => {
  try {
    const body = new URLSearchParams({ _csrf_token_: a.csrf, params: JSON.stringify({secTradeId: a.item.secTradeId}) });
    const summary = await (await fetch(a.config.summaryUrl, {method: "POST", credentials: "include", body})).json();
    if (!summary.data?.contact?.accountIdEncrypt) return {error: "querySummary contact 없음"};
    const html = await (await fetch(a.config.detailUrl + "?imInquiryId=" + encodeURIComponent(a.item.tradeId), {credentials: "include"})).text();
    const token = /window\.KHTAccessToken\s*=\s*['"]([^'"]+)['"]/.exec(html)?.[1];
    if (!token) return {error: "상세 HTML KHTAccessToken 없음"};
    const cookie = k => decodeURIComponent(document.cookie.split("; ").find(x => x.startsWith(k + "="))?.slice(k.length + 1) || "");
    const url = new URL(a.config.url);
    for (const [k,v] of Object.entries({buyerAccountId: summary.data.contact.accountIdEncrypt, secTradeId: summary.data.secTradeId,
      buyerLoginId: "", secReqToken: token, clientType: "", lang: "en_US", ctoken: cookie("ctoken"), _tb_token_: cookie("_tb_token_"), callback: "profileRead"})) url.searchParams.set(k,v);
    const text = await (await fetch(url, {credentials: "include"})).text();
    return JSON.parse(text.replace(/^[^(]*\(/, "").replace(/\);?\s*$/, ""));
  } catch { return {error: "고객 정보 조회/파싱 실패"}; }
};

// Tables with buyer-level rows (pages.json rangeTables[].allow): keep only the allowed row fields and
// the row count, in controller RAM, before anything reaches the temp file.
const rangeSignature = d => d.tableName + JSON.stringify(d.filters || []) + (d.time?.granularity && d.time.granularity !== "DAY" ? "|" + d.time.granularity : "");

const rangeTime = (time, start, end) => ({...time, beginDateTime: start + " 00:00:00", endDateTime: end + " 23:59:59"});

const prioritizeRangeJobs = (jobs) => [...jobs].sort((a, b) => Number(Boolean(b.tag)) - Number(Boolean(a.tag)));

const sanitizeAllowedTable = (text, allow) => {
  let j = null;
  try { j = JSON.parse(text); } catch (e) { return JSON.stringify({ sanitized: true, parsed: false }); }
  const q = j && j.data && j.data.queryResult;
  if (j?.ok === false || j?.success === false || !q || !Array.isArray(q.detailList)) return JSON.stringify({sanitized: true, captureError: "invalid_query_result"});
  const raw = q.detailList;
  const rows = raw.map((row) => {
    const keep = {};
    for (const k of allow) if (row && Object.prototype.hasOwnProperty.call(row, k)) keep[k] = row[k];
    return keep;
  });
  // key names only (never values): shows which columns the API really returns, e.g. the channel column
  const fieldNames = [...new Set(raw.flatMap((row) => (row && typeof row === "object" ? Object.keys(row) : [])))].sort();
  return JSON.stringify({ sanitized: true, data: { queryResult: { totalRows: q ? q.totalRows : null, detailList: rows, fieldNames } } });
};

// Read-only Operation Log requests observed on account_info.htm. Uses the same
// captured template, send function, pagination limits and sanitization as tables.
const captureOperationLogs = async (cap, config, start, end, maxPages, send, left) => {
  const records = [], notes = [];
  const pathOf = (r) => { try { return new URL(r.url, "https://www2.alibaba.com").pathname; } catch { return ""; } };
  const template = cap.find((r) => r.method === "POST" && pathOf(r) === "/api/log" && r.body)
    || cap.find((r) => r.method === "POST" && pathOf(r) === "/api/log/interval" && r.body);
  if (!template) return { records, notes: ["operation logs: no observed request template"] };
  let retentionStart = null, maxDate = null;
  const interval = cap.find((r) => pathOf(r) === "/api/log/interval");
  try { maxDate = JSON.parse(interval?.text || "{}").data?.maxDate; } catch {}
  if (/^\d{4}-\d{2}-\d{2}$/.test(maxDate || "")) {
    const [y, m, d] = maxDate.split("-").map(Number);
    const first = new Date(Date.UTC(y, m - 3, 1));
    const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
    first.setUTCDate(Math.min(d, last));
    retentionStart = first.toISOString().slice(0, 10);
    start = start < retentionStart ? retentionStart : start;
    end = end > maxDate ? maxDate : end;
  }
  notes.push("Operation Log retains two months; older daily settings cannot be reconstructed");
  if (start > end) return { records, notes: [...notes, "requested window is outside log retention"] };
  for (const query of config.queries || []) {
    for (let pg = 1; pg <= maxPages && left() > 12000; pg++) {
      const data = { pageIndex: pg, pageSize: 100, ...query, dateBegin: start, dateEnd: end };
      const body = new URLSearchParams(template.body);
      body.set("ads", JSON.stringify({ productLineId: query.productLineId }));
      body.set("data", JSON.stringify(data));
      const url = new URL("/api/log", new URL(template.url, "https://www2.alibaba.com")).href;
      const res = await send({ url, method: "POST", body: body.toString(), headers: template.headers });
      records.push({ via: "replay", url, method: "POST", body: body.toString(), headers: template.headers, ...res,
        replay: { kind: "ads-history", page: pg, start, end, retentionStart, maxDate, ...query }, keep: true });
      let j; try { j = JSON.parse(res.text); } catch { break; }
      if (j.ok !== true || !Array.isArray(j.data) || !j.data.length || pg * data.pageSize >= Number(j.totalCount)) break;
    }
  }
  return { records, notes };
};

// Campaign detail's observed read endpoints. IDs come only from the list response.
const captureCampaignTargets = async (cap, paths, send, left) => {
  const records = [];
  const tpl = cap.find(r => r.method === "POST" && /\/campaign\/type\/allList(?:\?|$)/.test(r.url) && r.body);
  if (!tpl) return records;
  let data; try { data = JSON.parse(tpl.text).data; } catch { return records; }
  const seen = new Set();
  for (const list of Object.values(data || {})) for (const c of Array.isArray(list) ? list : []) {
    if (!/^\d+$/.test(String(c.id)) || seen.has(String(c.id)) || c.productLineId !== 110106) continue;
    seen.add(String(c.id));
    for (const path of paths || []) {
      if (left() < 12000) return records;
      const url = new URL(path.replace("{campaignId}", c.id), new URL(tpl.url, "https://www2.alibaba.com")).href;
      const body = new URLSearchParams(tpl.body);
      body.set("ads", JSON.stringify({productLineId: c.productLineId}));
      body.set("type", "normal"); body.set("data", "{}");
      const res = await send({url, method: "POST", body: body.toString(), headers: tpl.headers});
      records.push({url, method: "POST", body: body.toString(), headers: tpl.headers, ...res,
        replay: {kind: "campaign-targets", campaignId: String(c.id), path}, keep: true});
    }
  }
  return records;
};

const page = await openTab(P.url);
const ALLOW = Object.fromEntries((P.rangeTables || []).filter((t) => t.allow).map((t) => [t.table, t.allow]));
await sleep(2500);
const who = await page.evaluate((lid) => ({ host: location.host, href: location.href, onLogin: /login\./.test(location.host), matches: !lid || decodeURIComponent(document.cookie).includes(lid) }), P.loginId);
BASE = who.href;
out.finalUrl = stripUrl(who.href);
if (who.onLogin) { out.status = "session_expired"; await save(); console.log("STATUS session_expired"); }
else if (!who.matches) { out.status = "wrong_account"; await save(); console.log("STATUS wrong_account"); }
else {
  const inj = await page.evaluate(async (hook) => {
    try {
      const html = await (await fetch(location.href, { credentials: "include" })).text();
      const tag = "<script>" + hook + "</scr" + "ipt>";
      const doc = /<head[^>]*>/i.test(html) ? html.replace(/<head[^>]*>/i, (m) => m + tag) : tag + html;
      document.open(); document.write(doc); document.close();
      return "ok";
    } catch (e) { return "inject_failed " + String(e).slice(0, 120); }
  }, HOOK);
  if (inj !== "ok") out.notes.push(inj);
  // wait until no new requests for 4 s (max 25 s), scrolling to trigger lazy loads
  let last = -1, still = 0;
  for (let i = 0; i < 25 && left() > 60000; i++) {
    await sleep(1000);
    const n = await page.evaluate(() => { window.scrollTo(0, document.body ? document.body.scrollHeight : 0); return (window.__raw || []).length; }).catch(() => last);
    if (n === last) { if (++still >= 4) break; } else { still = 0; last = n; }
  }
  const cap = await page.evaluate(() => window.__raw || []).catch(() => []);
  for (const r of cap) out.records.push({ ...r, replay: null });

  const send = (req) => page.evaluate(async (q) => {
    try {
      const h = {}; for (const [k, v] of Object.entries(q.headers || {})) if (/content-type|accept|x-requested-with/i.test(k)) h[k] = v;
      const x = await fetch(q.url, { method: q.method, body: q.method === "GET" ? undefined : q.body, headers: h, credentials: "include" });
      return { status: x.status, ct: x.headers.get("content-type"), text: await x.text() };
    } catch (e) { return { status: 0, error: String(e).slice(0, 200) }; }
  }, req);

  if (P.operationLogs) {
    const logs = await captureOperationLogs(cap, P.operationLogs, P.start, P.end, P.maxPages, send, left);
    out.records.push(...logs.records);
    out.notes.push(...logs.notes);
  }
  if (P.campaignTargetReads) out.records.push(...await captureCampaignTargets(cap, P.campaignTargetReads, send, left));

  // range replay: ads report tables over --start/--end, all pages. Tables = the ones the page
  // loaded + P.rangeTables (product/keyword reports that sit on other tabs). The first captured
  // component/data request is the template (its url, headers and form fields stay in the page).
  if (P.rangeReplay) {
    const tpl = cap.find((r) => r.method === "POST" && /dynamic\/component\/data/.test(r.url) && r.body);
    if (!tpl) out.notes.push("range replay: no component/data request captured");
    const days = Math.round((Date.parse(P.end) - Date.parse(P.start)) / 864e5) + 1;
    const jobs = [];
    for (const r of cap) {
      if (r.method !== "POST" || !/dynamic\/component\/data/.test(r.url) || !r.body) continue;
      const d = jparse(new URLSearchParams(r.body).get("data")); if (d && d.tableName) jobs.push({ d, ads: new URLSearchParams(r.body).get("ads") });
    }
    for (const t of P.rangeTables || []) jobs.push({ d: { dataMode: "struct", tableName: t.table, filters: t.filters, time: { granularity: t.granularity || "DAY" }, derives: [{ mode: "MAIN" }],
      extendParams: { context: { ...t.ctx } }, ...(t.paged ? { page: { index: 1, size: t.pageSize || 100 } } : {}) }, ads: JSON.stringify({ productLineId: t.adsProductLineId || t.ctx.productLineId }), tag: t.key, maxPages: t.maxPages });
    const seenT = new Set();
    for (const job of tpl ? prioritizeRangeJobs(jobs) : []) {
      if (left() < 15000) { out.notes.push("range replay stopped: time budget"); break; }
      const d = job.d; const sig = rangeSignature(d); if (seenT.has(sig)) continue; seenT.add(sig);
      const resume = P.rangeResume?.[sig];
      if (resume?.complete) continue;
      d.time = rangeTime(d.time || {}, P.start, P.end);
      d.extendParams = d.extendParams || {}; d.extendParams.context = { ...(d.extendParams.context || {}), dateRange: days };
      const sp = new URLSearchParams(tpl.body);
      if (job.ads) sp.set("ads", job.ads);
      const paged = !!d.page; let idx = paged ? resume?.nextPage || 1 : 1, got = paged ? resume?.covered || 0 : 0;
      const lastPage = idx - 1 + Math.max(P.maxPages, job.maxPages || 0);  // page cap per pass, so a resumed pass keeps going
      while (left() > 15000 && idx <= lastPage) {
        if (paged) d.page = { ...d.page, index: idx };
        sp.set("data", JSON.stringify(d));
        const res = await send({ url: tpl.url, method: "POST", body: sp.toString(), headers: tpl.headers });
        if (ALLOW[d.tableName]) res.text = sanitizeAllowedTable(res.text, ALLOW[d.tableName]);
        out.records.push({ via: "replay", method: "POST", url: tpl.url, body: sp.toString(), headers: tpl.headers, ...res, replay: { kind: "range", tag: job.tag || null, table: d.tableName, page: idx, start: P.start, end: P.end } });
        const j = jparse(res.text); const q = j && j.data && j.data.queryResult; const n = (q && q.detailList || []).length; got += n;
        if (!paged || !q || n === 0 || got >= (q.totalRows || 0)) break;
        idx += 1;
      }
    }
  }

  // extra GET queries (e.g. data advisor per-product stats). Token params are copied inside the
  // page from a request the page itself made to the same path; they are stripped before saving.
  for (const x of P.extra || []) {
    if (left() < 12000) { out.notes.push("extra stopped: time budget"); break; }
    const src = cap.map((r) => r.url).concat(await page.evaluate(() => performance.getEntriesByType("resource").map((e) => e.name)).catch(() => []))
      .find((u) => { try { return new URL(u, BASE).href.split("?")[0] === new URL(x.url).href && /[?&](ctoken|p_csrf|_tb_token_)=/.test(u); } catch (e) { return false; } });
    if (!src) { out.notes.push("extra " + x.tag + ": no token source on page"); continue; }
    for (let pg = 1; pg <= (x.pageParam ? Math.min(x.maxPages || P.maxPages, P.maxPages) : 1) && left() > 12000; pg++) {
      const u = new URL(x.url); const s0 = new URL(src, BASE);
      for (const [k, v] of Object.entries(x.params || {})) u.searchParams.set(k, String(v));
      if (x.pageParam) u.searchParams.set(x.pageParam, String(pg));
      for (const k of ["ctoken", "p_csrf", "_tb_token_"]) if (s0.searchParams.get(k)) u.searchParams.set(k, s0.searchParams.get(k));
      const res = await send({ url: u.toString(), method: "GET" });
      out.records.push({ via: "replay", method: "GET", url: u.toString(), body: null, ...res, replay: { kind: "extra", tag: x.tag, page: pg } });
      const jj = jparse(res.text); if (!x.pageParam || !jj || biggestArray(jj) === 0) break;
    }
  }

  // inquiry list (message center). List API only: never opens a conversation, so nothing is
  // marked read. Only an allowlist of fields is kept (no buyer name, avatar, chat token, subject or
  // message text); the page's own message-center requests are dropped below.
  if (P.inquiryList) {
    // Decode, repair and allowlist in controller RAM before any response reaches the temp file.
    const tokSrc = cap.find((r) => /message\/ajax\/feedback\//.test(r.url) && r.body && /_csrf_token_=/.test(r.body));
    if (!tokSrc) out.notes.push("inquiry list: no csrf source request on page");
    let total = null;
    for (let pg = 1; tokSrc && pg <= P.maxPages && left() > 12000; pg++) {
      const params = { listType: "all", pagination: { nextPage: pg, pageSize: 20 }, filter: { isShowAtm: false }, order: { order: "desc", orderBy: "latest_contact_time" }, search: {} };
      const res = await page.evaluate(async (a) => {
        const body = new URLSearchParams({ _csrf_token_: a.tok, postId: String(Date.now()), params: JSON.stringify(a.params) });
        const x = await fetch("/message/ajax/feedback/subjectList.htm", { method: "POST", credentials: "include", headers: { "content-type": "application/x-www-form-urlencoded; charset=UTF-8", "x-requested-with": "XMLHttpRequest" }, body });
        const t = await x.text(); const ta = document.createElement("textarea"); ta.innerHTML = t;  // the body is HTML-entity encoded
        return { status: x.status, text: ta.value };
      }, { tok: new URLSearchParams(tokSrc.body).get("_csrf_token_"), params });
      const n = (String(res.text).match(/"tradeId":/g) || []).length;
      const tm = /"totalCount":(\d+)/.exec(res.text || ""); if (tm) total = Number(tm[1]);
      const san = await page.evaluate(sanitizeInquiryResponse, { text: res.text, lookup: P.lookup || null });
      if (san.lookup) LOOKUP = san.lookup;
      delete san.lookup;
      out.records.push({ via: "replay", method: "POST", url: "https://message.alibaba.com/message/ajax/feedback/subjectList.htm", body: JSON.stringify(params), status: res.status, ct: "application/json",
        text: JSON.stringify(san), replay: { kind: "inquiry", tag: "inquiries", page: pg }, keep: true });
      if (P.inquiryExtra && san.list.length && left() > 5000) {
        const ep = { tradeIds: san.list.map(x => x.tradeId).filter(x => x != null),
                     secTradeIds: san.list.map(x => x.secTradeId).filter(x => x != null) };
        const er = await page.evaluate(async a => {
          const body = new URLSearchParams({ _csrf_token_: a.tok, postId: String(Date.now()), params: JSON.stringify(a.params) });
          const res = await fetch(a.url, { method: "POST", credentials: "include", headers: { "content-type": "application/x-www-form-urlencoded; charset=UTF-8", "x-requested-with": "XMLHttpRequest" }, body });
          const ta = document.createElement("textarea"); ta.innerHTML = await res.text();
          return {status: res.status, text: ta.value};
        }, {url: P.inquiryExtra.url, tok: new URLSearchParams(tokSrc.body).get("_csrf_token_"), params: ep});
        const clean = sanitizeInquiryExtra(er.text, P.inquiryExtra.allow);
        out.records.push({via: "replay", method: "POST", url: P.inquiryExtra.url, status: er.status,
          text: JSON.stringify(clean), replay: {kind: "inquiry-extra", page: pg}, keep: true});
      }
      if (P.inquiryProfile && !P.lookup) {
        const profiles = [];
        for (let i = 0; i < san.list.length && left() > 12000; i += 4) {
          const chunk = await Promise.all(san.list.slice(i, i + 4).map(async item => {
            const raw = await page.evaluate(fetchBuyerProfile, {item, config: P.inquiryProfile, csrf: new URLSearchParams(tokSrc.body).get("_csrf_token_")});
            return {tradeId: item.tradeId, ...sanitizeBuyerProfile(raw), ...(raw.error ? {reason: raw.error} : {})};
          }));
          profiles.push(...chunk);
        }
        if (profiles.length < san.list.length) out.notes.push("inquiry profiles incomplete: time budget");
        out.records.push({via: "replay", method: "GET", url: P.inquiryProfile.url,
          text: JSON.stringify({list: profiles, activityWindow: "90d", capturedAt: new Date().toISOString()}),
          replay: {kind: "inquiry-profile", page: pg}, keep: true});
      }
      if (!n || (total && pg * 20 >= total) || LOOKUP) break;
    }
    out.records = out.records.filter((r) => r.keep);
  }

  // pagination replay for list/query endpoints
  const seenP = new Set();
  for (const r of P.inquiryList ? [] : cap) {
    if (left() < 12000) { out.notes.push("pagination stopped: time budget"); break; }
    let u; try { u = new URL(r.url, BASE); } catch (e) { continue; }
    if (SKIP_URL.test(u.href) || /acs\.h\.alibaba\.com|mtop/.test(u.href) || WRITE_PATH.test(u.pathname) || !QUERY_PATH.test(u.pathname)) continue;
    const j = jparse(r.text); if (!j) continue;
    const total = findNum(j, TOTAL_KEYS); const rows = biggestArray(j);
    if (!total || !rows || total <= rows) continue;
    // where is the page number?
    let where = null, key = null, form = null, jb = null, inner = null;
    for (const k of PAGE_KEYS) if (u.searchParams.has(k)) { where = "query"; key = k; break; }
    if (!where && r.body) {
      try { jb = JSON.parse(r.body); } catch (e) {}
      if (jb && typeof jb === "object") { for (const k of PAGE_KEYS) if (k in jb) { where = "json"; key = k; break; } if (!where && jb.page && typeof jb.page === "object" && "index" in jb.page) { where = "json.page"; } }
      if (!where) { form = new URLSearchParams(r.body);
        for (const k of PAGE_KEYS) if (form.has(k)) { where = "form"; key = k; break; }
        if (!where) for (const f of ["data", "params", "param", "query", "request"]) { const v = jparse(form.get(f)); if (v && typeof v === "object") { for (const k of PAGE_KEYS) if (k in v) { where = "form." + f; key = k; inner = v; break; } if (!where && v.page && typeof v.page === "object" && "index" in v.page) { where = "form." + f + ".page"; inner = v; } if (where) break; } }
      }
    }
    if (!where) continue;
    const sig = u.origin + u.pathname + where; if (seenP.has(sig)) continue; seenP.add(sig);
    const pages = Math.min(P.maxPages, Math.ceil(total / Math.max(rows, 1)));
    for (let pg = 2; pg <= pages && left() > 12000; pg++) {
      let url = u.href, body = r.body;
      if (where === "query") { const x = new URL(u.href); x.searchParams.set(key, String(pg)); url = x.toString(); }
      else if (where === "json") { body = JSON.stringify({ ...jb, [key]: pg }); }
      else if (where === "json.page") { body = JSON.stringify({ ...jb, page: { ...jb.page, index: pg } }); }
      else if (where === "form") { const f = new URLSearchParams(r.body); f.set(key, String(pg)); body = f.toString(); }
      else { const f = new URLSearchParams(r.body); const field = where.split(".")[1]; const v = { ...inner }; if (where.endsWith(".page")) v.page = { ...v.page, index: pg }; else v[key] = pg; f.set(field, JSON.stringify(v)); body = f.toString(); }
      const res = await send({ url, method: r.method, body, headers: r.headers });
      out.records.push({ via: "replay", method: r.method, url, body, headers: r.headers, ...res, replay: { kind: "page", page: pg, of: pages, total } });
      const jj = jparse(res.text); if (!jj || biggestArray(jj) === 0) break;
    }
  }

  // sanitize before writing
  for (const r of out.records) {
    if (r.replay) continue;
    const t = r.body && /dynamic\/component\/data/.test(r.url || "") ? (jparse(new URLSearchParams(r.body).get("data")) || {}).tableName : null;
    if (t && ALLOW[t]) r.text = sanitizeAllowedTable(r.text, ALLOW[t]);
  }
  out.records = out.records.filter((r) => r.keep || !SKIP_URL.test(r.url || "")).map((r, i) => ({
    seq: i, via: r.via, method: r.method, url: stripUrl(r.url), reqContentType: Object.entries(r.headers || {}).find(([k]) => /content-type/i.test(k))?.[1] || null,
    body: stripBody(r.body), status: r.status, ct: r.ct, truncated: !!r.truncated, replay: r.replay, error: r.error || null, text: scrubText(r.text),
  }));
  await save();
  console.log("STATUS ok records=" + out.records.length + " replays=" + out.records.filter((r) => r.replay).length);
  if (P.lookup) console.log("LOOKUP " + JSON.stringify(LOOKUP));
}
await closeTab(page).catch(() => {});
