#!/usr/bin/env python3
"""Read-only queries over one or more seller_central_raw.py dump folders.

  python3 report.py <command> --from DIR [--from DIR ...] [--start D --end D] [--format table|json|csv] [--out DIR]

Commands: inquiry-list inquiry-stats inquiry-by-product inquiry-fields ads-report ads-products
          ads-conversions ads-conversion-fields keywords bids analytics-products ads-analyze coverage

Several --from folders (date windows of one long range) are merged: rows are de-duplicated by
their identity (table, product, campaign, keyword, day; inquiry id). Rows dated outside
--start..--end are dropped. Every output row carries query_start / query_end.
No buyer name, subject, message text, email or phone exists in a dump (http_collect.py allowlist),
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
import ads_facts as ar
from ad_capture_status import response_error
from store_metrics import report_store

PRODUCT_TABLES = ("component_ggs_whole_site_offline_product", "component_ggs_offline_product")
CAMPAIGN_TABLES = ("component_ggs_whole_site_offline_campaign",)
ACCOUNT_TABLES = ("component_ggs_whole_site_offline_account", "component_ggs_offline_account")
IDENTITY = ("productId", "campaignId", "campaignType", "channelType", "keyword", "searchWord", "adGroupId", "timeUs")
CONV_TABLE = "component_ggs_ad_shop_conv_detail"  # one row per AI conversion (shop-wide, campaignType 51)
POSITIONAL = {CONV_TABLE}  # rows have no stored id: identity = window + page + position
DATE_KEYS = ("timeUs", "date", "statDate", "day", "dt")
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
    return a / b if a is not None and b else None


def raw_num(value):
    value = ia.main_val(value)
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def raw_sum(values):
    values = [n for value in values if (n := raw_num(value)) is not None]
    return sum(values) if values else None


def inquiry_economics(spend, clicks, inquiries, spend_source, inquiries_source):
    cpc = div(spend, clicks) if spend is not None else None
    rate = div(inquiries, clicks) if inquiries is not None else None
    cost = div(spend, inquiries) if spend is not None else None
    return {"cpc_usd": r2(cpc), "cpc_usd_numerator": spend, "cpc_usd_denominator": clicks,
            "cpc_source": spend_source + " ÷ clicks",
            "conversion_rate": round(rate, 4) if rate is not None else None,
            "conversion_rate_numerator": inquiries, "conversion_rate_denominator": clicks,
            "conversion_rate_source": inquiries_source + " ÷ clicks",
            "cost_per_inquiry_usd": r2(cost), "cost_per_inquiry_usd_numerator": spend,
            "cost_per_inquiry_usd_denominator": inquiries,
            "cost_per_inquiry_by_cpc_usd": r2(cost) if clicks else None,
            "cost_per_inquiry_by_cpc_usd_numerator": cpc,
            "cost_per_inquiry_by_cpc_usd_denominator": rate,
            "cost_per_inquiry_source": "no_inquiry" if inquiries == 0 else
                "광고비 없음" if spend is None else spend_source + " ÷ " + inquiries_source}


class Dump:
    """One merged view over dump folders, limited to start..end (inclusive ISO dates)."""

    def __init__(self, dirs, start=None, end=None, selection=None):
        self.dirs = dirs
        self.selection = selection
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

    def selected(self, directory, page):
        if self.selection is None:
            return None
        return [s for s in self.selection if s["dir"] == directory and page in s["pages"]]

    def records(self, page):
        for _directory, _observed_at, record in self.located_records(page):
            yield record

    def located_records(self, page):
        from urllib.parse import parse_qs
        for d in self.dirs:
            selected = self.selected(d, page)
            if selected == [] and page != 'keyword-index':
                continue
            manifest = self._manifest(d)
            pages = manifest.get("pages") or []
            meta = next((p for p in pages if isinstance(p, dict) and p.get("key") == page), {})
            observed = meta.get("fetchedAt") or manifest.get("generatedAt")
            for record in ia.rows(os.path.join(d, "raw", page + ".jsonl")):
                rp = record.get('replay') or {}
                if selected and page == 'ads-report' and '/component/data' in record.get('url', ''):
                    allowed = {t for item in selected for t in item.get('tables', [])}
                    if allowed and rp.get('tag') not in allowed:
                        continue
                if selected and rp.get('kind') in ('extra', 'page'):
                    field = 'extras' if rp['kind'] == 'extra' else 'requests'
                    allowed = {t for item in selected for t in item.get(field, [])}
                    if allowed and not any(rp.get('tag', '') == t or rp.get('tag', '').startswith(t + '.') for t in allowed):
                        continue
                yield d, observed, record

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
        records = sorted(self.records("ads-report"), key=lambda r: (
            not bool((r.get("replay") or {}).get("tag")),
            (r.get("replay") or {}).get("kind") != "range"))
        for r in records:
            if "dynamic/component/data" not in (r.get("url") or ""):
                continue
            if response_error(r):
                continue
            q = (((r.get("json") or {}).get("data") or {}).get("queryResult")) or {}
            rp = r.get("replay") or {}
            table = rp.get("table") or self._table_of(r)
            if not table:
                continue
            from urllib.parse import parse_qs
            try:
                request = json.loads(parse_qs(r.get("body") or "").get("data", ["{}"])[0])
            except ValueError:
                request = {}
            scope = json.dumps(request.get("filters") or [], sort_keys=True, separators=(",", ":"))
            meta[table]["responses"] += 1
            meta[table]["reported_total"] = max(meta[table]["reported_total"], int(fnum(q.get("totalRows"))))
            query = query_filters(r)
            for pos, d in enumerate(q.get("detailList") or []):
                row = {k: ia.main_val(v) for k, v in d.items()}
                row["_query_filters"] = scope
                row["_query_tag"] = rp.get("tag") or ""
                row["_query_granularity"] = (request.get("time") or {}).get("granularity", "DAY")
                if table in POSITIONAL:
                    key = (table, rp.get("start"), rp.get("end"), rp.get("page"), pos)
                    if key in seen or not self.in_range(conv_day(row)):
                        continue
                    seen.add(key)
                    out[table].append(row)
                    continue
                if not self.in_range(row.get("timeUs")):
                    continue
                # Same query: only a byte-identical row is a duplicate (rows sharing productId/keyword/day can
                # differ in other columns, e.g. search terms per campaign). Another query on the same table
                # (overlapping filters): skip rows whose identity an earlier query already gave.
                full = (table, query, json.dumps(row, sort_keys=True))
                ident = tuple(str(row.get(k, "")) for k in IDENTITY)
                ident = (table, scope) + (ident if any(ident) else (full[2],))
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

    @property
    def operation_days(self):
        # An event does not establish the entire day's state. Only join an explicit
        # campaign identity, never guess it from HTML log descriptions.
        out = {}
        for event in ar.history(self):
            raw = event["raw"]
            cid = raw.get("campaignId")
            if cid is None:
                continue
            item = out.setdefault((str(cid), event["date"]), {
                "changes": [], "operations_reason": "당일 변경 raw만 존재; 일별 상태 스냅샷 없음"})
            item["changes"].append(raw)
        return out

    # -- campaigns ------------------------------------------------------------------------
    @functools.cached_property
    def campaigns(self):
        """{campaign id: config} from campaign list responses (ads-campaigns, ads-home)."""
        out = {}
        for page in ("ads-campaigns", "ads-home"):
            for _directory, observed, r in self.located_records(page):
                if "/campaign/type/allList" not in r.get("url", ""):
                    continue
                data = (r.get("json") or {}).get("data")
                if not isinstance(data, dict):
                    continue
                for ctype, items in data.items():
                    for c in items if isinstance(items, list) else []:
                        if not isinstance(c, dict) or "id" not in c or "properties" not in c:
                            continue
                        p = c.get("properties") or {}
                        previous = out.get(str(c["id"]))
                        if previous and (previous.get("observed_at") or "") > (observed or ""):
                            continue
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
                            "daily_budget": p.get("dailyBudget"), "bid": p.get("maxPrice"),
                            "budget_cycle_days": p.get("cycle") or c.get("cycle"),
                            "budget_reason": "dailyBudget raw 없음; budget은 캠페인 주기 예산일 수 있음" if p.get("dailyBudget") is None else "",
                            "products": c.get("productList"), "keywords": c.get("keywordList"),
                            "raw": c, "note": "조회 시점 설정; 과거 일별 상태가 아님",
                            "observed_at": observed,
                        }
        return out

    @functools.cached_property
    def campaign_targets(self):
        out = {}
        for _, observed, record in self.located_records("ads-campaigns"):
            rp = record.get("replay") or {}
            if rp.get("kind") != "campaign-targets":
                continue
            key = (str(rp.get("campaignId")), rp.get("path"))
            previous = out.get(key)
            if previous and (previous["observed_at"] or "") > (observed or ""):
                continue
            payload = record.get("json") or {}
            out[key] = {"campaign_id": key[0], "source_path": key[1], "observed_at": observed,
                        "ok": payload.get("ok"), "raw": payload.get("data")}
        return list(out.values())

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
    def inquiry_extra(self):
        out = {}
        for r in self.records("inquiries"):
            if (r.get("replay") or {}).get("kind") == "inquiry-extra":
                for item in (r.get("json") or {}).get("list", []):
                    out[str(item.get("tradeId"))] = item
        return out

    @functools.cached_property
    def inquiry_profiles(self):
        out = {}
        for r in self.records("inquiries"):
            if (r.get("replay") or {}).get("kind") == "inquiry-profile":
                j = r.get("json") or {}
                for item in j.get("list", []):
                    out[str(item.get("tradeId"))] = {**item, "captured_at": j.get("capturedAt", ""), "activity_window": j.get("activityWindow", "")}
        return out

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
            if ((r.get("replay") or {}).get("table") or self._table_of(r)) == CONV_TABLE and not response_error(r):
                names |= set((((r.get("json") or {}).get("data") or {}).get("queryResult") or {}).get("fieldNames") or [])
        return sorted(names)

    @functools.cached_property
    def ad_days(self):
        days, titles = collections.defaultdict(collections.Counter), {}
        for fact in ar.daily(self, "product"):
            row = fact["raw"]
            pid, day = str(row.get("productId") or ""), str(row.get("timeUs") or "")[:10]
            if not pid or not day:
                continue
            c = days[(pid, day)]
            for col, name in (("impsCnt", "ad_impressions"), ("clickCnt", "ad_clicks"), ("mcCnt", "ad_inquiries"),
                              ("atmSessionCnt", "ad_tm_chats"), ("orderCnt", "ad_orders")):
                value = ar.number(row.get(col))
                c[name] = None if value is None or name in c and c[name] is None else c.get(name, 0) + value
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
        """Allowlisted inquiry rows in range; only explicit product IDs establish a product link."""
        by_day = ia.ad_days_by_day(self.ad_days[0])
        out = []
        for it in sorted(self.inquiries_all, key=lambda x: x.get("createTime") or 0):
            t, day, direct, match, prods = ia.match_inquiry(it, by_day)
            if not self.in_range(day):
                continue
            lead_type, entry, device, ad = ia.lead_source(it, match)
            profile = self.inquiry_profiles.get(str(it.get("tradeId")), {})
            out.append({
                "inquiry_id": str(it.get("id")), "trade_id": str(it.get("tradeId") or ""),
                "created_us_pacific": t.isoformat(timespec="minutes") if t else "", "us_date": day,
                "lead_type": lead_type, "type_code": it.get("feedbackType"), "lead_entry": entry, "source_code": it.get("source"),
                "device": device, "product_id": direct, "product_id_reason": ia.product_id_reason(it, direct), "inflow": ia.inflow_of(it, direct),
                "buyer_country": it.get("buyerCountry") or profile.get("country") or "", "buyer_level": level_code(profile.get("growthLevel")) or level_code(self.inquiry_extra.get(str(it.get("tradeId")), {}).get("userNewLevel")) or buyer_level(it),
                "buyer_level_source": "queryCustomerInfo.buyerInfo.growthLevelInfo.growthLevel" if level_code(profile.get("growthLevel")) else "subjectListExtraInfo.sender.userNewLevel / subjectList buyerAttrs",
                "buyer_level_reason": "" if level_code(profile.get("growthLevel")) or level_code(self.inquiry_extra.get(str(it.get("tradeId")), {}).get("userNewLevel")) or buyer_level(it) else "바이어 등급 응답 없음",
                "viewed_product_ids": [str(p["productId"]) for p in profile.get("productVisitHistory") or [] if p.get("productId")],
                "viewed_products_reason": profile.get("viewedProductsReason") or profile.get("reason") or ("" if profile.get("productVisitHistory") else "고객 패널 미수집"),
                "spam_inquiry_count_90d": profile.get("spamInquiryMarkedBySupplierCount"),
                "blacklist_count_90d": profile.get("addedToBlacklistCount"),
                "product_views_90d": profile.get("productViewCount"),
                "buyer_activity_captured_at": profile.get("captured_at", ""),
                "buyer_activity_source": "queryCustomerInfo 고객 활동 최근 90일(문의 발생 기간과 별도)",
                "reply_status": it.get("replyStatus"), "unread": bool(it.get("hasUnread") or fnum(it.get("unreadCount")) > 0),
                "spam": it.get("spam"), "spam_reason": "" if it.get("spam") is not None else "subjectList spam 필드 없음",
                "buyer_activity_reason": "; ".join(k + " raw 없음" for k in ("spamInquiryMarkedBySupplierCount", "addedToBlacklistCount", "productViewCount") if profile.get(k) is None) if profile.get("available") else profile.get("reason", "고객 패널 미수집"), "match": match, "ad": ad, "matched_products": " ".join(prods),
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
                    if "productId" not in first or not {"cost", "costStr"}.intersection(first):
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
                    "ad_product_clicks_on_date": days[(ad_pid, day)].get("ad_clicks") if (ad_pid, day) in days else None,
                    "ad_product_clicks_reason": "" if (ad_pid, day) in days and days[(ad_pid, day)].get("ad_clicks") is not None else "상품·일자 클릭 raw 없음",
                    "converted_scope": "product" if conv_pid else "",
                    "converted_product_id_reason": "" if conv_pid else ("fbProdId 원본 값 없음; 전환 상품 식별 불가" if "fbProdId" in row else "fbProdId 원본 키 없음; 전환 상품 식별 불가"),
                    "converted_product_id": conv_pid,
                    "converted_product_name": (real_value(row.get("fbProductName")) or dump.titles.get(conv_pid, "")) if conv_pid else "",
                    "buyer_country": row.get("countryId") or "",
                    "buyer_level": level_code(row.get("buyerLevel")), "growth_level": level_code(row.get("senderMbrGrowthLevel")),
                    "channel": row.get("channelDisplay") or "",
                    "channel_reason": "" if real_value(row.get("channelDisplay")) else ("channelDisplay 원본 값 없음" if "channelDisplay" in row else "저장 응답에 channelDisplay 키 없음; 원본 키는 ads conversion-fields로 확인"), "conversion_type": row.get("fbType") or "",
                    "is_repeat": row.get("isRepeat"), "is_low_quality": row.get("isLowQuality"), "is_punish": row.get("isPunish"),
                    "source": "실측: %s (상점 전체 AI 전환, campaignType 51). 날짜 = chatTime 앞 10자(시간대 표기 없음). "
                              "이름 \"0\"/빈칸 → 상품표 제목. fbProdId 없으면 전환 범위 판정 불가" % CONV_TABLE})
    return out


def l3plus(level):
    return LEVEL_RANK.get(level, -1) >= 3


def buyer_level(it):
    if level_code(it.get("buyerLevel")):
        return level_code(it["buyerLevel"])
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
L1_SOURCE = "광고표 fiGrowthClickCnt(L1+ Clicks) 합 ÷ clickCnt 합 × 100 (화면 L1+ Buyer Click Percentage)"


L1_MISSING = "표에 L1+ 클릭 수(fiGrowthClickCnt) 열 없음 — 0% 아님"


def l1plus(l1_clicks, clicks, seen=True):
    """L1+ Buyer Click Percentage recomputed from summed counts (never an average of daily rates).
    seen=False: no source row carried fiGrowthClickCnt, so the share is unknown, not 0."""
    if not seen or l1_clicks is None:
        return {"l1plus_click_pct": None, "l1plus_click_numerator": None, "l1plus_click_denominator": clicks, "l1plus_clicks": None, "l1plus_source": L1_MISSING}
    return {"l1plus_click_pct": r2(div(l1_clicks * 100, clicks)), "l1plus_click_numerator": l1_clicks, "l1plus_click_denominator": clicks, "l1plus_clicks": int(l1_clicks),
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
    out = []
    # Campaign aggregates use the same dated facts as ads daily --level campaign.
    # Keep account observations separate: account and campaign are overlapping scopes.
    for scope in ("campaign", "account", "product"):
        daily = ar.daily(dump, scope, deduplicate_products=False)
        for row in daily:
            row["month"] = row["date"][:7]
            row["campaign_kind"] = campaign_kind(row["source_table"], row["raw"])
        levels = [("month", ("source_table", "month", "campaign_kind")), ("total", ("source_table", "campaign_kind"))]
        if scope == "campaign":
            levels = [("campaign", ("source_table", "campaign_id", "campaign_kind")), ("kind_total", ("campaign_kind",))]
        for level, keys in levels:
            for key, rows in ar.groups(daily, keys):
                m = ar.metrics(rows)
                out.append({"query_start": dump.start, "query_end": dump.end, "level": level,
                    "scope": scope, "key": key.get("campaign_id") or key.get("month") or "",
                    "campaign_kind": key["campaign_kind"], **m,
                    "spend_usd": m["spend"], "inquiries_cvr3": m["conversions"],
                    "table": key.get("source_table", ""), "daily_rows": len(rows)})
    return out


def product_performance(dump):
    conv_by = collections.defaultdict(list)
    for c in conversions(dump):
        conv_by[c["ad_product_id"]].append(c)
    out = []
    for key, rows in ar.groups(ar.daily(dump, "product"), ("product_id",)):
        pid = key["product_id"]
        if not pid:
            continue
        m = ar.metrics(rows)
        cv = conv_by[pid]
        out.append({"query_start": dump.start, "query_end": dump.end, **key,
            "product_title": dump.titles.get(pid, ""), **m,
            "spend_actual_usd": m["spend"],
            "spend_actual_source": "일별 상품 raw cost/costStr 합" if m["spend"] is not None else "일별 상품 raw에 cost/costStr 누락",
            "ad_inquiries_mcCnt": m["inquiries"], "ad_tm_chats_atmSessionCnt": m["tm_chats"],
            "inquiries_cvr3": m["conversions"],
            "inquiries_source": "mcCnt = 광고 문의; cvr3 = 전환(문의·TM 등 포함)",
            "cost_per_inquiry_source": "no_inquiry" if m["inquiries"] == 0 else "실제 cost 합 ÷ mcCnt 합",
            "cost_basis": "실측" if m["spend"] is not None else "",
            "conv_detail_count": len(cv),
            "conv_detail_growth_levels": count_str(collections.Counter(c["growth_level"] or "?" for c in cv)),
            "conv_detail_countries": count_str(collections.Counter(c["buyer_country"] or "?" for c in cv)),
            **ar.ratio("conv_detail_l3plus_share_pct", "conv_detail_l3plus_count", "conv_detail_denominator",
                       sum(1 for c in cv if l3plus(c["growth_level"])), len(cv), 100),
            "conv_detail_source": "실측: " + CONV_TABLE if cv else "",
            "bid_usd": None, "bid_source": "상품별 입찰가 raw 없음",
            "daily_rows": len(rows), "cost_filled_rows": sum(r["spend"] is not None for r in rows)})
    out.sort(key=lambda r: (-(r["inquiries_cvr3"] or 0), -(r["clicks"] or 0)))
    return out, []


def inquiry_quality(dump):
    groups = collections.defaultdict(list)
    for q in dump.inquiries:
        if q["product_id"]:
            groups[(q["product_id"], "직접(상품 문의)")].append(q)
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
            **ar.ratio("l3plus_share_pct", "l3plus_numerator", "known_level_count", sum(1 for l in known if LEVEL_RANK[l] >= 3), len(known), 100),
            "countries": count_str(collections.Counter(c["buyer_country"] for c in cs)),
            "spam": None, "low_quality_count": sum(1 for c in cs if is_true(c["is_low_quality"])),
            "reply_status": "", "app": "", "web": "",
            "conversion_types": count_str(collections.Counter(c["conversion_type"] for c in cs)),
            "channels": count_str(collections.Counter(c["channel"] for c in cs)),
            "repeat": sum(1 for c in cs if is_true(c["is_repeat"])),
            "source": "실측: %s (low_quality_count = isLowQuality, 등급 = senderMbrGrowthLevel)" % CONV_TABLE,
        })
    for (pid, link), qs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        levels = collections.Counter(q["buyer_level"] or "미수집" for q in qs)
        known = [q["buyer_level"] for q in qs if q["buyer_level"] in LEVEL_RANK]
        out.append({
            "query_start": dump.start, "query_end": dump.end, "product_id": pid, "product_title": dump.titles.get(pid, ""),
            "link": link, "inquiries": len(qs), "buyer_levels": count_str(levels),
            **ar.ratio("l3plus_share_pct", "l3plus_numerator", "known_level_count", sum(1 for l in known if LEVEL_RANK[l] >= 3), len(known), 100),
            "countries": count_str(collections.Counter(q["buyer_country"] for q in qs)),
            "spam": sum(is_true(q["spam"]) for q in qs), "reply_status": count_str(collections.Counter(str(q["reply_status"]) for q in qs)),
            "app": sum(q["device"] == "앱" for q in qs), "web": sum(q["device"] == "웹" for q in qs),
            "source": "inquiries subjectList (상품 직접 연결 기록)",
        })
    return out


def bid_analysis(dump, products):
    """Only actual metrics at the same scope; never replace a bid with CPC."""
    out = []
    for level, keys in (("campaign", ("source_table", "campaign_id")),
                        ("keyword", ("source_table", "campaign_id", "product_id", "keyword"))):
        for key, rows in ar.groups(ar.daily(dump, level), keys):
            meta = dump.campaigns.get(key.get("campaign_id"), {}) if level == "campaign" else {}
            out.append({"query_start": dump.start, "query_end": dump.end, "level": level, **key,
                        **ar.metrics(rows), "bid_usd": meta.get("bid_usd"),
                        "bid_unit": meta.get("bid_unit"), "bid_source": meta.get("bid_field"),
                        "bid_note": "조회 시점 설정; 과거 입찰가 raw 없음"})
    return out


def top_products(dump, products, quality):
    rows = sorted((p for p in products if p["cost_per_inquiry_usd"] is not None),
                  key=lambda p: p["cost_per_inquiry_usd"])
    return [{"rank": n, **p} for n, p in enumerate(rows, 1)]


def summary_md(dump, products, top, undated, bq=()):
    rows = ar.daily(dump, "campaign")
    cost = ar.total(rows, "spend")
    return ("# 광고 분석 요약\n\n- 기간: %s ~ %s\n- 캠페인 일별 raw 광고비 합: %s\n"
            "- 상품 광고비 채워진 행: %d / %d. 일별 raw에 없는 값은 빈칸.\n"
            "- 문의 비용 = cost 합 ÷ mcCnt 합. cvr3 전환 수는 별도 열.\n"
            "- L1+ 비율 = fiGrowthClickCnt 합 ÷ clickCnt 합 × 100.\n" %
            (dump.start, dump.end, cost if cost is not None else "빈칸", sum(p["spend_actual_usd"] is not None for p in products), len(products)))


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
        # An inquiry's product_id is distinct from the customer's actual visit history.
        ids = r["viewed_product_ids"]
        risk = ["스팸 표시" if is_true(r["spam"]) else ""]
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
            "viewed_products_source": "queryCustomerInfo.buyerShopBehaviorInfo.productVisitHistory" if ids else r["viewed_products_reason"],
            "risk_source": "목록 spam; 고객 패널 spamInquiryMarkedBySupplierCount·addedToBlacklistCount(최근 90일)",
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
    detail = [{k: q[k] for k in ("inflow", "inquiry_id", "created_us_pacific", "lead_type", "lead_entry", "device", "buyer_country", "buyer_level", "reply_status", "match", "matched_products", "spam", "spam_inquiry_count_90d", "blacklist_count_90d", "viewed_product_ids", "viewed_products_reason")} for q in dump.inquiries]
    return {"inquiry_by_product_counts": q_rows(dump, rows), "inquiry_by_product": q_rows(dump, detail)}


def cmd_inquiry_fields(dump, a):
    names = dump.inquiry_field_names
    level = [n for n in names if any(x in n.lower() for x in ("level", "lvl", "grade"))]
    return {"inquiry_fields": [{"field": n, "level_candidate": n in level} for n in names] or
            [{"field": "", "level_candidate": False, "note": "이 덤프는 fieldNames 수집 전 버전: dump --pages inquiries 로 다시 받으면 키 이름이 나온다"}]}


def daily_output(dump, level):
    result = []
    for row in ar.daily(dump, level):
        calculated = ar.metrics([row])
        result.append({**{k: v for k, v in row.items() if k not in calculated}, **calculated})
    return q_rows(dump, result)


def cmd_ads_daily(dump, a):
    rows = daily_output(dump, a.level)
    return {"ad_daily": rows, "daily_coverage": [{
        "level": a.level, "rows": len(rows),
        "cost_filled_rows": sum(r["spend"] is not None for r in rows),
        "inquiries_filled_rows": sum(r["inquiries"] is not None for r in rows),
        "l1plus_filled_rows": sum(r["l1plus_clicks"] is not None for r in rows),
        "operations_filled_rows": sum(r["online_status"] is not None for r in rows),
    }]}


def cmd_ads_campaigns(dump, a):
    return {"campaigns": list(dump.campaigns.values()), "campaign_targets": dump.campaign_targets,
            **({"_exit": 3} if any(r["ok"] is not True for r in dump.campaign_targets) else {})}


def cmd_ads_history(dump, a):
    coverage = ar.history_coverage(dump)
    return {"history": ar.history(dump), "history_coverage": coverage,
            **({"_exit": 3} if any(not r["complete"] for r in coverage) else {})}


def cmd_ads_product_inquiries(dump, a):
    linked = collections.defaultdict(list)
    for inquiry in dump.inquiries:
        # Product identity only. Date coincidences do not prove ad attribution.
        if inquiry["product_id"]:
            linked[inquiry["product_id"]].append(inquiry)
    out = []
    for product in product_performance(dump)[0]:
        matches = linked[product["product_id"]]
        for inquiry in matches or [{}]:
            out.append({**product, "linked_inquiry_count": len(matches),
                "inquiry_id": inquiry.get("inquiry_id"), "inquiry_date": inquiry.get("us_date"),
                "buyer_country": inquiry.get("buyer_country"), "buyer_level": inquiry.get("buyer_level"),
                "link_basis": "문의 raw product ID 일치; 광고 기여 여부 미확인" if inquiry else "상품 ID가 일치하는 문의 raw 없음"})
    return {"product_inquiries": out}


def cmd_ads_report(dump, a):
    return {level + "_day": daily_output(dump, level)
            for level in ("account", "campaign", "product", "keyword")}


def range_completeness(dump):
    """Per ads report table and query window: rows got vs totalRows the API reported."""
    from urllib.parse import parse_qs
    seen = {}
    errors = {}
    for r in dump.records("ads-report"):
        rp = r.get("replay") or {}
        if rp.get("kind") != "range":
            continue
        try:
            d = json.loads(parse_qs(r.get("body") or "").get("data", ["{}"])[0])
        except ValueError:
            continue
        request_key = (rp.get("table") or d.get("tableName", ""), json.dumps(d.get("filters") or [], sort_keys=True), rp.get("start"), rp.get("end"), rp.get("page", 1), (d.get("time") or {}).get("granularity", "DAY"))
        error = response_error(r)
        if error:
            errors[request_key] = {"table": rp.get("table") or d.get("tableName", ""), "range_table_key": rp.get("tag") or "",
                           "window_start": rp.get("start"), "window_end": rp.get("end"), "rows_got": None, "rows_total_api": None,
                           "complete": "NO (capture failed)", "reason": error}
            continue
        errors.pop(request_key, None)
        q = r["json"]["data"]["queryResult"]
        key = (d.get("tableName", ""), json.dumps(d.get("filters") or [], ensure_ascii=False), rp.get("start"), rp.get("end"), (d.get("time") or {}).get("granularity", "DAY"))
        size = int((d.get("page") or {}).get("size") or len(q["detailList"]) or 1)
        if rp.get("split"):  # per-day refetch of a table whose pages repeated rows: folded into its window below
            key = ("split",) + key
        e = seen.setdefault(key, {"tag": rp.get("tag") or "", "covered": 0, "total": 0, "pages": 0, "rows": 0, "distinct": set()})
        e["covered"] = max(e["covered"], (int(rp.get("page") or 1) - 1) * size + len(q["detailList"]))
        e["rows"] += len(q["detailList"])
        e["distinct"] |= {json.dumps(row, sort_keys=True) for row in q["detailList"]}
        e["total"] = max(e["total"], int(fnum(q.get("totalRows"))))
        e["pages"] += 1
    split = collections.defaultdict(dict)
    for key in [k for k in seen if k[0] == "split"]:
        _, table, filters, day, _end, granularity = key
        split[(table, filters, granularity)][day] = seen.pop(key)
    out = []
    for (table, filters, start, end, granularity), e in sorted(seen.items()):
        # distinct rows, not last page x size: pages that repeat rows (unstable API order) leave rows unfetched
        distinct, days = set(e["distinct"]), split.get((table, filters, granularity), {})
        inside = {day: x for day, x in days.items() if start <= day <= end}
        for x in inside.values():
            distinct |= x["distinct"]
        span = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days + 1 if start and end else 0
        days_ok = sum(1 for x in inside.values() if len(x["distinct"]) >= x["total"])
        got = len(distinct)
        ok = got == e["total"] and (days_ok == span if inside else e["rows"] == got)
        out.append({"table": table, "range_table_key": e["tag"], "filters": filters, "window_start": start, "window_end": end, "granularity": granularity,
                    "rows_got": got, "rows_total_api": e["total"], "pages": e["pages"] + sum(x["pages"] for x in inside.values()),
                    "repeated_rows": e["rows"] - len(e["distinct"]), "last_page_reach": e["covered"],
                    "split_days": "%d/%d" % (days_ok, span) if inside else "",
                    "complete": "yes" if ok else "NO (%d short)" % max(e["total"] - got, 0) if got < e["total"] else
                                "NO (일별 재수집 %d/%d일)" % (days_ok, span) if inside else "NO (count or duplicate mismatch)"})
    for directory in dump.dirs:
        for meta in dump._manifest(directory).get("pages", []):
            if not isinstance(meta, dict) or meta.get("key") != "ads-report":
                continue
            tags = {(r.get("replay") or {}).get("tag") for r in ia.rows(os.path.join(directory, "raw", "ads-report.jsonl"))}
            missing = set(meta.get("rangeTables") or []) - tags
            if meta.get("status") != "ok" or meta.get("rangeIncomplete") or missing:
                errors[(directory, "manifest")] = {"table": "", "range_table_key": ",".join(sorted(missing)), "rows_got": None, "rows_total_api": None,
                               "complete": "NO (capture incomplete)", "reason": "manifest: 광고표 수집 실패/미완료; 다시 수집 필요"}
    if not out and not errors and "ads-report" in dump.pages():
        errors["missing"] = {"table": "", "range_table_key": "", "rows_got": None, "rows_total_api": None,
                             "complete": "NO (no range response)", "reason": "광고 기간 요청 응답 미수집; 0행 확인 불가"}
    return out + list(errors.values())


def date_set(start, end):
    if not start or not end:
        return set()
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    return {(s + dt.timedelta(days=n)).isoformat() for n in range(max(0, (e-s).days+1))}


def period_coverage(dump):
    groups = collections.defaultdict(list)
    for row in range_completeness(dump):
        groups[(row["table"], row.get("filters", ""), row.get("granularity", "DAY"))].append(row)
    out = []
    expected = date_set(dump.start, dump.end)
    for (table, filters, granularity), windows in sorted(groups.items()):
        requested, complete_days, overlap = set(), set(), 0
        for w in windows:
            days = date_set(w.get("window_start"), w.get("window_end")) & expected
            overlap += len(requested & days)
            requested |= days
            if w["complete"] == "yes":
                complete_days |= days
        out.append({"table": table, "filters": filters, "query_start": dump.start, "query_end": dump.end,
                    "granularity": granularity, "windows": windows, "requested_days": len(requested), "overlapping_window_days": overlap,
                    "missing_request_days": len(expected-requested), "unverified_days": len(expected-complete_days),
                    "note": "totalRows 일치는 반환 행 완결성만 확인; 서버의 날짜 잘림은 별도 검증 필요"})
    # Direct HTTP manifests include explicit row/total evidence for every contract.
    direct_pages = set()
    for directory in dump.dirs:
        for page in dump._manifest(directory).get("pages", []):
            if page["key"] == "ads-report" or dump.selected(directory, page["key"]) == []:
                continue
            for c in page.get("coverage", []):
                direct_pages.add(page["key"])
                out.append({"table": c["table"], "query_start": dump.start, "query_end": dump.end,
                            "windows": [c], "rows_got": c["rowsGot"], "rows_total_api": c["totalRows"],
                            "complete": "yes" if c["complete"] else "미확인",
                            "missing_request_days": None, "unverified_days": len(expected),
                            "note": "목록/선택 주기 API: 행 완결성과 일자 완결성은 별개"})
    # Non-ads APIs expose period selectors or all-history lists, not daily report rows.
    for page in dump.pages():
        if page == "ads-report" or page in direct_pages:
            continue
        seen = {}
        for r in dump.records(page):
            rp = r.get("replay") or {}
            if rp.get("kind") not in ("extra", "inquiry"):
                continue
            tag = rp.get("tag") or page
            j = r.get("json") or {}
            rows = j.get("list") if rp.get("kind") == "inquiry" else ((j.get("value") or {}).get("products") or {}).get("data")
            if not isinstance(rows, list):
                rows = []
            e = seen.setdefault(tag, {"pages": {}, "total": None})
            e["pages"][rp.get("page", 1)] = len(rows)
            total = (j.get("pagination") or {}).get("totalCount") if rp.get("kind") == "inquiry" else ((j.get("value") or {}).get("products") or {}).get("recordCount")
            if isinstance(total, (int,float)):
                e["total"] = total
        for tag, e in seen.items():
            out.append({"table": tag, "query_start": dump.start, "query_end": dump.end,
                        "rows_got": sum(e["pages"].values()), "rows_total_api": e["total"],
                        "complete": "yes" if e["total"] is not None and sum(e["pages"].values()) == e["total"] and len(e["pages"]) == max(e["pages"], default=0) else "미확인",
                        "missing_request_days": None, "unverified_days": len(expected),
                        "note": "전체 목록/고정 주기 조회; 일자별 완결성 미확인"})
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
    camp = [r for r in ar.daily(dump, "campaign") if str(r["raw"].get("campaignType") or "") == "51"]
    camp_cost = ar.total(camp, "spend")
    camp_conv = ar.total(camp, "conversions")
    kept = [c for c in rows if not is_true(c["is_repeat"]) and not is_true(c["is_punish"])]
    has_channel = any("channelDisplay" in r for r in dump.ad_tables.get(CONV_TABLE, []))
    return {"query_start": dump.start, "query_end": dump.end,
            "campaign_table_conversions": camp_conv, "campaign_table_l3plus": ar.total(camp, "l3plus_inquiries"),
            "campaign_table_source": "실측: %s cvr3·l3PlusCvr3 일별 합 (화면 Conversions·L3+ 와 같은 표)" % CAMPAIGN_TABLES[0],
            "campaign_spend_usd": r2(camp_cost),
            **ar.ratio("spend_per_conversion_usd", "conversion_cost_sum", "conversion_count", camp_cost, camp_conv),
            "detail_rows": len(rows),
            "detail_l3plus_buyer_level": sum(1 for c in rows if l3plus(c["buyer_level"])),
            "detail_l3plus_growth_level": sum(1 for c in rows if l3plus(c["growth_level"])),
            "detail_valid_conversions": len(kept),
            "detail_l3plus": sum(1 for c in rows if l3plus(c["growth_level"])),
            "detail_count_source": "명세 raw 행 집계: 건수 = isRepeat·isPunish 아닌 행, L3+ = senderMbrGrowthLevel L3 이상(전체 행). "
                                      "화면 명세 카드가 senderMbrGrowthLevel 을 보여줌",
            "by_type": count_str(collections.Counter(c["conversion_type"] for c in rows)),
            "by_scope": count_str(collections.Counter(c["converted_scope"] or "?" for c in rows)),
            "by_growth_level": count_str(collections.Counter(c["growth_level"] or "?" for c in rows)),
            "by_buyer_level": count_str(collections.Counter(c["buyer_level"] or "?" for c in rows)),
            "channel_source": "channelDisplay" if has_channel else "없음: 응답 행에 channelDisplay 키 없음(화면 설정 이름). 실제 키 = ads conversion-fields",
            "source": "캠페인 광고비 또는 전환 raw 없음 또는 전환 0" if div(camp_cost, camp_conv) is None else "명세 = %s, 광고비 = %s cost 합 ÷ cvr3 합" % (CONV_TABLE, CAMPAIGN_TABLES[0])}


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
    for r in ad_keyword_rows(dump):
        if r["bid"] is not None:
            rows.append({"level": "keyword", "keyword": r["keyword"], "product_id": r["product_id"],
                         "bid_field": r["table"] + "." + r["bid_field"], "bid_usd": r["bid"]})
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
    add("캠페인 입찰가/목표 전환단가", bool(dump.campaigns), len(dump.campaigns), "ads-campaigns allList properties.maxPrice",
        " ".join("%s=%s(%s)" % (c["campaign_id"], c["bid_usd"], c["bid_unit"]) for c in dump.campaigns.values()))
    kw_ad = sum(len(v) for t, v in dump.ad_tables.items() if "keyword" in t)
    kw_bids = sum(r["bid"] is not None for r in ad_keyword_rows(dump))
    add("키워드 입찰가", bool(kw_bids), kw_bids, "ads-report Word Report 실제 입찰가 필드", "실제 입찰가 없음(키워드 광고 표 %d행)" % kw_ad if not kw_bids else "")
    add("상품별 유입 키워드(노출·클릭)", bool(dump.keyword_effect), len(dump.keyword_effect), "analytics-product keywordEffect (월별)")
    add("분석 키워드 목록", bool(dump.analytics_keywords), len(dump.analytics_keywords), "analytics-keyword getKeywords", "" if dump.analytics_keywords else "응답 total 0")
    inq = dump.inquiries
    add("문의 목록", bool(inq), len(inq), "message subjectList (허용 필드만)", "덤프 전체 %d건 중 기간 내" % len(dump.inquiries_all))
    add("바이어 국가", any(q["buyer_country"] for q in inq), sum(1 for q in inq if q["buyer_country"]), "sender.countryCode")
    lv = sum(1 for q in inq if q["buyer_level"])
    add("바이어 레벨(문의)", lv > 0, lv, "subjectListExtraInfo.sender.userNewLevel / subjectList buyerAttrs",
        "" if lv else "등급 응답 없음: dump --pages inquiries --fresh 로 추가 정보 요청 수집")
    cv = conversions(dump)
    add("AI 전환 명세(바이어 등급·국가·유입·유형)", bool(cv), len(cv), CONV_TABLE + " (허용 필드만)",
        "" if cv else "이 덤프에 전환 명세 표 없음 또는 0행: ads conversions 로 라이브 덤프")
    add("바이어 L3+ 전환(광고)", True, raw_sum(p["l3plus_inquiries"] for p in prods), "광고 상품표 l3PlusCvr3")
    add("스팸 여부", any(q["spam"] is not None for q in inq), sum(1 for q in inq if q["spam"] is not None), "subjectList spam")
    add("답장 상태", True, len(inq), "subjectList replyStatus")
    return {"coverage": rows, "period_coverage": period_coverage(dump)}


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
                     "ctr_pct": r2(div(m_["clicks"] * 100, m_["views"])),
                     "inquiry_rate_pct": r2(div(m_["inquiries"] * 100, m_["visitors"])),
                     "visitors_note": "기간별 방문자 수의 합(여러 기간에 온 같은 방문자는 중복)" if len(keys) > 1 else "",
                     "is_p4p_product": p.get("isP4pProd"), "is_p4p": p.get("isP4p"), "is_kwa_search": p.get("isKwSearch"),
                     "periods_listed": int(m_["periods_listed"]),
                     "source": "mydata getVipEffectiveProductsAndStats statisticsType=%s (selected=기간별)" % gran})
    rows.sort(key=lambda r: (-r["views"], -r["clicks"]))
    totals = {**head, **{name: sum(r[name] for r in rows) for name, _f in PRODUCT_METRICS}, "products": len(rows)}
    totals["ctr_pct"] = r2(div(totals["clicks"] * 100, totals["views"]))
    totals["inquiry_rate_pct"] = r2(div(totals["inquiries"] * 100, totals["visitors"]))
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
                        "click_rate_pct": pct(k.get("clickRate")), "click_rate_raw": k.get("clickRate"),
                        "click_rate_pct_numerator": None, "click_rate_pct_denominator": None,
                        "search_index_change_pct": pct(k.get("pvCrc")), "search_index_change_raw": k.get("pvCrc"),
                        "seller_scale_change_pct": pct(k.get("resultCntCrc")), "seller_scale_change_raw": k.get("resultCntCrc"),
                        "seller_scale_change_pct_numerator": None, "seller_scale_change_pct_denominator": None,
                        "search_index_change_pct_numerator": None, "search_index_change_pct_denominator": None,
                        "ratio_source": "Keyword Index가 비율만 반환. 원시 분자·분모 없음", "in_our_product_name": k.get("isInProdName"),
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
        bid_key = next((k for k in ("bidPrice", "bid", "maxPrice", "bidRate") if r.get(k) not in (None, "")), None)
        value = lambda *keys: next((fnum(r[k]) for k in keys if r.get(k) not in (None, "")), None)
        rows.append({"table": t, "kind": "검색어" if "searchword" in t else "키워드", "keyword": kw, "product_id": str(r.get("productId") or ""),
                     "day": r.get("timeUs") or "", "impressions": value("impsCnt"), "clicks": value("clickCnt"),
                     "cost": value("cost"), "inquiries": value("cvr3", "businessLeadsCnt"),
                     "l1plus_clicks": value("fiGrowthClickCnt"), "bid": r.get(bid_key) if bid_key else None, "bid_field": bid_key or ""})
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
        o = out.setdefault(pid, {"impressions": 0, "clicks": 0, "inquiries": 0, "l1plus_clicks": 0})
        for col, raw in (("impressions", "impsCnt"), ("clicks", "clickCnt"),
                         ("inquiries", "cvr3"), ("l1plus_clicks", "fiGrowthClickCnt")):
            o[col] = None if o[col] is None or r.get(raw) in (None, "") else o[col] + int(fnum(r[raw]))
    return out


def product_line(dump, pid, nums):
    """One line of this product's own period numbers (product level, not per keyword) and why keyword cells are blank."""
    _, ad_kw, _, own = nums
    a = product_ad_numbers(dump).get(pid)
    parts = []
    if a:
        show = lambda v: "" if v is None else str(v)
        pct = lambda n, d: show(r2(div(n * 100, d))) if n is not None else ""
        parts.append("광고 노출 %s·클릭 %s·CTR %s%%·문의 %s·L1+ %s%%(L1+ 클릭 %s)" % (
            show(a["impressions"]), show(a["clicks"]), pct(a["clicks"], a["impressions"]),
            show(a["inquiries"]), pct(a["l1plus_clicks"], a["clicks"]), show(a["l1plus_clicks"])))
        if any(v is None for v in a.values()):
            parts.append("빈칸: 광고 상품표 raw 숫자 누락")
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


