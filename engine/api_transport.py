"""Seller-center data over plain HTTP: the internal API calls the pages make, sent directly with the
saved login cookies (no browser tab). One capture() per page returns what capture.js returns for the
browser ({key, url, status, finalUrl, records, notes}), so seller_central_raw.write_page and every
report read the same raw records whichever transport fetched them.

Which calls: engine/api_catalog.json (page-load calls and their anti-CSRF token) plus pages.json
(extra GETs, ads range tables, inquiry list). Saved records never hold a token: the token goes in the
request only, and the saved url/body are the ones without it.
"""
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

import cookie_store

HERE = os.path.dirname(os.path.abspath(__file__))
LOGIN_HOST = re.compile(r"^https?://(?:login|passport)\.(?:[\w-]+\.)*alibaba\.com", re.I)
PUNISH = re.compile(r"(?i)punish|_____tmd_____|x5secdata|captcha|err\.taobao\.com")
INQUIRY_KEEP = ("id", "tradeId", "feedbackId", "secTradeId", "createTime", "lastestReplyTime", "modifiedTime", "source", "feedbackType",
                "targetIds", "appFrom", "inquiryCount", "replyStatus", "hasUnread", "unreadCount", "process", "imInquiry", "historyInquiry", "spam")
LEVEL_KEY, LEVEL_VALUE = re.compile(r"(level|lvl|grade)", re.I), re.compile(r"^[A-Za-z0-9_.+-]{1,12}$")
TIMEOUT = 40


def load_catalog():
    with open(os.path.join(HERE, "api_catalog.json"), encoding="utf-8") as fh:
        return json.load(fh)


class Blocked(Exception):
    """The site answered a call with a bot check / punish page instead of data."""


class SessionExpired(Exception):
    """The site sent a call to the login page."""


class Http:
    """Cookie jar over the saved login cookies: Set-Cookie answers (XSRF-TOKEN, session ids) are kept
    for the next calls of this run only, in RAM."""

    def __init__(self, cookies, opener=None):
        self.cookies = [dict(c) for c in cookies]
        self.open = opener or urllib.request.build_opener(cookie_store._NoRedirect).open

    def cookie(self, name, host):
        return next((c["value"] for c in reversed(self.cookies) if c["name"] == name and
                     (host == c["domain"].lstrip(".") or host.endswith("." + c["domain"].lstrip(".")))), None)

    def _keep(self, headers, host):
        for sc in headers.get_all("Set-Cookie") or []:
            name, _, value = sc.split(";")[0].partition("=")
            attrs = {k.strip().lower(): v.strip() for k, _, v in (p.partition("=") for p in sc.split(";")[1:])}
            domain = attrs.get("domain") or host
            self.cookies = [c for c in self.cookies if not (c["name"] == name.strip() and c["domain"].lstrip(".") == domain.lstrip("."))]
            self.cookies.append({"name": name.strip(), "value": value, "domain": domain, "path": attrs.get("path") or "/", "expires": -1, "secure": "secure" in sc.lower()})

    def request(self, method, url, form=None, referer=None):
        """-> {status, ct, text, location}. Raises SessionExpired / Blocked on a login or bot-check answer."""
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        headers = {"Cookie": cookie_store.cookie_header(self.cookies, url), "User-Agent": cookie_store.UA,
                   "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
                   "Referer": referer or url, "X-Requested-With": "XMLHttpRequest"}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            u = urllib.parse.urlsplit(referer or url)
            headers["Origin"] = "%s://%s" % (u.scheme, u.netloc)
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.open(req, timeout=TIMEOUT) as r:
                status, hdrs, body = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            status, hdrs, body = e.code, e.headers, e.read()
        self._keep(hdrs, urllib.parse.urlsplit(url).hostname or "")
        location = urllib.parse.urljoin(url, hdrs.get("Location") or "") if hdrs.get("Location") else ""
        text = body.decode("utf-8", "replace")
        if LOGIN_HOST.match(location) or cookie_store.is_login_expired(location or url, status, text if status != 200 else text[:300]):
            raise SessionExpired("%s %s -> %s" % (method, url.split("?")[0], (location or "status %d" % status).split("?")[0]))
        if (status in (301, 302, 303, 307) and PUNISH.search(location)) or (status in (200, 403, 405) and PUNISH.search(text[:2000]) and not text.lstrip().startswith(("{", "["))):
            raise Blocked("%s %s -> %s" % (method, url.split("?")[0], (location or "status %d bot check" % status).split("?")[0]))
        return {"status": status, "ct": hdrs.get("content-type"), "text": text, "location": location}


