#!/usr/bin/env python3
"""Dump seller-center (Alibaba.com Seller Central) data as raw JSON through the Aside browser.

For every page in pages.json it records each XHR/fetch request the page makes and the raw
response (capture.js), replays list endpoints page by page, and replays the ads report
tables over --start/--end. Read-only: nothing that looks like a write endpoint is replayed.

Output (<out>/):
  raw/<page>.jsonl        one line per request: method, url, body, status, json|text, replay
  tables/<page>/*.csv     every array of objects found in the responses, flattened
  manifest.json           per page: status, record count, endpoints, notes

Accounts come from a JSON file outside any repo (default ~/.aliops-ops/seller-report/accounts.json):
  {"<name>": {"loginId": "<seller login id in the cookie>", "outDir": "...",
              "keychainService": "...", "keychainAccount": "<login email>"}}
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cookie_store  # noqa: E402
import secret_store  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.expanduser("~/.aliops-ops/seller-report/accounts.json")
# Any query/form key with csrf/xsrf/token in its name (ctoken, _tb_token_, _csrf_token_, p_csrf, access_token, ...).
SECRET_RES = [re.compile(p, re.I) for p in (r"[\w-]*(?:csrf|xsrf|token)[\w-]*=[A-Za-z0-9]", r"_m_h5_tk(?:_enc)?=[A-Za-z0-9]",
                                               r"\"[\w-]*(?:csrf|xsrf)[\w-]*\\?\"\s*:\s*\\?\"[A-Za-z0-9]")]
REDACT = re.compile(r"((?:[\w-]*(?:csrf|xsrf|token)[\w-]*|_m_h5_tk(?:_enc)?)=)[^&\"'\s;\\]+", re.I)
REDACT_JSON = re.compile(r"(\\?\"[\w-]*(?:csrf|xsrf)[\w-]*\\?\"\s*:\s*\\?\")[^\"\\]+", re.I)
EXIT_SESSION, EXIT_ACCOUNT, EXIT_SECRET, EXIT_BLOCKED = 2, 3, 4, 5
EXIT_LOGIN = 3  # login needed: no saved cookies, or still expired after one re-read from the browser
RUNNERS = ("auto", "aside", "chrome", "edge")
CDP_RUNNER = os.path.join(HERE, "cdp_runner.mjs")
LOGIN_URL = re.compile(r"^https?://(?:login|passport)\.(?:[\w-]+\.)*alibaba\.com", re.I)


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--account", help="account name in the config file")
    p.add_argument("--start", help="YYYY-MM-DD for the ads report range replay (default: end - 29 days)")
    p.add_argument("--end", help="YYYY-MM-DD (default: yesterday)")
    p.add_argument("--pages", default="all", help="comma-separated page keys from pages.json, or 'all'")
    p.add_argument("--list-pages", action="store_true", help="print the page keys and exit")
    p.add_argument("--max-pages", type=int, default=30, help="max list pages replayed per endpoint")
    p.add_argument("--resume", action="store_true", help="retain existing records and continue unfinished ad range tables")
    p.add_argument("--max-passes", type=int, default=20,
                   help="ads report: rerun the capture (keeping what it got) until every range table reaches totalRows, at most this many runs")
    p.add_argument("--config", default=DEFAULT_CONFIG)
    p.add_argument("--out", help="output directory (default: <outDir>/sellercentral-raw-<end>)")
    p.add_argument("--login", action="store_true", help="sign Aside in with the keychain password, then exit")
    p.add_argument("--browser-login", choices=("chrome", "edge", "aside"),
                   help="store the seller-center cookies of this browser (cookie_store.py), then exit")
    p.add_argument("--profile", help="--browser-login: browser profile folder (Default, Profile 1 ...); default = the one signed in as loginId")
    p.add_argument("--via-cdp", action="store_true", help="--browser-login: read through the browser itself (CDP) instead of its cookie file")
    p.add_argument("--session-status", action="store_true", help="check the stored cookies with one read request, then exit")
    p.add_argument("--runner", choices=RUNNERS, help="browser: aside, chrome/edge (saved login cookies, headless), "
                   "auto = chrome when saved cookies exist else aside (default; account key 'runner' also sets it)")
    p.add_argument("--lookup-inquiry-id", help="OneTalk lookup: print the target's match keys as one LOOKUP stdout line (never saved)")
    p.add_argument("--lookup-trade-id", help="same, by trade id")
    p.add_argument("--granularity", choices=("week", "month", "both"), default="month",
                   help="data advisor product stats periods replayed over --start..--end (analytics-product page)")
    p.add_argument("--seeds", default="", help="keyword-index page: seed head terms separated by '|' (1-2 words each)")
    p.add_argument("--kw-nd", default="30d", help="keyword-index window: 7d, 30d, 90d ... (Alibaba nd form)")
    p.add_argument("--kw-pages", type=int, default=3, help="keyword-index pages of 100 per seed")
    p.add_argument("--tables", default="", help="ads range tables to replay, comma-separated rangeTables keys (default: all)")
    a = p.parse_args(argv)
    a.seeds = [x.strip() for x in a.seeds.split("|") if x.strip()]
    if not __import__("re").fullmatch(r"\d+d", a.kw_nd):
        p.error("--kw-nd must look like 30d")
    end = dt.date.fromisoformat(a.end) if a.end else dt.date.today() - dt.timedelta(days=1)
    start = dt.date.fromisoformat(a.start) if a.start else end - dt.timedelta(days=29)
    if start > end:
        p.error("--start is after --end")
    a.start_d, a.end_d = start, end
    return a


def load_pages():
    with open(os.path.join(HERE, "pages.json"), encoding="utf-8") as fh:
        return json.load(fh)["pages"]


def load_account(path, name):
    with open(path, encoding="utf-8") as fh:
        accounts = json.load(fh)
    if name not in accounts:
        raise SystemExit("account %r not in %s (have: %s)" % (name, path, ", ".join(sorted(accounts))))
    acc = dict(accounts[name])
    if not isinstance(acc.get("loginId"), str) or not acc["loginId"].strip():
        raise SystemExit("nonempty loginId is required")
    acc.setdefault("asideAccount", "u0")
    acc["name"] = name
    if acc.get("runner", "auto") not in RUNNERS:
        raise SystemExit("runner must be one of %s" % ", ".join(RUNNERS))
    if not re.fullmatch(r"u\d+", acc["asideAccount"]):
        raise SystemExit("invalid asideAccount")
    return acc


def range_resume(records):
    state = {}
    for record in records:
        replay = record.get("replay") or {}
        if replay.get("kind") != "range":
            continue
        data = json.loads(parse_qs(record.get("body") or "").get("data", ["{}"])[0])
        result = ((record.get("json") or {}).get("data") or {}).get("queryResult")
        if not isinstance(result, dict) or not isinstance(result.get("detailList"), list):
            continue
        signature = data.get("tableName", "") + json.dumps(data.get("filters") or [], separators=(",", ":"), ensure_ascii=False)
        page = int(replay.get("page") or 1)
        size = int((data.get("page") or {}).get("size") or len(result["detailList"]) or 1)
        covered = (page - 1) * size + len(result["detailList"])
        total = result.get("totalRows")
        if not result["detailList"] and isinstance(total, (int, float)) and covered < total:
            continue
        entry = state.setdefault(signature, {"nextPage": 1, "covered": 0, "complete": False})
        entry["nextPage"] = max(entry["nextPage"], page + 1)
        entry["covered"] = max(entry["covered"], covered)
        entry["complete"] = entry["complete"] or isinstance(total, (int, float)) and covered >= total
    return state


def today_pst():
    from pacific_tz import pacific
    return dt.datetime.now(pacific()).date()


def week_selections(start, end, today):
    """[(week start Sunday, selected)] for the PST Sunday-Saturday weeks touching start..end.
    Data advisor week selected=0 is the latest complete week (seen 2026-11-17: Week 45 = 2026-11-08..14,
    "update cycle every Tuesday"); the latest complete week ends on the last Saturday before today.
    Weeks after it have no data yet and are skipped."""
    back_to_sat = (today.weekday() - 5) % 7 or 7
    latest_start = today - dt.timedelta(days=back_to_sat + 6)
    ws = start - dt.timedelta(days=(start.weekday() + 1) % 7)
    out = []
    while ws <= end:
        back = (latest_start - ws).days // 7
        if back >= 0:
            out.append((ws, back))
        ws += dt.timedelta(days=7)
    return out


def _json_of(record):
    if "json" in record:
        return record["json"]
    try:
        return json.loads(record.get("text") or "null")
    except ValueError:
        return None


def range_state(records):
    """range_resume over records that may hold raw text (fresh capture) or parsed json (from file)."""
    return range_resume([{**r, "json": _json_of(r)} for r in records])


def incomplete_ranges(state, pg, a):
    """Range table signatures still short of totalRows: started-but-unfinished ones, plus listed ones never reached."""
    sigs = {k for k, v in state.items() if not v["complete"]}
    for t in range_tables(pg, a):
        sig = t["table"] + json.dumps(t.get("filters") or [], separators=(",", ":"), ensure_ascii=False)
        if not (state.get(sig) or {}).get("complete"):
            sigs.add(sig)
    return sigs


def choose_runner(a, acc):
    """-> session {runner, cookies, refreshed}, or None when a browser runner has no saved login.
    --runner, else the account's "runner", else auto = chrome when saved cookies load, else aside."""
    want = a.runner or acc.get("runner") or "auto"
    if want == "aside":
        return {"runner": "aside", "cookies": None, "refreshed": False}
    try:
        cookies = cookie_store.load_cookies(acc)
    except secret_store.StoreError as e:  # auto falls back to Aside; an explicit browser says why it cannot run
        if want != "auto":
            raise SystemExit("--runner %s: saved cookies unreadable (%s)" % (want, type(e).__name__))
        print("runner auto: saved cookies unreadable (%s) -> aside" % type(e).__name__, file=sys.stderr)
        cookies = None
    runner = want if want != "auto" else ("chrome" if cookies else "aside")
    if runner != "aside" and not cookies:
        print("no saved login cookies for %s: run alibaba-data login --account %s --browser %s first"
              % (acc["name"], acc["name"], runner), file=sys.stderr)
        return None
    return {"runner": runner, "cookies": cookies if runner != "aside" else None, "refreshed": False}


