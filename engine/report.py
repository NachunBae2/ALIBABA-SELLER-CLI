#!/usr/bin/env python3
"""Read-only queries over one or more seller_central_raw.py dump folders.

  python3 report.py <command> --from DIR [--from DIR ...] [--start D --end D] [--format table|json|csv] [--out DIR]

Commands: inquiry-list inquiry-stats inquiry-by-product inquiry-fields ads-report ads-products
          keywords bids ads-analyze coverage

Several --from folders (date windows of one long range) are merged: rows are de-duplicated by
their identity (table, product, campaign, keyword, day; inquiry id). Rows dated outside
--start..--end are dropped. Every output row carries query_start / query_end.
No buyer name, subject, message text, email or phone exists in a dump (capture.js allowlist),
so none can reach these outputs.
"""
import argparse
import collections
import csv
import datetime as dt
import functools
import io
import json
import os
import re
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inquiry_attribution as ia  # noqa: E402

PRODUCT_TABLES = ("component_ggs_whole_site_offline_product", "component_ggs_offline_product")
CAMPAIGN_TABLES = ("component_ggs_whole_site_offline_campaign",)
ACCOUNT_TABLES = ("component_ggs_whole_site_offline_account", "component_ggs_offline_account")
IDENTITY = ("productId", "campaignId", "keyword", "adGroupId", "timeUs")
DATE_KEYS = ("timeUs", "date", "statDate", "day", "dt")
MIN_RATE_CLICKS = 20  # a click->conversion rate needs this many clicks before it is used for a prediction
LEVEL_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}


