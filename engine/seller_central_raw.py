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

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.expanduser("~/.aliops-ops/seller-report/accounts.json")
# Any query/form key with csrf/xsrf/token in its name (ctoken, _tb_token_, _csrf_token_, p_csrf, access_token, ...).
SECRET_RES = [re.compile(p, re.I) for p in (r"[\w-]*(?:csrf|xsrf|token)[\w-]*=[A-Za-z0-9]", r"_m_h5_tk(?:_enc)?=[A-Za-z0-9]",
                                               r"\"[\w-]*(?:csrf|xsrf)[\w-]*\\?\"\s*:\s*\\?\"[A-Za-z0-9]")]
REDACT = re.compile(r"((?:[\w-]*(?:csrf|xsrf|token)[\w-]*|_m_h5_tk(?:_enc)?)=)[^&\"'\s;\\]+", re.I)
REDACT_JSON = re.compile(r"(\\?\"[\w-]*(?:csrf|xsrf)[\w-]*\\?\"\s*:\s*\\?\")[^\"\\]+", re.I)
EXIT_SESSION, EXIT_ACCOUNT, EXIT_SECRET = 2, 3, 4


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--account", help="account name in the config file")
    p.add_argument("--start", help="YYYY-MM-DD for the ads report range replay (default: end - 29 days)")
    p.add_argument("--end", help="YYYY-MM-DD (default: yesterday)")
    p.add_argument("--pages", default="all", help="comma-separated page keys from pages.json, or 'all'")
    p.add_argument("--list-pages", action="store_true", help="print the page keys and exit")
    p.add_argument("--max-pages", type=int, default=30, help="max list pages replayed per endpoint")
    p.add_argument("--resume", action="store_true", help="retain existing records and continue unfinished ad range tables")
    p.add_argument("--config", default=DEFAULT_CONFIG)
    p.add_argument("--out", help="output directory (default: <outDir>/sellercentral-raw-<end>)")
    p.add_argument("--login", action="store_true", help="sign Aside in with the keychain password, then exit")
    p.add_argument("--granularity", choices=("week", "month", "both"), default="month",
                   help="data advisor product stats periods replayed over --start..--end (analytics-product page)")
    a = p.parse_args(argv)
    end = dt.date.fromisoformat(a.end) if a.end else dt.date.today() - dt.timedelta(days=1)
    start = dt.date.fromisoformat(a.start) if a.start else end - dt.timedelta(days=29)
    if start > end:
        p.error("--start is after --end")
    a.start_d, a.end_d = start, end
    return a


def load_pages():
    with open(os.path.join(HERE, "pages.json")) as fh:
        return json.load(fh)["pages"]


def load_account(path, name):
    with open(path) as fh:
        accounts = json.load(fh)
    if name not in accounts:
        raise SystemExit("account %r not in %s (have: %s)" % (name, path, ", ".join(sorted(accounts))))
    acc = dict(accounts[name])
    if not isinstance(acc.get("loginId"), str) or not acc["loginId"].strip():
        raise SystemExit("nonempty loginId is required")
    acc.setdefault("asideAccount", "u0")
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
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo("America/Los_Angeles")).date()


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


def run_capture(pg, acc, a, previous):
    work_root = os.path.expanduser("~/.aside/u/%s" % acc["asideAccount"][1:])
    os.makedirs(work_root, exist_ok=True)
    work = tempfile.mkdtemp(prefix="scraw-", dir=work_root)
    try:
        out_file = os.path.join(work, "out.json")
        extra = []
        for x in pg.get("extra") or []:
            if x.get("perWeek"):
                if a.granularity in ("week", "both"):
                    for week_start, back in week_selections(a.start_d, a.end_d, today_pst()):
                        extra.append({**x, "tag": "%s.%s" % (x["tag"], week_start.isoformat()), "params": {**x["params"], "selected": str(back + int(x.get("selectedBase", 0)))}})
            elif x.get("perMonth") and a.granularity in ("month", "both"):
                # one query per month in --start..--end. Data advisor picks the period with
                # selected=N months back from the latest complete month; statDate is ignored.
                # The count starts at selectedBase: 1 for getProdcutSummary (checked against the
                # statDate it returns), 0 for getVipEffectiveProductsAndStats (checked by product
                # totals: 2026-07 = 307 and 2026-08 = 17 in both).
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
                  "rangeReplay": bool(pg.get("rangeReplay")), "rangeTables": pg.get("rangeTables") or [],
                  "rangeResume": range_resume(previous) if a.resume else {},
                  "extra": extra, "inquiryList": bool(pg.get("inquiryList")), "budgetMs": 105000}
        with open(os.path.join(HERE, "capture.js")) as fh:
            src = fh.read().replace("__PARAMS__", json.dumps(params))
        try:
            proc = subprocess.run(["aside", "--host", "local", "--account", acc["asideAccount"], "repl", src], capture_output=True, text=True, timeout=170)
            tail = (proc.stdout + proc.stderr)[-400:]
        except subprocess.TimeoutExpired:
            tail = "aside_timeout"
        if not os.path.exists(out_file):
            return {"key": pg["key"], "status": "aside_failed", "detail": REDACT.sub(r"\1***", tail), "records": []}
        with open(out_file) as fh:
            res = json.load(fh)
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