def login_expired(res):
    """Capture result shows the login page (or an API said not logged in)."""
    if res.get("status") == "session_expired" or LOGIN_URL.match(res.get("finalUrl") or ""):
        return True
    return any(cookie_store.is_login_expired(r.get("url") or "", r.get("status") or 0, r.get("text") or "") for r in (res.get("records") or [])[:3])


def run_once(src, out_file, key, acc, session):
    """One capture.js run in the session's browser. Cookies go to the CDP runner on stdin only."""
    runner = session["runner"]
    if runner == "aside":
        cmd, stdin = [aside_exe(), "--host", "local", "--account", acc["asideAccount"], "repl", src], None
    else:
        cmd = [os.environ.get("ALIBABA_SELLER_NODE") or shutil.which("node") or "node", CDP_RUNNER, "--browser", runner]
        stdin = json.dumps({"cookies": session["cookies"], "script": src})
    lines = []
    try:
        proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=170)
        # LOOKUP line (OneTalk match keys) goes to our stdout pipe only: never into a file or the error tail
        lines = (proc.stdout + proc.stderr).splitlines()
        for line in lines:
            if line.startswith("LOOKUP "):
                print(line, flush=True)
        tail = "\n".join(l for l in lines if "LOOKUP" not in l)[-400:]
    except subprocess.TimeoutExpired:
        tail = runner + "_timeout"
    if not os.path.exists(out_file):
        blocked = next((l[9:] for l in lines if l.startswith("BOTBLOCK ")), None)
        if blocked:
            return {"key": key, "status": "bot_blocked", "detail": blocked, "records": []}
        return {"key": key, "status": runner + "_failed", "detail": REDACT.sub(r"\1***", tail), "records": []}
    with open(out_file, encoding="utf-8") as fh:
        return json.load(fh)


