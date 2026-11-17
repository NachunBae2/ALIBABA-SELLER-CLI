# 테스트 전용 파일 — CLI 실행에 쓰이지 않음. 안의 ID·금액·클릭 수는 동작 확인용 예시 값이며 실제 셀러·고객 데이터가 아님.
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
    return {"campaignId": m(cid), "campaignType": m(51), "timeUs": m(day), "cost": m(cost), "clickCnt": m(clicks), "cvr3": m(conv)}


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


def tables(command, dirs, **kw):
    """Every table the command returns, '_' notes included (run() only sees the JSON part)."""
    got = {}
    real = report.emit
    report.emit = lambda t, a: got.update(t)
    try:
        run_argv = [command] + sum((["--from", d] for d in dirs), [])
        for k, v in kw.items():
            run_argv += ["--" + k.replace("_", "-")] + ([] if v is True else [str(v)])
        report.main(run_argv)
    finally:
        report.emit = real
    return got


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

    def test_missing_product_cost_and_bid_stay_blank(self):
        rows = {r["product_id"]: r for r in run("ads-products", [self.dump])["product_ad_performance"]}
        p = rows["100"]
        self.assertIsNone(p["spend_actual_usd"])
        self.assertEqual(p["clicks"], 10)
        self.assertEqual(p["inquiries_cvr3"], 2)
        self.assertEqual(p["inquiries"], 0)
        self.assertIsNone(p["cost_per_inquiry_usd"])
        self.assertIsNone(p["bid_usd"])
        self.assertEqual(p["cost_filled_rows"], 0)

    def test_unrelated_product_cost_is_not_added_to_ad_spend(self):
        extra = write_dump({"showcase": [{"url": "https://x.test/api/report", "json": {"data": [
            {"productId": 100, "cost": 7.5, "statDate": "2026-07-01"}]}}]})
        rows = run("ads-products", [self.dump, extra])["product_ad_performance"]
        self.assertTrue(all(p["spend_actual_usd"] is None for p in rows))

    def test_windows_merge_without_double_counting(self):
        rows = run("ads-products", [self.dump, self.dump], start="2026-07-01", end="2026-07-31")["product_ad_performance"]
        self.assertEqual({r["product_id"]: r["clicks"] for r in rows}, {"100": 10, "200": 30})

    def test_bid_is_raw_current_target_not_inquiry_cost(self):
        rows = run("ads-analyze", [self.dump])["bid_analysis"]
        camp = next(r for r in rows if r["level"] == "campaign")
        self.assertEqual(camp["bid_usd"], 19.84)
        self.assertIsNone(camp["cost_per_inquiry_usd"])
        self.assertEqual(camp["bid_unit"], "전환 1건 목표가")



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
        self.assertFalse(any(r["product_id"] == "200" for r in quality))

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
    def test_zero_denominators_leave_rates_blank(self):
        d = write_dump({'analytics-product': [advisor_record('product_stats.month.2026-07', [advisor_product(1, 0, 0, 0, 0)])]})
        out = run('analytics-products', [d], granularity='month')
        for table in ('analytics_products', 'analytics_summary'):
            self.assertIsNone(out[table][0]['ctr_pct'])
            self.assertIsNone(out[table][0]['inquiry_rate_pct'])

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