KW_SCORE_FORMULA = ("점수 = 검색량² ÷ 경쟁 × (1 + 클릭) × (1 + 문의) × (1 + L1+% ÷ 100) ÷ (1 + 입찰가), 필수 숫자가 없으면 점수도 빈칸 "
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
    ad_rows = ad_keyword_rows(dump)
    sources = {}
    for r in ad_rows:
        k = (r["product_id"], kw_norm(r["keyword"]))
        if k not in sources or r["table"].endswith("_keyword"):
            sources[k] = r["table"]
    for r in ad_rows:
        key = kw_norm(r["keyword"])
        if r["table"] != sources[(r["product_id"], key)]:
            continue
        if r["product_id"]:
            for c in ("impressions", "clicks", "inquiries", "l1plus_clicks"):
                o = ad[r["product_id"]][key]
                if r[c] is None or c in o and o[c] is None:
                    o[c] = None
                else:
                    o[c] += r[c]
        if r["bid"] not in (None, ""):
            bid[(r["product_id"], key)] = r["bid"]
    own = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(float)))
    for k in dump.keyword_effect:
        o = own[k["product_id"]][kw_norm(k["keyword"])]
        o["views"] += k["views"]
        o["clicks"] += k["clicks"]
    return idx, ad, bid, own


def recommend_for(dump, pid, nums, top):
    """Up to `top` keywords for one product. Candidates come from the product itself (its title words,
    its own buyer search terms, its own ad keywords; a Keyword Index query only when every word of it is
    in the title). Measured terms without index volume keep blank volume and score."""
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
            # Keep measured product keywords reviewable even if Keyword Index has no volume for them.
            k = {"keyword": key, "search_index": None, "seller_scale": None}
        o, a = own[pid].get(key), ad[pid].get(key)
        views = int(o["views"]) if o else int(a["impressions"]) if a and a.get("impressions") is not None else None
        clicks = int(o["clicks"]) if o else int(a["clicks"]) if a and a.get("clicks") is not None else None
        inq = int(a["inquiries"]) if a and a.get("inquiries") is not None else None
        l1 = r2(div(a["l1plus_clicks"] * 100, a["clicks"])) if a and a.get("clicks") and a.get("l1plus_clicks") is not None else None
        b = fnum(bid[(pid, key)]) if (pid, key) in bid else None
        sv, comp = k["search_index"], k["seller_scale"]
        sv = fnum(sv) if sv not in (None, "") else None
        comp = fnum(comp) if comp not in (None, "") else None
        score_numerator = sv * sv * (1 + clicks) * (1 + inq) * (1 + l1 / 100) if all(v is not None for v in (sv, clicks, inq, l1)) else None
        score_denominator = comp * (1 + b) if comp is not None and b is not None else None
        score = div(score_numerator, score_denominator)
        ctr = r2(div(clicks * 100, views)) if views and clicks is not None else None
        num = lambda v: int(fnum(v)) if fnum(v) == int(fnum(v)) else fnum(v)
        row = dict(zip(KW_COLS, (k["keyword"], int(round(score)) if score is not None else None, num(sv) if sv is not None else None, num(comp) if comp is not None else None, b, views, clicks, ctr, inq, l1)))
        row.update({"점수_numerator": score_numerator, "점수_denominator": score_denominator,
                    "CTR %_numerator": clicks, "CTR %_denominator": views,
                    "L1+ %_numerator": a.get("l1plus_clicks") if a else None, "L1+ %_denominator": a.get("clicks") if a else None,
                    "노출·클릭 출처": "상품 keywordEffect" if o else "상품 광고 Word Report" if a else "",
                    "빈칸 사유": "; ".join(reason for missing, reason in [
                        (score is None, "점수 계산에 필요한 raw 숫자 없음 또는 분모 0"), (sv is None, "Keyword Index 검색량 없음"), (b is None, "상품×키워드 입찰가 응답 없음"), (views is None or clicks is None, "상품×키워드 노출·클릭 응답 없음"),
                        (inq is None, "상품×키워드 문의 응답 없음"), (l1 is None, "상품×키워드 L1+ 클릭 또는 분모 없음")] if missing)})
        rows.append(row)
    rows.sort(key=lambda r: (r["점수"] is None, -fnum(r["점수"]), -fnum(r["검색량"]), r["키워드"]))
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
    foot = "\n%s\n- 기간(노출·클릭·문의·L1+): %s ~ %s · 검색량·경쟁 = Keyword Index(%s 창) · 빈칸 = 그 숫자 raw 없음\n" % (
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
    "store-summary": report_store,
    "store-trends": report_store,
    "store-visitors": report_store,
    "store-today": report_store,
    "store-rating": report_store,
    "store-health": report_store,
    "store-indicators": report_store,
    "store-thresholds": report_store,
    "store-country-rules": report_store,
    "store-benefits": report_store,

    "inquiry-list": cmd_inquiry_list, "inquiry-stats": cmd_inquiry_stats, "inquiry-by-product": cmd_inquiry_by_product,
    "ads-daily": cmd_ads_daily, "ads-campaigns": cmd_ads_campaigns, "ads-history": cmd_ads_history,
    "ads-product-inquiries": cmd_ads_product_inquiries, "inquiry-fields": cmd_inquiry_fields, "ads-report": cmd_ads_report, "ads-tables": cmd_ads_tables, "ads-products": cmd_ads_products,
    "keywords": cmd_keywords, "bids": cmd_bids, "analytics-products": cmd_analytics_products, "ads-conversions": cmd_ads_conversions, "ads-conversion-fields": cmd_ads_conversion_fields, "ads-analyze": cmd_ads_analyze, "ads-quality": cmd_ads_quality, "coverage": cmd_coverage,
    "keyword-seeds": cmd_keyword_seeds, "keywords-recommend": cmd_keywords_recommend,
}