def run_capture(pg, acc, a, previous, session, resume_state=None):
    work_root = None
    if session["runner"] == "aside":  # Aside writes files only under its own folder
        work_root = os.path.join(aside_home(), "u", acc["asideAccount"][1:])
        os.makedirs(work_root, exist_ok=True)
    work = tempfile.mkdtemp(prefix="scraw-", dir=work_root)
    try:
        out_file = os.path.join(work, "out.json")
        extra = []
        for x in pg.get("extra") or []:
            if x.get("perSeed"):
                # keyword index: one paged query per seed (1-2 word head term), nd window from --kw-nd
                for seed in a.seeds:
                    extra.append({**x, "tag": "%s.%s" % (x["tag"], seed), "maxPages": a.kw_pages,
                                  "params": {**x["params"], "queryRaw": seed, "nd": a.kw_nd}})
                continue
            if x.get("perWeek"):
                if a.granularity in ("week", "both"):
                    for week_start, back in week_selections(a.start_d, a.end_d, today_pst()):
                        extra.append({**x, "tag": "%s.%s" % (x["tag"], week_start.isoformat()), "params": {**x["params"], "selected": str(back + int(x.get("selectedBase", 0)))}})
            elif x.get("perMonth") and a.granularity in ("month", "both"):
                # one query per month in --start..--end. Data advisor picks the period with
                # selected=N months back from the latest complete month; statDate is ignored.
                # The count starts at selectedBase = 1 for both getProdcutSummary (checked against the
                # statDate it returns) and getVipEffectiveProductsAndStats (checked 2026-11-17: products
                # with visitors per selected = the summary's totalVisitProductCount, Aug 222 / Sep 17;
                # report analytics_periods.visit_products_match re-checks it on every run).
                latest = dt.date.today().replace(day=1) - dt.timedelta(days=1)
                m = a.start_d.replace(day=1)
                while m <= a.end_d.replace(day=1):
                    base = int(x.get("selectedBase", 0))
                    back = (latest.year * 12 + latest.month) - (m.year * 12 + m.month) + base
                    if back >= base:
                        extra.append({**x, "tag": "%s.%s" % (x["tag"], m.strftime("%Y-%m")), "params": {**x["params"], "selected": str(back)}})
                    m = (m + dt.timedelta(days=32)).replace(day=1)
            extra.append(x)
        params = {"key": pg["key"], "url": pg["url"], "outFile": out_file, "loginId": acc["loginId"],
                  "start": a.start_d.isoformat(), "end": a.end_d.isoformat(), "maxPages": a.max_pages,
                  "rangeReplay": bool(pg.get("rangeReplay")), "rangeTables": range_tables(pg, a),
                  "rangeResume": resume_state if resume_state is not None else (range_resume(previous) if a.resume else {}),
                  "extra": extra, "inquiryList": bool(pg.get("inquiryList")), "budgetMs": 105000}
        if pg.get("inquiryList") and (a.lookup_inquiry_id or a.lookup_trade_id):
            params["lookup"] = {"inquiryId": a.lookup_inquiry_id, "tradeId": a.lookup_trade_id}
        with open(os.path.join(HERE, "capture.js"), encoding="utf-8") as fh:
            src = fh.read().replace("__PARAMS__", json.dumps(params))
        while True:
            res = run_once(src, out_file, pg["key"], acc, session)
            if session["runner"] == "aside" or not login_expired(res):
                return res
            if session["refreshed"]:
                res["status"] = "login_expired"
                return res
            # Saved cookies went stale: read them again from the source browser, once per run.
            session["refreshed"] = True
            try:
                session["cookies"] = cookie_store.refresh_from_browser(acc)
            except secret_store.StoreError as e:  # no source browser, browser closed, wrong seller, keychain: login is needed
                session["cookies"] = None
                res["detail"] = "cookie re-read failed: %s" % type(e).__name__
            if not session["cookies"]:
                res["status"] = "login_expired"
                return res
            if os.path.exists(out_file):
                os.remove(out_file)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def aside_home():
    """Aside data folder: ALIBABA_SELLER_ASIDE_HOME, else ~/.aside (same layout on macOS and Windows)."""
    return os.environ.get("ALIBABA_SELLER_ASIDE_HOME") or os.path.join(os.path.expanduser("~"), ".aside")


