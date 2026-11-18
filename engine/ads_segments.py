"""Ads report screen tabs the CLI lacked: region / hour / target breakdowns and the industry view.

Bodies mirror the 2026-11-18 screen capture (thedozers, specs.json): same component names, same
filters, and the requested period inside `data.time` — the screen sends its own 7-day default for
some sub-tabs, so the CLI never relies on a server default.
"""
import datetime as dt

COMPONENT_URL = "https://www2.alibaba.com/api/data/dynamic/component/data"
INDUSTRY_URL = "https://www2.alibaba.com/api/report/v2/industry"
PAGE_SIZE = 50  # the screen's largest page size; bigger values make the API repeat page 1 rows
AI_PL, MANUAL_PL = 110106, 110101
TAG_NAMES = {13: "지역타겟팅", 14: "잠재고객", 15: "배달터미널", 16: "배달시나리오", 17: "결합레이블"}
DIAGNOSIS = "component_offline_product_diagnosis_ggs"
DIAGNOSIS_TYPES = ("high", "low")
DIAGNOSIS_ORDERS = {
    "high": [{"field": "ctr", "mode": "DESC"}, {"field": "impr", "mode": "DESC"}],
    "low": [{"field": "ctr", "mode": "DESC"}, {"field": "impr", "mode": "ASC"}],
    "cate": [{"field": "finalScore", "mode": "DESC"}, {"field": "ctr", "mode": "DESC"},
             {"field": "productId", "mode": "ASC"}],  # stable paging: score ties otherwise repeat rows
}
# The caps the screen's own components use (captured dateRange 31 for the 2026-07 range).
SEGMENT_WINDOW_DAYS = 31
DIAGNOSIS_WINDOW_DAYS = 31

# One entry per sub-tab the screen offers; `dimension` is the report table name.
SEGMENTS = [
    {"dimension": "region", "group": "ai", "campaign_type": "51", "view": "샵", "product_line": AI_PL,
     "component": "component_ggs_whole_site_offline_region", "max_days": SEGMENT_WINDOW_DAYS},
    {"dimension": "region", "group": "ai", "campaign_type": "47", "view": "제품", "product_line": AI_PL,
     "component": "component_ggs_whole_site_offline_region", "max_days": SEGMENT_WINDOW_DAYS},
    {"dimension": "region", "group": "manual", "campaign_type": "1,2", "view": "수동", "product_line": MANUAL_PL,
     "component": "component_ggs_offline_region", "max_days": DIAGNOSIS_WINDOW_DAYS},
    {"dimension": "region", "group": "manual", "campaign_type": "4,10", "view": "보관", "product_line": MANUAL_PL,
     "component": "component_ggs_offline_region", "max_days": DIAGNOSIS_WINDOW_DAYS},
    {"dimension": "hour", "group": "ai", "campaign_type": "4,10", "view": "산업", "product_line": MANUAL_PL,
     "component": "component_ggs_offline_hour", "max_days": DIAGNOSIS_WINDOW_DAYS},
    {"dimension": "hour", "group": "manual", "campaign_type": "1,2", "view": "수동", "product_line": MANUAL_PL,
     "component": "component_ggs_offline_hour", "max_days": DIAGNOSIS_WINDOW_DAYS},
    {"dimension": "target", "group": "ai", "campaign_type": "4,10", "view": "산업", "product_line": MANUAL_PL,
     "component": "component_ggs_offline_tag", "max_days": DIAGNOSIS_WINDOW_DAYS, "tag_types": tuple(TAG_NAMES)},
    {"dimension": "target", "group": "manual", "campaign_type": "1,2", "view": "수동", "product_line": MANUAL_PL,
     "component": "component_ggs_offline_tag", "max_days": DIAGNOSIS_WINDOW_DAYS, "tag_types": tuple(TAG_NAMES)},
]