def cmd_discovery(dump, a, page):
    from discovery import business_error, raw_tables, with_missing_reasons
    from http_collect import get_path
    from urllib.parse import parse_qs, urlsplit
    tables, errors, contexts = {}, [], []
    found = False
    for directory in dump.dirs:
        meta = next((p for p in dump._manifest(directory).get("pages", []) if p.get("key") == page["key"]), {})
        if meta and meta.get("status") != "ok":
            errors.append({"status": meta.get("status"), "message": "; ".join(meta.get("notes", []))})
    for record in dump.records(page["key"]):
        if (record.get("replay") or {}).get("kind") == "binding":
            continue
        found = True
        value = record.get("json")
        error = business_error(value)
        if record.get("status") != 200 or error:
            errors.append({"http": record.get("status"), "message": error or "HTTP error"})
            continue
        query = {k: v[0] for k, v in parse_qs(urlsplit(record.get("url", "")).query).items()
                 if k in ("nd", "queryRaw", "cateId", "cateLv3Id", "byrGroup", "dimName", "kwType")}
        if query and query not in contexts:
            contexts.append(query)
        data = get_path(value, page["report"]["path"])
        if not isinstance(data, (list, dict)):
            errors.append({"message": "response path missing: " + page["report"]["path"]})
            continue
        for key, rows in raw_tables(data, page["key"].replace("-", "_"), page["report"].get("modules")).items():
            tables.setdefault(key, []).extend(rows)
    if not found:
        errors.append({"message": "requested response not collected: " + page["key"]})
    for key, rows in tables.items():
        tables[key] = with_missing_reasons(rows)
        if not rows:
            print("안내: %s 0행 — API 응답 배열이 비어 있음" % key, file=sys.stderr)
    if contexts:
        tables["request_context"] = with_missing_reasons(contexts)
    if errors:
        tables["errors"] = errors
        tables["_exit"] = 5
    return tables


