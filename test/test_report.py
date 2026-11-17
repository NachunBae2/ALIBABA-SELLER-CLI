import csv
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from urllib.parse import urlencode

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
import report  # noqa: E402

PRIVATE = "private-fixture-text"
# 2026-07-01 12:00 US Pacific
T_0701 = 1782932400000


def m(v):
    return {"main": str(v)}


def range_record(table, rows, total=None):
    body = urlencode({"data": json.dumps({"tableName": table})})
    return {"url": "https://www2.alibaba.com/api/data/dynamic/component/data", "body": body,
            "replay": {"kind": "range", "table": table, "page": 1},
            "json": {"data": {"queryResult": {"detailList": rows, "totalRows": total if total is not None else len(rows)}}}}


def product_row(pid, day, clicks, conv, imps=100, l3=0, mc=0, atm=0):
    return {"productId": m(pid), "timeUs": m(day), "clickCnt": m(clicks), "cvr3": m(conv), "impsCnt": m(imps),
            "l3PlusCvr3": m(l3), "mcCnt": m(mc), "atmSessionCnt": m(atm), "orderCnt": m(0), "subject": m("")}


def campaign_row(cid, day, cost, clicks, conv):
    return {"campaignId": m(cid), "timeUs": m(day), "cost": m(cost), "clickCnt": m(clicks), "cvr3": m(conv)}