class Conversions(unittest.TestCase):
    def test_conversion_detail_counts_levels_and_spend_per_conversion(self):
        def conv(pid, level, ctype, page=1):
            return {"timeUs": m("2026-07-01"), "chatTime": m("2026-07-01 10:00:00"), "productId": m(pid), "fbProdId": m(pid),
                    "countryId": m("US"), "channelDisplay": m("Site Search"), "fbType": m(ctype), "buyerLevel": m(level), "senderMbrGrowthLevel": m(level)}
        rows = [conv("100", "L3", "Inquiry"), conv("100", "L1", "TM Consulting"), conv("100", "L3", "Inquiry"), conv("200", "2", "Inquiry")]
        rec = range_record("component_ggs_ad_shop_conv_detail", rows)
        rec["replay"].update({"start": "2026-07-01", "end": "2026-07-31"})
        d = write_dump({"ads-report": [rec, rec,  # same page recorded twice: counted once
                                       range_record("component_ggs_whole_site_offline_product", [product_row("100", "2026-07-01", 10, 3)]),
                                       range_record("component_ggs_whole_site_offline_campaign", [campaign_row(9001, "2026-07-01", 100.0, 40, 4)])]})
        out = run("ads-conversions", [d], start="2026-07-01", end="2026-07-31")
        s = out["conversion_summary"][0]
        self.assertEqual((s["detail_rows"], s["detail_l3plus_buyer_level"], s["spend_per_conversion_usd"]), (4, 2, 25.0))
        self.assertEqual(s["by_buyer_level"], "L3:2 L1:1 L2:1")
        self.assertEqual(s["channel_source"], "channelDisplay")
        analysis = run("ads-analyze", [d], start="2026-07-01", end="2026-07-31")
        p = next(r for r in analysis["product_ad_performance"] if r["product_id"] == "100")
        self.assertEqual((p["conv_detail_count"], p["conv_detail_l3plus_share_pct"]), (3, 66.67))
        self.assertIn("실측", p["conv_detail_source"])
        self.assertIsNone(p["spend_actual_usd"])
        q = next(r for r in analysis["inquiry_quality"] if r["product_id"] == "100" and r["link"].startswith("AI 전환"))
        self.assertEqual(q["conversion_types"], "Inquiry:2 TM Consulting:1")


    def test_live_shapes_date_name_scope_levels_and_detail_count(self):
        """Shapes seen in the 2026-11-17 live run: timeUs is the query range, productName "0",
        no channelDisplay key, empty fbProdId, two level fields."""
        def conv(chat, pid, fb, buyer, growth, repeat="0", punish="0"):
            return {"timeUs": m("2026-07-01~2026-07-31"), "chatTime": m(chat), "productId": m(pid), "productName": m("0"),
                    "fbProdId": m(fb), "fbProductName": m(""), "countryId": m("US"), "fbType": m("atm"),
                    "buyerLevel": m(buyer), "senderMbrGrowthLevel": m(growth), "isRepeat": m(repeat), "isLowQuality": m("0"), "isPunish": m(punish)}
        rows = [conv("2026-07-03 09:00:00", "100", "100", "L1", "L3"),
                conv("2026-07-05 23:59:59", "100", "", "L3", "L4"),
                conv("2026-07-05 08:00:00", "100", "0", "L2", "L1", repeat="1"),
                conv("2026-07-10 08:00:00", "200", "200", "L0", "L3", punish="1"),
                conv("2026-07-20 08:00:00", "100", "100", "L4", "L4")]  # outside --end 2026-07-15
        rec = range_record("component_ggs_ad_shop_conv_detail", rows)
        rec["replay"].update({"start": "2026-07-01", "end": "2026-07-31"})
        rec["json"]["data"]["queryResult"]["fieldNames"] = ["chatTime", "fbProdId", "productId", "trafficChannelName"]
        named = dict(product_row("100", "2026-07-05", 7, 1), subject=m("Serum A"))
        camp = dict(campaign_row(9001, "2026-07-05", 90.0, 30, 3), l3PlusCvr3=m(2))
        d = write_dump({"ads-report": [rec, range_record("component_ggs_whole_site_offline_product", [named]),
                                       range_record("component_ggs_whole_site_offline_campaign", [camp])]})
        out = run("ads-conversions", [d], start="2026-07-01", end="2026-07-15")
        cs = out["conversions"]
        # 1. date comes from chatTime, the range filter uses it, and the daily ad match uses it
        self.assertEqual([c["conversion_date"] for c in cs], ["2026-07-03", "2026-07-05", "2026-07-05", "2026-07-10"])
        self.assertNotIn("us_date", cs[0])
        self.assertEqual([c["ad_product_clicks_on_date"] for c in cs], [None, 7, 7, None])
        # 2. "0" product name -> blank, then filled from the product table title
        self.assertEqual([c["ad_product_name"] for c in cs], ["Serum A", "Serum A", "Serum A", ""])
        self.assertEqual(cs[0]["converted_product_name"], "Serum A")
        # 3. no channelDisplay in the reply: channel blank, summary says so, key names are listed
        self.assertEqual({c["channel"] for c in cs}, {""})
        s = out["conversion_summary"][0]
        self.assertTrue(s["channel_source"].startswith("없음"))
        fields = {f["field"]: f for f in run("ads-conversion-fields", [d])["conversion_fields"]}
        self.assertEqual(sorted(fields), ["chatTime", "fbProdId", "productId", "trafficChannelName"])
        self.assertTrue(fields["trafficChannelName"]["channel_candidate"])
        self.assertFalse(fields["chatTime"]["channel_candidate"])
        # 4. empty or "0" fbProdId -> shop-level conversion
        self.assertEqual([(c["converted_scope"], c["converted_product_id"]) for c in cs],
                         [("product", "100"), ("", ""), ("", ""), ("product", "200")])
        # 5. both level columns, campaign-table screen values, and the detail rows
        self.assertEqual([(c["buyer_level"], c["growth_level"]) for c in cs], [("L1", "L3"), ("L3", "L4"), ("L2", "L1"), ("L0", "L3")])
        self.assertEqual((s["campaign_table_conversions"], s["campaign_table_l3plus"], s["spend_per_conversion_usd"]), (3, 2, 30.0))
        self.assertEqual((s["detail_rows"], s["detail_l3plus_buyer_level"], s["detail_l3plus_growth_level"]), (4, 1, 3))
        self.assertEqual((s["detail_valid_conversions"], s["detail_l3plus"]), (2, 3))
        self.assertIn("senderMbrGrowthLevel", s["detail_count_source"])
        self.assertEqual(s["by_scope"], "product:2 ?:2")

    def test_conversion_fields_on_old_dump_says_redump(self):
        d = write_dump({"ads-report": [range_record("component_ggs_ad_shop_conv_detail", [])]})
        self.assertIn("다시 받으면", run("ads-conversion-fields", [d])["conversion_fields"][0]["note"])