# Each command shares the fixed request/report schema from pages.json.
with open(os.path.join(os.path.dirname(__file__), "pages.json"), encoding="utf-8") as _catalog_file:
    for _page in json.load(_catalog_file)["pages"]:
        if _page.get("discovery"):
            COMMANDS[_page["report"]["command"]] = functools.partial(cmd_discovery, page=_page)


# -- zero-period notices ----------------------------------------------------------------------------
AD_COMMANDS = {"ads-tables", "ads-daily", "ads-campaigns", "ads-history", "ads-product-inquiries", "ads-conversion-fields", "ads-report", "ads-products", "ads-conversions", "ads-analyze", "ads-quality", "bids", "keywords-recommend"}
INQUIRY_COMMANDS = {"inquiry-list", "inquiry-stats", "inquiry-by-product"}


def zero_notices(dump, command):
    """One line each when the period itself is empty, so a 0-row table is not read as a failure."""
    period = "%s~%s" % (dump.start or "", dump.end or "")
    out = []
    if command == "ads-history":
        coverage = ar.history_coverage(dump)
        out.append("안내: 운영 기록은 셀러센터에서 2개월만 보존; 이전 일별 설정 복원 불가" if coverage else
                   "안내: 운영 기록 요청 미수집 — ads history 실조회 필요 (셀러센터 보존 2개월)")
    if command in AD_COMMANDS and command not in ("ads-campaigns", "ads-history"):
        # The AI (GGS whole-site) account table reads 0 even on spending days: count campaign and product rows too.
        rows = dump.rows_of(ACCOUNT_TABLES + CAMPAIGN_TABLES + PRODUCT_TABLES)
        cost = [ar.number(r.get("cost", r.get("costStr"))) for _, r in rows]
        clicks = [ar.number(r.get("clickCnt")) for _, r in rows]
        if not rows:
            out.append("안내: 이 기간(%s) 광고 일별표 0행 — 광고 기록 없음" % period)
        elif all(v == 0 for v in cost) and all(v == 0 for v in clicks):
            out.append("안내: 이 기간(%s) 광고비·클릭 0(운영 상태는 별도 raw 필요) — 계정·캠페인·상품 일별표 %d행이 전부 0" % (period, len(rows)))
    if command in INQUIRY_COMMANDS and not dump.inquiries:
        times = [fnum(it.get("createTime")) for it in dump.inquiries_all if it.get("createTime")]
        if times:
            last = dt.datetime.fromtimestamp(max(times) / 1000, ia.US).date().isoformat()
            out.append("안내: 기간 안 문의 0건(마지막 문의 %s, 받은 목록 %d건)" % (last, len(times)))
        else:
            out.append("안내: 기간 안 문의 0건(받은 문의 목록도 0건)")
    if command == "analytics-products" and not any(advisor_periods(dump, g)[0] for g in ("week", "month")):
        out.append("안내: 이 기간(%s) Product Analyzer 상품 기록 0행(응답 없음 또는 미수집)" % period)
    if command == "keywords" and not dump.keyword_effect and not ad_keyword_rows(dump) and not dump.analytics_keywords:
        out.append("안내: 이 기간(%s) 키워드 실적 0행(응답 없음 또는 미수집); Keyword Index는 별도 nd 창" % period)
    if command == "bids" and not dump.rows_of(ACCOUNT_TABLES + CAMPAIGN_TABLES + PRODUCT_TABLES) and not out:
        out.append("안내: 이 기간(%s) 광고 실적 0행; 입찰 설정은 현재 값" % period)
    if command == "coverage" and not dump.inquiries and not dump.rows_of(ACCOUNT_TABLES + CAMPAIGN_TABLES + PRODUCT_TABLES) and not any(advisor_periods(dump, g)[0] for g in ("week", "month")):
        out.append("안내: 이 기간(%s) 문의·광고·상품 실적 0행(응답 없음 또는 미수집)" % period)
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