def fnum(v):
    v = ia.main_val(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def r2(x):
    return None if x is None else round(x, 2)


def div(a, b):
    return a / b if b else None


def inquiry_economics(spend, clicks, inquiries, spend_source, inquiries_source):
    """Owner definitions: conversion_rate = inquiries / clicks; cost_per_inquiry = spend / inquiries,
    also computed as CPC / conversion_rate. Both stay when they differ. No inquiry -> rate 0, cost blank."""
    cpc = div(spend, clicks) if spend is not None else None
    out = {"cpc_usd": r2(cpc), "cpc_source": ("%s ÷ clicks" % spend_source) if cpc is not None else "광고비 또는 클릭 없음",
           "conversion_rate": round(inquiries / clicks, 4) if clicks else 0.0,
           "conversion_rate_source": "%s ÷ clicks" % inquiries_source}
    if not inquiries:
        return {**out, "conversion_rate": 0.0, "cost_per_inquiry_usd": None, "cost_per_inquiry_by_cpc_usd": None, "cost_per_inquiry_source": "no_inquiry"}
    by_spend = div(spend, inquiries) if spend is not None else None
    by_cpc = div(cpc, inquiries / clicks) if cpc is not None and clicks else None
    same = by_spend is not None and by_cpc is not None and abs(by_spend - by_cpc) < 0.005
    return {**out, "cost_per_inquiry_usd": r2(by_spend), "cost_per_inquiry_by_cpc_usd": r2(by_cpc),
            "cost_per_inquiry_source": "%s ÷ %s%s" % (spend_source, inquiries_source, " (CPC÷전환율과 같음)" if same else " (CPC÷전환율 별도 열)")}


class Dump:
    """One merged view over dump folders, limited to start..end (inclusive ISO dates)."""

    def __init__(self, dirs, start=None, end=None):
        self.dirs = dirs
        manifests = [self._manifest(d) for d in dirs]
        self.start = start or min((m.get("start") for m in manifests if m.get("start")), default="")
        self.end = end or max((m.get("end") for m in manifests if m.get("end")), default="")

    @staticmethod
    def _manifest(d):
        try:
            with open(os.path.join(d, "manifest.json")) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def in_range(self, day):
        day = (day or "")[:10]
        return not day or ((not self.start or day >= self.start) and (not self.end or day <= self.end))

    def records(self, page):
        for d in self.dirs:
            yield from ia.rows(os.path.join(d, "raw", page + ".jsonl"))

    def pages(self):
        names = set()
        for d in self.dirs:
            raw = os.path.join(d, "raw")
            if os.path.isdir(raw):
                names |= {f[:-6] for f in os.listdir(raw) if f.endswith(".jsonl")}
        return sorted(names)

    # -- ads report tables ------------------------------------------------------------
    @functools.cached_property
    def ad_tables(self):
        """{table: [row with plain values]} from ads-report range replays and page loads."""
        out, seen, meta = collections.defaultdict(list), set(), collections.defaultdict(lambda: {"reported_total": 0, "responses": 0})
        for r in self.records("ads-report"):
            if "dynamic/component/data" not in (r.get("url") or ""):
                continue
            q = (((r.get("json") or {}).get("data") or {}).get("queryResult")) or {}
            rp = r.get("replay") or {}
            table = rp.get("table") or self._table_of(r)
            if not table:
                continue
            meta[table]["responses"] += 1
            meta[table]["reported_total"] = max(meta[table]["reported_total"], int(fnum(q.get("totalRows"))))
            for d in q.get("detailList") or []:
                row = {k: ia.main_val(v) for k, v in d.items()}
                ident = tuple(str(row.get(k, "")) for k in IDENTITY)
                key = (table,) + (ident if any(ident) else (json.dumps(row, sort_keys=True),))
                if key in seen or not self.in_range(row.get("timeUs")):
                    continue
                seen.add(key)
                out[table].append(row)
        self.ad_table_meta = meta
        return out

    @staticmethod
    def _table_of(r):
        from urllib.parse import parse_qs
        try:
            return json.loads(parse_qs(r.get("body") or "").get("data", ["{}"])[0]).get("tableName")
        except ValueError:
            return None

    def rows_of(self, tables):
        return [(t, row) for t in tables for row in self.ad_tables.get(t, [])]

    # -- campaigns ------------------------------------------------------------------------
    @functools.cached_property
    def campaigns(self):
        """{campaign id: config} from campaign list responses (ads-campaigns, ads-home)."""
        out = {}
        for page in ("ads-campaigns", "ads-home"):
            for r in self.records(page):
                data = (r.get("json") or {}).get("data")
                if not isinstance(data, dict):
                    continue
                for ctype, items in data.items():
                    for c in items if isinstance(items, list) else []:
                        if not isinstance(c, dict) or "id" not in c or "properties" not in c:
                            continue
                        p = c.get("properties") or {}
                        bc = p.get("bidConfig") or {}
                        target = str(p.get("optimizeTarget") or bc.get("targetBidEffectType") or "")
                        out[str(c["id"])] = {
                            "campaign_id": str(c["id"]), "campaign_type": ctype, "title": c.get("title"),
                            "sub_type": c.get("subType"), "online_status": c.get("onlineStatus"),
                            "bid_type": p.get("bidType"), "optimize_target": target,
                            "bid_usd": p.get("maxPrice") if p.get("maxPrice") is not None else c.get("bidRate"),
                            "bid_field": "properties.maxPrice" if p.get("maxPrice") is not None else "bidRate",
                            "bid_unit": "전환 1건 목표가" if target.endswith("fb") else "클릭당",
                            "budget_usd": p.get("budget"), "start": c.get("startTime"), "end": c.get("endTime"),
                        }
        return out

    # -- inquiries --------------------------------------------------------------------------
    @functools.cached_property
    def inquiries_all(self):
        out = {}
        for r in self.records("inquiries"):
            if (r.get("replay") or {}).get("kind") == "inquiry":
                for it in (r.get("json") or {}).get("list") or []:
                    out[str(it.get("id"))] = it
        return list(out.values())

    @functools.cached_property
    def inquiry_field_names(self):
        names = set()
        for r in self.records("inquiries"):
            names |= set((r.get("json") or {}).get("fieldNames") or [])
        return sorted(names)

    @functools.cached_property
    def ad_days(self):
        days, titles = collections.defaultdict(collections.Counter), {}
        for _t, row in self.rows_of(PRODUCT_TABLES):
            pid, day = str(row.get("productId") or ""), str(row.get("timeUs") or "")[:10]
            if not pid or not day:
                continue
            c = days[(pid, day)]
            for col, name in (("impsCnt", "ad_impressions"), ("clickCnt", "ad_clicks"), ("mcCnt", "ad_inquiries"),
                              ("atmSessionCnt", "ad_tm_chats"), ("orderCnt", "ad_orders")):
                c[name] += fnum(row.get(col))
            if row.get("subject"):
                titles[pid] = row["subject"]
        return days, titles

    @functools.cached_property
    def titles(self):
        titles = dict(self.ad_days[1])
        for r in self.records("analytics-product"):
            if ((r.get("replay") or {}).get("tag") or "").startswith("product_stats"):
                for p in ((((r.get("json") or {}).get("value") or {}).get("products") or {}).get("data")) or []:
                    if p.get("subject"):
                        titles.setdefault(str(p.get("id")), p["subject"])
        return titles

    @functools.cached_property
    def inquiries(self):
        """Allowlisted inquiry rows in range, each matched to products like inquiry_attribution."""
        by_day = ia.ad_days_by_day(self.ad_days[0])
        out = []
        for it in sorted(self.inquiries_all, key=lambda x: x.get("createTime") or 0):
            t, day, direct, match, prods = ia.match_inquiry(it, by_day)
            if not self.in_range(day):
                continue
            lead_type, entry, device, ad = ia.lead_source(it, match)
            out.append({
                "inquiry_id": str(it.get("id")), "trade_id": str(it.get("tradeId") or ""),
                "created_us_pacific": t.isoformat(timespec="minutes") if t else "", "us_date": day,
                "lead_type": lead_type, "type_code": it.get("feedbackType"), "lead_entry": entry, "source_code": it.get("source"),
                "device": device, "product_id": direct, "inflow": ia.inflow_of(it, direct),
                "buyer_country": it.get("buyerCountry") or "", "buyer_level": buyer_level(it),
                "reply_status": it.get("replyStatus"), "unread": bool(it.get("hasUnread") or fnum(it.get("unreadCount")) > 0),
                "spam": bool(it.get("spam")), "match": match, "ad": ad, "matched_products": " ".join(prods),
            })
        return out

    # -- keywords -----------------------------------------------------------------------------
    def months_in_range(self):
        if not self.start or not self.end:
            return None
        s, e = self.start[:7], self.end[:7]
        return lambda m: s <= m <= e

    @functools.cached_property
    def keyword_effect(self):
        """Per product x keyword search views/clicks from data advisor product stats, per month in range."""
        want = self.months_in_range()
        rows, seen = [], set()
        for r in self.records("analytics-product"):
            tag = (r.get("replay") or {}).get("tag") or ""
            if not tag.startswith("product_stats.month."):
                continue
            month = tag.rsplit(".", 1)[1]
            if want and not want(month):
                continue
            for p in ((((r.get("json") or {}).get("value") or {}).get("products") or {}).get("data")) or []:
                for k in p.get("keywordEffect") or []:
                    key = (month, str(p.get("id")), k.get("keyword"))
                    if key in seen or not k.get("keyword"):
                        continue
                    seen.add(key)
                    rows.append({"month": month, "product_id": str(p.get("id")), "keyword": k.get("keyword"),
                                 "views": fnum(k.get("views")), "clicks": fnum(k.get("clicks")),
                                 "ad_views": fnum(k.get("p4pViews")), "ad_clicks": fnum(k.get("p4pClicks")),
                                 "is_ad_keyword": bool(k.get("isP4pKeyword")), "ad_campaign_status": k.get("p4pCampaignStatus")})
        return rows

    @functools.cached_property
    def analytics_keywords(self):
        rows = []
        for r in self.records("analytics-keyword"):
            if "getKeywords" not in (r.get("url") or ""):
                continue
            for k in ((((r.get("json") or {}).get("data") or {}).get("keywords") or {}).get("data")) or []:
                if isinstance(k, dict):
                    rows.append({kk: ia.main_val(v) for kk, v in k.items()})
        return rows

    # -- product cost tables anywhere in the dump ----------------------------------------------
    @functools.cached_property
    def product_cost_tables(self):
        """Every array of objects that has a product id and a cost field: {(page, endpoint): info}."""
        found = {}
        for page in self.pages():
            for r in self.records(page):
                j = r.get("json")
                if j is None:
                    continue
                for path, arr in _object_arrays(j):
                    first = {k: ia.main_val(v) for k, v in arr[0].items()}
                    if "productId" not in first or "cost" not in first:
                        continue
                    date_key = next((k for k in DATE_KEYS if re.match(r"^\d{4}-\d{2}-\d{2}", str(first.get(k) or ""))), None)
                    key = (page, (r.get("url") or "").split("?")[0] + " " + path)
                    info = found.setdefault(key, {"page": page, "endpoint": key[1], "date_key": date_key, "rows": [], "seen": set()})
                    for row in arr:
                        row = {k: ia.main_val(v) for k, v in row.items()}
                        ident = (str(row.get("productId")), str(row.get(date_key) if date_key else ""), str(row.get("campaignId", "")))
                        if ident in info["seen"]:
                            continue
                        info["seen"].add(ident)
                        info["rows"].append(row)
        return found


def _object_arrays(o, path="$", depth=0):
    if depth > 8:
        return
    if isinstance(o, list):
        if o and all(isinstance(x, dict) for x in o):
            yield path, o
        for x in o[:1]:
            yield from _object_arrays(x, path + "[]", depth + 1)
    elif isinstance(o, dict):
        for k, v in o.items():
            yield from _object_arrays(v, path + "." + k, depth + 1)


def buyer_level(it):
    if it.get("buyerLevel"):
        return str(it["buyerLevel"])
    for k, v in sorted((it.get("buyerAttrs") or {}).items()):
        s = str(v).upper()
        if s in LEVEL_RANK:
            return s
        if s.isdigit() and int(s) <= 4:
            return "L" + s
    return ""


def count_str(counter):
    return " ".join("%s:%d" % (k or "?", n) for k, n in counter.most_common())


# -- ad spend and conversions per product ------------------------------------------------------
def daily_cpc(dump):
    """{US date: (cpc, source label)}: campaign daily cost/clicks, else account daily, else range average."""
    by = {}
    for label, tables in (("component_ggs_whole_site_offline_campaign", CAMPAIGN_TABLES),
                          ("account", ACCOUNT_TABLES)):
        sums = collections.defaultdict(lambda: [0.0, 0.0])
        for t, row in dump.rows_of(tables):
            day = str(row.get("timeUs") or "")[:10]
            sums[(t, day)][0] += fnum(row.get("cost"))
            sums[(t, day)][1] += fnum(row.get("clickCnt"))
        for (t, day), (cost, clicks) in sums.items():
            if clicks and day not in by:
                by[day] = (cost / clicks, "%s 일별 cost÷clickCnt" % t)
    total_cost = sum(fnum(r.get("cost")) for _t, r in dump.rows_of(CAMPAIGN_TABLES))
    total_clicks = sum(fnum(r.get("clickCnt")) for _t, r in dump.rows_of(CAMPAIGN_TABLES))
    fallback = (total_cost / total_clicks, "캠페인 기간 평균 cost÷clickCnt") if total_clicks else (None, "")
    return by, fallback


def campaign_days(dump):
    """{US date: {campaign ids with cost that day}}."""
    out = collections.defaultdict(set)
    for _t, row in dump.rows_of(CAMPAIGN_TABLES):
        if fnum(row.get("cost")) > 0:
            out[str(row.get("timeUs") or "")[:10]].add(str(row.get("campaignId")))
    return out


def product_performance(dump):
    cpc, fallback = daily_cpc(dump)
    camp_days = campaign_days(dump)
    agg = collections.defaultdict(lambda: collections.defaultdict(float))
    est_sources, camps = collections.defaultdict(set), collections.defaultdict(set)
    for _t, row in dump.rows_of(PRODUCT_TABLES):
        pid, day = str(row.get("productId") or ""), str(row.get("timeUs") or "")[:10]
        if not pid:
            continue
        a = agg[pid]
        clicks = fnum(row.get("clickCnt"))
        for col in ("impsCnt", "clickCnt", "mcCnt", "atmSessionCnt", "cvr3", "l1PlusCvr3", "l3PlusCvr3", "orderCnt"):
            a[col] += fnum(row.get(col))
        if clicks:
            rate, src = cpc.get(day) or fallback
            if rate is not None:
                a["est_cost"] += clicks * rate
                est_sources[pid].add(src)
            camps[pid] |= camp_days.get(day, set())

    actual, actual_src, undated = collections.defaultdict(float), {}, []
    for info in dump.product_cost_tables.values():
        if not info["date_key"]:
            undated.append(info)
            continue
        for row in info["rows"]:
            if dump.in_range(row.get(info["date_key"])):
                pid = str(row.get("productId"))
                actual[pid] += fnum(row.get("cost"))
                actual_src[pid] = "%s %s.cost" % (info["page"], info["endpoint"])

    out = []
    for pid in sorted(set(agg) | set(actual)):
        a = agg[pid]
        if pid not in actual_src and not (a["impsCnt"] or a["clickCnt"] or a["cvr3"]):
            continue
        conv = a["cvr3"]
        spend_actual = actual.get(pid) if pid in actual_src else None
        spend_est = a["est_cost"] if est_sources[pid] else None
        basis_spend, basis = (spend_actual, "실측") if spend_actual is not None else (spend_est, "추정")
        econ = inquiry_economics(basis_spend, a["clickCnt"], conv, "광고비(%s)" % basis, "inquiries(cvr3)")
        bids = sorted({dump.campaigns[c]["bid_usd"] for c in camps[pid] if c in dump.campaigns and dump.campaigns[c]["bid_usd"] is not None})
        out.append({
            "query_start": dump.start, "query_end": dump.end, "product_id": pid, "product_title": dump.titles.get(pid, ""),
            "spend_actual_usd": r2(spend_actual), "spend_actual_source": actual_src.get(pid, "없음: 기간 있는 productId+cost 표 없음"),
            "spend_estimated_usd": r2(spend_est),
            "spend_estimated_source": ("추정: " + " / ".join(sorted(est_sources[pid])) + " × 상품 clickCnt") if est_sources[pid] else "",
            "impressions": int(a["impsCnt"]), "clicks": int(a["clickCnt"]), "ctr_pct": r2(div(a["clickCnt"] * 100, a["impsCnt"])),
            "ad_inquiries_mcCnt": int(a["mcCnt"]), "ad_tm_chats_atmSessionCnt": int(a["atmSessionCnt"]),
            "inquiries_cvr3": int(conv), "inquiries_source": "%s cvr3 = 비즈니스 기회(전환). mcCnt·atmSessionCnt 는 그 내역" % PRODUCT_TABLES[0],
            **econ, "cost_basis": basis if basis_spend is not None else "",
            "l3plus_inquiries": int(a["l3PlusCvr3"]), "l1plus_inquiries": int(a["l1PlusCvr3"]),
            "l3plus_share_pct": r2(div(a["l3PlusCvr3"] * 100, conv)), "l3plus_source": "광고 상품표 l3PlusCvr3",
            "orders": int(a["orderCnt"]),
            "bid_usd": (bids[0] if len(bids) == 1 else ("%s~%s" % (bids[0], bids[-1]) if bids else None)),
            "bid_source": ("ads-campaigns properties.maxPrice (캠페인 %s, 상품 클릭일에 비용 난 캠페인)" % " ".join(sorted(camps[pid]))) if bids else "",
        })
    out.sort(key=lambda r: (-(r["inquiries_cvr3"]), -(r["clicks"])))
    return out, undated


def inquiry_quality(dump):
    groups = collections.defaultdict(list)
    for q in dump.inquiries:
        if q["product_id"]:
            groups[(q["product_id"], "직접(상품 문의)")].append(q)
        elif q["match"] == "ad-day-only":
            groups[(q["matched_products"], "광고일 추정(그날 광고 대화 상품 1개)")].append(q)
        elif q["match"] == "ad-day-candidates":
            for pid in q["matched_products"].split():
                groups[(pid, "광고일 후보(여러 상품 중 하나)")].append(q)
    out = []
    for (pid, link), qs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        levels = collections.Counter(q["buyer_level"] or "미수집" for q in qs)
        known = [q["buyer_level"] for q in qs if q["buyer_level"] in LEVEL_RANK]
        out.append({
            "query_start": dump.start, "query_end": dump.end, "product_id": pid, "product_title": dump.titles.get(pid, ""),
            "link": link, "inquiries": len(qs), "buyer_levels": count_str(levels),
            "l3plus_share_pct": r2(div(sum(1 for l in known if LEVEL_RANK[l] >= 3) * 100, len(known))) if known else None,
            "countries": count_str(collections.Counter(q["buyer_country"] for q in qs)),
            "spam": sum(q["spam"] for q in qs), "reply_status": count_str(collections.Counter(str(q["reply_status"]) for q in qs)),
            "app": sum(q["device"] == "앱" for q in qs), "web": sum(q["device"] == "웹" for q in qs),
            "source": "inquiries subjectList (허용 필드만) + 광고 상품표 날짜 매칭",
        })
    return out


def bid_analysis(dump, products):
    out = []
    camp_rows = collections.defaultdict(lambda: collections.defaultdict(float))
    for _t, row in dump.rows_of(CAMPAIGN_TABLES):
        c = camp_rows[str(row.get("campaignId"))]
        for col in ("cost", "clickCnt", "cvr3", "l3PlusCvr3"):
            c[col] += fnum(row.get(col))
    tot_clicks = sum(p["clicks"] for p in products)
    tot_conv = sum(p["inquiries_cvr3"] for p in products)
    account_rate = div(tot_conv, tot_clicks)
    tot_spend = sum((p["spend_actual_usd"] if p["spend_actual_usd"] is not None else p["spend_estimated_usd"] or 0) for p in products)
    account_cpc = div(tot_spend, tot_clicks)
    for cid in sorted(set(camp_rows) | set(dump.campaigns)):
        meta, c = dump.campaigns.get(cid, {}), camp_rows[cid]
        rate = div(c["cvr3"], c["clickCnt"])
        bid = meta.get("bid_usd")
        per_conv = meta.get("bid_unit") == "전환 1건 목표가"
        out.append({
            "query_start": dump.start, "query_end": dump.end, "level": "campaign", "campaign_id": cid, "keyword": "",
            "product_id": "", "title": meta.get("title", ""), "bid_usd": bid, "bid_unit": meta.get("bid_unit", ""),
            "bid_source": ("ads-campaigns " + meta["bid_field"]) if meta else "캠페인 설정 없음",
            "spend_usd": r2(c["cost"]), "spend_source": "실측: %s cost" % CAMPAIGN_TABLES[0], "clicks": int(c["clickCnt"]), "inquiries": int(c["cvr3"]),
            **inquiry_economics(c["cost"], c["clickCnt"], c["cvr3"], "광고비(실측 캠페인 cost)", "inquiries(cvr3)"), "rate_level": "campaign",
            "predicted_cost_per_inquiry_usd": r2(bid if per_conv else div(bid, rate)) if bid is not None else None,
            "prediction_rule": "목표 전환단가 그대로" if per_conv else "입찰가 ÷ 전환율",
        })
    prod = {p["product_id"]: p for p in products}
    kw_rows = []
    for t, row in dump.rows_of(tuple(t for t in dump.ad_tables if "keyword" in t)):
        kw_rows.append(("ads " + t, row.get("keyword"), str(row.get("productId") or ""), fnum(row.get("clickCnt")),
                        fnum(row.get("cvr3")) if "cvr3" in row else None, next((row[k] for k in row if "bid" in k.lower() and row[k] not in (None, "")), None)))
    agg = collections.defaultdict(lambda: [0.0, 0.0])
    for k in dump.keyword_effect:
        a = agg[(k["keyword"], k["product_id"])]
        a[0] += k["clicks"]
        a[1] += k["ad_clicks"]
    for (kw, pid), (clicks, ad_clicks) in agg.items():
        if clicks or ad_clicks:
            kw_rows.append(("analytics-product keywordEffect", kw, pid, clicks, None, None))
    for src, kw, pid, clicks, conv, bid in kw_rows:
        p = prod.get(pid)
        if conv is not None and clicks >= MIN_RATE_CLICKS and conv > 0:
            rate, level = conv / clicks, "keyword"
        elif p and p["clicks"] >= MIN_RATE_CLICKS and p["inquiries_cvr3"] > 0:
            rate, level = p["inquiries_cvr3"] / p["clicks"], "product"
        else:
            rate, level = account_rate, "account"
        if bid is not None:
            bid_v, bid_src = fnum(bid), src + " 입찰가 필드"
        else:
            p_spend = p and (p["spend_actual_usd"] if p["spend_actual_usd"] is not None else p["spend_estimated_usd"])
            bid_v = r2(div(p_spend, p["clicks"])) if p and p_spend else r2(account_cpc)
            bid_src = "입찰가 없음 → %s 평균 CPC(추정)" % ("상품" if p and p_spend else "계정")
        out.append({
            "query_start": dump.start, "query_end": dump.end, "level": "keyword", "campaign_id": "", "keyword": kw,
            "product_id": pid, "title": dump.titles.get(pid, ""), "bid_usd": bid_v, "bid_unit": "클릭당", "bid_source": bid_src,
            "spend_usd": "", "spend_source": "", "clicks": int(clicks), "inquiries": "" if conv is None else int(conv),
            "cpc_usd": bid_v, "cpc_source": bid_src, "conversion_rate": round(rate, 4) if rate else 0.0,
            "conversion_rate_source": "%s 수준 inquiries÷clicks" % level, "cost_per_inquiry_usd": None,
            "cost_per_inquiry_by_cpc_usd": r2(div(bid_v, rate)) if bid_v is not None else None,
            "cost_per_inquiry_source": "예측: 입찰가 ÷ 전환율" if rate else "no_inquiry", "rate_level": level,
            "predicted_cost_per_inquiry_usd": r2(div(bid_v, rate)) if bid_v is not None else None,
            "prediction_rule": "입찰가 ÷ 전환율(%s, 최소 클릭 %d)" % (level, MIN_RATE_CLICKS),
        })
    return out


def top_products(dump, products, quality):
    linked = collections.Counter()
    for q in quality:
        linked[q["product_id"]] += q["inquiries"]
    rows = [p for p in products if p["inquiries_cvr3"] > 0 and p["cost_per_inquiry_usd"] is not None]
    rows.sort(key=lambda p: (p["cost_per_inquiry_usd"], -p["inquiries_cvr3"]))
    out = []
    for i, p in enumerate(rows, 1):
        l3 = (p["l3plus_share_pct"] or 0) / 100
        out.append({
            "query_start": dump.start, "query_end": dump.end, "rank": i, "product_id": p["product_id"], "product_title": p["product_title"],
            "cost_per_inquiry_usd": p["cost_per_inquiry_usd"], "cost_basis": p["cost_basis"],
            "inquiries": p["inquiries_cvr3"], "l3plus_share_pct": p["l3plus_share_pct"],
            "linked_inquiries": linked[p["product_id"]],
            "quality_score": r2(100 * (0.6 * l3 + 0.4 * min(p["inquiries_cvr3"], 10) / 10)),
            "label": "제안(규칙: SKILL.md)",
        })
    return out


def summary_md(dump, products, top, undated):
    est_total = sum(p["spend_estimated_usd"] or 0 for p in products)
    camp_total = sum(fnum(r.get("cost")) for _t, r in dump.rows_of(CAMPAIGN_TABLES))
    lines = ["# 광고 분석 요약", "", "- 쿼리 기간(US 태평양 날짜): %s ~ %s" % (dump.start, dump.end),
             "- 캠페인 일별표 광고비 합: $%.2f · 상품별 추정 광고비 합: $%.2f (상품별 실측 광고비 표 %s)" % (camp_total, est_total, "있음" if any(p["spend_actual_usd"] is not None for p in products) else "없음"),
             "- 정의: conversion_rate = 문의(비즈니스 기회, 광고표 cvr3) ÷ 클릭. cost_per_inquiry = 광고비 ÷ 문의 = CPC ÷ conversion_rate. L3+ = l3PlusCvr3.", ""]
    if undated:
        lines.append("- 참고: 기간 없는 productId+cost 표 %d개(%s)는 기간을 알 수 없어 광고비에 쓰지 않음." % (len(undated), ", ".join(sorted({u["page"] for u in undated}))))
        lines.append("")
    lines.append("## 문의 1건 비용(cost_per_inquiry) 낮은 순 상위 10")
    lines.append("")
    by_id = {p["product_id"]: p for p in products}
    for t in top[:10]:
        p = by_id[t["product_id"]]
        spend = p["spend_actual_usd"] if p["spend_actual_usd"] is not None else p["spend_estimated_usd"]
        lines.append("- 상품 %s %s: 광고비 $%.2f(%s), 문의(전환) %d건, 문의 1건 $%.2f, L3+ 바이어 비율 %s%%, 연결 문의 %d건" % (
            t["product_id"], (t["product_title"] or "")[:50], spend, t["cost_basis"], t["inquiries"], t["cost_per_inquiry_usd"],
            "-" if t["l3plus_share_pct"] is None else t["l3plus_share_pct"], t["linked_inquiries"]))
    return "\n".join(lines) + "\n"


# -- commands ------------------------------------------------------------------------------------
def q_rows(dump, rows):
    return [{"query_start": dump.start, "query_end": dump.end, **r} for r in rows]


def cmd_inquiry_list(dump, a):
    rows = dump.inquiries
    if a.unread:
        rows = [r for r in rows if r["unread"]]
    if a.type:
        code = {"product": "1", "company": "2", "tm": "6"}[a.type]
        rows = [r for r in rows if str(r["type_code"]) == code]
    if a.country:
        rows = [r for r in rows if r["buyer_country"].upper() == a.country.upper()]
    if a.status is not None:
        rows = [r for r in rows if str(r["reply_status"]) == a.status]
    rows = list(reversed(rows))
    return {"inquiries": q_rows(dump, rows[: a.limit] if a.limit else rows)}


def cmd_inquiry_stats(dump, a):
    rows = []
    for dim in ("lead_type", "lead_entry", "device", "buyer_country", "reply_status", "us_date", "buyer_level", "spam"):
        for v, n in collections.Counter(str(q[dim]) for q in dump.inquiries).most_common():
            rows.append({"dimension": dim, "value": v, "inquiries": n})
    return {"inquiry_stats": q_rows(dump, rows)}


def cmd_inquiry_by_product(dump, a):
    counts = collections.Counter(q["inflow"] for q in dump.inquiries)
    rows = [{"inflow": k, "product_title": dump.titles.get(k, ""), "inquiries": n} for k, n in counts.most_common()]
    detail = [{k: q[k] for k in ("inflow", "inquiry_id", "created_us_pacific", "lead_type", "lead_entry", "device", "buyer_country", "buyer_level", "reply_status", "match", "matched_products")} for q in dump.inquiries]
    return {"inquiry_by_product_counts": q_rows(dump, rows), "inquiry_by_product": q_rows(dump, detail)}


def cmd_inquiry_fields(dump, a):
    names = dump.inquiry_field_names
    level = [n for n in names if any(x in n.lower() for x in ("level", "lvl", "grade"))]
    return {"inquiry_fields": [{"field": n, "level_candidate": n in level} for n in names] or
            [{"field": "", "level_candidate": False, "note": "이 덤프는 fieldNames 수집 전 버전: dump --pages inquiries 로 다시 받으면 키 이름이 나온다"}]}


def cmd_ads_report(dump, a):
    out = {}
    for table, rows in sorted(dump.ad_tables.items()):
        name = {"component_ggs_whole_site_offline_product": "ai_product_day", "component_ggs_whole_site_offline_campaign": "ai_campaign_day",
                "component_ggs_whole_site_offline_account": "ai_account_day", "component_ggs_offline_account": "manual_account_day",
                "component_ggs_offline_product": "manual_product_day", "component_ggs_offline_keyword": "keyword"}.get(table, table)
        out[name] = q_rows(dump, [{"source_table": table, **r} for r in rows])
    return out


def cmd_ads_products(dump, a):
    return {"product_ad_performance": product_performance(dump)[0]}


def cmd_keywords(dump, a):
    rows = []
    for t, r in dump.rows_of(tuple(t for t in dump.ad_tables if "keyword" in t)):
        rows.append({"source": "ads " + t, "keyword": r.get("keyword"), "product_id": r.get("productId", ""), "period": r.get("timeUs", ""),
                     "impressions": r.get("impsCnt"), "clicks": r.get("clickCnt"), "ad_impressions": "", "ad_clicks": "",
                     "cpc_usd": r.get("cpc"), "cost_usd": r.get("cost"), "bid_usd": next((r[k] for k in r if "bid" in k.lower()), "")})
    for r in dump.analytics_keywords:
        rows.append({"source": "analytics-keyword getKeywords", "keyword": r.get("keyword") or r.get("kw"), "product_id": "", "period": "",
                     "impressions": r.get("pv") or r.get("views"), "clicks": r.get("clk") or r.get("clicks"), "ad_impressions": "", "ad_clicks": "",
                     "cpc_usd": "", "cost_usd": "", "bid_usd": ""})
    for k in dump.keyword_effect:
        rows.append({"source": "analytics-product keywordEffect", "keyword": k["keyword"], "product_id": k["product_id"], "period": k["month"],
                     "impressions": int(k["views"]), "clicks": int(k["clicks"]), "ad_impressions": int(k["ad_views"]), "ad_clicks": int(k["ad_clicks"]),
                     "cpc_usd": "", "cost_usd": "", "bid_usd": ""})
    rows.sort(key=lambda r: -(fnum(r["clicks"]) * 1000 + fnum(r["impressions"])))
    return {"keywords": q_rows(dump, rows)}


def cmd_bids(dump, a):
    rows = [{"level": "campaign", **c} for c in dump.campaigns.values()]
    for t, r in dump.rows_of(tuple(t for t in dump.ad_tables if "keyword" in t)):
        for k in r:
            if "bid" in k.lower() or "price" in k.lower():
                rows.append({"level": "keyword", "keyword": r.get("keyword"), "bid_field": "%s.%s" % (t, k), "bid_usd": r[k]})
    if not any(r["level"] == "keyword" for r in rows):
        rows.append({"level": "keyword", "bid_field": "", "bid_usd": "", "note": "키워드 입찰가 필드 없음: 광고 키워드 표 %d행" % sum(len(v) for t, v in dump.ad_tables.items() if "keyword" in t)})
    return {"bids": q_rows(dump, rows)}


def cmd_ads_analyze(dump, a):
    products, undated = product_performance(dump)
    quality = inquiry_quality(dump)
    top = top_products(dump, products, quality)
    tables = {"product_ad_performance": products, "bid_analysis": bid_analysis(dump, products),
              "inquiry_quality": quality, "top_products": top}
    tables["_summary.md"] = summary_md(dump, products, top, undated)
    return tables


def cmd_coverage(dump, a):
    rows = []

    def add(item, ok, n, source, note=""):
        rows.append({"query_start": dump.start, "query_end": dump.end, "item": item, "status": "추출" if ok else "없음",
                     "rows": n, "source": source, "note": note})

    meta = getattr(dump, "ad_table_meta", None) or (dump.ad_tables and dump.ad_table_meta)
    for t in PRODUCT_TABLES + CAMPAIGN_TABLES + ACCOUNT_TABLES + ("component_ggs_offline_keyword",):
        n = len(dump.ad_tables.get(t, []))
        m = (meta or {}).get(t, {})
        nonzero = sum(1 for r in dump.ad_tables.get(t, []) if fnum(r.get("cost")) or fnum(r.get("clickCnt")) or fnum(r.get("impsCnt")))
        add("광고 표 " + t, n > 0 and nonzero > 0, n, "ads-report component/data",
            "응답 수 %s, API totalRows 최대 %s, 값 있는 행 %d" % (m.get("responses", 0), m.get("reported_total", 0), nonzero))
    prods, undated = product_performance(dump)
    actual = [p for p in prods if p["spend_actual_usd"] is not None]
    add("상품별 광고비(실측)", bool(actual), len(actual), "기간 있는 productId+cost 표",
        "상품 광고표에 cost 없음" + ("; 기간 없는 표: " + ", ".join("%s(%d행)" % (u["page"], len(u["rows"])) for u in undated) if undated else ""))
    est = [p for p in prods if p["spend_estimated_usd"] is not None]
    add("상품별 광고비(추정)", bool(est), len(est), "캠페인 일별 cost÷clickCnt × 상품 clickCnt", "estimated 열에만 들어감")
    add("캠페인 입찰가/목표 전환단가", bool(dump.campaigns), len(dump.campaigns), "ads-campaigns allList properties.maxPrice",
        " ".join("%s=%s(%s)" % (c["campaign_id"], c["bid_usd"], c["bid_unit"]) for c in dump.campaigns.values()))
    kw_ad = sum(len(v) for t, v in dump.ad_tables.items() if "keyword" in t)
    add("키워드 입찰가", False if not kw_ad else True, kw_ad, "ads-report component_ggs_offline_keyword", "키워드 광고 표 행 없음(AI 전체 상점 캠페인은 키워드 표 없음)" if not kw_ad else "")
    add("상품별 유입 키워드(노출·클릭)", bool(dump.keyword_effect), len(dump.keyword_effect), "analytics-product keywordEffect (월별)")
    add("분석 키워드 목록", bool(dump.analytics_keywords), len(dump.analytics_keywords), "analytics-keyword getKeywords", "" if dump.analytics_keywords else "응답 total 0")
    inq = dump.inquiries
    add("문의 목록", bool(inq), len(inq), "message subjectList (허용 필드만)", "덤프 전체 %d건 중 기간 내" % len(dump.inquiries_all))
    add("바이어 국가", any(q["buyer_country"] for q in inq), sum(1 for q in inq if q["buyer_country"]), "sender.countryCode")
    lv = sum(1 for q in inq if q["buyer_level"])
    add("바이어 레벨(문의)", lv > 0, lv, "subjectList level/grade 키(capture.js buyerAttrs)",
        "" if lv else "이 덤프에 레벨 필드 없음(이전 허용 필드에서 버려짐). 새 capture.js 로 dump --pages inquiries 후 inquiry fields 로 키 확인")
    add("바이어 L3+ 전환(광고)", True, sum(p["l3plus_inquiries"] for p in prods), "광고 상품표 l3PlusCvr3")
    add("스팸 여부", True, sum(1 for q in inq if q["spam"]), "subjectList spam")
    add("답장 상태", True, len(inq), "subjectList replyStatus")
    return {"coverage": rows}


# -- data advisor product analyzer -----------------------------------------------------------------
PRODUCT_METRICS = (("views", "sumProdShowNum"), ("clicks", "sumProdClickNum"), ("visitors", "sumProdVisitorCnt"),
                   ("add_to_favorite", "fav"), ("shared", "share"), ("add_to_compare", "cmp"), ("inquiries", "sumProdFbNum"),
                   ("inquiry_buyers", "mcFbUv"))


def period_bounds(granularity, key):
    """Tag suffix -> (first day, last day) of that data advisor period."""
    if granularity == "week":
        s = dt.date.fromisoformat(key)
        return s, s + dt.timedelta(days=6)
    s = dt.date.fromisoformat(key + "-01")
    return s, (s.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)


def advisor_periods(dump, granularity):
    """{period key: {product id: product dict}} for product_stats.<granularity>.<key> replays touching start..end."""
    prefix = "product_stats.%s." % granularity
    out = collections.defaultdict(dict)
    summaries = {}
    for r in dump.records("analytics-product"):
        tag = (r.get("replay") or {}).get("tag") or ""
        if tag.startswith("product_summary.%s." % granularity):
            stat = ((((r.get("json") or {}).get("data") or {}).get("statDate")) or {}).get("value")
            summaries[tag.rsplit(".", 1)[1] if granularity == "month" else tag[len("product_summary.week."):]] = stat
        if not tag.startswith(prefix):
            continue
        key = tag[len(prefix):]
        first, last = period_bounds(granularity, key)
        if (dump.end and first.isoformat() > dump.end) or (dump.start and last.isoformat() < dump.start):
            continue
        for p in ((((r.get("json") or {}).get("value") or {}).get("products") or {}).get("data")) or []:
            out[key].setdefault(str(p.get("id")), p)
    return out, summaries


def cmd_analytics_products(dump, a):
    gran = a.granularity or "week"
    periods, summaries = advisor_periods(dump, gran)
    keys = sorted(periods)
    covered = (period_bounds(gran, keys[0])[0].isoformat(), period_bounds(gran, keys[-1])[1].isoformat()) if keys else ("", "")
    head = {"query_start": dump.start, "query_end": dump.end, "covered_start": covered[0], "covered_end": covered[1],
            "granularity": gran, "periods": len(keys)}
    agg = collections.defaultdict(lambda: collections.defaultdict(float))
    info, kw_rows = {}, []
    for key in keys:
        for pid, p in periods[key].items():
            a_ = agg[pid]
            for name, field in PRODUCT_METRICS:
                a_[name] += fnum(p.get(field))
            a_["periods_listed"] += 1
            info[pid] = p
            for k in p.get("keywordEffect") or []:
                kw_rows.append({**head, "period": key, "product_id": pid, "keyword": k.get("keyword"), "views": int(fnum(k.get("views"))),
                                "clicks": int(fnum(k.get("clicks"))), "p4p_views": int(fnum(k.get("p4pViews"))),
                                "p4p_clicks": int(fnum(k.get("p4pClicks"))), "p4p_campaign_status": k.get("p4pCampaignStatus"),
                                "is_p4p_keyword": k.get("isP4pKeyword")})
    rows = []
    for pid, m_ in agg.items():
        p = info[pid]
        owner = (p.get("fullName") or "").strip() or " ".join(x for x in (p.get("firstName"), p.get("lastName")) if x and x.strip())
        rows.append({**head, "product_id": pid, "product_title": p.get("subject") or dump.titles.get(pid, ""), "owner": owner,
                     **{name: int(m_[name]) for name, _f in PRODUCT_METRICS},
                     "ctr_pct": r2(div(m_["clicks"] * 100, m_["views"])) or 0.0,
                     "inquiry_rate_pct": r2(div(m_["inquiries"] * 100, m_["visitors"])) or 0.0,
                     "visitors_note": "기간별 방문자 수의 합(여러 기간에 온 같은 방문자는 중복)" if len(keys) > 1 else "",
                     "is_p4p_product": p.get("isP4pProd"), "is_p4p": p.get("isP4p"), "is_kwa_search": p.get("isKwSearch"),
                     "periods_listed": int(m_["periods_listed"]),
                     "source": "mydata getVipEffectiveProductsAndStats statisticsType=%s (selected=기간별)" % gran})
    rows.sort(key=lambda r: (-r["views"], -r["clicks"]))
    totals = {**head, **{name: sum(r[name] for r in rows) for name, _f in PRODUCT_METRICS}, "products": len(rows)}
    totals["ctr_pct"] = r2(div(totals["clicks"] * 100, totals["views"])) or 0.0
    totals["inquiry_rate_pct"] = r2(div(totals["inquiries"] * 100, totals["visitors"])) or 0.0
    if not keys:
        totals["note"] = "이 덤프에 %s 단위 상품 기록 없음: analytics products --granularity %s 로 라이브 덤프 필요" % (gran, gran)
    checks = [{**head, "period": k, "products": len(periods[k]), "summary_statDate": summaries.get(k, ""),
               "statDate_matches": (summaries.get(k, "") or "")[:10] == period_bounds(gran, k)[0].isoformat() if summaries.get(k) else ""}
              for k in keys]
    kw_rows.sort(key=lambda r: (-r["views"], r["product_id"]))
    return {"analytics_summary": [totals], "analytics_periods": checks, "analytics_products": rows, "analytics_product_keywords": kw_rows}


COMMANDS = {
    "inquiry-list": cmd_inquiry_list, "inquiry-stats": cmd_inquiry_stats, "inquiry-by-product": cmd_inquiry_by_product,
    "inquiry-fields": cmd_inquiry_fields, "ads-report": cmd_ads_report, "ads-products": cmd_ads_products,
    "keywords": cmd_keywords, "bids": cmd_bids, "analytics-products": cmd_analytics_products, "ads-analyze": cmd_ads_analyze, "coverage": cmd_coverage,
}


# -- output ---------------------------------------------------------------------------------------
def columns(rows):
    cols = []
    for r in rows:
        for c in r:
            if c not in cols:
                cols.append(c)
    return cols


def to_csv(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns(rows))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def to_table(name, rows, limit=30):
    if not rows:
        return "== %s (0행)\n" % name
    cols = columns(rows)
    shown = rows[:limit]
    cell = lambda v: "" if v is None else str(v)[:60]
    width = {c: max(len(c), *(len(cell(r.get(c))) for r in shown)) for c in cols}
    lines = ["== %s (%d행%s)" % (name, len(rows), ", 앞 %d행" % limit if len(rows) > limit else ""),
             "  ".join(c.ljust(width[c]) for c in cols)]
    lines += ["  ".join(cell(r.get(c)).ljust(width[c]) for c in cols) for r in shown]
    return "\n".join(lines) + "\n"


def emit(tables, a):
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        for name, rows in tables.items():
            path = os.path.join(a.out, name[1:] if name.startswith("_") else name + ".csv")
            with open(path, "w", newline="") as fh:
                fh.write(rows if isinstance(rows, str) else to_csv(rows) if rows else "")
    data = {k: v for k, v in tables.items() if not k.startswith("_")}
    if a.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=1, default=str))
    elif a.format == "csv":
        for name, rows in data.items():
            if len(data) > 1:
                print("# " + name)
            sys.stdout.write(to_csv(rows) if rows else "")
    else:
        for name, rows in data.items():
            sys.stdout.write(to_table(name, rows, a.limit or 30))
        for name, text in tables.items():
            if name.startswith("_"):
                sys.stdout.write("\n" + text)
        if a.out:
            print("written: " + a.out)


def main(argv=None):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=sorted(COMMANDS))
    p.add_argument("--from", dest="dirs", action="append", required=True, help="dump folder (repeat for date windows)")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--format", choices=("table", "json", "csv"), default="table")
    p.add_argument("--out")
    p.add_argument("--limit", type=int)
    p.add_argument("--unread", action="store_true")
    p.add_argument("--type", choices=("product", "company", "tm"))
    p.add_argument("--country")
    p.add_argument("--status")
    p.add_argument("--granularity", choices=("week", "month"))
    a = p.parse_args(argv)
    for d in a.dirs:
        if not os.path.isdir(os.path.join(d, "raw")):
            raise SystemExit("not a dump folder (no raw/): " + d)
    dump = Dump(a.dirs, a.start, a.end)
    emit(COMMANDS[a.command](dump, a), a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