ASIDE_FALLBACKS = ("~/.local/bin/aside", "/opt/homebrew/bin/aside", "/usr/local/bin/aside")


def aside_exe(env=None, which=shutil.which, exists=os.path.isfile):
    """Full path of the aside command (Windows npm installs aside.cmd, which needs the exact path).
    ALIBABA_SELLER_ASIDE overrides; then PATH; then the usual install spots, because agent and
    launchd shells often start without ~/.local/bin on PATH."""
    env = os.environ if env is None else env
    if env.get("ALIBABA_SELLER_ASIDE"):
        return env["ALIBABA_SELLER_ASIDE"]
    found = which("aside")
    if found:
        return found
    for cand in ASIDE_FALLBACKS:
        full = os.path.expanduser(cand)
        if exists(full):
            return full
    return "aside"


def login(acc):
    """Keychain password -> this process -> Aside stdin -> password box. Never printed or saved."""
    svc, user = acc.get("keychainService"), acc.get("keychainAccount")
    if not svc or not user:
        return "no_keychain_config"
    try:
        pw = secret_store.get_secret(svc, user)
    except secret_store.KeychainTimeout as e:
        return "keychain_timeout: %s" % e
    except secret_store.StoreError as e:
        return "secret_store_error: %s" % e
    if not pw:
        return "keychain_item_missing"
    with open(os.path.join(HERE, "login.js"), encoding="utf-8") as fh:
        src = fh.read().replace("__PARAMS__", json.dumps({"email": user, "pw": pw, "loginId": acc["loginId"]}))
    try:
        command = "await eval(%s)\n" % json.dumps("(async () => {\n" + src + "\n})()")
        proc = subprocess.run([aside_exe(), "--host", "local", "--account", acc["asideAccount"], "repl"], input=command, capture_output=True, text=True, timeout=170)
        text = (proc.stdout + proc.stderr).replace(pw, "***")
    finally:
        del src, command, pw
    line = next((l for l in text.splitlines() if l.startswith("STATUS ")), "")
    return line[7:] if line else "aside_failed: " + text.strip()[-200:]


