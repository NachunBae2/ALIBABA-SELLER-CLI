"""Dated projections of ads tables, with raw operands. No separate collector or parser."""
import collections
import datetime as dt
import json
import math
import re
from decimal import Decimal


def number(value):
    if isinstance(value, dict):
        value = value.get("main")
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    text = re.sub(r"^(?:US\$|USD\s*|\$)", "", text).strip()
    try:
        result = float(text)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def day(value):
    value = str(value or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return ""
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        return ""


def total(rows, field):
    values = [number(r.get(field)) for r in rows]
    if not values or any(v is None for v in values):
        return None
    return float(sum((Decimal(str(v)) for v in values), Decimal(0)))


def ratio(value, numerator, denominator, n, d, scale=1, digits=2):
    return {value: round(n / d * scale, digits) if n is not None and d else None,
            numerator: n, denominator: d}


def metrics(rows):
    sums = {k: total(rows, k) for k in ("spend", "impressions", "clicks", "inquiries", "conversions", "l1plus_clicks", "l3plus_inquiries", "l1plus_inquiries", "tm_chats", "orders")}
    return {**ratio("l1plus_click_pct", "l1plus_clicks", "l1plus_click_denominator", sums["l1plus_clicks"], sums["clicks"], 100),
            **ratio("ctr_pct", "ctr_clicks", "ctr_impressions", sums["clicks"], sums["impressions"], 100),
            **ratio("cpc_usd", "cpc_cost_sum", "cpc_clicks", sums["spend"], sums["clicks"]),
            **ratio("conversion_rate", "conversion_numerator", "conversion_clicks", sums["conversions"], sums["clicks"], digits=4),
            **ratio("cost_per_inquiry_usd", "cost_sum", "cost_inquiries", sums["spend"], sums["inquiries"]),
            **ratio("l3plus_share_pct", "l3plus_numerator", "l3plus_conversions", sums["l3plus_inquiries"], sums["conversions"], 100),
            **{k: v for k, v in sums.items() if k not in ("l1plus_clicks",)}}


def daily(dump, level):
    out = []
    for table, rows in dump.ad_tables.items():
        if level == "keyword":
            selected = table.endswith("_keyword")
        else:
            selected = table.endswith("_" + level) and "offline" in table
        if not selected:
            continue
        # Prefer the explicitly requested rangeTables over the default UI tab.
        # They can share table+date while covering different campaign types.
        # ads tables retains both variants with their request filters.
        configured = [r for r in rows if r.get("_query_tag")]
        if configured:
            rows = configured
        for raw in rows:
            date = day(raw.get("timeUs"))
            if not date or not dump.in_range(date):
                continue
            cost_key = next((k for k in ("cost", "costStr") if number(raw.get(k)) is not None), "")
            inquiry_key = "mcCnt" if "mcCnt" in raw else ""
            conversion_key = "cvr3" if "cvr3" in raw else "businessLeadsCnt"
            tm_key = "atmSessionCnt" if "atmSessionCnt" in raw else "atmSessioncCnt"
            fact = {"date": date, "level": level, "source_table": table,
                    "source_query": raw.get("_query_tag", ""),
                    "source_filters": raw.get("_query_filters", ""),
                    "campaign_id": str(raw.get("campaignId") or ""),
                    "product_id": str(raw.get("productId") or ""), "keyword": raw.get("keyword", ""),
                    "spend": number(raw.get(cost_key)), "cost_field": cost_key,
                    "impressions": number(raw.get("impsCnt")), "clicks": number(raw.get("clickCnt")),
                    "inquiries": number(raw.get(inquiry_key)), "inquiries_field": inquiry_key,
                    "conversions": number(raw.get(conversion_key)), "conversions_field": conversion_key,
                    "l1plus_clicks": number(raw.get("fiGrowthClickCnt")),
                    "l3plus_inquiries": number(raw.get("l3PlusCvr3")),
                    "l1plus_inquiries": number(raw.get("l1PlusCvr3")),
                    "tm_chats": number(raw.get(tm_key)), "orders": number(raw.get("orderCnt")),
                    "online_status": None, "daily_budget": None, "bid": None, "changes": None,
                    "operations_reason": "해당 일자 운영 상태 raw 없음; 현재 설정을 과거에 적용하지 않음",
                    "cost_reason": "" if cost_key else "일별 raw에 cost/costStr 없음",
                    "raw": raw}
            # Only an explicit dated operational snapshot may populate the same date.
            operation = dump.operation_days.get((fact["campaign_id"], date)) if fact["campaign_id"] else None
            if operation:
                fact.update(operation)
            out.append(fact)
    return sorted(out, key=lambda r: (r["date"], r["source_table"], r["campaign_id"], r["product_id"], str(r["keyword"])))


def groups(rows, keys):
    buckets = collections.defaultdict(list)
    for row in rows:
        buckets[tuple(row.get(k, "") for k in keys)].append(row)
    for key, values in sorted(buckets.items()):
        yield dict(zip(keys, key)), values


def history(dump):
    result, seen = [], set()
    for record in dump.records("ads-history"):
        # Capture definitions mark the exact observed history list response.
        if (record.get("replay") or {}).get("kind") != "ads-history":
            continue
        for row in (record.get("json") or {}).get("list", []):
            date = day(str(row.get("date") or row.get("time") or "")[:10])
            key = json.dumps(row, sort_keys=True)
            if date and dump.in_range(date) and key not in seen:
                result.append({"date": date, "raw": row})
                seen.add(key)
    return result
