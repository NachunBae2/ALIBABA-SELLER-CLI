#!/usr/bin/env python3
"""Read-only queries over one or more seller_central_raw.py dump folders.

  python3 report.py <command> --from DIR [--from DIR ...] [--start D --end D] [--format table|json|csv] [--out DIR]

Commands: inquiry-list inquiry-stats inquiry-by-product inquiry-fields ads-report ads-products
          ads-conversions ads-conversion-fields keywords bids analytics-products ads-analyze coverage

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
IDENTITY = ("productId", "campaignId", "keyword", "searchWord", "adGroupId", "timeUs")
CONV_TABLE = "component_ggs_ad_shop_conv_detail"  # one row per AI conversion (shop-wide, campaignType 51)
POSITIONAL = {CONV_TABLE}  # rows have no stored id: identity = window + page + position
DATE_KEYS = ("timeUs", "date", "statDate", "day", "dt")
MIN_RATE_CLICKS = 20  # a click->conversion rate needs this many clicks before it is used for a prediction
LEVEL_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}


def query_filters(r):
    """The filters of an ads component/data request (rows from different filters are different queries)."""
    from urllib.parse import parse_qs
    try:
        d = json.loads(parse_qs(r.get("body") or "").get("data", ["{}"])[0])
    except ValueError:
        return ""
    return json.dumps(d.get("filters") or [], sort_keys=True, ensure_ascii=False)


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
            with open(os.path.join(d, "manifest.json"), encoding="utf-8") as fh:
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
        owners = {}
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
            query = query_filters(r)
            for pos, d in enumerate(q.get("detailList") or []):
                row = {k: ia.main_val(v) for k, v in d.items()}
                if not self.in_range(conv_day(row) if table == CONV_TABLE else row.get("timeUs")):
                    continue
                if table in POSITIONAL:
                    key = (table, rp.get("start"), rp.get("end"), rp.get("page"), pos)
                    if key in seen:
                        continue
                    seen.add(key)
                    out[table].append(row)
                    continue
                # Same query: only a byte-identical row is a duplicate (rows sharing productId/keyword/day can
                # differ in other columns, e.g. search terms per campaign). Another query on the same table
                # (overlapping filters): skip rows whose identity an earlier query already gave.
                full = (table, query, json.dumps(row, sort_keys=True))
                ident = tuple(str(row.get(k, "")) for k in IDENTITY)
                ident = (table,) + (ident if any(ident) else (full[2],))
                owner = owners.setdefault(ident, query)
                if full in seen or owner != query:
                    continue
                seen.add(full)
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

    @functools.cached_property
    def product_rows(self):
        """Product x day ad rows counted once: the whole-site product table already holds Manual clicks,
        so a Manual product-table row is used only for a product-day the whole-site table lacks."""
        whole, manual = PRODUCT_TABLES
        have = {(str(r.get("productId") or ""), str(r.get("timeUs") or "")[:10]) for r in self.ad_tables.get(whole, [])}
        return self.rows_of((whole,)) + [(manual, r) for r in self.ad_tables.get(manual, [])
                                          if (str(r.get("productId") or ""), str(r.get("timeUs") or "")[:10]) not in have]

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
    def conversion_field_names(self):
        """Key names (no values) of the raw conversion-detail rows, before the allowlist."""
        names = set()
        for r in self.records("ads-report"):
            if ((r.get("replay") or {}).get("table") or self._table_of(r)) == CONV_TABLE:
                names |= set((((r.get("json") or {}).get("data") or {}).get("queryResult") or {}).get("fieldNames") or [])
        return sorted(names)

    @functools.cached_property
    def ad_days(self):
        days, titles = collections.defaultdict(collections.Counter), {}
        for _t, row in self.product_rows:
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
                "target_ids": [str(x) for x in (it.get("targetIds") if isinstance(it.get("targetIds"), list) else
                                                 str(it.get("targetIds") or "").replace(";", ",").split(",")) if str(x).strip()],
                "inquiry_count": it.get("inquiryCount"), "history_inquiry": it.get("historyInquiry"),
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


def level_code(v):
    s = str(v if v is not None else "").strip().upper()
    if s in LEVEL_RANK:
        return s
    return "L" + s if s.isdigit() and int(s) <= 4 else ""


def conv_day(row):
    """Date of one conversion. chatTime is the event time; timeUs is the query range ("start~end")
    when the table is read with granularity ALL, so it is used only when it is a single date."""
    for k in ("chatTime", "timeUs"):
        s = str(row.get(k) or "")
        if re.match(r"^\d{4}-\d{2}-\d{2}", s) and "~" not in s:
            return s[:10]
    return ""


def real_value(v):
    """The API writes "0" for an id or name it does not fill."""
    s = str(v if v is not None else "").strip()
    return "" if s in ("", "0") else s


def is_true(v):
    return str(v).strip().lower() in ("1", "true", "y")


def conversions(dump):
    """AI conversion detail rows in range (allowlisted fields only).
    growth_level (senderMbrGrowthLevel) is the level the screen's detail card shows; buyer_level is buyerLevel."""
    days = dump.ad_days[0]
    out = []
    for row in dump.ad_tables.get(CONV_TABLE, []):
        day = conv_day(row)
        ad_pid, conv_pid = real_value(row.get("productId")), real_value(row.get("fbProdId"))
        out.append({"query_start": dump.start, "query_end": dump.end,
                    "conversion_time": str(row.get("chatTime") or ""), "conversion_date": day,
                    "ad_product_id": ad_pid, "ad_product_name": real_value(row.get("productName")) or dump.titles.get(ad_pid, ""),
                    "ad_product_clicks_on_date": int(fnum(days[(ad_pid, day)]["ad_clicks"])) if (ad_pid, day) in days else 0,
                    "converted_scope": "product" if conv_pid else "shop",
                    "converted_product_id": conv_pid,
                    "converted_product_name": (real_value(row.get("fbProductName")) or dump.titles.get(conv_pid, "")) if conv_pid else "",
                    "buyer_country": row.get("countryId") or "",
                    "buyer_level": level_code(row.get("buyerLevel")), "growth_level": level_code(row.get("senderMbrGrowthLevel")),
                    "channel": row.get("channelDisplay") or "", "conversion_type": row.get("fbType") or "",
                    "is_repeat": row.get("isRepeat"), "is_low_quality": row.get("isLowQuality"), "is_punish": row.get("isPunish"),
                    "source": "실측: %s (상점 전체 AI 전환, campaignType 51). 날짜 = chatTime 앞 10자(시간대 표기 없음). "
                              "이름 \"0\"/빈칸 → 상품표 제목. converted_scope shop = fbProdId 없음(상점 단위 전환)" % CONV_TABLE})
    return out