def ratio_evidence(row):
    pairs = {"ctr_pct": ("clicks", "impressions" if "impressions" in row else "views"),
             "inquiry_rate_pct": ("inquiries", "visitors"), "l3plus_share_pct": ("l3plus_inquiries", "inquiries_cvr3"),
             "conversion_rate": ("inquiries_cvr3", "clicks"), "cpc_usd": ("spend_usd", "clicks"),
             "cost_per_inquiry_usd": ("spend_usd", "inquiries_cvr3")}
    if "ctr_clicks" in row or "l3plus_numerator" in row:
        return row
    out = {}
    for key, value in row.items():
        if key.endswith("_numerator") or key.endswith("_denominator") and key[:-12] + "_numerator" in row:
            continue
        out[key] = value
        if key in pairs or key + "_numerator" in row:
            n, d = pairs.get(key, ("", ""))
            out[key + "_numerator"] = row.get(key + "_numerator", row.get(n))
            out[key + "_denominator"] = row.get(key + "_denominator", row.get(d))
    return out


def emit(tables, a):
    tables = {k: [ratio_evidence(r) for r in v] if isinstance(v, list) else v for k, v in tables.items()}
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
    p.add_argument("--selection", help="JSON request/window selection from CLI")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--format", choices=("table", "json", "csv"), default="table")
    p.add_argument("--out")
    p.add_argument("--limit", type=int)
    p.add_argument("--unread", action="store_true")
    p.add_argument("--type", choices=("product", "company", "tm"))
    p.add_argument("--country")
    p.add_argument("--status")
    p.add_argument("--level", choices=("campaign", "product", "keyword"), default="campaign")
    p.add_argument("--granularity", choices=("week", "month"))
    p.add_argument("--top", type=int, help="keywords-recommend: keywords per product (default 10)")
    p.add_argument("--product", help="keywords-recommend / keyword-seeds: product id or part of its title")
    p.add_argument("--all-products", action="store_true", help="keywords-recommend: every product")
    p.add_argument("--max-seeds", type=int, help="keyword-seeds: how many seeds (default 5)")
    a = p.parse_args(argv)
    for d in a.dirs:
        if not os.path.isdir(os.path.join(d, "raw")):
            raise SystemExit("not a dump folder (no raw/): " + d)
    dump = Dump(a.dirs, a.start, a.end, json.loads(a.selection) if a.selection else None)
    tables = COMMANDS[a.command](dump, a)
    if a.command.startswith("ads-") and a.command not in ("ads-tables", "ads-campaigns", "ads-history"):
        incomplete = [r for r in range_completeness(dump) if r["complete"] != "yes"]
        if incomplete:
            tables["completeness"] = incomplete
            tables["_exit"] = 3
            print("안내: 광고표 %d개가 API totalRows보다 부족함 — 기간 합계 검증 불가; ads tables로 이어 받기" % len(incomplete), file=sys.stderr)
    for line in ([] if tables.get("_exit") == 3 else zero_notices(dump, a.command)):
        print(line, file=sys.stderr)
    if a.command.startswith("ads-"):
        for name, rows in tables.items():
            if not name.startswith("_") and isinstance(rows, list) and not rows:
                print("안내: %s 기간 %s~%s 0행 — 해당 raw 없음" % (name, dump.start, dump.end), file=sys.stderr)
    emit(tables, a)
    return tables.get("_exit", 0)


if __name__ == "__main__":
    sys.exit(main())
