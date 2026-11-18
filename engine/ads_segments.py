"""Ads segment / industry contracts replayed by seller_central_raw.py.

Bodies mirror the screen captures in specs.json (앱 중계 캡처, 2026-11-18):
every request states its own time window, so the CLI never inherits the screen's
default 7 days. Values are only ever sent to Alibaba; nothing here is output.
"""

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
     "types": ["high", "low"]},
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