def day(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def span(start, end):
    """Inclusive calendar span in days (the screen's dateRange)."""
    return (end - start).days + 1


def windows(start, end, max_days):
    first = start
    while first <= end:
        last = min(end, first + dt.timedelta(days=max(1, max_days) - 1))
        yield first, last
        first = last + dt.timedelta(days=1)


def numbers(row):
    """Struct cells arrive as {'main': value}; numeric strings become numbers."""
    out = {}
    for key, value in row.items():
        if isinstance(value, dict) and "main" in value:
            value = value["main"]
        if not isinstance(value, str):
            out[key] = value
            continue
        text = value.replace(",", "").strip()
        body = text[1:] if text[:1] in "+-" else text
        if body.isdigit():
            out[key] = int(text)
        elif body.replace(".", "", 1).isdigit() and "." in body:
            out[key] = float(text)
        else:
            out[key] = value
    return out


def segment_job(entry, start, end, tag_type=None):
    date_range = span(start, end)
    filters = [{"field": "campaignType", "value": entry["campaign_type"]}]
    if tag_type is not None:
        filters.append({"field": "tagType", "value": tag_type})
    context = {"dateRange": date_range, "productLineId": entry["product_line"]}
    if entry["product_line"] == AI_PL:
        context["campaignType"] = int(entry["campaign_type"])
    else:
        context["summaryTypes"] = ["search"]
        if tag_type is not None:
            context["tagRefType"] = tag_type
    data = {"dataMode": "struct", "tableName": entry["component"],
            "page": {"index": 1, "size": PAGE_SIZE}, "filters": filters,
            "time": {"granularity": "ALL", "beginDateTime": day(start) + " 00:00:00",
                     "endDateTime": day(end) + " 23:59:59"},
            "derives": [{"mode": "MAIN"}], "extendParams": {"context": context}}
    meta = {"table": entry["dimension"], "group": entry["group"], "campaign_type": entry["campaign_type"],
            "view": entry["view"], "tag_type": tag_type, "diagnosis_type": None, "category_id": None}
    return {"tag": "segment.%s.%s.%s.%s" % (entry["dimension"], entry["group"], tag_type or 0, day(start)),
            "url": COMPONENT_URL, "method": "POST",
            "params": {"type": "normal", "ads": {"productLineId": entry["product_line"]}, "data": data},
            "rowsPath": "data.queryResult.detailList", "totalPath": "data.queryResult.totalRows",
            "pageParam": "data.page.index", "kind": "range", "meta": meta,
            "windowStart": day(start), "windowEnd": day(end)}


def diagnosis_job(dimension, diagnosis_type, start, end, category_id):
    filters = [{"field": "type", "mode": "EQ", "value": diagnosis_type}]
    if diagnosis_type == "cate":
        filters.insert(0, {"field": "cateLv2Id", "mode": "EQ", "value": category_id})
    data = {"tableName": DIAGNOSIS,
            "time": {"beginDateTime": day(start) + " 00:00:00",
                     "endDateTime": day(end) + " 23:59:59", "granularity": "ALL"},
            "page": {"index": 1, "size": PAGE_SIZE}, "filters": filters,
            "orders": DIAGNOSIS_ORDERS[diagnosis_type]}
    meta = {"table": dimension, "group": "ai", "campaign_type": "", "view": diagnosis_type,
            "tag_type": None, "diagnosis_type": diagnosis_type, "category_id": category_id}
    return {"tag": "industry.%s.%s.%s" % (dimension, diagnosis_type, day(start)),
            "url": COMPONENT_URL, "method": "POST",
            "params": {"type": "normal", "ads": {"productLineId": MANUAL_PL}, "data": data},
            "rowsPath": "data.queryResult.detailList", "totalPath": "data.queryResult.totalRows",
            "pageParam": "data.page.index", "kind": "range", "meta": meta,
            "dedupeKey": "productId" if diagnosis_type == "cate" else None,
            "windowStart": day(start), "windowEnd": day(end)}


def compare_job(start, end, category_id):
    """The top comparison only serves the 1/7/30-day presets: keep the period's last 30 days."""
    requested = span(start, end)
    if requested not in (1, 7, 30):
        start = end - dt.timedelta(days=29)
    data = {"statDate": day(end), "categoryId": category_id, "dateRange": span(start, end)}
    meta = {"table": "industry_compare", "group": "industry", "campaign_type": "",
            "view": "지난 %d일" % span(start, end), "tag_type": None, "diagnosis_type": None,
            "category_id": category_id, "requested_days": requested}
    return {"tag": "industry.compare.%s" % day(end), "url": INDUSTRY_URL, "method": "POST",
            "params": {"type": "normal", "ads": {"productLineId": MANUAL_PL}, "data": data},
            "rowsPath": "data", "kind": "range", "meta": meta,
            "windowStart": day(start), "windowEnd": day(end)}


def command(mode, start, end, category_id=0):
    """pages.json-shaped page whose jobs each carry the window they ask for."""
    jobs = []
    if mode == "segments":
        for entry in SEGMENTS:
            for first, last in windows(start, end, entry["max_days"]):
                for tag_type in entry.get("tag_types") or (None,):
                    jobs.append(segment_job(entry, first, last, tag_type))
        return {"key": "ads-segments", "url": "https://www2.alibaba.com/ads/report.htm",
                "rangeReplay": {"jobs": jobs, "start": day(start), "end": day(end)}}
    jobs.append(compare_job(start, end, category_id))
    for diagnosis_type in DIAGNOSIS_TYPES + ("cate",):
        first = start
        if diagnosis_type != "cate":  # our own products: the screen serves the last 31 days only
            first = max(start, end - dt.timedelta(days=DIAGNOSIS_WINDOW_DAYS - 1))
        dimension = "category_products" if diagnosis_type == "cate" else "product_diagnosis"
        for window_start, window_end in windows(first, end, DIAGNOSIS_WINDOW_DAYS):
            jobs.append(diagnosis_job(dimension, diagnosis_type, window_start, window_end, category_id))
    return {"key": "ads-industry", "url": "https://www2.alibaba.com/ads/report.htm",
            "rangeReplay": {"jobs": jobs, "start": day(start), "end": day(end)}}


def report(dump, args):
    """One table per screen tab; every row carries the window its response answers for."""
    from http_collect import get_path, parse
    from ads_extra import failure

    mode = "segments" if args.command == "ads-segments" else "industry"
    start = dt.date.fromisoformat(args.start or dump.start)
    end = dt.date.fromisoformat(args.end or dump.end)
    category = getattr(args, "category_id", None) or 0
    rows, requests, errors = {}, [], []
    seen_products = set()
    seen = 0
    for record in dump.records(args.command):
        seen += 1
        replay = record.get("replay") or {}
        meta = replay.get("meta") or {}
        value = record.get("json") if "json" in record else parse(record.get("text", ""))
        scope = {"table": meta.get("table"), "tag": replay.get("tag"), "start": replay.get("start"),
                 "end": replay.get("end"), "group": meta.get("group"),
                 "campaign_type": meta.get("campaign_type"), "tag_type": meta.get("tag_type"),
                 "view": meta.get("view"), "http": record.get("status"), "page": replay.get("page")}
        if not replay.get("rowsPath"):
            errors.append({**scope, "message": "이 실행의 요청 계약에 없는 응답"})
            continue
        error = failure(record, value)
        if error:
            # 3 when the server answered and refused this window; 5 when the request itself failed.
            refused = record.get("status") == 200 and isinstance(value, dict) and any(
                k in value for k in ("code", "status", "error", "errorVO", "message", "msg"))
            errors.append({**scope, "message": error, "business": refused})
            continue
        if meta.get("table") == "industry_compare":
            data = get_path(value, replay["rowsPath"]) or {}
            got = 0
            for name in ("mine", "cateAvg", "cateTop10"):
                part = data.get(name) if isinstance(data, dict) else None
                if not isinstance(part, dict):
                    continue
                got += 1
                rows.setdefault("industry_compare", []).append({
                    **numbers(part), "scope": name, "category_id": meta.get("category_id"),
                    "group": meta.get("group"), "view": meta.get("view"),
                    "requested_start": day(start), "requested_end": day(end),
                    "period_start": replay.get("start"), "period_end": replay.get("end")})
            requests.append({**scope, "rows": got, "totalRows": None,
                             "reason": "" if got else "API data null/빈 응답 (0행)"})
            continue
        batch = get_path(value, replay["rowsPath"])
        if batch is None:
            # A refused window comes back with its own code/message; keep it verbatim.
            errors.append({**scope, "message": {k: value.get(k) for k in ("code", "status", "message", "msg")}
                           if isinstance(value, dict) else "응답 본문 없음",
                           "business": isinstance(value, dict)})
            continue
        batch = batch if isinstance(batch, list) else [batch]
        table = meta.get("table") or "rows"
        kept = 0
        for raw in batch:
            base = numbers(raw)
            base.update({"group": meta.get("group"), "campaign_type": meta.get("campaign_type"),
                         "view": meta.get("view"), "period_start": replay.get("start"),
                         "period_end": replay.get("end")})
            if meta.get("tag_type") is not None:
                base["tag_name"] = TAG_NAMES.get(meta["tag_type"], "")
            if meta.get("diagnosis_type"):
                base["grade"] = meta["diagnosis_type"]
            if table == "category_products":
                # The market ranking shifts while paging, so the same product can repeat; keep one row.
                key = base.get("productId")
                if key in seen_products:
                    continue
                seen_products.add(key)
            rows.setdefault(table, []).append(base)
            kept += 1
        requests.append({**scope, "rows": kept, "totalRows": get_path(value, replay["totalPath"]),
                         "reason": "" if kept else "API 0행 (기간 안에 해당 조건 없음)"})
    out = dict(rows)
    out["requests"] = requests
    # Paging splits one table across several records: compare the summed rows with the API total.
    per_table = {}
    for r in requests:
        key = (r["table"], r["tag"])
        cell = per_table.setdefault(key, {"table": r["table"], "tag": r["tag"], "rows": 0, "totalRows": 0})
        cell["rows"] += r["rows"]
        cell["totalRows"] = max(cell["totalRows"] or 0, r["totalRows"] or 0)
    out["completeness"] = [
        {**cell, "complete": "yes" if not cell["totalRows"] or cell["rows"] >= cell["totalRows"]
         else "deduped" if cell["table"] in ("category_products", "industry_compare") else "no"}
        for cell in per_table.values()]
    out["period"] = [{"requested_start": day(start), "requested_end": day(end),
                      "note": "요청 기간을 본문 time 에 명시; 행의 period_start/period_end 가 응답이 답한 범위"}]
    if not seen:
        errors.append({"message": "요청 덤프 없음; 실조회 필요"})
    if errors:
        out["errors"] = errors
        out["_exit"] = 3 if any(e.get("business") for e in errors) else 5
    return out