class ZeroNotices(unittest.TestCase):
    def acct(self, day):
        return {"timeUs": m(day), "cost": m("0.00"), "clickCnt": m(0)}

    def test_ads_off_only_when_account_campaign_and_product_tables_are_all_zero(self):
        off = write_dump({"ads-report": [range_record("component_ggs_whole_site_offline_account", [self.acct("2026-07-01"), self.acct("2026-07-02")])]})
        self.assertEqual(len(report.zero_notices(report.Dump([off], "2026-07-01", "2026-07-31"), "ads-quality")), 1)
        self.assertIn("운영 상태는 별도 raw 필요", report.zero_notices(report.Dump([off], "2026-07-01", "2026-07-31"), "ads-products")[0])
        # account table reads 0 on a spending day (GGS): campaign table decides
        on = write_dump({"ads-report": [range_record("component_ggs_whole_site_offline_account", [self.acct("2026-07-01")]),
                                        range_record("component_ggs_whole_site_offline_campaign", [campaign_row("9", "2026-07-01", 12.5, 30, 1)])]})
        self.assertEqual(report.zero_notices(report.Dump([on], "2026-07-01", "2026-07-31"), "ads-quality"), [])

    def test_no_inquiry_in_period_names_the_last_one(self):
        inq = {"url": "x", "replay": {"kind": "inquiry"}, "json": {"list": [{"id": 1, "createTime": 1782950400000}]}}  # 2026-07-01 US Pacific
        d = write_dump({"inquiries": [inq]}, start="2026-08-01", end="2026-08-31")
        line = report.zero_notices(report.Dump([d], "2026-08-01", "2026-08-31"), "inquiry-list")[0]
        self.assertIn("기간 안 문의 0건(마지막 문의 2026-07-01", line)


class Coverage(unittest.TestCase):
    def test_reports_missing_items_with_reason(self):
        d = write_dump({"ads-report": [range_record("component_ggs_whole_site_offline_product", [product_row("1", "2026-07-01", 2, 0)], total=5)]})
        rows = {r["item"]: r for r in run("coverage", [d])["coverage"]}
        self.assertEqual(rows["상품별 광고비(실측)"]["status"], "없음")
        self.assertIn("API totalRows 최대 5", rows["광고 표 component_ggs_whole_site_offline_product"]["note"])
        self.assertEqual(rows["바이어 레벨(문의)"]["status"], "없음")


def pa_month(month, products):
    return {"url": "https://mydata.alibaba.com/self/.json", "replay": {"kind": "extra", "tag": "product_stats.month." + month},
            "json": {"value": {"products": {"data": [
                {"id": pid, "subject": title, "keywordEffect": [{"keyword": k, "views": str(v), "clicks": str(c), "p4pViews": "0", "p4pClicks": "0"} for k, v, c in kws]}
                for pid, title, kws in products]}}}}


