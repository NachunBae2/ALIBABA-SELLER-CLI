// Aside lookup for OneTalk only. alibaba-data uses http_collect.py.
const P = __PARAMS__;
if (!P.lookup || !P.inquiryList) throw new Error("OneTalk lookup only; use direct HTTP for data");
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
          country: sv(["countryCode", "countryName"]),
          // OneTalk rows carry a 12-13 digit ali id the list API does not have; the display name (row
          // data-name) is the key that matches. RAM + stdout pipe only, like the other keys.
          names: sv(["name"]) };
      }
    }
  }
  const res = { code: parsed?.code ?? null, pagination: parsed?.data?.pagination ?? null, list: items, parsed: success, fieldNames: [...fieldNames].sort() };
  if (lookup) res.lookup = found;
  return res;
};

const page = await openTab(P.url);
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

  out.records = out.records.filter((r) => r.keep || !SKIP_URL.test(r.url || "")).map((r, i) => ({
    seq: i, via: r.via, method: r.method, url: stripUrl(r.url), reqContentType: Object.entries(r.headers || {}).find(([k]) => /content-type/i.test(k))?.[1] || null,
    body: stripBody(r.body), status: r.status, ct: r.ct, truncated: !!r.truncated, replay: r.replay, error: r.error || null, text: scrubText(r.text),
  }));
  await save();
  console.log("STATUS ok records=" + out.records.length + " replays=" + out.records.filter((r) => r.replay).length);
  if (P.lookup) console.log("LOOKUP " + JSON.stringify(LOOKUP));
}
await closeTab(page).catch(() => {});