def l3plus(level):
    return LEVEL_RANK.get(level, -1) >= 3


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


L1_SOURCE = "광고표 fiGrowthClickCnt(L1+ Clicks) 합 ÷ clickCnt 합 × 100 (화면 L1+ Buyer Click Percentage)"


L1_MISSING = "표에 L1+ 클릭 수(fiGrowthClickCnt) 열 없음 — 0% 아님"


def l1plus(l1_clicks, clicks, seen=True):
    """L1+ Buyer Click Percentage recomputed from summed counts (never an average of daily rates).
    seen=False: no source row carried fiGrowthClickCnt, so the share is unknown, not 0."""
    if clicks and not seen:
        return {"l1plus_click_pct": None, "l1plus_clicks": None, "l1plus_source": L1_MISSING}
    return {"l1plus_click_pct": r2(div(l1_clicks * 100, clicks)), "l1plus_clicks": int(l1_clicks),
            "l1plus_source": L1_SOURCE if clicks else "클릭 없음"}


# whole_site tables cover every campaign (the whole-site campaign table holds Manual type 2 too, and the
# whole-site product clicks = AI + Manual campaign clicks, seen 2026-11-17), so a row there without a
# campaignType is the account total, not AI.
ALL_KIND = "전체(AI+Manual)"
MANUAL_CAMPAIGN_TYPES = {"1", "2"}  # ads report filters: Manual = campaignType 1,2 (AI = 4,10 keyword, 47 whole site, 51 shop)


def campaign_kind(table, row=None):
    """Manual / AI Smart by the row's campaignType when it has one (the whole-site campaign table also
    carries Manual type-2 campaigns, seen 2026-11-17), else by the table name."""
    ctype = str((row or {}).get("campaignType") or "").strip()
    if ctype:
        return "Manual" if ctype in MANUAL_CAMPAIGN_TYPES else "AI Smart"
    return ALL_KIND if "whole_site" in table else "Manual"


def buyer_quality(dump):
    """L1+ buyer click share and inquiry quality per campaign, per month and for the whole range."""
    out = []

    def row(level, key, kind, a, spend, spend_src, table):
        econ = inquiry_economics(spend, a["clickCnt"], a["cvr3"], spend_src, "cvr3") if spend is not None else {
            "cpc_usd": None, "conversion_rate": round(a["cvr3"] / a["clickCnt"], 4) if a["clickCnt"] else 0.0,
            "cost_per_inquiry_usd": None, "cost_per_inquiry_source": "광고비 없음(상품표에 cost 없음)"}
        return {"query_start": dump.start, "query_end": dump.end, "level": level, "key": key, "campaign_kind": kind,
                **l1plus(a["fiGrowthClickCnt"], a["clickCnt"], a["l1_rows"] > 0),
                "impressions": int(a["impsCnt"]), "clicks": int(a["clickCnt"]), "spend_usd": r2(spend),
                "inquiries_cvr3": int(a["cvr3"]), "l3plus_inquiries": int(a["l3PlusCvr3"]),
                "l3plus_share_pct": r2(div(a["l3PlusCvr3"] * 100, a["cvr3"])),
                "cpc_usd": econ.get("cpc_usd"), "conversion_rate": econ.get("conversion_rate"),
                "cost_per_inquiry_usd": econ.get("cost_per_inquiry_usd"), "cost_per_inquiry_source": econ.get("cost_per_inquiry_source"),
                "table": table}

    cols = ("impsCnt", "clickCnt", "fiGrowthClickCnt", "cvr3", "l3PlusCvr3", "cost")
    camp = collections.defaultdict(lambda: collections.defaultdict(float))
    kinds = {}
    for t, r in dump.rows_of(CAMPAIGN_TABLES):
        key = (t, str(r.get("campaignId") or ""))
        a = camp[key]
        kinds[key] = campaign_kind(t, r)
        a["l1_rows"] += "fiGrowthClickCnt" in r
        for c in cols:
            a[c] += fnum(r.get(c))
    by_kind = collections.defaultdict(lambda: collections.defaultdict(float))
    for (t, cid), a in sorted(camp.items()):
        name = (dump.campaigns.get(cid) or {}).get("title") or ""
        out.append(row("campaign", cid + (" " + name if name else ""), kinds[(t, cid)], a, a["cost"], "캠페인 일별표 cost", t))
        for c, v in a.items():
            by_kind[(kinds[(t, cid)], t)][c] += v
    for (kind, t), a in sorted(by_kind.items()):
        out.append(row("kind_total", "%s~%s" % (dump.start, dump.end), kind, a, a["cost"], "캠페인 일별표 cost(유형별 합)", t))

    month = collections.defaultdict(lambda: collections.defaultdict(float))
    total = collections.defaultdict(lambda: collections.defaultdict(float))
    for t, r in dump.rows_of(PRODUCT_TABLES):
        kind = campaign_kind(t, r)
        for key, bucket in ((str(r.get("timeUs") or "")[:7], month), ("", total)):
            a = bucket[(kind, key, t)]
            a["l1_rows"] += "fiGrowthClickCnt" in r
            for c in cols[:-1]:
                a[c] += fnum(r.get(c))
    camp_cost_month = collections.defaultdict(float)
    for t, r in dump.rows_of(CAMPAIGN_TABLES):
        kind = campaign_kind(t, r)
        camp_cost_month[(kind, str(r.get("timeUs") or "")[:7])] += fnum(r.get("cost"))
        camp_cost_month[(kind, "")] += fnum(r.get("cost"))
        camp_cost_month[(ALL_KIND, str(r.get("timeUs") or "")[:7])] += fnum(r.get("cost"))
        camp_cost_month[(ALL_KIND, "")] += fnum(r.get("cost"))
    for level, bucket in (("month", month), ("total", total)):
        for (kind, key, t), a in sorted(bucket.items()):
            spend = camp_cost_month.get((kind, key))
            out.append(row(level, key or "%s~%s" % (dump.start, dump.end), kind, a, spend,
                           "캠페인 일별표 cost(같은 기간·유형)", t))
    return out