def kwindex(seed, rows, nd="30d"):
    return {"url": "https://mydata.alibaba.com/self/.json?iName=vip%2FkwIndex%2FsearchWords&nd=" + nd + "&queryRaw=" + seed,
            "replay": {"kind": "extra", "tag": "kwindex." + seed, "page": 1},
            "json": {"code": 0, "data": [{"queryRaw": k, "query": k, "pv": pv, "resultCnt": rc, "clickRate": 0.05, "pvCrc": 0.1,
                                          "resultCntCrc": 0, "isInProdName": False, "isProdKeyword": False, "isP4pKeyword": False,
                                          "p4pCampaignStatus": "NOT_JOIN", "trends": [1, 2]} for k, pv, rc in rows]}}


class Keywords(unittest.TestCase):
    PRODUCTS = [("100", "Vitamin C Face Serum 50ml", [("vitamin c serum", 40, 3), ("face serum", 20, 0)]),
                ("200", "Hydrating Face Cream", [("face cream", 10, 0), ("night cream face", 5, 0)])]

    def test_seeds_come_from_our_search_terms_and_titles_with_basis(self):
        d = write_dump({"analytics-product": [pa_month("2026-07", self.PRODUCTS)]})
        seeds = run("keyword-seeds", [d])["keyword_seeds"]
        names = [x["seed"] for x in seeds]
        self.assertEqual(names[0], "face")          # in 3 search terms and 2 titles
        self.assertIn("serum", names)
        self.assertNotIn("night", names)            # one search term only
        self.assertTrue(all("유입 검색어" in x["basis"] for x in seeds))

    def test_product_seeds_are_its_search_terms_then_title_words(self):
        d = write_dump({"analytics-product": [pa_month("2026-07", self.PRODUCTS)]})
        seeds = run("keyword-seeds", [d], product="100")["keyword_seeds"]
        self.assertEqual([x["seed"] for x in seeds], ["vitamin c serum", "face serum", "serum", "face", "vitamin"])
        self.assertIn("유입 검색어 2개", seeds[2]["basis"])

    def recommend_dump(self):
        idx = kwindex("serum", [("vitamin c serum", 900, 30), ("face serum vitamin", 500, 10), ("hair serum", 800, 5), ("face serum", 300, 300)])
        sw = range_record("component_ggs_offline_searchword", [
            {"productId": m("100"), "searchWord": m("vitamin c serum"), "timeUs": m("2026-07-03"), "impsCnt": m(50), "clickCnt": m(4),
             "fiGrowthClickCnt": m(1), "cost": m(2.0), "cvr3": m(1)}])
        return write_dump({"analytics-product": [pa_month("2026-07", self.PRODUCTS)], "keyword-index": [idx], "ads-report": [sw]})

    def test_recommend_one_product_numbers_only_ranked_by_one_score(self):
        out = tables("keywords-recommend", [self.recommend_dump()], product="vitamin", top=10)
        rows = out["keyword_recommendations"]
        self.assertEqual(list(rows[0])[:10], ["키워드", "점수", "검색량", "경쟁(셀러 수)", "입찰가 USD", "노출", "클릭", "CTR %", "문의", "L1+ %"])
        self.assertEqual([r["키워드"] for r in rows], ["vitamin c serum", "face serum vitamin", "face serum"])
        top = rows[0]
        # 900^2/30 * (1+3 clicks) * (1+1 inquiry) * (1+25/100) / (1+0)
        self.assertIsNone(top["점수"])  # absent bid cannot be replaced with 0
        self.assertEqual((top["노출"], top["클릭"], top["CTR %"], top["문의"], top["L1+ %"]), (40, 3, 7.5, 1, 25.0))
        self.assertIsNone(top["입찰가 USD"])                         # no bid field: blank, not CPC
        mid = rows[1]
        self.assertEqual((mid["노출"], mid["클릭"], mid["문의"], mid["L1+ %"]), (None, None, None, None))  # no numbers: blank
        self.assertNotIn("hair serum", [r["키워드"] for r in rows])   # only 1 title word matches
        self.assertIn("점수 = 검색량² ÷ 경쟁", out["_keyword_recommend.md"])
        self.assertTrue(out["_head"].startswith("상품 100 "))

    def test_recommend_retains_actual_search_terms_without_inventing_volume(self):
        rows = run("keywords-recommend", [self.recommend_dump()], product="200")["keyword_recommendations"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["검색량"] is None and r["점수"] is None for r in rows))
        self.assertEqual(rows[0]["노출"], 10)

    def test_recommend_without_product_lists_products_and_all_products_batches(self):
        d = self.recommend_dump()
        out = tables("keywords-recommend", [d])
        self.assertNotIn("keyword_recommendations", out)
        self.assertEqual([p["상품ID"] for p in out["products"]], ["100", "200"])
        self.assertIn("--product", out["_keyword_recommend.md"])
        self.assertEqual(tables("keywords-recommend", [d], product="face")["_exit"], 64)   # 2 matches: pick one
        rows = run("keywords-recommend", [d], all_products=True)["keyword_recommendations"]
        self.assertEqual({r["상품ID"] for r in rows}, {"100", "200"})

    def test_keywords_raw_lists_index_and_search_terms(self):
        idx = kwindex("serum", [("vitamin c serum", 900, 30)])
        sw = range_record("component_ggs_offline_searchword", [
            {"searchWord": m("vitamin c serum"), "timeUs": m("2026-07-03"), "impsCnt": m(50), "clickCnt": m(4), "cost": m(2.0)},
            {"searchWord": m("face serum"), "timeUs": m("2026-07-03"), "impsCnt": m(5), "clickCnt": m(0), "cost": m(0)}])
        d = write_dump({"keyword-index": [idx], "ads-report": [sw]})
        out = run("keywords", [d])
        self.assertEqual(len([r for r in out["keywords"] if "searchword" in r["source"]]), 2)
        self.assertEqual(out["keyword_index"][0]["search_index"], 900)