def login(acc):
    """Keychain password -> this process -> Aside stdin -> password box. Never printed or saved."""
    svc, user = acc.get("keychainService"), acc.get("keychainAccount")
    if not svc or not user:
        return "no_keychain_config"
    kc = subprocess.run(["security", "find-generic-password", "-s", svc, "-a", user, "-w"], capture_output=True, text=True)
    if kc.returncode != 0:
        return "keychain_item_missing"
    pw = kc.stdout.rstrip("\n")
    with open(os.path.join(HERE, "login.js")) as fh:
        src = fh.read().replace("__PARAMS__", json.dumps({"email": user, "pw": pw, "loginId": acc["loginId"]}))
    try:
        command = "await eval(%s)\n" % json.dumps("(async () => {\n" + src + "\n})()")
        proc = subprocess.run(["aside", "--host", "local", "--account", acc["asideAccount"], "repl"], input=command, capture_output=True, text=True, timeout=170)
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


def write_page(res, out_dir):
    """raw/<page>.jsonl = every record as captured. tables/<page>/<name>.csv = every array of
    objects; arrays from the pages of one replay (same tag/table + path) go into one CSV."""
    key = res.get("key") or "page"
    files, endpoints, groups = [], {}, {}
    raw_path = os.path.join(out_dir, "raw", key + ".jsonl")
    os.makedirs(os.path.dirname(raw_path), exist_ok=True)
    with open(raw_path, "w") as fh:
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
        with open(tp, "w", newline="") as tf:
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
        with open(p, errors="ignore") as fh:
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
        return 0 if st in ("signed_in", "already_signed_in") else 1
    want = None if a.pages == "all" else {k.strip() for k in a.pages.split(",") if k.strip()}
    if want:
        unknown = want - {p["key"] for p in pages}
        if unknown:
            raise SystemExit("unknown page keys: %s (see --list-pages)" % ", ".join(sorted(unknown)))
        pages = [p for p in pages if p["key"] in want]
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
            with open(prior_path) as fh:
                previous = [json.loads(line) for line in fh if line.strip()]
        res = run_capture(pg, acc, a, previous)
        if previous:
            for record in previous:
                if "json" in record:
                    record["text"] = json.dumps(record.pop("json"), ensure_ascii=False)
            res["records"] = previous + res.get("records", [])
            for index, record in enumerate(res["records"]):
                record["seq"] = index
        st = res.get("status")
        summary, fs = write_page(res, out_dir)
        manifest["pages"].append(summary)
        files += fs
        print("%-28s %-15s records=%s replays=%s tables=%s" % (pg["key"], st, summary["records"], summary["replays"], summary["tables"]), flush=True)
        if st == "session_expired":
            code = EXIT_SESSION
            break
        if st == "wrong_account":
            code = EXIT_ACCOUNT
            break
    mp = os.path.join(out_dir, "manifest.json")
    if os.path.exists(mp):  # rerun of some pages into an existing folder: keep the other pages' entries
        try:
            with open(mp) as fh:
                prev = json.load(fh)
            done = {p["key"] for p in manifest["pages"]}
            manifest["pages"] = [p for p in prev.get("pages", []) if p.get("key") not in done] + manifest["pages"]
        except (OSError, ValueError):
            pass
    with open(mp, "w") as fh:
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
    if code == EXIT_SESSION:
        print("session expired -> run with --login, then rerun", file=sys.stderr)
    elif code == EXIT_ACCOUNT:
        print("Aside is signed in to a different seller than loginId -> sign out in Aside, then --login", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
