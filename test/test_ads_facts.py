import contextlib
import io
import json
import unittest
from urllib.parse import urlencode

from test_report import run, write_dump, range_record, product_row, campaign_row, m, CAMPAIGNS, T_0701
import ads_facts as ar
import report


class DailyFacts(unittest.TestCase):
    def test_history_actual_schema_filters_dates_and_does_not_invent_state(self):
        event = {"gmtCreate": "2026-07-01 12:00:00", "campaignId": 9001,
                 "logDetail": "fixture change", "operatorType": "fixture"}
        record = {"replay": {"kind": "ads-history", "page": 1, "start": "2026-07-01",
                  "end": "2026-07-31", "productLineId": 1, "logMajor": "campaign"},
                  "json": {"ok": True, "data": [event, {"gmtCreate": "2026-08-01 12:00:00"}], "totalCount": 2}}
        d = write_dump({"ads-history": [record, record], "ads-report": [range_record(
            "component_ggs_whole_site_offline_campaign", [campaign_row(9001, "2026-07-01", 3, 10, 2)])]})
        result = run("ads-history", [d])
        self.assertEqual(len(result["history"]), 1)
        self.assertTrue(result["history_coverage"][0]["complete"])
        daily = run("ads-daily", [d])["ad_daily"][0]
        self.assertEqual(daily["changes"], [event])
        self.assertIsNone(daily["online_status"])
        self.assertIsNone(daily["daily_budget"])

    def test_history_failed_response_is_not_complete_empty(self):
        d = write_dump({"ads-history": [{"replay": {"kind": "ads-history", "page": 1},
                        "json": {"ok": False, "data": [], "totalCount": 0}}]})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(report.main(["ads-history", "--from", d, "--format", "json"]), 3)

    def test_default_ui_scope_does_not_hide_requested_account_cost(self):
        table = "component_ggs_whole_site_offline_account"
        default = range_record(table, [{"timeUs": m("2026-07-01"), "cost": m(0), "clickCnt": m(0)}])
        default["body"] = urlencode({"data": json.dumps({"tableName": table,
            "filters": [{"field": "campaignType", "value": "47"}]})})
        requested = range_record(table, [{"timeUs": m("2026-07-01"), "cost": m(12.34), "clickCnt": m(10)}])
        requested["replay"]["tag"] = "ai.account_daily"
        d = write_dump({"ads-report": [default, requested]})
        data = run("ads-report", [d])
        self.assertEqual(len(data["account_day"]), 1)
        self.assertEqual(data["account_day"][0]["spend"], 12.34)
        raw = run("ads-tables", [d])["table_" + table]
        self.assertEqual(len(raw), 2)
        self.assertEqual({r["cost"] for r in raw}, {"0", "12.34"})
        self.assertNotEqual(raw[0]["_query_filters"], raw[1]["_query_filters"])

    def test_tagged_range_replaces_page_load_of_same_scope(self):
        table = "component_ggs_whole_site_offline_product"
        old = range_record(table, [product_row("100", "2026-07-01", 0, 0)])
        old["replay"] = None
        fresh = range_record(table, [product_row("100", "2026-07-01", 12, 2)])
        fresh["replay"]["tag"] = "ai.product"
        d = write_dump({"ads-report": [old, fresh]})
        self.assertEqual(run("ads-daily", [d], level="product")["ad_daily"][0]["clicks"], 12)

    def test_tables_keep_period_raw_but_daily_does_not_sum_it(self):
        records = [product_row("100", "2026-07-01", 10, 2),
                   product_row("100", "2026-07-01~2026-07-31", 800, 99)]
        records[0]["cost"] = m(3)
        records[1]["cost"] = m(400)
        d = write_dump({"ads-report": [range_record("component_ggs_whole_site_offline_product", records)]})
        tables = run("ads-tables", [d])
        self.assertEqual(len(tables["table_component_ggs_whole_site_offline_product"]), 2)
        self.assertEqual(run("ads-products", [d])["product_ad_performance"][0]["spend"], 3)

    def test_all_calculation_operands_are_adjacent(self):
        row = product_row("100", "2026-07-01", 10, 2, mc=1)
        row.update(cost=m(3), fiGrowthClickCnt=m(4))
        d = write_dump({"ads-report": [range_record("component_ggs_whole_site_offline_product", [row])]})
        for command, table, kw in (("ads-daily", "ad_daily", {"level": "product"}),
                                    ("ads-products", "product_ad_performance", {})):
            cols = list(run(command, [d], **kw)[table][0])
            for metric, operands in (("l1plus_click_pct", ["l1plus_clicks", "l1plus_click_denominator"]),
                                     ("cpc_usd", ["cpc_cost_sum", "cpc_clicks"]),
                                     ("cost_per_inquiry_usd", ["cost_sum", "cost_inquiries"])):
                at = cols.index(metric)
                self.assertEqual(cols[at + 1:at + 3], operands)

    def test_incomplete_range_is_reported_on_daily_and_products(self):
        d = write_dump({"ads-report": [range_record("component_ggs_whole_site_offline_product",
                         [product_row("100", "2026-07-01", 10, 2)], total=100)]})
        for command in ("ads-products", "ads-daily", "ads-quality"):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                rc = report.main([command, "--from", d, "--format", "json"])
            self.assertEqual(rc, 3)
            self.assertIn("totalRows", err.getvalue())

    def test_missing_metric_is_not_announced_as_zero(self):
        d = write_dump({"ads-report": [range_record("component_ggs_whole_site_offline_product",
                         [product_row("100", "2026-07-01", 0, 0)])]})
        with contextlib.redirect_stderr(io.StringIO()) as err:
            run("ads-products", [d])
        self.assertNotIn("전부 0", err.getvalue())

    def test_sum_dated_raw_only_with_cost_string_zero_and_missing(self):
        rows = []
        for date, cost in (("2026-07-01", "US$ 1,234.56"), ("2026-07-02", "0.00"),
                           ("2026-07-01~2026-07-31", "9999"), ("2026-08-01", "99")):
            row = product_row("100", date, 10, 3, mc=2)
            row.update(costStr=m(cost), fiGrowthClickCnt=m(4))
            rows.append(row)
        d = write_dump({"ads-report": [range_record("component_ggs_whole_site_offline_product", rows)]})
        daily = run("ads-daily", [d, d], level="product")["ad_daily"]
        self.assertEqual(len(daily), 2)
        self.assertEqual([r["spend"] for r in daily], [1234.56, 0])
        p = run("ads-products", [d])["product_ad_performance"][0]
        self.assertEqual(p["spend_actual_usd"], 1234.56)
        self.assertEqual(p["cost_per_inquiry_usd"], 308.64)
        self.assertEqual((p["cost_sum"], p["cost_inquiries"]), (1234.56, 4))
        self.assertEqual((p["l1plus_click_pct"], p["l1plus_clicks"], p["l1plus_click_denominator"]), (40, 8, 20))
        self.assertEqual(p["inquiries_cvr3"], 6)

    def test_missing_day_metric_does_not_become_zero_or_partial_total(self):
        first = product_row("100", "2026-07-01", 10, 1)
        first.update(cost=m(5), fiGrowthClickCnt=m(2))
        second = product_row("100", "2026-07-02", 20, 0)
        d = write_dump({"ads-report": [range_record("component_ggs_whole_site_offline_product", [first, second])]})
        p = run("ads-products", [d])["product_ad_performance"][0]
        self.assertIsNone(p["spend"])
        self.assertIsNone(p["l1plus_click_pct"])
        self.assertEqual(p["cost_filled_rows"], 1)
        self.assertEqual(p["daily_rows"], 2)

    def test_campaign_configuration_is_not_backdated(self):
        d = write_dump({"ads-campaigns": [CAMPAIGNS], "ads-report": [range_record(
            "component_ggs_whole_site_offline_campaign", [campaign_row(9001, "2026-07-01", 0, 0, 0)])]})
        daily = run("ads-daily", [d])["ad_daily"][0]
        self.assertEqual(daily["spend"], 0)
        self.assertIsNone(daily["daily_budget"])
        self.assertIsNone(daily["online_status"])
        campaign = run("ads-campaigns", [d])["campaigns"][0]
        self.assertEqual(campaign["bid"], 19.84)
        self.assertIsNone(campaign["daily_budget"])
        self.assertEqual(campaign["budget_usd"], 600)
        self.assertEqual(campaign["raw"]["id"], 9001)

    def test_keyword_daily_and_campaign_identity_do_not_collapse(self):
        rows = []
        for cid in (1, 2):
            row = campaign_row(cid, "2026-07-01", 3, 10, 2)
            row.update(productId=m(100), keyword=m("fixture"), fiGrowthClickCnt=m(3))
            rows.append(row)
        d = write_dump({"ads-report": [range_record("component_ggs_offline_keyword", rows)]})
        data = run("ads-daily", [d], level="keyword")["ad_daily"]
        self.assertEqual(len(data), 2)
        self.assertEqual(sum(r["spend"] for r in data), 6)

    def test_product_inquiries_only_matches_actual_product_id(self):
        d = write_dump({"ads-report": [range_record("component_ggs_whole_site_offline_product", [
            product_row("100", "2026-07-01", 10, 1), product_row("200", "2026-07-01", 5, 0)])],
            "inquiries": [{"replay": {"kind": "inquiry"}, "json": {"list": [
                {"id": 1, "createTime": T_0701, "feedbackType": 1, "targetIds": [100], "buyerCountry": "US"},
                {"id": 2, "createTime": T_0701, "feedbackType": 6, "targetIds": []}]}}]})
        rows = run("ads-product-inquiries", [d])["product_inquiries"]
        self.assertEqual(len(rows), 2)
        by = {r["product_id"]: r for r in rows}
        self.assertEqual(by["100"]["inquiry_id"], "1")
        self.assertIsNone(by["200"]["inquiry_id"])

    def test_every_empty_ads_command_has_notice(self):
        d = write_dump({})
        for command in ("daily", "products", "report", "quality", "analyze", "conversions", "conversion-fields", "campaigns", "history", "product-inquiries"):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()) as stderr:
                run("ads-" + command, [d])
                self.assertIn("안내:", stderr.getvalue())

    def test_numeric_absence_and_dates(self):
        for value in (None, "--", "NaN", "Infinity", True):
            self.assertIsNone(ar.number(value))
        self.assertEqual(ar.number("$0.00"), 0)
        self.assertEqual(ar.day("2026-02-30"), "")


if __name__ == "__main__":
    unittest.main()