class AdvisorMonthCheck(unittest.TestCase):
    def test_period_label_checked_against_summary_visit_count_and_empty_period_listed(self):
        def summ(month, visit):
            return {"url": "https://mydata.alibaba.com/self/.json", "replay": {"kind": "extra", "tag": "product_summary.month." + month},
                    "json": {"code": 0, "data": {"statDate": {"value": month + "-01"}, "totalVisitProductCount": {"value": visit}}}}
        def stats(month, visitors):
            return {"url": "https://mydata.alibaba.com/self/.json", "replay": {"kind": "extra", "tag": "product_stats.month." + month},
                    "json": {"value": {"products": {"data": [{"id": str(i), "subject": "p", "sumProdVisitorCnt": str(v)} for i, v in enumerate(visitors)]}}}}
        d = write_dump({"analytics-product": [summ("2026-08", 2), stats("2026-08", [3, 1, 0]), summ("2026-09", 5), stats("2026-09", [])]},
                       start="2026-08-01", end="2026-09-30")
        rows = {r["period"]: r for r in run("analytics-products", [d], granularity="month")["analytics_periods"]}
        self.assertEqual((rows["2026-08"]["products_with_visitors"], rows["2026-08"]["visit_products_match"]), (2, True))
        self.assertEqual((rows["2026-09"]["products"], rows["2026-09"]["visit_products_match"]), (0, False))


class InquiryListColumns(unittest.TestCase):
    def test_three_columns_first_and_tm_inquiry_has_no_viewed_product(self):
        items = [{"id": 1, "createTime": T_0701, "feedbackType": 1, "targetIds": ["100"], "buyerCountry": "US", "spam": False, "inquiryCount": 2},
                 {"id": 2, "createTime": T_0701 + 1000, "feedbackType": 6, "targetIds": ["4350522996442"], "buyerCountry": "PK", "spam": True}]
        d = write_dump({"inquiries": [{"replay": {"kind": "inquiry"}, "json": {"list": items}}]})
        rows = run("inquiry-list", [d])["inquiries"]
        self.assertEqual(list(rows[0].keys())[4:7], ["viewed_products", "buyer_country", "risk"])
        by = {r["inquiry_id"]: r for r in rows}
        self.assertEqual(by["1"]["viewed_products"], "")
        self.assertEqual(by["1"]["product_id"], "100")
        self.assertNotIn("targetIds", by["1"]["viewed_products_source"])
        self.assertEqual(by["2"]["viewed_products"], "")
        self.assertIn("스팸", by["2"]["risk"])
        self.assertIn("inquiryCount 2", by["1"]["risk"])