def write_dump(pages, start="2026-07-01", end="2026-07-31"):
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "raw"))
    for page, records in pages.items():
        with open(os.path.join(d, "raw", page + ".jsonl"), "w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
    with open(os.path.join(d, "manifest.json"), "w") as fh:
        json.dump({"start": start, "end": end}, fh)
    return d


CAMPAIGNS = {"url": "https://www2.alibaba.com/api/campaign/type/allList", "json": {"data": {"51": [
    {"id": 9001, "title": "fixture campaign", "subType": "ggs_ai_whole_site",
     "properties": {"maxPrice": 19.84, "bidType": 4, "optimizeTarget": "whole_site_fb", "budget": 600}}]}}}


def run(command, dirs, **kw):
    argv = [command] + sum((["--from", d] for d in dirs), []) + ["--format", "json"]
    for k, v in kw.items():
        argv += ["--" + k.replace("_", "-")] + ([] if v is True else [str(v)])
    buf = io.StringIO()
    with redirect_stdout(buf):
        report.main(argv)
    return json.loads(buf.getvalue())


class Economics(unittest.TestCase):
    def test_owner_example_600_dollars_21_inquiries(self):
        e = report.inquiry_economics(600.0, 700, 21, "spend", "inq")
        self.assertEqual(e["cost_per_inquiry_usd"], 28.57)
        self.assertEqual(e["cost_per_inquiry_by_cpc_usd"], 28.57)
        self.assertEqual(e["conversion_rate"], 0.03)
        self.assertEqual(e["cpc_usd"], 0.86)

    def test_no_inquiry_gives_zero_rate_and_blank_cost(self):
        e = report.inquiry_economics(50.0, 40, 0, "spend", "inq")
        self.assertEqual(e["conversion_rate"], 0.0)
        self.assertIsNone(e["cost_per_inquiry_usd"])
        self.assertEqual(e["cost_per_inquiry_source"], "no_inquiry")


class AdsProducts(unittest.TestCase):
    def setUp(self):
        self.dump = write_dump({
            "ads-report": [
                range_record("component_ggs_whole_site_offline_product", [
                    product_row("100", "2026-07-01", 10, 2, l3=1), product_row("200", "2026-07-01", 30, 0),
                    product_row("100", "2026-08-02", 50, 5)]),  # outside the range
                range_record("component_ggs_whole_site_offline_campaign", [campaign_row(9001, "2026-07-01", 80.0, 40, 2)]),
            ],
            "ads-campaigns": [CAMPAIGNS],
        })

    def test_estimate_is_campaign_daily_cpc_times_product_clicks_and_labelled(self):
        rows = {r["product_id"]: r for r in run("ads-products", [self.dump], start="2026-07-01", end="2026-07-31")["product_ad_performance"]}
        p = rows["100"]
        self.assertIsNone(p["spend_actual_usd"])
        self.assertEqual(p["spend_estimated_usd"], 20.0)  # 80 / 40 clicks = 2.00 * 10
        self.assertIn("추정", p["spend_estimated_source"])
        self.assertEqual(p["clicks"], 10)  # the 2026-08-02 row is outside the queried range
        self.assertEqual(p["inquiries_cvr3"], 2)
        self.assertEqual(p["cost_per_inquiry_usd"], 10.0)
        self.assertEqual(p["cost_basis"], "추정")
        self.assertEqual(p["bid_usd"], 19.84)
        self.assertEqual((p["query_start"], p["query_end"]), ("2026-07-01", "2026-07-31"))
        self.assertEqual(rows["200"]["cost_per_inquiry_source"], "no_inquiry")

    def test_dated_product_cost_table_fills_actual_column_only(self):
        extra = write_dump({"showcase": [{"url": "https://x.test/api/report", "json": {"data": [
            {"productId": 100, "cost": 7.5, "statDate": "2026-07-01"}, {"productId": 100, "cost": 9.0, "statDate": "2026-09-01"}]}}]})
        undated = write_dump({"showcase": [{"url": "https://x.test/api/adgroup", "json": {"data": [{"productId": 100, "cost": 99.0, "statDate": None}]}}]})
        rows = {r["product_id"]: r for r in run("ads-products", [self.dump, extra, undated], start="2026-07-01", end="2026-07-31")["product_ad_performance"]}
        self.assertEqual(rows["100"]["spend_actual_usd"], 7.5)
        self.assertEqual(rows["100"]["spend_estimated_usd"], 20.0)
        self.assertEqual(rows["100"]["cost_basis"], "실측")
        self.assertEqual(rows["100"]["cost_per_inquiry_usd"], 3.75)

    def test_windows_merge_without_double_counting(self):
        rows = run("ads-products", [self.dump, self.dump], start="2026-07-01", end="2026-07-31")["product_ad_performance"]
        self.assertEqual({r["product_id"]: r["clicks"] for r in rows}, {"100": 10, "200": 30})

    def test_bid_prediction_records_rate_level(self):
        rows = run("ads-analyze", [self.dump], start="2026-07-01", end="2026-07-31")["bid_analysis"]
        camp = next(r for r in rows if r["level"] == "campaign")
        self.assertEqual(camp["predicted_cost_per_inquiry_usd"], 19.84)
        self.assertEqual(camp["cost_per_inquiry_usd"], 40.0)
        self.assertEqual(camp["bid_unit"], "전환 1건 목표가")

    def test_keyword_prediction_falls_back_product_then_account(self):
        kw = {"url": "https://mydata.alibaba.com/self/.json", "replay": {"kind": "extra", "tag": "product_stats.month.2026-07"},
              "json": {"value": {"products": {"data": [
                  {"id": 100, "subject": "fixture", "keywordEffect": [{"keyword": "serum", "views": "50", "clicks": "4", "p4pViews": "0", "p4pClicks": "0"}]},
                  {"id": 200, "subject": "fixture 2", "keywordEffect": [{"keyword": "toner", "views": "9", "clicks": "1"}]}]}}}}
        d = write_dump({"ads-report": [range_record("component_ggs_whole_site_offline_product", [
            product_row("100", "2026-07-01", 25, 5), product_row("200", "2026-07-01", 5, 0)]),
            range_record("component_ggs_whole_site_offline_campaign", [campaign_row(9001, "2026-07-01", 60.0, 30, 5)])],
            "analytics-product": [kw]})
        rows = {r["keyword"]: r for r in run("ads-analyze", [d], start="2026-07-01", end="2026-07-31")["bid_analysis"] if r["level"] == "keyword"}
        self.assertEqual(rows["serum"]["rate_level"], "product")  # 25 clicks >= 20, 5 inquiries
        self.assertEqual(rows["serum"]["predicted_cost_per_inquiry_usd"], 10.0)  # cpc 2.00 / 0.2
        self.assertEqual(rows["toner"]["rate_level"], "account")  # product 200 has 5 clicks only
        self.assertIn("입찰가 없음", rows["toner"]["cpc_source"])


class Inquiries(unittest.TestCase):
    def setUp(self):
        items = [
            {"id": 1, "createTime": T_0701, "feedbackType": 1, "targetIds": [100], "source": "main_app_detail_x", "appFrom": "Android",
             "buyerCountry": "US", "replyStatus": 1, "spam": False, "hasUnread": False, "buyerAttrs": {"sender.buyerLevel": "L3"}},
            {"id": 2, "createTime": T_0701, "feedbackType": 6, "targetIds": [], "source": "CONTACT_MKT_IMOpportunity", "appFrom": "web_weblite",
             "buyerCountry": "DE", "replyStatus": 0, "spam": True, "hasUnread": True},
        ]
        self.dump = write_dump({
            "inquiries": [{"replay": {"kind": "inquiry"}, "json": {"list": items, "fieldNames": ["id", "sender.buyerLevel", "sender.name"]}}],
            "ads-report": [range_record("component_ggs_whole_site_offline_product", [product_row("200", "2026-07-01", 3, 1, atm=1)])],
        })

    def test_filters(self):
        q = lambda **kw: [r["inquiry_id"] for r in run("inquiry-list", [self.dump], start="2026-07-01", end="2026-07-31", **kw)["inquiries"]]
        self.assertEqual(sorted(q()), ["1", "2"])
        self.assertEqual(q(unread=True), ["2"])
        self.assertEqual(q(type="product"), ["1"])
        self.assertEqual(q(country="de"), ["2"])
        self.assertEqual(q(status="1"), ["1"])
        self.assertEqual(len(q(limit=1)), 1)
        self.assertEqual(run("inquiry-list", [self.dump], start="2026-08-01", end="2026-08-31")["inquiries"], [])

    def test_by_product_and_quality(self):
        counts = {r["inflow"]: r["inquiries"] for r in run("inquiry-by-product", [self.dump], start="2026-07-01", end="2026-07-31")["inquiry_by_product_counts"]}
        self.assertEqual(counts, {"100": 1, "TM 유입": 1})
        quality = run("ads-analyze", [self.dump], start="2026-07-01", end="2026-07-31")["inquiry_quality"]
        direct = next(r for r in quality if r["product_id"] == "100")
        self.assertEqual(direct["buyer_levels"], "L3:1")
        self.assertEqual(direct["l3plus_share_pct"], 100.0)
        ad_day = next(r for r in quality if r["product_id"] == "200")
        self.assertIn("광고일 추정", ad_day["link"])
        self.assertEqual(ad_day["spam"], 1)

    def test_fields_lists_names_only(self):
        rows = run("inquiry-fields", [self.dump])["inquiry_fields"]
        self.assertEqual([r["field"] for r in rows if r["level_candidate"]], ["sender.buyerLevel"])


class NoPrivateData(unittest.TestCase):
    def test_outputs_never_carry_unlisted_fields(self):
        items = [{"id": 5, "createTime": T_0701, "feedbackType": 1, "targetIds": [100], "buyerCountry": "US",
                  "subject": PRIVATE, "buyerName": PRIVATE, "email": PRIVATE, "content": PRIVATE}]
        d = write_dump({"inquiries": [{"replay": {"kind": "inquiry"}, "json": {"list": items}}],
                        "ads-report": [range_record("component_ggs_whole_site_offline_product", [product_row("100", "2026-07-01", 3, 1)])]})
        out = tempfile.mkdtemp()
        for cmd in ("inquiry-list", "inquiry-stats", "inquiry-by-product", "ads-analyze", "coverage"):
            text = json.dumps(run(cmd, [d], start="2026-07-01", end="2026-07-31", out=out), ensure_ascii=False)
            self.assertNotIn(PRIVATE, text, cmd)
        for f in os.listdir(out):
            with open(os.path.join(out, f)) as fh:
                self.assertNotIn(PRIVATE, fh.read(), f)
        self.assertTrue(os.path.exists(os.path.join(out, "summary.md")))


def advisor_record(tag, products, page=1):
    return {"url": "https://mydata.alibaba.com/self/.json", "replay": {"kind": "extra", "tag": tag, "page": page},
            "json": {"successed": True, "value": {"products": {"data": products}}}}


def advisor_product(pid, views, clicks, visitors, inquiries, keywords=()):
    return {"id": pid, "subject": "fixture %s" % pid, "fullName": " Owner ", "sumProdShowNum": views, "sumProdClickNum": clicks,
            "sumProdVisitorCnt": visitors, "fav": 1, "share": 0, "cmp": 0, "sumProdFbNum": inquiries, "mcFbUv": inquiries,
            "isP4pProd": "Y", "isKwSearch": "N", "keywordEffect": [{"keyword": k, "views": "5", "clicks": "1", "p4pViews": "0", "p4pClicks": "0", "p4pCampaignStatus": "NOT_JOIN"} for k in keywords]}


class AnalyticsProducts(unittest.TestCase):
    def test_weeks_in_range_are_summed_and_covered_period_reported(self):
        d = write_dump({"analytics-product": [
            advisor_record("product_stats.week.2026-10-25", [advisor_product(1, 100, 10, 8, 1, ["serum"])]),
            advisor_record("product_stats.week.2026-11-01", [advisor_product(1, 50, 5, 4, 0, ["serum", "toner"]), advisor_product(2, 10, 0, 0, 0)]),
            advisor_record("product_stats.week.2026-11-01", [advisor_product(1, 50, 5, 4, 0)], page=2),  # same product again: counted once
            advisor_record("product_stats.week.2026-11-08", [advisor_product(1, 999, 99, 99, 9)]),  # after --end
            {"replay": {"kind": "extra", "tag": "product_summary.week.2026-11-01"}, "json": {"data": {"statDate": {"value": "2026-11-01"}}}},
        ]})
        out = run("analytics-products", [d], start="2026-10-28", end="2026-11-05", granularity="week")
        total = out["analytics_summary"][0]
        self.assertEqual((total["covered_start"], total["covered_end"], total["periods"]), ("2026-10-25", "2026-11-07", 2))
        p1 = next(r for r in out["analytics_products"] if r["product_id"] == "1")
        self.assertEqual((p1["views"], p1["clicks"], p1["visitors"], p1["inquiries"], p1["add_to_favorite"]), (150, 15, 12, 1, 2))
        self.assertEqual(p1["ctr_pct"], 10.0)
        self.assertEqual(p1["inquiry_rate_pct"], 8.33)
        self.assertEqual(p1["owner"], "Owner")
        self.assertEqual(sorted((k["period"], k["keyword"]) for k in out["analytics_product_keywords"]),
                         [("2026-10-25", "serum"), ("2026-11-01", "serum"), ("2026-11-01", "toner")])
        check = {c["period"]: c["statDate_matches"] for c in out["analytics_periods"]}
        self.assertEqual(check["2026-11-01"], True)

    def test_engine_week_selection(self):
        import importlib.util
        from pathlib import Path
        import datetime as dt
        spec = importlib.util.spec_from_file_location("raw_engine", Path(__file__).resolve().parent.parent / "engine" / "seller_central_raw.py")
        eng = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(eng)
        today = dt.date(2026, 11, 16)  # Monday PST: latest complete week = 2026-11-08..14 (selected 0)
        got = eng.week_selections(dt.date(2026, 10, 28), dt.date(2026, 11, 20), today)
        self.assertEqual([(w.isoformat(), n) for w, n in got], [("2026-10-25", 2), ("2026-11-01", 1), ("2026-11-08", 0)])


class Coverage(unittest.TestCase):
    def test_reports_missing_items_with_reason(self):
        d = write_dump({"ads-report": [range_record("component_ggs_whole_site_offline_product", [product_row("1", "2026-07-01", 2, 0)], total=5)]})
        rows = {r["item"]: r for r in run("coverage", [d])["coverage"]}
        self.assertEqual(rows["상품별 광고비(실측)"]["status"], "없음")
        self.assertIn("API totalRows 최대 5", rows["광고 표 component_ggs_whole_site_offline_product"]["note"])
        self.assertEqual(rows["바이어 레벨(문의)"]["status"], "없음")


if __name__ == "__main__":
    unittest.main()