def product_performance(dump):
    cpc, fallback = daily_cpc(dump)
    camp_days = campaign_days(dump)
    agg = collections.defaultdict(lambda: collections.defaultdict(float))
    est_sources, camps = collections.defaultdict(set), collections.defaultdict(set)
    for _t, row in dump.product_rows:
        pid, day = str(row.get("productId") or ""), str(row.get("timeUs") or "")[:10]
        if not pid:
            continue
        a = agg[pid]
        a["l1_rows"] += "fiGrowthClickCnt" in row
        clicks = fnum(row.get("clickCnt"))
        for col in ("impsCnt", "clickCnt", "fiGrowthClickCnt", "mcCnt", "atmSessionCnt", "cvr3", "l1PlusCvr3", "l3PlusCvr3", "orderCnt"):
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

    conv_by = collections.defaultdict(list)
    for c in conversions(dump):
        conv_by[c["ad_product_id"]].append(c)
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
            **l1plus(a["fiGrowthClickCnt"], a["clickCnt"], a["l1_rows"] > 0),
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
            "conv_detail_count": len(conv_by[pid]),
            "conv_detail_growth_levels": count_str(collections.Counter(c["growth_level"] or "?" for c in conv_by[pid])),
            "conv_detail_countries": count_str(collections.Counter(c["buyer_country"] or "?" for c in conv_by[pid])),
            "conv_detail_l3plus_share_pct": r2(div(sum(1 for c in conv_by[pid] if l3plus(c["growth_level"])) * 100, len(conv_by[pid]))),
            "conv_detail_source": ("실측: %s 행(이 상품이 광고 상품인 전환), 등급 = senderMbrGrowthLevel(화면 명세 카드 열). 광고비는 %s" % (CONV_TABLE, "추정" if spend_actual is None else "실측")) if conv_by[pid] else "",
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
    conv_groups = collections.defaultdict(list)
    for c in conversions(dump):
        conv_groups[c["ad_product_id"]].append(c)
    for pid, cs in sorted(conv_groups.items(), key=lambda kv: -len(kv[1])):
        known = [c["growth_level"] for c in cs if c["growth_level"] in LEVEL_RANK]
        out.append({
            "query_start": dump.start, "query_end": dump.end, "product_id": pid, "product_title": cs[0]["ad_product_name"] or dump.titles.get(pid, ""),
            "link": "AI 전환 명세(실측, 광고 상품)", "inquiries": len(cs),
            "buyer_levels": count_str(collections.Counter(c["growth_level"] or "?" for c in cs)),
            "l3plus_share_pct": r2(div(sum(1 for l in known if LEVEL_RANK[l] >= 3) * 100, len(known))) if known else None,
            "countries": count_str(collections.Counter(c["buyer_country"] for c in cs)),
            "spam": sum(1 for c in cs if is_true(c["is_low_quality"])),
            "reply_status": "", "app": "", "web": "",
            "conversion_types": count_str(collections.Counter(c["conversion_type"] for c in cs)),
            "channels": count_str(collections.Counter(c["channel"] for c in cs)),
            "repeat": sum(1 for c in cs if is_true(c["is_repeat"])),
            "source": "실측: %s (spam 열 = isLowQuality, 등급 = senderMbrGrowthLevel)" % CONV_TABLE,
        })
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
            "l1plus_click_pct": p["l1plus_click_pct"],
            "cost_per_inquiry_usd": p["cost_per_inquiry_usd"], "cost_basis": p["cost_basis"],
            "inquiries": p["inquiries_cvr3"], "l3plus_share_pct": p["l3plus_share_pct"],
            "linked_inquiries": linked[p["product_id"]],
            "converted_buyer_levels": p["conv_detail_growth_levels"], "converted_buyer_countries": p["conv_detail_countries"],
            "quality_score": r2(100 * (0.6 * l3 + 0.4 * min(p["inquiries_cvr3"], 10) / 10)),
            "label": "제안(규칙: SKILL.md)",
        })
    return out