class ActualFields(unittest.TestCase):
    def test_extra_level_join_preserves_unknown_spam(self):
        d = write_dump({'inquiries': [
            {'replay': {'kind': 'inquiry'}, 'json': {'list': [
                {'id': 1, 'tradeId': 11, 'createTime': T_0701, 'feedbackType': 1, 'targetIds': ['100']},
                {'id': 2, 'tradeId': 22, 'createTime': T_0701, 'spam': False}]}},
            {'replay': {'kind': 'inquiry-extra'}, 'json': {'list': [{'tradeId': '11', 'userNewLevel': 'L0'}]}}
        ]})
        rows = {r['inquiry_id']: r for r in run('inquiry-list', [d])['inquiries']}
        self.assertEqual(rows['1']['buyer_level'], 'L0')
        self.assertIsNone(rows['1']['spam'])
        self.assertIs(rows['2']['spam'], False)
        self.assertEqual(rows['2']['buyer_level'], '')
        self.assertEqual(run('inquiry-by-product', [d])['inquiry_by_product'][0]['buyer_level'], 'L0')

    def test_keyword_unknown_metrics_and_other_product_bid_stay_blank(self):
        idx = kwindex('serum', [('serum', 50, 10)])
        ads = range_record('component_ggs_offline_keyword', [
            {'keyword': m('serum'), 'productId': m('100'), 'timeUs': m('2026-07-01'), 'impsCnt': m(100), 'clickCnt': m(4)},
            {'keyword': m('serum'), 'productId': m('200'), 'timeUs': m('2026-07-01'), 'bidPrice': m(9)}])
        d = write_dump({'keyword-index':[idx], 'analytics-product':[pa_month('2026-07',[('100','Serum',[])])], 'ads-report':[ads]})
        r = run('keywords-recommend',[d],product='100')['keyword_recommendations'][0]
        self.assertEqual((r['노출'],r['클릭'],r['CTR %']), (100,4,4))
        self.assertIsNone(r['입찰가 USD'])
        self.assertIsNone(r['문의'])
        self.assertIsNone(r['L1+ %'])
        keys=list(r); n=keys.index('CTR %')
        self.assertEqual(keys[n+1:n+3], ['CTR %_numerator','CTR %_denominator'])
        self.assertEqual((r[keys[n+1]],r[keys[n+2]]),(4,100))

    def test_empty_period_commands_have_notice(self):
        d=report.Dump([write_dump({})])
        for cmd in ('inquiry-list','inquiry-stats','inquiry-by-product','keywords','analytics-products','bids','coverage'):
            self.assertTrue(report.zero_notices(d,cmd),cmd)

    def test_zero_denominator_is_unknown_not_zero(self):
        r=report.inquiry_economics(None,0,None,'cost','inquiries')
        self.assertIsNone(r['conversion_rate'])
        self.assertIsNone(r['cpc_usd'])
        self.assertIsNone(r['cost_per_inquiry_usd'])

class ProfileRows(unittest.TestCase):
    def test_tm_uses_actual_visit_ids_and_keeps_activity_window(self):
        d=write_dump({'inquiries':[
            {'replay':{'kind':'inquiry'},'json':{'list':[{'id':1,'tradeId':11,'createTime':T_0701,'feedbackType':6,'targetIds':['999']}]}},
            {'replay':{'kind':'inquiry-profile'},'json':{'capturedAt':'2026-11-17T00:00:00Z','activityWindow':'90d','list':[{
                'tradeId':'11','growthLevel':'L2','country':'US','spamInquiryMarkedBySupplierCount':0,'addedToBlacklistCount':1,
                'productVisitHistory':[{'productId':'100','productName':'Fixture'}]}]}}
        ]})
        r=run('inquiry-list',[d])['inquiries'][0]
        self.assertEqual(r['viewed_products'],'100')
        self.assertNotIn('999',r['viewed_products'])
        self.assertEqual(r['buyer_level'],'L2')
        self.assertEqual(r['buyer_country'],'US')
        self.assertEqual(r['spam_inquiry_count_90d'],0)
        self.assertEqual(r['blacklist_count_90d'],1)
        self.assertIn('90일',r['buyer_activity_source'])
        self.assertIn('2026-11-17',r['buyer_activity_captured_at'])


