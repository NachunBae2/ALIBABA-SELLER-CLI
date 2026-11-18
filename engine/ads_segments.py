"""Ads segment / industry contracts replayed by seller_central_raw.py.

Bodies mirror the screen captures in specs.json (앱 중계 캡처, 2026-11-18):
every request states its own time window, so the CLI never inherits the screen's
default 7 days. Values are only ever sent to Alibaba; nothing here is output.
"""
import datetime as dt

COMPONENT_URL = "https://www2.alibaba.com/api/data/dynamic/component/data"
INDUSTRY_URL = "https://www2.alibaba.com/api/report/v2/industry"
PAGE_SIZE = 50  # screen offers 10/20/50

# campaignType: 51/47 = 산업 지능형(AI), 4/10 = 수동, 1/2 = 기존 수동 캠페인 보고서
AI, MANUAL = "51", "4,10"

SEGMENTS = [
    {
        "key": "region",
        "title": "지역",
        "description": "광고 성과를 지역별로 볼 때 씁니다.",
        "dimensions": ["region"],
        "tables": [
            {"key": "region_shop", "table": "component_ggs_whole_site_offline_region", "group": "ai",
             "campaignType": AI, "productLineId": 110106, "summaryTypes": False},
            {"key": "region_product", "table": "component_ggs_whole_site_offline_region", "group": "ai",
             "campaignType": "47", "productLineId": 110106, "summaryTypes": False},
            {"key": "region_manual", "table": "component_ggs_offline_region", "group": "manual",
             "campaignType": MANUAL, "productLineId": 110101, "summaryTypes": True,
             "extraFilters": [{"field": "summaryTypes", "value": "search"}]},
        ],
    },
    {
        "key": "hour",
        "title": "시간대",
        "description": "광고 성과를 시간대별로 볼 때 씁니다.",
        "dimensions": ["hourRange"],
        "tables": [
            {"key": "hour_manual", "table": "component_ggs_offline_hour", "group": "manual",
             "campaignType": MANUAL, "productLineId": 110101, "summaryTypes": True},
        ],
    },
    {
        "key": "target",
        "title": "맞춤 타겟",
        "description": "광고 성과를 타겟 조건(지역·잠재고객·배달)별로 볼 때 씁니다.",
        "dimensions": ["tagType"],
        "tables": [
            {"key": "tag_%d" % tag, "table": "component_ggs_offline_tag", "group": "manual",
             "campaignType": MANUAL, "productLineId": 110101, "summaryTypes": True,
             "tagType": tag, "extraFilters": [{"field": "tagType", "value": tag}]}
            for tag in (13, 14, 15, 16, 17)
        ],
    },
]

TAG_NAMES = {13: "지역타겟팅", 14: "잠재고객", 15: "배달터미널", 16: "배달시나리오", 17: "결합레이블"}

INDUSTRY = [
    {"key": "industry_compare", "table": "industry_compare", "group": "industry",
     "title": "업계 비교", "url": INDUSTRY_URL, "kind": "industry_compare", "paged": False},
    {"key": "product_diagnosis", "table": "component_offline_product_diagnosis_ggs", "group": "product",
     "title": "우리 상품 구매율", "url": COMPONENT_URL, "kind": "diagnosis", "paged": True,
     "types": ["high", "low"], "servingDays": 31},
    {"key": "category_products", "table": "component_offline_product_diagnosis_ggs", "group": "market",
     "title": "같은 카테고리 타사 상품 순위", "url": COMPONENT_URL, "kind": "diagnosis", "paged": True,
     "types": ["cate"]},
]

DIAGNOSIS_ORDERS = {
    "high": [{"field": "ctr", "mode": "DESC"}, {"field": "impr", "mode": "DESC"}],
    "low": [{"field": "ctr", "mode": "DESC"}, {"field": "impr", "mode": "ASC"}],
    "cate": [{"field": "finalScore", "mode": "DESC"}, {"field": "ctr", "mode": "DESC"}],
}

# dateRange the screen sends: "지난 N일" ending on statDate, or 31 for a custom window.
PRESET_RANGES = (1, 7, 30)