def summary_md(dump, products, top, undated, bq=()):
    est_total = sum(p["spend_estimated_usd"] or 0 for p in products)
    camp_total = sum(fnum(r.get("cost")) for _t, r in dump.rows_of(CAMPAIGN_TABLES))
    lines = ["# 광고 분석 요약", "", "- 쿼리 기간(US 태평양 날짜): %s ~ %s" % (dump.start, dump.end),
             "- 캠페인 일별표 광고비 합: $%.2f · 상품별 추정 광고비 합: $%.2f (상품별 실측 광고비 표 %s)" % (camp_total, est_total, "있음" if any(p["spend_actual_usd"] is not None for p in products) else "없음"),
             "- 정의: conversion_rate = 문의(비즈니스 기회, 광고표 cvr3) ÷ 클릭. cost_per_inquiry = 광고비 ÷ 문의 = CPC ÷ conversion_rate. L3+ = l3PlusCvr3.", ""]
    for r in bq:
        if r["level"] == "total":
            l1 = ("%s%% (L1+ 클릭 %d ÷ 클릭 %d)" % (r["l1plus_click_pct"], r["l1plus_clicks"], r["clicks"])
                  if r["l1plus_click_pct"] is not None else "- (%s, 클릭 %d)" % (r["l1plus_source"], r["clicks"]))
            lines.insert(3, "- L1+ 바이어 클릭 비율(%s): %s, 문의 %d건, L3+ %d건" % (
                r["campaign_kind"], l1, r["inquiries_cvr3"], r["l3plus_inquiries"]))
    convs = conversions(dump)
    if convs:
        s = conversion_summary(dump, convs)
        lines.append("- AI 전환: 캠페인 일별표 cvr3 %d건·L3+ %d건(화면 값과 같은 표), 광고비 ÷ 전환 $%s. 명세 %d행(%s), 반복·처벌 제외 %d행, 성장 레벨 L3+ %d행" % (
            s["campaign_table_conversions"], s["campaign_table_l3plus"], s["spend_per_conversion_usd"], s["detail_rows"],
            s["by_type"], s["screen_estimate_conversions"], s["screen_estimate_l3plus"]))
        lines.append("")
    if undated:
        lines.append("- 참고: 기간 없는 productId+cost 표 %d개(%s)는 기간을 알 수 없어 광고비에 쓰지 않음." % (len(undated), ", ".join(sorted({u["page"] for u in undated}))))
        lines.append("")
    lines.append("## 문의 1건 비용(cost_per_inquiry) 낮은 순 상위 10")
    lines.append("")
    by_id = {p["product_id"]: p for p in products}
    for t in top[:10]:
        p = by_id[t["product_id"]]
        spend = p["spend_actual_usd"] if p["spend_actual_usd"] is not None else p["spend_estimated_usd"]
        lines.append("- 상품 %s %s: L1+ 클릭 %s%%, 광고비 $%.2f(%s), 문의(전환) %d건, 문의 1건 $%.2f, L3+ 바이어 비율 %s%%, 연결 문의 %d건" % (
            t["product_id"], (t["product_title"] or "")[:50], "-" if p["l1plus_click_pct"] is None else p["l1plus_click_pct"],
            spend, t["cost_basis"], t["inquiries"], t["cost_per_inquiry_usd"],
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
    rows = rows[: a.limit] if a.limit else rows
    out = []
    for r in rows:
        # targetIds are product ids only on product inquiries (feedbackType 1); TM/company rows carry other ids
        ids = [r["product_id"]] if r["product_id"] else []
        risk = ["스팸 표시" if r["spam"] else ""]
        if r["buyer_level"]:
            risk.append("등급 " + r["buyer_level"])
        if r["inquiry_count"] not in (None, ""):
            risk.append("inquiryCount %s" % r["inquiry_count"])
        out.append({
            "inquiry_id": r["inquiry_id"], "created_us_pacific": r["created_us_pacific"],
            # three columns first: what the buyer looked at, where from, spam/risk (screen values only)
            "viewed_products": "; ".join("%s %s" % (i, (dump.titles.get(i) or "")[:60]) for i in ids).strip(),
            "buyer_country": r["buyer_country"],
            "risk": " · ".join(x for x in risk if x),
            "spam": r["spam"], "buyer_level": r["buyer_level"], "inquiry_count": r["inquiry_count"], "history_inquiry": r["history_inquiry"],
            "viewed_products_source": "상품 문의의 문의 상품(targetIds). TM·회사 문의는 목록 API 에 상품 없음(빈칸). OneTalk 고객 패널 방문 기록은 대화를 열어야 보여 목록 API 에 없음",
            "risk_source": "목록 API spam·등급 코드·inquiryCount. 가입일·신고·차단 기록은 목록 API 에 없음(빈칸)",
            **{k: v for k, v in r.items() if k not in ("inquiry_id", "created_us_pacific", "buyer_country", "spam", "buyer_level",
                                                        "inquiry_count", "history_inquiry", "target_ids")},
        })
    return {"inquiries": q_rows(dump, out)}


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


def range_completeness(dump):
    """Per ads report table and query window: rows got vs totalRows the API reported."""
    from urllib.parse import parse_qs
    seen = {}
    for r in dump.records("ads-report"):
        rp = r.get("replay") or {}
        if rp.get("kind") != "range":
            continue
        try:
            d = json.loads(parse_qs(r.get("body") or "").get("data", ["{}"])[0])
        except ValueError:
            continue
        q = (((r.get("json") or {}).get("data") or {}).get("queryResult")) or {}
        if not isinstance(q.get("detailList"), list):
            continue
        key = (d.get("tableName", ""), json.dumps(d.get("filters") or [], ensure_ascii=False), rp.get("start"), rp.get("end"))
        size = int((d.get("page") or {}).get("size") or len(q["detailList"]) or 1)
        e = seen.setdefault(key, {"tag": rp.get("tag") or "", "covered": 0, "total": 0, "pages": 0, "rows": 0, "distinct": set()})
        e["covered"] = max(e["covered"], (int(rp.get("page") or 1) - 1) * size + len(q["detailList"]))
        e["rows"] += len(q["detailList"])
        e["distinct"] |= {json.dumps(row, sort_keys=True) for row in q["detailList"]}
        e["total"] = max(e["total"], int(fnum(q.get("totalRows"))))
        e["pages"] += 1
    out = []
    for (table, filters, start, end), e in sorted(seen.items()):
        # distinct rows, not last page x size: pages that repeat rows (unstable API order) leave rows unfetched
        got = len(e["distinct"])
        out.append({"table": table, "range_table_key": e["tag"], "filters": filters, "window_start": start, "window_end": end,
                    "rows_got": got, "rows_total_api": e["total"], "pages": e["pages"],
                    "repeated_rows": e["rows"] - got, "last_page_reach": e["covered"],
                    "complete": "yes" if got >= e["total"] else "NO (%d short)" % (e["total"] - got)})
    return out


def cmd_ads_tables(dump, a):
    """Every ads table in full: all columns as the API returns them, one CSV per table, plus a
    completeness table (rows got vs API totalRows per window) and the campaign list."""
    out = {}
    for table, rows in sorted(dump.ad_tables.items()):
        out["table_" + table] = q_rows(dump, rows)
    camps = list(dump.campaigns.values())
    if camps:
        out["campaigns"] = q_rows(dump, camps)
    for (page, endpoint), info in sorted(dump.product_cost_tables.items()):
        name = "product_cost_%s_%s" % (page, re.sub(r"[^A-Za-z0-9]+", "_", endpoint.split("/")[-1])[:40])
        out[name] = q_rows(dump, [{"source": "%s %s" % (page, endpoint), **r} for r in info["rows"]])
    comp = range_completeness(dump)
    out["completeness"] = comp
    short = [c for c in comp if c["complete"] != "yes"]
    head = "광고표 %d개, 행 합 %d. " % (len(dump.ad_tables), sum(len(r) for r in dump.ad_tables.values()))
    head += ("전부 totalRows 까지 받음.\n" if comp and not short else
             ("덜 받은 표 %d개 (completeness 표) — 같은 명령을 다시 돌리면 이어 받는다.\n" % len(short)) if short else
             "광고 리포트 범위 응답 0건 — 로그인·계정·Aside 확인.\n")
    out["_head"] = head
    if short or not comp:
        out["_exit"] = 3
    return out


def cmd_ads_products(dump, a):
    return {"product_ad_performance": product_performance(dump)[0]}


def conversion_summary(dump, rows):
    camp = dump.rows_of(CAMPAIGN_TABLES)
    camp_cost = sum(fnum(r.get("cost")) for _t, r in camp)
    camp_conv = int(sum(fnum(r.get("cvr3")) for _t, r in camp))
    kept = [c for c in rows if not is_true(c["is_repeat"]) and not is_true(c["is_punish"])]
    has_channel = any("channelDisplay" in r for r in dump.ad_tables.get(CONV_TABLE, []))
    return {"query_start": dump.start, "query_end": dump.end,
            "campaign_table_conversions": camp_conv, "campaign_table_l3plus": int(sum(fnum(r.get("l3PlusCvr3")) for _t, r in camp)),
            "campaign_table_source": "실측: %s cvr3·l3PlusCvr3 일별 합 (화면 Conversions·L3+ 와 같은 표)" % CAMPAIGN_TABLES[0],
            "campaign_spend_usd": r2(camp_cost) if camp_cost else None,
            "spend_per_conversion_usd": r2(div(camp_cost, camp_conv)),
            "detail_rows": len(rows),
            "detail_l3plus_buyer_level": sum(1 for c in rows if l3plus(c["buyer_level"])),
            "detail_l3plus_growth_level": sum(1 for c in rows if l3plus(c["growth_level"])),
            "screen_estimate_conversions": len(kept),
            "screen_estimate_l3plus": sum(1 for c in rows if l3plus(c["growth_level"])),
            "screen_estimate_source": "화면 기준 추정(명세에서 계산): 건수 = isRepeat·isPunish 아닌 행, L3+ = senderMbrGrowthLevel L3 이상(전체 행). "
                                      "화면 명세 카드가 senderMbrGrowthLevel 을 보여줌",
            "by_type": count_str(collections.Counter(c["conversion_type"] for c in rows)),
            "by_scope": count_str(collections.Counter(c["converted_scope"] for c in rows)),
            "by_growth_level": count_str(collections.Counter(c["growth_level"] or "?" for c in rows)),
            "by_buyer_level": count_str(collections.Counter(c["buyer_level"] or "?" for c in rows)),
            "channel_source": "channelDisplay" if has_channel else "없음: 응답 행에 channelDisplay 키 없음(화면 설정 이름). 실제 키 = ads conversion-fields",
            "source": "명세 = %s, 광고비 = %s cost 합 ÷ cvr3 합" % (CONV_TABLE, CAMPAIGN_TABLES[0])}


def cmd_ads_conversions(dump, a):
    rows = conversions(dump)
    return {"conversion_summary": [conversion_summary(dump, rows)], "conversions": rows}


def cmd_ads_conversion_fields(dump, a):
    names = dump.conversion_field_names
    stored = set().union(*(r.keys() for r in dump.ad_tables.get(CONV_TABLE, []))) if dump.ad_tables.get(CONV_TABLE) else set()
    return {"conversion_fields": [{"field": n, "stored": n in stored, "channel_candidate": "channel" in n.lower() or "source" in n.lower()} for n in names] or
            [{"field": "", "stored": False, "channel_candidate": False, "note": "이 덤프는 fieldNames 수집 전 버전: ads conversions 로 다시 받으면 키 이름이 나온다"}]}


def cmd_keywords(dump, a):
    rows = []
    for t, r in dump.rows_of(tuple(t for t in dump.ad_tables if "keyword" in t or "searchword" in t)):
        rows.append({"source": "ads " + t, "keyword": r.get("keyword") or r.get("searchWord"), "product_id": r.get("productId", ""), "period": r.get("timeUs", ""),
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
    idx = keyword_index_rows(dump)
    return {"keywords": q_rows(dump, rows), "keyword_index": idx}


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
    bq = buyer_quality(dump)
    tables = {"buyer_quality": bq, "product_ad_performance": products, "bid_analysis": bid_analysis(dump, products),
              "inquiry_quality": quality, "top_products": top}
    tables["_summary.md"] = summary_md(dump, products, top, undated, bq)
    return tables


def cmd_ads_quality(dump, a):
    return {"buyer_quality": buyer_quality(dump)}


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
    cv = conversions(dump)
    add("AI 전환 명세(바이어 등급·국가·유입·유형)", bool(cv), len(cv), CONV_TABLE + " (허용 필드만)",
        "" if cv else "이 덤프에 전환 명세 표 없음 또는 0행: ads conversions 로 라이브 덤프")
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
    summaries, visit_counts, queried = {}, {}, set()
    for r in dump.records("analytics-product"):
        tag = (r.get("replay") or {}).get("tag") or ""
        if tag.startswith("product_summary.%s." % granularity):
            data = ((r.get("json") or {}).get("data")) or {}
            k = tag.rsplit(".", 1)[1] if granularity == "month" else tag[len("product_summary.week."):]
            summaries[k] = (data.get("statDate") or {}).get("value")
            visit_counts[k] = (data.get("totalVisitProductCount") or {}).get("value")
        if not tag.startswith(prefix):
            continue
        key = tag[len(prefix):]
        first, last = period_bounds(granularity, key)
        if (dump.end and first.isoformat() > dump.end) or (dump.start and last.isoformat() < dump.start):
            continue
        queried.add(key)
        for p in ((((r.get("json") or {}).get("value") or {}).get("products") or {}).get("data")) or []:
            out[key].setdefault(str(p.get("id")), p)
    advisor_periods.last = {"visit_counts": visit_counts, "queried": queried}
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
    vc, queried = advisor_periods.last["visit_counts"], advisor_periods.last["queried"]
    checks = []
    for k in sorted(queried | set(keys)):
        with_visitors = sum(1 for p in periods.get(k, {}).values() if fnum(p.get("sumProdVisitorCnt")) > 0)
        checks.append({**head, "period": k, "products": len(periods.get(k, {})), "products_with_visitors": with_visitors,
                       "summary_statDate": summaries.get(k, ""),
                       "statDate_matches": (summaries.get(k, "") or "")[:10] == period_bounds(gran, k)[0].isoformat() if summaries.get(k) else "",
                       "summary_visit_products": vc.get(k, ""),
                       "visit_products_match": (with_visitors == int(fnum(vc[k]))) if vc.get(k) not in (None, "") else "",
                       "check_rule": "상품표 방문자>0 상품 수 = 요약 totalVisitProductCount 이면 기간 라벨이 맞다"})
    kw_rows.sort(key=lambda r: (-r["views"], r["product_id"]))
    return {"analytics_summary": [totals], "analytics_periods": checks, "analytics_products": rows, "analytics_product_keywords": kw_rows}


# -- keyword index + keyword recommendation --------------------------------------------------------
KW_STOP = {"the", "and", "for", "with", "from", "made", "korea", "korean", "new", "best", "top", "hot", "sale", "pcs", "set",
           "ml", "oem", "odm", "private", "label", "wholesale", "factory", "supplier", "price", "free", "sample", "custom"}
KW_TOKEN = re.compile(r"[a-z][a-z0-9\-]+")


def kw_norm(s):
    return " ".join(str(s or "").lower().split())


def kw_tokens(s):
    return [t for t in KW_TOKEN.findall(kw_norm(s)) if len(t) >= 3 and t not in KW_STOP]


def keyword_index_rows(dump):
    """Keyword Index (Analytics > Keyword Index, vip/kwIndex/searchWords) rows: search index = pv,
    seller scale (competition) = resultCnt. Window = nd (e.g. 30d), not --start/--end."""
    out, seen = [], set()
    for r in dump.records("keyword-index"):
        rp = r.get("replay") or {}
        tag = rp.get("tag") or ""
        if not tag.startswith("kwindex."):
            continue
        seed = tag[len("kwindex."):]
        from urllib.parse import urlparse, parse_qs
        nd = (parse_qs(urlparse(r.get("url") or "").query).get("nd") or [""])[0]
        data = (r.get("json") or {}).get("data")
        for k in data if isinstance(data, list) else []:
            kw = k.get("queryRaw") or k.get("query")
            if not kw or (seed, kw_norm(kw)) in seen:
                continue
            seen.add((seed, kw_norm(kw)))
            pct = lambda v: None if v is None else r2(fnum(v) * 100)
            out.append({"seed": seed, "nd": nd, "keyword": kw, "search_index": k.get("pv"), "seller_scale": k.get("resultCnt"),
                        "click_rate_pct": pct(k.get("clickRate")), "search_index_change_pct": pct(k.get("pvCrc")),
                        "seller_scale_change_pct": pct(k.get("resultCntCrc")), "in_our_product_name": k.get("isInProdName"),
                        "is_our_product_keyword": k.get("isProdKeyword"), "is_ad_keyword": k.get("isP4pKeyword"),
                        "ad_campaign_status": k.get("p4pCampaignStatus"),
                        "trend": " ".join(str(x) for x in (k.get("trends") or []))})
    return out


AD_KW_TABLES = ("component_ggs_offline_keyword", "component_ggs_offline_searchword")


def ad_keyword_rows(dump):
    """Ad report Word Report rows (Optional Keywords + Search Terms), per keyword x day."""
    rows = []
    for t, r in dump.rows_of(AD_KW_TABLES):
        kw = r.get("keyword") or r.get("searchWord") or r.get("word")
        if not kw:
            continue
        bid_key = next((k for k in r if "bid" in k.lower() and k.lower() != "bidtype"), None)
        rows.append({"table": t, "kind": "검색어" if "searchword" in t else "키워드", "keyword": kw, "product_id": str(r.get("productId") or ""),
                     "day": r.get("timeUs") or "", "impressions": fnum(r.get("impsCnt")), "clicks": fnum(r.get("clickCnt")),
                     "cost": fnum(r.get("cost")), "inquiries": fnum(r.get("cvr3") or r.get("businessLeadsCnt")),
                     "l1plus_clicks": fnum(r.get("fiGrowthClickCnt")), "bid": r.get(bid_key) if bid_key else None, "bid_field": bid_key or ""})
    return rows


def kw_grams(s):
    toks = kw_tokens(s)
    return set(toks) | {" ".join(toks[i:i + 2]) for i in range(len(toks) - 1)}


def brand_tokens(titles):
    """Words in >= 90% of >= 3 titles: the shop's brand, not a product feature."""
    if len(titles) < 3:
        return set()
    c = collections.Counter(t for title in titles.values() for t in set(kw_tokens(title)))
    return {t for t, n in c.items() if n >= 0.9 * len(titles)}


def keyword_seeds(dump, max_seeds=5, product=None):
    """Seed head terms for Keyword Index, each with its basis. Only from our own data.
    With product: its top 2 own buyer search terms, then its title words (its features): in its search
    terms first, then shared by more of our titles (category words over one-off names), then title order. Without: terms in >= 2 distinct buyer search terms of our products that
    also appear in a product title; without search terms, title words in >= 2 titles but not brand-like."""
    titles = {pid: kw_norm(t) for pid, t in dump.titles.items() if t}
    brand = brand_tokens(titles)
    if product:
        own = collections.defaultdict(lambda: [0, 0])
        for k in dump.keyword_effect:
            if k["product_id"] == product and (k["views"] or k["clicks"]):
                own[kw_norm(k["keyword"])][0] += k["clicks"]
                own[kw_norm(k["keyword"])][1] += k["views"]
        terms = [kw_tokens(q) for q in own]
        seeds = [{"seed": q, "basis": "상품 %s 유입 검색어(클릭 %d·노출 %d)" % (product, c, v)}
                 for q, (c, v) in sorted(own.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))[:2]]
        common = collections.Counter(t for title in titles.values() for t in set(kw_tokens(title)))
        order = [t for t in dict.fromkeys(kw_tokens(titles.get(product, ""))) if t not in brand]
        hits = {t: sum(1 for q in terms if t in q) for t in order}
        ranked = sorted(order, key=lambda t: (-hits[t], -common[t], order.index(t)))
        for t in ranked:
            if len(seeds) >= max_seeds:
                break
            if t not in {x["seed"] for x in seeds}:
                seeds.append({"seed": t, "basis": "상품 %s 제목 단어(상품 제목 %d개에 있음%s)" % (
                    product, common[t], ", 이 상품 유입 검색어 %d개에 포함" % hits[t] if hits[t] else "")})
        return seeds
    in_titles = lambda term: sum(1 for t in titles.values() if re.search(r"\b%s\b" % re.escape(term), t))
    cand = collections.Counter()
    for q in {kw_norm(k["keyword"]) for k in dump.keyword_effect if k["views"] or k["clicks"]}:
        cand.update(kw_grams(q))
    seeds = []
    for g, n in sorted(cand.items(), key=lambda kv: (-kv[1], len(kv[0].split()), kv[0])):
        if n < 2:
            break
        m = in_titles(g)
        if m:
            seeds.append({"seed": g, "basis": "우리 유입 검색어 %d개·상품 제목 %d개에 포함 (Product Analyzer keywordEffect)" % (n, m)})
        if len(seeds) >= max_seeds:
            break
    if not seeds and titles:
        c = collections.Counter(t for title in titles.values() for t in set(kw_tokens(title)))
        for g, n in c.most_common():
            if n < 2:
                break
            if g in brand:
                continue
            seeds.append({"seed": g, "basis": "상품 제목 %d개에 포함(유입 검색어 없음)" % n})
            if len(seeds) >= max_seeds:
                break
    return seeds


def kw_products(dump):
    """Products to pick from: id, title, search views/clicks (Product Analyzer keywordEffect sum), inquiries."""
    agg = collections.defaultdict(lambda: [0, 0])
    for k in dump.keyword_effect:
        agg[k["product_id"]][0] += k["views"]
        agg[k["product_id"]][1] += k["clicks"]
    ad = product_ad_numbers(dump)
    rows = [{"상품ID": pid, "제목": dump.titles.get(pid, ""), "검색 노출": int(agg[pid][0]), "검색 클릭": int(agg[pid][1]),
             "광고 클릭": ad[pid]["clicks"] if pid in ad else None, "광고 문의": ad[pid]["inquiries"] if pid in ad else None}
            for pid in sorted(set(dump.titles) | set(agg))]
    rows.sort(key=lambda r: (-r["검색 클릭"], -(r["광고 클릭"] or 0), -r["검색 노출"], r["상품ID"]))
    return rows


def product_ad_numbers(dump):
    """{product id: ad impressions, clicks, inquiries (cvr3), L1+ clicks} summed over the period (ad product tables)."""
    out = {}
    for _, r in dump.product_rows:
        pid = str(r.get("productId") or "")
        if not pid:
            continue
        o = out.setdefault(pid, {"impressions": 0, "clicks": 0, "inquiries": 0, "l1plus_clicks": 0, "l1_rows": 0})
        o["l1_rows"] += "fiGrowthClickCnt" in r
        o["impressions"] += int(fnum(r.get("impsCnt")))
        o["clicks"] += int(fnum(r.get("clickCnt")))
        o["inquiries"] += int(fnum(r.get("cvr3")))
        o["l1plus_clicks"] += int(fnum(r.get("fiGrowthClickCnt")))
    return out


def product_line(dump, pid, nums):
    """One line of this product's own period numbers (product level, not per keyword) and why keyword cells are blank."""
    _, ad_kw, _, own = nums
    a = product_ad_numbers(dump).get(pid)
    parts = []
    if a:
        l1 = ("%s%%(L1+ 클릭 %d)" % (r2(div(a["l1plus_clicks"] * 100, a["clicks"])) if a["clicks"] else "", a["l1plus_clicks"])
              if a["l1_rows"] else "-(" + L1_MISSING + ")")
        parts.append("광고 노출 %d·클릭 %d·CTR %s%%·문의 %d·L1+ %s" % (
            a["impressions"], a["clicks"], r2(div(a["clicks"] * 100, a["impressions"])) if a["impressions"] else "",
            a["inquiries"], l1))
    else:
        parts.append("광고 상품표에 이 상품 없음")
    v = sum(o["views"] for o in own[pid].values())
    c = sum(o["clicks"] for o in own[pid].values())
    parts.append("검색 유입(Product Analyzer) 노출 %d·클릭 %d, 검색어 %d개" % (v, c, len(own[pid])))
    kw_rows = len(ad_kw.get(pid, {}))
    parts.append("광고 키워드·검색어표의 이 상품 행 %d개%s" % (kw_rows, "" if kw_rows else " → 키워드별 광고 칸(입찰가·문의·L1+)은 빈칸"))
    return "이 상품 기간 합(상품 단위): " + " · ".join(parts) + "\n"


def resolve_product(dump, query):
    """--product value -> matching product ids (exact id, else case-insensitive part of the title)."""
    q = str(query or "").strip()
    ids = {r["상품ID"] for r in kw_products(dump)}
    if q in ids:
        return [q]
    low = q.lower()
    return [pid for pid in sorted(ids) if low and low in (dump.titles.get(pid) or "").lower()]


KW_SCORE_FORMULA = ("점수 = 검색량² ÷ 경쟁 × (1 + 클릭) × (1 + 문의) × (1 + L1+% ÷ 100) ÷ (1 + 입찰가), 빈칸은 0으로 계산 "
                    "(검색량² ÷ 경쟁 = KEI, 검색 많고 셀러 적을수록 큼)")
KW_COLS = ("키워드", "점수", "검색량", "경쟁(셀러 수)", "입찰가 USD", "노출", "클릭", "CTR %", "문의", "L1+ %")


def keyword_numbers(dump):
    """Numbers per keyword: Keyword Index (search volume, sellers), ad Word Report per product
    (inquiries, L1+ clicks, bid), Product Analyzer per product (views, clicks)."""
    idx = {}
    for k in keyword_index_rows(dump):
        if k["search_index"] not in (None, "") and k["seller_scale"] not in (None, ""):
            idx.setdefault(kw_norm(k["keyword"]), k)
    ad = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(float)))
    bid = {}
    for r in ad_keyword_rows(dump):
        key = kw_norm(r["keyword"])
        if r["product_id"]:
            for c in ("clicks", "inquiries", "l1plus_clicks"):
                ad[r["product_id"]][key][c] += r[c]
        if r["bid"] not in (None, ""):
            bid[key] = r["bid"]
    own = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(float)))
    for k in dump.keyword_effect:
        o = own[k["product_id"]][kw_norm(k["keyword"])]
        o["views"] += k["views"]
        o["clicks"] += k["clicks"]
    return idx, ad, bid, own