class RawMetricGaps(unittest.TestCase):
    def test_keyword_product_summary_preserves_partial_missing_counts(self):
        rows = [{'productId': m('100'), 'timeUs': m('2026-07-01'), 'impsCnt': m(40), 'clickCnt': m(3)},
                {'productId': m('100'), 'timeUs': m('2026-07-02'), 'impsCnt': m(20), 'clickCnt': m(2), 'cvr3': m(1)}]
        d = report.Dump([write_dump({'ads-report': [range_record(report.PRODUCT_TABLES[0], rows)]})])
        self.assertEqual(report.product_ad_numbers(d)['100'],
                         {'impressions': 60, 'clicks': 5, 'inquiries': None, 'l1plus_clicks': None})
        line = report.product_line(d, '100', report.keyword_numbers(d))
        self.assertIn('문의 ·L1+ %', line)
        self.assertIn('raw 숫자 누락', line)

    def test_missing_product_counts_are_not_reported_as_zero(self):
        d=write_dump({'ads-report':[range_record(report.PRODUCT_TABLES[0],[{'productId':m('100'),'timeUs':m('2026-07-01'),'impsCnt':m(40),'clickCnt':m(3)}])]})
        r=run('ads-products',[d])['product_ad_performance'][0]
        self.assertIsNone(r['inquiries_cvr3'])
        self.assertIsNone(r['l1plus_click_pct'])
        self.assertIsNone(r['conversion_rate'])
        self.assertIn('cvr3',r['missing_metrics_reason'])
        self.assertEqual(r['ctr_pct'],7.5)
        self.assertEqual(r['ctr_clicks'],3)
        self.assertEqual(r['ctr_impressions'],40)

    def test_keyword_and_search_terms_are_alternative_sources_not_additive(self):
        base={'keyword':m('serum'),'productId':m('100'),'timeUs':m('2026-07-01'),'impsCnt':m(20),'clickCnt':m(2),'cvr3':m(0)}
        d=write_dump({'analytics-product':[pa_month('2026-07',[('100','Serum',[])])], 'ads-report':[
            range_record('component_ggs_offline_keyword',[base]),
            range_record('component_ggs_offline_searchword',[{**base,'cvr3':m(5)}])]})
        r=run('keywords-recommend',[d],product='100')['keyword_recommendations'][0]
        self.assertEqual((r['노출'],r['클릭'],r['문의']),(20,2,0))
        self.assertIsNone(r['L1+ %'])


if __name__ == "__main__":
    unittest.main()


class AdsTables(unittest.TestCase):
    def test_every_table_in_full_with_completeness_and_short_exit(self):
        full = range_record("component_ggs_offline_product", [product_row("p1", "2026-07-02", 3, 1)])
        short = range_record("component_ggs_offline_keyword", [{"keyword": m("filter"), "timeUs": m("2026-07-02"), "clickCnt": m(2)}], total=5)
        d = write_dump({"ads-report": [full, short], "ads-campaigns": [CAMPAIGNS]})
        got = tables("ads-tables", [d])
        self.assertEqual(len(got["table_component_ggs_offline_product"]), 1)
        self.assertEqual(got["table_component_ggs_offline_keyword"][0]["keyword"], "filter")
        self.assertEqual(got["campaigns"][0]["campaign_id"], "9001")
        comp = {c["table"]: c["complete"] for c in got["completeness"]}
        self.assertEqual(comp["component_ggs_offline_product"], "yes")
        self.assertEqual(comp["component_ggs_offline_keyword"], "NO (4 short)")
        self.assertEqual(got["_exit"], 3)

    def test_all_complete_exits_zero(self):
        d = write_dump({"ads-report": [range_record("component_ggs_offline_product", [product_row("p1", "2026-07-02", 3, 1)])]})
        got = tables("ads-tables", [d])
        self.assertNotIn("_exit", got)
        self.assertIn("전부 totalRows", got["_head"])


    def test_rows_sharing_identity_but_differing_are_kept_and_repeats_are_short(self):
        """Search-term rows can share searchWord+day yet differ (e.g. per match type): keep both.
        A page that repeats an earlier row leaves a row unfetched: completeness counts distinct rows."""
        a = {"searchWord": m("w"), "timeUs": m("2026-07-02"), "matchType": m("broad"), "clickCnt": m(1)}
        b = {**a, "matchType": m("exact"), "clickCnt": m(2)}
        rec = range_record("component_ggs_offline_searchword", [a, b, a], total=3)
        got = tables("ads-tables", [write_dump({"ads-report": [rec]})])
        self.assertEqual(len(got["table_component_ggs_offline_searchword"]), 2)
        c = got["completeness"][0]
        self.assertEqual((c["rows_got"], c["repeated_rows"], c["complete"]), (2, 1, "NO (1 short)"))


    def test_per_day_refetch_completes_a_window_whose_pages_repeated(self):
        """Fixture values only: the whole-window pages repeat row a and miss c; the day queries bring c."""
        def rec(rows, total, start, end, split=False):
            r = range_record("component_ggs_offline_searchword", rows, total=total)
            r["replay"].update({"start": start, "end": end, "tag": "manual.searchword", **({"split": True} if split else {})})
            return r
        a = {"searchWord": m("a"), "timeUs": m("2026-07-01")}
        b = {"searchWord": m("b"), "timeUs": m("2026-07-01")}
        c = {"searchWord": m("c"), "timeUs": m("2026-07-02")}
        recs = [rec([a, b, a], 3, "2026-07-01", "2026-07-02"),
                rec([a, b], 2, "2026-07-01", "2026-07-01", split=True), rec([c], 1, "2026-07-02", "2026-07-02", split=True)]
        got = tables("ads-tables", [write_dump({"ads-report": recs})])
        self.assertEqual(len(got["completeness"]), 1)
        row = got["completeness"][0]
        self.assertEqual((row["rows_got"], row["split_days"], row["complete"]), (3, "2/2", "yes"))
        self.assertEqual(len(got["table_component_ggs_offline_searchword"]), 3)
        self.assertNotIn("_exit", got)