# -- post-processing -------------------------------------------------------------
def jparse(text):
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        m = re.match(r"^[\w$.]+\(([\s\S]*)\)\s*;?\s*$", text.strip())
        if m:
            try:
                return json.loads(m.group(1))
            except ValueError:
                return None
    return None


def object_arrays(o, path="$", depth=0):
    """Yield (path, list_of_dicts) for every array of objects in o."""
    if depth > 8:
        return
    if isinstance(o, list):
        if o and all(isinstance(x, dict) for x in o):
            yield path, o
        for i, x in enumerate(o[:1]):
            yield from object_arrays(x, path + "[0]", depth + 1)
    elif isinstance(o, dict):
        for k, v in o.items():
            yield from object_arrays(v, path + "." + k, depth + 1)


def flatten(row):
    out = {}
    for k, v in row.items():
        out[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
    return out


def slug(s, n=60):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:n] or "x"


def range_tables(pg, a):
    tables = pg.get("rangeTables") or []
    want = {x.strip() for x in (a.tables or "").split(",") if x.strip()}
    return [t for t in tables if t["key"] in want] if want else tables


def page_meta(pg, a):
    """What a cached page was fetched with, so the CLI can reuse it only for the same request."""
    meta = {"fetchedAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "granularity": a.granularity}
    if pg.get("rangeTables"):
        meta["rangeTables"] = [t["key"] for t in range_tables(pg, a)]
        meta["rangeTablesFiltered"] = bool(a.tables)
    if pg["key"] == "keyword-index":
        meta.update({"seeds": a.seeds, "nd": a.kw_nd, "kwPages": a.kw_pages})
    return meta


def write_page(res, out_dir):
    """raw/<page>.jsonl = every record as captured. tables/<page>/<name>.csv = every array of
    objects; arrays from the pages of one replay (same tag/table + path) go into one CSV."""
    key = res.get("key") or "page"
    files, endpoints, groups = [], {}, {}
    raw_path = os.path.join(out_dir, "raw", key + ".jsonl")
    os.makedirs(os.path.dirname(raw_path), exist_ok=True)
    with open(raw_path, "w", encoding="utf-8") as fh:
        for r in res.get("records", []):
            j = jparse(r.get("text"))
            line = {k: r.get(k) for k in ("seq", "via", "method", "url", "reqContentType", "body", "status", "ct", "truncated", "replay", "error")}
            if j is not None:
                line["json"] = j
            else:
                line["text"] = r.get("text")
            fh.write(REDACT_JSON.sub(r"\1***", REDACT.sub(r"\1***", json.dumps(line, ensure_ascii=False))) + "\n")
            ep = (r.get("method") or "") + " " + (r.get("url") or "").split("?")[0]
            endpoints[ep] = endpoints.get(ep, 0) + 1
            if j is None:
                continue
            rp = r.get("replay") or {}
            label = rp.get("tag") or rp.get("table")
            if label and rp.get("kind") == "range":
                label = "range-" + label
            if not label:
                m = re.search(r"[?&]iName=([^&]+)", r.get("url") or "") or re.search(r"(?:^|&)iName=([^&]+)", r.get("body") or "")
                label = "%04d-%s" % (r.get("seq", 0), (m.group(1) if m else ep.split("/")[-1]))
                if rp.get("kind") == "page":
                    label = "page-" + label
            for path, rows in object_arrays(j):
                g = groups.setdefault((slug(label, 70), slug(path, 60)), [])
                for row in rows:
                    g.append({"_seq": r.get("seq"), "_page": rp.get("page", 1), **flatten(row)})
    tdir = os.path.join(out_dir, "tables", key)
    for (label, path), rows in groups.items():
        os.makedirs(tdir, exist_ok=True)
        cols = []
        for row in rows:
            for c in row:
                if c not in cols:
                    cols.append(c)
        tp = os.path.join(tdir, "%s__%s.csv" % (label, path))
        with open(tp, "w", newline="", encoding="utf-8") as tf:
            w = csv.DictWriter(tf, fieldnames=cols)
            w.writeheader()
            for row in rows:
                w.writerow({c: (REDACT_JSON.sub(r"\1***", REDACT.sub(r"\1***", v)) if isinstance(v, str) else v) for c, v in row.items()})
        files.append(tp)
    files.append(raw_path)
    summary = {"key": key, "url": res.get("url"), "finalUrl": res.get("finalUrl"), "status": res.get("status"),
               "records": len(res.get("records", [])), "replays": sum(1 for r in res.get("records", []) if r.get("replay")),
               "tables": len(groups), "endpoints": endpoints, "notes": res.get("notes", []) + ([res["detail"]] if res.get("detail") else [])}
    return summary, files


def scan(paths):
    hits = []
    for p in paths:
        with open(p, errors="ignore", encoding="utf-8") as fh:
            t = fh.read()
        hits += ["%s:%s" % (os.path.relpath(p), r.pattern[:10]) for r in SECRET_RES if r.search(t)]
    return hits


def main(argv=None):
    a = parse_args(argv if argv is not None else sys.argv[1:])
    pages = load_pages()
    if a.list_pages:
        for pg in pages:
            print("%-28s %s%s" % (pg["key"], pg["url"], "  (ads range replay)" if pg.get("rangeReplay") else ""))
        return 0
    if not a.account:
        raise SystemExit("--account is required")
    acc = load_account(a.config, a.account)
    if a.login:
        st = login(acc)
        print(json.dumps({"account": a.account, "login": st}, ensure_ascii=False))
        return 0 if st in ("signed_in", "already_signed_in") else 69 if st.startswith("keychain_timeout") else 1
    if a.browser_login:
        try:
            rec = cookie_store.capture(acc, a.browser_login, a.profile, a.via_cdp)
        except secret_store.StoreError as e:
            print("오류: %s" % e, file=sys.stderr)
            return e.code
        print(json.dumps({"account": a.account, "login": "saved", **cookie_store.summary(rec)}, ensure_ascii=False))
        return 0
    if a.session_status:
        try:
            st = cookie_store.session_status(acc)
        except secret_store.StoreError as e:
            print("오류: %s" % e, file=sys.stderr)
            return e.code
        print(json.dumps({"account": a.account, **st}, ensure_ascii=False))
        return {"valid": 0, "expired": EXIT_SESSION, "none": EXIT_SESSION, "wrong_account": EXIT_ACCOUNT}.get(st["session"], 1)
    want = None if a.pages == "all" else {k.strip() for k in a.pages.split(",") if k.strip()}
    if want:
        unknown = want - {p["key"] for p in pages}
        if unknown:
            raise SystemExit("unknown page keys: %s (see --list-pages)" % ", ".join(sorted(unknown)))
        pages = [p for p in pages if p["key"] in want]
    if not a.seeds:
        skipped = [p["key"] for p in pages if p.get("seedOnly")]
        pages = [p for p in pages if not p.get("seedOnly")]
        if skipped and want:
            print("%-28s %-15s (시드 없음: --seeds 'a|b')" % (skipped[0], "skipped"), flush=True)
    session = choose_runner(a, acc)
    if session is None:
        return EXIT_LOGIN
    base = acc.get("outDir") or os.path.expanduser("~/30_Assets/%s/seller-report" % a.account)
    out_dir = a.out or os.path.join(base, "sellercentral-raw-%s" % a.end_d.isoformat())
    os.makedirs(out_dir, exist_ok=True)
    manifest = {"account": a.account, "start": a.start_d.isoformat(), "end": a.end_d.isoformat(),
                "generatedAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "pages": []}
    files, code = [], 0
    for pg in pages:
        previous = []
        prior_path = os.path.join(out_dir, "raw", pg["key"] + ".jsonl")
        if a.resume and os.path.isfile(prior_path):
            with open(prior_path, encoding="utf-8") as fh:
                previous = [json.loads(line) for line in fh if line.strip()]
        res = run_capture(pg, acc, a, previous, session)
        if pg.get("rangeReplay") and res.get("status") == "ok":
            # The capture has a ~105 s budget; big tables stop short. Keep what it got and rerun from
            # the next page until every range table reaches totalRows (or a run adds nothing).
            got = previous + res.get("records", [])
            for n in range(1, max(1, a.max_passes)):
                state = range_state(got)
                left = incomplete_ranges(state, pg, a)
                if not left:
                    break
                covered = sum(v["covered"] for v in state.values())
                print("%-28s %-15s pass=%d tables_left=%d rows=%d" % (pg["key"], "continue", n + 1, len(left), covered), flush=True)
                more = run_capture(pg, acc, a, got, session, resume_state=state)
                if more.get("status") != "ok":
                    if more.get("status") in ("login_expired", "bot_blocked"):
                        res["status"] = more["status"]
                    break
                got += more.get("records", [])
                if sum(v["covered"] for v in range_state(got).values()) <= covered:
                    break
            res["records"] = got[len(previous):]
        if previous:
            for record in previous:
                if "json" in record:
                    record["text"] = json.dumps(record.pop("json"), ensure_ascii=False)
            res["records"] = previous + res.get("records", [])
            for index, record in enumerate(res["records"]):
                record["seq"] = index
        st = res.get("status")
        summary, fs = write_page(res, out_dir)
        summary.update(page_meta(pg, a))
        if pg.get("rangeReplay"):
            short = incomplete_ranges(range_state(res.get("records", [])), pg, a)
            summary["rangeIncomplete"] = len(short)
            if short:
                print("%-28s %-15s range tables short of totalRows: %d (rerun continues them)" % (pg["key"], "incomplete", len(short)), flush=True)
        manifest["pages"].append(summary)
        files += fs
        print("%-28s %-15s records=%s replays=%s tables=%s" % (pg["key"], st, summary["records"], summary["replays"], summary["tables"]), flush=True)
        if st == "session_expired":
            code = EXIT_SESSION
            break
        if st == "wrong_account":
            code = EXIT_ACCOUNT
            break
        if st == "login_expired":
            code = EXIT_LOGIN
            break
        if st == "bot_blocked":
            code = EXIT_BLOCKED
            break
    mp = os.path.join(out_dir, "manifest.json")
    if os.path.exists(mp):  # rerun of some pages into an existing folder: keep the other pages' entries
        try:
            with open(mp, encoding="utf-8") as fh:
                prev = json.load(fh)
            done = {p["key"] for p in manifest["pages"]}
            manifest["pages"] = [p for p in prev.get("pages", []) if p.get("key") not in done] + manifest["pages"]
        except (OSError, ValueError):
            pass
    with open(mp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    files.append(mp)
    hits = scan(files)
    if hits:
        for f in files:
            os.remove(f)
        print("ABORT: token-like strings found, outputs removed: %s" % ", ".join(hits[:10]), file=sys.stderr)
        return EXIT_SECRET
    print(json.dumps({"out": out_dir, "pages": len(manifest["pages"]),
                      "ok": sum(1 for p in manifest["pages"] if p["status"] == "ok"),
                      "records": sum(p["records"] for p in manifest["pages"]),
                      "tables": sum(p["tables"] for p in manifest["pages"])}, ensure_ascii=False))
    if os.path.exists(os.path.join(out_dir, "raw", "inquiries.jsonl")):
        import inquiry_attribution
        inquiry_attribution.main([out_dir])
    stop = {"session_expired": "session expired -> run with --login, then rerun",
            "wrong_account": "the browser is signed in to a different seller than loginId -> sign in as loginId, then rerun",
            "login_expired": "saved login cookies expired, still expired after one re-read from the source browser"
                             " -> sign in to Seller Central in that browser, then alibaba-data login --browser ...",
            "bot_blocked": "Alibaba answered with a bot check (slider/captcha) also in an off-screen window"
                           " -> rerun later, or --runner aside"}
    if code and st in stop:
        print(stop[st], file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