def recommend_for(dump, pid, nums, top):
    """Up to `top` keywords for one product. Candidates come from the product itself (its title words,
    its own buyer search terms, its own ad keywords; a Keyword Index query only when every word of it is
    in the title); only those with a Keyword Index volume are kept."""
    idx, ad, bid, own = nums
    ttoks = set(kw_tokens(dump.titles.get(pid, "")))
    cands = set(own[pid]) | set(ad[pid])
    for key in idx:
        toks = set(kw_tokens(key))
        if toks and toks <= ttoks:
            cands.add(key)
    rows = []
    for key in cands:
        k = idx.get(key)
        if not k:
            continue  # no search volume number: not recommended
        o, a = own[pid].get(key), ad[pid].get(key)
        views = int(o["views"]) if o else None
        clicks = int(o["clicks"]) if o else None
        inq = int(a["inquiries"]) if a else None
        l1 = r2(div(a["l1plus_clicks"] * 100, a["clicks"])) if a and a["clicks"] else None
        b = fnum(bid[key]) if key in bid else None
        sv, comp = fnum(k["search_index"]), fnum(k["seller_scale"])
        score = (sv * sv / comp if comp else 0) * (1 + (clicks or 0)) * (1 + (inq or 0)) * (1 + (l1 or 0) / 100) / (1 + (b or 0))
        ctr = r2(div(clicks * 100, views)) if views else None
        num = lambda v: int(fnum(v)) if fnum(v) == int(fnum(v)) else fnum(v)
        rows.append(dict(zip(KW_COLS, (k["keyword"], int(round(score)), num(sv), num(comp), b, views, clicks, ctr, inq, l1))))
    rows.sort(key=lambda r: (-r["점수"], -fnum(r["검색량"]), r["키워드"]))
    return rows[:top]