def jparse(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        m = re.match(r"^[\w$.]+\(([\s\S]*)\)\s*;?\s*$", (text or "").strip())
        if m:
            try:
                return json.loads(m.group(1))
            except ValueError:
                return None
    return None


def biggest_array(o, depth=0):
    if isinstance(o, list):
        return len(o)
    if isinstance(o, dict) and depth < 6:
        return max([biggest_array(v, depth + 1) for v in o.values()] or [0])
    return 0


def encode_form(form):
    return {k: (v if isinstance(v, str) else json.dumps(v, separators=(",", ":"), ensure_ascii=False)) for k, v in form.items()}


# -- tokens --------------------------------------------------------------------------------------
def token(http, catalog, kind):
    """-> (form param name, value) to add to each POST, or None."""
    spec = catalog["tokens"].get(kind) or {}
    if kind == "www2":
        j = jparse(http.request("GET", spec["url"], referer="https://www2.alibaba.com/ads/report.htm")["text"]) or {}
        if not j.get("token"):
            raise Blocked("GET %s: no token in the answer" % spec["url"].split("?")[0])
        return j.get("parameterName") or "_csrf", j["token"]
    if kind == "message":
        http.request("GET", spec["url"], referer="https://i.alibaba.com/")
        value = http.cookie(spec["cookie"], urllib.parse.urlsplit(spec["url"]).hostname)
        if not value:
            raise Blocked("GET %s: no %s cookie set" % (spec["url"], spec["cookie"]))
        return spec["param"], value
    return None


# -- sanitizers (same allowlists as capture.js) ---------------------------------------------------
def parse_loose(text):
    """JSON whose string values may hold bare quotes (decoded HTML entities in message text): escape
    the quote before each parse error position until it parses. -> object or None."""
    value = text
    for _ in range(5000):
        try:
            return json.loads(value)
        except ValueError as e:
            pos = getattr(e, "pos", None)
            if not pos:
                return None
            quote = value.rfind('"', 0, pos)
            if quote < 0:
                return None
            value = value[:quote] + "\\" + value[quote:]
    return None


def sanitize_inquiries(text):
    """subjectList answer -> {code, pagination, list (allowlisted), parsed, fieldNames}, as capture.js."""
    if "&quot;" in text[:200]:
        text = html.unescape(text)
    parsed = parse_loose(text)
    data = (parsed or {}).get("data") if isinstance(parsed, dict) else None
    items, names = [], set()
    groups = (data or {}).get("list") if isinstance(data, dict) else None
    for group in groups if isinstance(groups, list) else []:
        for item in group if isinstance(group, list) else [group]:
            if not isinstance(item, dict) or not re.fullmatch(r"\d+", str(item.get("id") or "")):
                continue
            entry = {k: item[k] for k in INQUIRY_KEEP if k in item}
            sender = item.get("sender") if isinstance(item.get("sender"), dict) else {}
            entry["buyerCountry"] = sender.get("countryCode")
            attrs = {}
            for prefix, obj in (("", item), ("sender.", item.get("sender")), ("buyer.", item.get("buyer"))):
                if not isinstance(obj, dict):
                    continue
                for k, v in obj.items():
                    names.add(prefix + k)
                    if LEVEL_KEY.search(k) and (isinstance(v, (int, float)) and not isinstance(v, bool) or isinstance(v, str) and LEVEL_VALUE.match(v)):
                        attrs[prefix + k] = v
            if attrs:
                entry["buyerAttrs"] = attrs
            items.append(entry)
    return {"code": (parsed or {}).get("code") if isinstance(parsed, dict) else None,
            "pagination": (data or {}).get("pagination") if isinstance(data, dict) else None,
            "list": items, "parsed": parsed is not None, "fieldNames": sorted(names)}


def inquiry_levels(text, trade_ids):
    """subjectListExtraInfo answer -> {tradeId: "L3"} (sender.userNewLevel, the row's buyer level badge).
    Read by position like capture.js: the buyers' message text in the same answer can break the JSON."""
    want, levels, row = {str(t) for t in trade_ids}, {}, None
    for m in re.finditer(r'"(\d{6,})":\{|"userNewLevel":"(L\d)"', text.replace("&quot;", '"')):
        if m.group(1):
            if m.group(1) in want:
                row = m.group(1)
        elif row and row not in levels:
            levels[row] = m.group(2)
    return levels


def sanitize_allowed(text, allow):
    j = jparse(text)
    if not isinstance(j, dict):
        return json.dumps({"sanitized": True, "parsed": False})
    q = (j.get("data") or {}).get("queryResult") if isinstance(j.get("data"), dict) else None
    raw = q.get("detailList") if isinstance(q, dict) and isinstance(q.get("detailList"), list) else []
    rows = [{k: r[k] for k in allow if k in r} for r in raw if isinstance(r, dict)]
    names = sorted({k for r in raw if isinstance(r, dict) for k in r})
    return json.dumps({"sanitized": True, "data": {"queryResult": {"totalRows": q.get("totalRows") if isinstance(q, dict) else None,
                                                                    "detailList": rows, "fieldNames": names}}})


# -- page capture ----------------------------------------------------------------------------------
class Capture:
    def __init__(self, http, catalog, pg, params, spec):
        self.http, self.catalog, self.pg, self.p, self.spec = http, catalog, pg, params, spec
        self.out = {"key": pg["key"], "url": pg["url"], "status": "ok", "finalUrl": None, "records": [], "notes": [], "transport": "api"}
        self.tok = None
        self.vars = {}

    def call(self, method, url, form=None, replay=None, saved_body=None):
        """One API call; the record keeps the url/body without the token."""
        sent = dict(form) if form is not None else None
        if sent is not None and self.tok:
            sent[self.tok[0]] = self.tok[1]
        res = self.http.request(method, url, sent, referer=self.spec.get("referer"))
        rec = {"via": "api", "method": method, "url": url, "body": saved_body if saved_body is not None else (urllib.parse.urlencode(form) if form is not None else None),
               "status": res["status"], "ct": res["ct"], "text": res["text"], "replay": replay}
        self.out["records"].append(rec)
        return rec

    def run(self):
        self.tok = token(self.http, self.catalog, self.spec.get("token", "none"))
        for c in self.spec.get("calls") or []:
            self.page_load(c)
        if self.p.get("rangeReplay"):
            self.range_replay()
        for x in self.p.get("extra") or []:
            self.extra(x)
        if self.p.get("inquiryList"):
            self.inquiry_list()
        for i, r in enumerate(self.out["records"]):
            r["seq"] = i
        return self.out

    def page_load(self, c):
        """A page-load call; with "page" (data.page / query.pageNO) the next pages too, until a page
        comes back empty or totalCount/total is reached. "{name}" in it = the "data" of an earlier
        call with "saveAs": name (e.g. the showcase campaign id); skipped when that call had none."""
        text = json.dumps(c)
        for name in re.findall(r"\{(\w+)\}", text):
            if self.vars.get(name) in (None, ""):
                self.out["notes"].append("api: %s skipped, %s not answered" % (c["url"].split("?")[0], name))
                return
            text = text.replace('"{%s}"' % name, json.dumps(self.vars[name])).replace("{%s}" % name, str(self.vars[name]))
        c = json.loads(text)
        where, _, key = (c.get("page") or "").partition(".")
        for n in range(1, (self.p.get("maxPages") or 30) + 1 if where else 2):
            if c["method"] == "GET":
                q = dict(c.get("query") or {})
                if where == "query":
                    q[key] = str(n)
                url = c["url"] + ("?" + urllib.parse.urlencode(q) if q else "")
                rec = self.call("GET", url, replay=None if n == 1 else {"kind": "page", "page": n})
            else:
                form = json.loads(json.dumps(c.get("form") or {}))
                if where in form and isinstance(form[where], dict):
                    form[where][key] = n
                rec = self.call("POST", c["url"], encode_form(form), replay=None if n == 1 else {"kind": "page", "page": n})
            j = jparse(rec["text"])
            if c.get("saveAs") and n == 1:
                self.vars[c["saveAs"]] = j.get("data") if isinstance(j, dict) and isinstance(j.get("data"), (int, str)) else None
            rows = biggest_array(j)
            total = None
            if isinstance(j, dict):
                total = j.get("totalCount") or ((j.get("data") or {}).get("total") if isinstance(j.get("data"), dict) else None)
            size = int(((c.get("query") or {}).get("pageSize")) or (((c.get("form") or {}).get("data") or {}).get("size")) or 0) if where else 0
            if not where or not rows or (size and rows < size) or (total and n * max(size, rows) >= int(total)):
                break

    def range_replay(self):
        days = (dt_date(self.p["end"]) - dt_date(self.p["start"])).days + 1
        jobs = [{**t, "tag": None} for t in self.spec.get("rangeJobs") or []] + [{**t, "tag": t["key"]} for t in self.p.get("rangeTables") or []]
        resume = self.p.get("rangeResume") or {}
        allow = {t["table"]: t["allow"] for t in self.p.get("rangeTables") or [] if t.get("allow")}
        seen = set()
        for t in jobs:
            d = {"dataMode": "struct", "tableName": t["table"], "filters": t.get("filters") or [], "time": {"granularity": t.get("granularity") or "DAY"},
                 "derives": [{"mode": "MAIN"}], "extendParams": {"context": dict(t.get("ctx") or {})}}
            if t.get("paged"):
                # Sorted like the site's own paged calls; unsorted pages overlap (rows twice, others missed).
                d["page"] = {"index": 1, "size": t.get("pageSize") or 100}
                d["orders"] = t.get("orders") or [{"field": "timeUs", "mode": "desc"}]
            sig = d["tableName"] + json.dumps(d["filters"], separators=(",", ":"), ensure_ascii=False)
            if sig in seen or (resume.get(sig) or {}).get("complete"):
                continue
            seen.add(sig)
            d["time"].update({"beginDateTime": self.p["start"] + " 00:00:00", "endDateTime": self.p["end"] + " 00:00:00"})
            d["extendParams"]["context"]["dateRange"] = days
            ads = json.dumps({"productLineId": t.get("adsProductLineId") or (t.get("ctx") or {}).get("productLineId")}, separators=(",", ":"))
            paged = "page" in d
            idx = (resume.get(sig) or {}).get("nextPage", 1) if paged else 1
            got = (resume.get(sig) or {}).get("covered", 0) if paged else 0
            last = idx - 1 + max(self.p.get("maxPages") or 30, t.get("maxPages") or 0)
            while idx <= last:
                if paged:
                    d["page"]["index"] = idx
                form = {"ads": ads, "type": "normal", "data": json.dumps(d, separators=(",", ":"), ensure_ascii=False)}
                rec = self.call("POST", self.spec["rangeUrl"], form, replay={"kind": "range", "tag": t["tag"], "table": t.get("as") or d["tableName"], "page": idx,
                                                                              "start": self.p["start"], "end": self.p["end"]})
                if d["tableName"] in allow:
                    rec["text"] = sanitize_allowed(rec["text"], allow[d["tableName"]])
                j = jparse(rec["text"])
                q = ((j or {}).get("data") or {}).get("queryResult") if isinstance((j or {}).get("data"), dict) else None
                n = len((q or {}).get("detailList") or [])
                got += n
                if not paged or not q or n == 0 or got >= (q.get("totalRows") or 0):
                    break
                idx += 1

    def extra(self, x):
        pages = min(x.get("maxPages") or self.p["maxPages"], self.p["maxPages"]) if x.get("pageParam") else 1
        for n in range(1, pages + 1):
            q = {k: str(v) for k, v in (x.get("params") or {}).items()}
            if x.get("pageParam"):
                q[x["pageParam"]] = str(n)
            rec = self.call("GET", x["url"] + "?" + urllib.parse.urlencode(q), replay={"kind": "extra", "tag": x["tag"], "page": n})
            if not x.get("pageParam") or biggest_array(jparse(rec["text"])) == 0:
                break

    def inquiry_list(self):
        url = "https://message.alibaba.com/message/ajax/feedback/subjectList.htm"
        total = None
        for pg in range(1, (self.p.get("maxPages") or 30) + 1):
            params = {"listType": "all", "pagination": {"nextPage": pg, "pageSize": 20}, "filter": {"isShowAtm": False},
                      "order": {"order": "desc", "orderBy": "latest_contact_time"}, "search": {}}
            form = {"postId": "1", "params": json.dumps(params, separators=(",", ":"))}
            rec = self.call("POST", url, form, replay={"kind": "inquiry", "tag": "inquiries", "page": pg}, saved_body=json.dumps(params, separators=(",", ":")))
            raw = rec["text"]
            san = sanitize_inquiries(raw)
            m = re.search(r'totalCount(?:&quot;|")\s*:\s*(\d+)', raw)
            if m:
                total = int(m.group(1))
            if san["list"]:
                ids = [e.get("tradeId") for e in san["list"] if e.get("tradeId") is not None]
                extra = self.http.request("POST", "https://message.alibaba.com/message/ajax/feedback/subjectListExtraInfo.htm",
                                          {self.tok[0]: self.tok[1], "postId": "2", "params": json.dumps({"secTradeIds": [e["secTradeId"] for e in san["list"] if e.get("secTradeId")], "tradeIds": ids})},
                                          referer=self.spec.get("referer"))
                levels = inquiry_levels(extra["text"], ids)
                for e in san["list"]:
                    if levels.get(str(e.get("tradeId"))):
                        e["buyerLevel"] = levels[str(e["tradeId"])]
                if extra["status"] != 200 or not levels:
                    self.out["notes"].append("inquiry list page %d: buyer levels not answered (status %s)" % (pg, extra["status"]))
            rec["text"] = json.dumps(san, ensure_ascii=False)
            rec["ct"] = "application/json"
            if not san["list"] or (total and pg * 20 >= total):
                break


def dt_date(s):
    import datetime
    return datetime.date.fromisoformat(s)


def capture(pg, params, cookies, login_id, catalog=None, http=None):
    """-> capture.js-shaped result for one page, or {"status": "no_api", "detail": why} when the
    catalog has no entry for it (the caller then uses the browser for this page and says so)."""
    catalog = catalog or load_catalog()
    spec = catalog["pages"].get(pg["key"])
    if spec is None:
        return {"key": pg["key"], "status": "no_api", "detail": "api_catalog.json has no calls for page %s" % pg["key"], "records": []}
    who = cookie_store.identity(cookies, login_id)
    if who != "ok":
        return {"key": pg["key"], "url": pg["url"], "status": "wrong_account" if who == "wrong_account" else "session_expired", "records": [], "notes": []}
    cap = Capture(http or Http(cookies), catalog, pg, params, spec)
    try:
        return cap.run()
    except SessionExpired as e:
        return {**cap.out, "status": "session_expired", "detail": "api: %s" % e}
    except Blocked as e:
        return {**cap.out, "status": "api_blocked", "detail": "api: %s" % e}