def day(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def span(start, end):
    """Inclusive calendar span in days (the screen's dateRange)."""
    return (end - start).days + 1


def segment_job(entry, start, end):
    filters = [{"field": "campaignType", "value": entry["campaignType"]}]
    filters += entry.get("extraFilters", [])
    context = {"dateRange": span(start, end), "productLineId": entry["productLineId"]}
    if entry["group"] == "ai":
        context["campaignType"] = int(entry["campaignType"])
    data = {
        "dataMode": "struct",
        "tableName": entry["table"],
        "page": {"index": 1, "size": PAGE_SIZE},
        "orders": [{"field": "timeUs", "mode": "desc"}],
        "filters": filters,
        "time": {"granularity": "ALL",
                 "beginDateTime": day(start) + " 00:00:00",
                 "endDateTime": day(end) + " 23:59:59"},
        "derives": [{"mode": "MAIN"}],
        "extendParams": {"context": context},
    }
    if entry.get("summaryTypes"):
        data["extendParams"]["context"]["summaryTypes"] = ["search"]
    return {
        "tag": "segment.%s.%s" % (entry["group"], entry["key"]),
        "url": COMPONENT_URL,
        "method": "POST",
        "params": {"type": "normal", "ads": {"productLineId": entry["productLineId"]}, "data": data},
        "rowsPath": "data.queryResult.detailList",
        "totalPath": "data.queryResult.totalRows",
        "tableName": entry["table"],
        "pageParam": "data.page.index",
        "kind": "rows",
        "meta": {"group": entry["group"], "campaign_type": entry["campaignType"],
                 "tag_type": entry.get("tagType"), "table_key": entry["key"]},
    }


def industry_job(entry, start, end, category_id, types=None):
    """One industry endpoint call. `start` is the first day the screen would ask for."""
    if entry["kind"] == "industry_compare":
        data = {"statDate": day(end), "categoryId": category_id, "dateRange": span(start, end)}
        return {
            "tag": "industry.%s" % entry["key"], "url": entry["url"], "method": "POST",
            "params": {"type": "normal", "ads": {"productLineId": 110101}, "data": data},
            "rowsPath": "data", "kind": "industry_compare", "tableName": entry["table"], "pageParam": None,
            "meta": {"group": entry["group"], "table": entry["table"], "category_id": category_id},
        }
    jobs = []
    for kind in (types or entry["types"]):
        filters = [{"field": "type", "mode": "EQ", "value": kind}]
        if kind == "cate":
            filters.insert(0, {"field": "cateLv2Id", "mode": "EQ", "value": category_id})
        data = {
            "tableName": entry["table"],
            "time": {"beginDateTime": day(start) + " 00:00:00",
                     "endDateTime": day(end) + " 23:59:59", "granularity": "ALL"},
            "page": {"index": 1, "size": PAGE_SIZE},
            "filters": filters,
            "orders": DIAGNOSIS_ORDERS[kind],
        }
        jobs.append({
            "tag": "industry.%s.%s" % (kind, day(start)),
            "url": entry["url"],
            "method": "POST",
            "params": {"type": "normal", "ads": {"productLineId": 110101}, "data": data},
            "rowsPath": "data.queryResult.detailList",
            "totalPath": "data.queryResult.totalRows",
            "tableName": entry["table"],
            "pageParam": "data.page.index",
            "kind": "rows",
            "meta": {"group": entry["group"], "table": entry["table"], "diagnosis_type": kind,
                     "category_id": category_id},
        })
    return jobs


def segment_jobs(kind, start, end):
    entries = next(s for s in SEGMENTS if s["key"] == kind)
    return entries, [segment_job(e, start, end) for e in entries["tables"]]


def industry_jobs(table, start, end, category_id, types=None):
    entry = next(i for i in INDUSTRY if i["key"] == table)
    jobs = industry_job(entry, start, end, category_id, types)
    return entry, jobs if isinstance(jobs, list) else [jobs]


def segment_requests(kind, start, end, maxDays):
    """pages.json-shaped page: one rangeReplay per (table, window)."""
    entries, jobs = segment_jobs(kind, start, end)
    return {
        "key": "ads-segments-" + kind,
        "url": "https://www2.alibaba.com/ads/report.htm",
        "rangeReplay": {"jobs": jobs, "start": day(start), "end": day(end)},
        "rangeTables": [
            {"key": job["meta"]["table_key"], "table": job["tableName"], "paged": True,
             "contractKey": job["tag"], "requestStart": day(start),
             "period": {"mode": "range", "maxDays": maxDays, "status": "verified"}}
            for job in jobs],
        "segmentKind": kind,
        "titles": entries["title"],
    }


def industry_requests(table, start, end, category_id, maxDays, types=None, serving_from=None):
    """serving_from: first day the endpoint answers for (diagnosis keeps only its own span)."""
    entry, jobs = industry_jobs(table, start, end, category_id, types)
    return {
        "key": "ads-industry-" + table,
        "url": "https://www2.alibaba.com/ads/report.htm",
        "rangeReplay": {"jobs": jobs, "start": day(start), "end": day(end)},
        "rangeTables": [
            {"key": job["meta"].get("diagnosis_type", job["meta"]["table"]), "table": job["tableName"],
             "paged": job["pageParam"] is not None, "contractKey": job["tag"],
             "requestStart": serving_from or day(start),
             "period": {"mode": "range", "maxDays": maxDays, "status": "verified"}}
            for job in jobs],
        "industryTable": table,
    }


def command(mode, start, end, category_id):
    """pages.json-shaped page for `ads segments` / `ads industry`.

    Every body carries its own time window, so the CLI never sends the screen's default 7 days.
    Report ranges are capped where the screen caps them: 30-day segments, 31-day diagnosis.
    """
    if mode == "segments":
        jobs, tables = [], []
        for entry in SEGMENTS:
            for tbl in entry["tables"]:
                maxDays = 30 if tbl["group"] == "ai" else 31
                for window in windows(start, end, maxDays):
                    job = segment_job(tbl, window["start"], window["end"])
                    job["tag"] = "%s.%s" % (job["tag"], window["start"])
                    jobs.append(job)
                    tables.append({"key": tbl["key"], "table": tbl["table"], "paged": True,
                                   "contractKey": job["tag"], "requestStart": window["start"],
                                   "period": {"mode": "range", "maxDays": maxDays, "status": "verified"}})
        return {"key": "ads-segments", "url": "https://www2.alibaba.com/ads/report.htm",
                "rangeReplay": {"jobs": jobs, "start": day(start), "end": day(end)},
                "rangeTables": tables, "adsSegments": {"kinds": [s["key"] for s in SEGMENTS], "categoryId": category_id}}
    jobs, tables = [], []
    for entry in INDUSTRY:
        if entry["kind"] == "industry_compare":
            first, last = max(start, end - dt.timedelta(days=29)), end  # screen presets are 1/7/30 days
            for job in industry_jobs(entry["key"], first, last, category_id)[1]:
                job["meta"]["requested_start"], job["meta"]["served_start"] = day(start), day(first)
                jobs.append(job)
                tables.append({"key": entry["key"], "table": entry["table"], "paged": False,
                               "contractKey": job["tag"], "requestStart": day(first),
                               "period": {"mode": "range", "maxDays": 30, "status": "verified"}})
            continue
        first = start if not entry.get("servingDays") else max(start, end - dt.timedelta(days=entry["servingDays"] - 1))
        for window in windows(first, end, 31):
            for job in industry_jobs(entry["key"], window["start"], window["end"], category_id)[1]:
                jobs.append(job)
                tables.append({"key": job["meta"]["diagnosis_type"], "table": entry["table"], "paged": True,
                               "contractKey": job["tag"], "requestStart": window["start"],
                               "period": {"mode": "range", "maxDays": 31, "status": "verified"}})
    return {"key": "ads-industry", "url": "https://www2.alibaba.com/ads/report.htm",
            "rangeReplay": {"jobs": jobs, "start": day(start), "end": day(end)},
            "rangeTables": tables, "adsIndustry": {"categoryId": category_id,
                "unavailable": "산업 비교는 지난 1/7/30일만 제공; 그 밖 기간은 요청한 달 마지막 30일로 조회"}}


def windows(start, end, maxDays):
    first = start
    while first <= end:
        last = min(end, first + dt.timedelta(days=max(1, maxDays) - 1))
        yield {"start": first, "end": last}
        first = last + dt.timedelta(days=1)


def load(mode, start, end, category_id, selected=None):
    """pages.json-shaped page for `ads segments` / `ads industry`."""
    return command(mode, start, end, category_id)


def numbers(row):
    """Numeric response values as floats (the screen shows both strings and numbers)."""
    out = {}
    for key, value in row.items():
        text = value.replace(",", "").strip() if isinstance(value, str) else None
        if text and (text.isdigit() or (text[:1] in "+-" and text[1:].isdigit())):
            out[key] = int(text)
        elif text and text.replace(".", "", 1).replace("-", "", 1).isdigit() and "." in text:
            out[key] = float(text)
        else:
            out[key] = value
    return out


def segment_rows(rows, table, window):
    """Dimension + metrics per row, keeping the response's own numbers (no recomputation)."""
    return [{**numbers(row), "group": table.get("group"), "campaign_type": table.get("campaignType"),
             "period_start": window[0], "period_end": window[1]} for row in rows]


def report(dump, args):
    """Rows per table, each tagged with the window its response answers for.

    Values stay as the API returned them (만 숫자 문자열은 숫자로); a table the server refuses
    or answers outside the requested span is reported with its own reason, never as 0행.
    """
    from http_collect import get_path, parse
    from ads_extra import failure
    from report import fnum
    mode = "segments" if args.command == "ads segments" else "industry"
    tables, requests, errors = {}, [], []
    for record in dump.records(args.command):
        replay = record.get("replay") or {}
        meta = replay.get("meta") or {}
        value = record.get("json") if "json" in record else parse(record.get("text", ""))
        job = next((j for j in command(mode, args.start_d, args.end_d, meta.get("category_id") or 0)["rangeReplay"]["jobs"]
                    if j["tag"] == replay.get("tag")), None)
        scope = {"table": meta.get("table_key") or meta.get("diagnosis_type") or meta.get("table"),
                 "tag": replay.get("tag"), "start": replay.get("start"), "end": replay.get("end"),
                 "group": meta.get("group"), "campaign_type": meta.get("campaign_type"),
                 "tag_type": meta.get("tag_type"), "diagnosis_type": meta.get("diagnosis_type"),
                 "http": record.get("status"), "page": replay.get("page")}
        error = failure(record, value) if job else {"message": "이 실행의 요청 계약에 없는 응답"}
        if error:
            errors.append({**scope, "message": error})
            continue
        rows = get_path(value, job["rowsPath"])
        total = get_path(value, job["totalPath"])
        if rows is None:
            # A refused window comes back with its own code/message; keep it verbatim.
            errors.append({**scope, "message": {k: value.get(k) for k in ("code", "status", "message", "msg")}
                           if isinstance(value, dict) else "응답 본문 없음"})
            continue
        batch = rows if isinstance(rows, list) else [rows] if isinstance(rows, dict) else []
        kind = meta.get("diagnosis_type") or job["meta"].get("table_key") or meta.get("table")
        tables.setdefault(kind, {"rows": [], "reported_total": None, "pages": 0})
        for row in batch:
            base = numbers(row)
            if mode == "segments":
                base.update(group=meta.get("group"), campaign_type=meta.get("campaign_type"),
                           tag_type=meta.get("tag_type"), table_key=kind)
            else:
                base.update(group=meta.get("group"), diagnosis_type=meta.get("diagnosis_type"))
            tables[kind]["rows"].append({**base, "period_start": replay.get("start"), "period_end": replay.get("end")})
        tables[kind]["pages"] += 1
        if total is not None:
            tables[kind]["reported_total"] = max(tables[kind]["reported_total"] or 0, int(fnum(total)))
        requests.append({**scope, "rows": len(batch), "totalRows": total,
                         "reason": "" if batch else "API 0행 (기간 안에 해당 조건 없음)"})
    out = {"data": [row for t in tables.values() for row in t["rows"]], "tables": {}, "requests": requests}
    for kind, t in tables.items():
        rows = t["rows"]
        out["tables"][kind] = {"rows": len(rows), "reported_total": t["reported_total"], "pages": t["pages"],
                               "complete": t["reported_total"] is not None and len(rows) >= t["reported_total"],
                               "sum": {k: round(sum(r[k] for r in rows if isinstance(r.get(k), (int, float))), 4)
                                       for k in ("impr", "click", "cost", "ctr") if any(k in r for r in rows)}}
    if mode == "industry":
        out["industry_compare"] = [r for r in out["data"] if "statDate" in r and "categoryId" in r]
    out["period"] = {"requested": [dump.start, dump.end],
                     "served": sorted({(r.get("period_start"), r.get("period_end")) for r in out["data"]}),
                     "note": "요청 기간을 본문 time 에 명시; 응답이 준 기간만 기록" if mode == "segments" else
                             "산업 비교·상품 진단은 서버가 정한 기간(30/31일)만 답한다; 행의 period_start/end 가 실제 범위"}
    if not tables:
        errors.append({"message": "요청 덤프 없음; 실조회 필요"})
    if errors:
        out["errors"] = errors
        # 3: the server answered but refused/adjusted the window; 5: a request failed or nothing was captured.
        out["_exit"] = 3 if any(isinstance(e.get("message"), dict) for e in errors) else 5
    return out


def needs_days(mode):
    return 0