def cmd_keyword_seeds(dump, a):
    if not a.product:
        return {"keyword_seeds": keyword_seeds(dump, a.max_seeds or 5)}
    ids = resolve_product(dump, a.product)
    if len(ids) != 1:
        return {"keyword_seeds": [], "_exit": 64}
    return {"keyword_seeds": keyword_seeds(dump, a.max_seeds or 5, ids[0])}


def cmd_keywords_recommend(dump, a):
    idx_rows = keyword_index_rows(dump)
    window = ", ".join(sorted({k["nd"] for k in idx_rows})) or "없음"
    foot = "\n%s\n- 기간(노출·클릭·문의·L1+): %s ~ %s · 검색량·경쟁 = Keyword Index(%s 창) · 빈칸 = 그 숫자 없음(추정 안 함)\n" % (
        KW_SCORE_FORMULA, dump.start, dump.end, window)
    products = kw_products(dump)
    if a.all_products:
        nums, out = keyword_numbers(dump), []
        for p in products:
            for i, r in enumerate(recommend_for(dump, p["상품ID"], nums, a.top or 10), 1):
                out.append({"상품ID": p["상품ID"], "순위": i, **r})
        return {"keyword_recommendations": out, "_keyword_recommend.md": foot}
    ids = resolve_product(dump, a.product) if a.product else []
    if len(ids) != 1:
        if a.product and not ids:
            head, shown = "상품 '%s' 없음. 아래에서 고르세요." % a.product, products
        elif a.product:
            head, shown = "'%s' 에 맞는 상품 %d개. 상품ID 로 하나만 고르세요." % (a.product, len(ids)), [p for p in products if p["상품ID"] in ids]
        else:
            head, shown = "상품 하나를 고르세요.", products
        guide = ("\n%s\n  alibaba-data keywords recommend --account <계정키> --start … --end … --product <상품ID 또는 이름 일부>\n"
                 "  (전 상품 일괄: --all-products)\n") % head
        return {"products": shown, "_keyword_recommend.md": guide, "_exit": 64 if a.product else 0}
    pid = ids[0]
    nums = keyword_numbers(dump)
    rows = recommend_for(dump, pid, nums, a.top or 10)
    head = "상품 %s %s — 추천 키워드 %d개 (점수 순)\n" % (pid, dump.titles.get(pid, ""), len(rows))
    head += product_line(dump, pid, nums)
    return {"_head": head, "keyword_recommendations": rows, "_keyword_recommend.md": foot}