class L1PlusMissing(unittest.TestCase):
    def test_table_without_l1_count_is_unknown_not_zero(self):
        """Live 2026-11-17: the Manual product table gives fiGrowthClickRate but no fiGrowthClickCnt."""
        with_l1 = {**product_row("p2", "2026-07-02", 10, 1), "fiGrowthClickCnt": m(4)}
        no_l1 = {**product_row("p1", "2026-07-02", 20, 1), "fiGrowthClickRate": m("0.3")}
        d = write_dump({"ads-report": [range_record("component_ggs_offline_product", [no_l1]),
                                       range_record("component_ggs_whole_site_offline_product", [with_l1])]})
        totals = {r["table"]: r for r in run("ads-quality", [d])["buyer_quality"] if r["level"] == "total"}
        manual = totals["component_ggs_offline_product"]
        self.assertIsNone(manual["l1plus_click_pct"])
        self.assertIn("없음", manual["l1plus_source"])
        self.assertEqual(totals["component_ggs_whole_site_offline_product"]["l1plus_click_pct"], 40.0)
        prod = {r["product_id"]: r for r in run("ads-products", [d])["product_ad_performance"]}
        self.assertIsNone(prod["p1"]["l1plus_click_pct"])
        self.assertEqual(prod["p2"]["l1plus_click_pct"], 40.0)


class CampaignKind(unittest.TestCase):
    def test_manual_type_rows_in_whole_site_campaign_table_count_as_manual_spend(self):
        """Live shape 2026-11-17: the whole-site campaign table holds a Manual (type 2) and an AI (type 51) campaign."""
        camp = "component_ggs_whole_site_offline_campaign"
        manual = {**campaign_row("9101", "2026-07-02", 1000.0, 800, 3), "campaignType": m(2)}
        ai = {**campaign_row("9102", "2026-07-02", 500.0, 200, 5), "campaignType": m(51)}
        d = write_dump({"ads-report": [
            range_record(camp, [manual, ai]),
            range_record("component_ggs_offline_product", [product_row("p1", "2026-07-02", 800, 3)]),
            # whole-site product table = every campaign: p1 Manual clicks again + p2 AI clicks
            range_record("component_ggs_whole_site_offline_product", [product_row("p1", "2026-07-02", 800, 3),
                                                                       product_row("p2", "2026-07-02", 200, 5)])]})
        rows = run("ads-quality", [d])["buyer_quality"]
        by_campaign = {r["key"]: (r["campaign_kind"], r["spend_usd"]) for r in rows if r["level"] == "campaign"}
        self.assertEqual(by_campaign["9101"], ("Manual", 1000.0))
        self.assertEqual(by_campaign["9102"], ("AI Smart", 500.0))
        kinds = {r["campaign_kind"]: (r["spend_usd"], r["clicks"], r["cpc_usd"]) for r in rows if r["level"] == "kind_total"}
        self.assertEqual(kinds, {"Manual": (1000.0, 800, 1.25), "AI Smart": (500.0, 200, 2.5)})
        totals = {r["campaign_kind"]: (r["spend_usd"], r["clicks"]) for r in rows if r["level"] == "total"}
        self.assertEqual(totals, {"Manual": (None, 800), "전체(AI+Manual)": (None, 1000)})  # product tables have no cost; campaign totals stay in kind_total
        prod = {r["product_id"]: r["clicks"] for r in run("ads-products", [d])["product_ad_performance"]}
        self.assertEqual(prod, {"p1": 800, "p2": 200})  # Manual product-day not counted twice