COMMANDS = {
    "inquiry-list": cmd_inquiry_list, "inquiry-stats": cmd_inquiry_stats, "inquiry-by-product": cmd_inquiry_by_product,
    "inquiry-fields": cmd_inquiry_fields, "ads-report": cmd_ads_report, "ads-tables": cmd_ads_tables, "ads-products": cmd_ads_products,
    "keywords": cmd_keywords, "bids": cmd_bids, "analytics-products": cmd_analytics_products, "ads-conversions": cmd_ads_conversions, "ads-conversion-fields": cmd_ads_conversion_fields, "ads-analyze": cmd_ads_analyze, "ads-quality": cmd_ads_quality, "coverage": cmd_coverage,
    "keyword-seeds": cmd_keyword_seeds, "keywords-recommend": cmd_keywords_recommend,
}


# -- zero-period notices ----------------------------------------------------------------------------
AD_COMMANDS = {"ads-report", "ads-tables", "ads-products", "ads-conversions", "ads-analyze", "ads-quality", "bids", "keywords-recommend"}
INQUIRY_COMMANDS = {"inquiry-list", "inquiry-stats", "inquiry-by-product"}


def zero_notices(dump, command):
    """One line each when the period itself is empty, so a 0-row table is not read as a failure."""
    period = "%s~%s" % (dump.start or "", dump.end or "")
    out = []
    if command in AD_COMMANDS and "ads-report" in dump.pages():
        # The AI (GGS whole-site) account table reads 0 even on spending days: count campaign and product rows too.
        rows = dump.rows_of(ACCOUNT_TABLES + CAMPAIGN_TABLES + PRODUCT_TABLES)
        cost = sum(fnum(r.get("cost")) for _, r in rows)
        clicks = sum(fnum(r.get("clickCnt")) for _, r in rows)
        if not rows:
            out.append("안내: 이 기간(%s) 광고 일별표 0행 — 광고 기록 없음" % period)
        elif cost == 0 and clicks == 0:
            out.append("안내: 이 기간(%s) 광고비·클릭 0(광고 꺼짐) — 계정·캠페인·상품 일별표 %d행이 전부 0" % (period, len(rows)))
    if command in INQUIRY_COMMANDS and "inquiries" in dump.pages() and not dump.inquiries:
        times = [fnum(it.get("createTime")) for it in dump.inquiries_all if it.get("createTime")]
        if times:
            last = dt.datetime.fromtimestamp(max(times) / 1000, ia.US).date().isoformat()
            out.append("안내: 기간 안 문의 0건(마지막 문의 %s, 받은 목록 %d건)" % (last, len(times)))
        else:
            out.append("안내: 기간 안 문의 0건(받은 문의 목록도 0건)")
    if command == "analytics-products" and "analytics-product" in dump.pages() and not any(
            fnum(k["views"]) or fnum(k["clicks"]) for k in dump.keyword_effect):
        out.append("안내: 이 기간(%s) Product Analyzer 검색 노출·클릭 0" % period)
    return out


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
            if name in ("_head", "_exit"):
                continue
            path = os.path.join(a.out, name[1:] if name.startswith("_") else name + ".csv")
            with open(path, "w", newline="", encoding="utf-8") as fh:
                fh.write(rows if isinstance(rows, str) else to_csv(rows) if rows else "")
    if "_head" in tables and a.format == "table":
        sys.stdout.write(tables["_head"])
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
            if name.startswith("_") and name not in ("_head", "_exit"):
                sys.stdout.write("\n" + text)
        if a.out:
            print("written: " + a.out)


def main(argv=None):
    if hasattr(signal, "SIGPIPE"):  # not on Windows
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
    p.add_argument("--top", type=int, help="keywords-recommend: keywords per product (default 10)")
    p.add_argument("--product", help="keywords-recommend / keyword-seeds: product id or part of its title")
    p.add_argument("--all-products", action="store_true", help="keywords-recommend: every product")
    p.add_argument("--max-seeds", type=int, help="keyword-seeds: how many seeds (default 5)")
    a = p.parse_args(argv)
    for d in a.dirs:
        if not os.path.isdir(os.path.join(d, "raw")):
            raise SystemExit("not a dump folder (no raw/): " + d)
    dump = Dump(a.dirs, a.start, a.end)
    tables = COMMANDS[a.command](dump, a)
    for line in zero_notices(dump, a.command):
        print(line, file=sys.stderr)
    emit(tables, a)
    return tables.get("_exit", 0)


if __name__ == "__main__":
    sys.exit(main())
