import email.message
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

sys.dont_write_bytecode = True
ENGINE = Path(__file__).resolve().parent.parent / "engine"
sys.path.insert(0, str(ENGINE))
import api_transport as at  # noqa: E402
import report  # noqa: E402

spec = importlib.util.spec_from_file_location("seller_raw_api", ENGINE / "seller_central_raw.py")
raw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(raw)

LOGIN_ID = "fixture-seller"
COOKIES = [{"name": "cookie2", "value": "c2-fixture-%s" % LOGIN_ID, "domain": ".alibaba.com", "path": "/", "expires": -1}]
TOKEN = "fake"


class Answer:
    def __init__(self, status=200, body="", headers=None):
        self.status, self._body = status, body.encode()
        self.headers = email.message.Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def read(self, *_):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeSite:
    """Answers like the seller center: www2 csrf + XSRF cookie, message XSRF cookie, paged tables."""

    def __init__(self, rows=5, size=2, login_after=None, punish=False):
        self.rows, self.size, self.calls, self.login_after, self.punish = rows, size, [], login_after, punish

    def __call__(self, req, timeout=None):
        u = urlsplit(req.full_url)
        form = {k: v[0] for k, v in parse_qs((req.data or b"").decode()).items()}
        self.calls.append((req.get_method(), u.netloc + u.path, form, req.headers.get("Cookie", "")))
        if self.login_after is not None and len(self.calls) > self.login_after:
            return Answer(302, "", {"Location": "https://login.alibaba.com/newlogin/icbuLogin.htm?return_url=x"})
        if self.punish and u.path.endswith("component/data"):
            return Answer(302, "", {"Location": "https://www2.alibaba.com//_____tmd_____/punish?x5secdata=x"})
        if u.path == "/api/csrf":
            return Answer(200, json.dumps({"headerName": "X-XSRF-TOKEN", "parameterName": "_csrf", "token": TOKEN}),
                          {"Set-Cookie": "XSRF-TOKEN=%s; Path=/" % TOKEN})
        if u.path.endswith("default.htm"):
            return Answer(200, "<html>page</html>", {"Set-Cookie": "XSRF-TOKEN=%s; Path=/" % TOKEN})
        if u.path.endswith("component/data"):
            if form.get("_csrf") != TOKEN or ("XSRF-TOKEN=%s" % TOKEN) not in req.headers.get("Cookie", ""):
                return Answer(302, "", {"Location": "https://err.taobao.com/error1.html"})
            d = json.loads(form["data"])
            page = (d.get("page") or {}).get("index", 1)
            if d["tableName"] == "component_ggs_ad_shop_conv_detail":
                rows = [{"timeUs": {"main": "2026-07-0%d" % (i + 1)}, "buyerLevel": {"main": "L3"}, "convUserName": {"main": "private-fixture"}} for i in range(2)]
                return Answer(200, json.dumps({"ok": True, "data": {"queryResult": {"totalRows": 2, "detailList": rows}}}))
            lo = (page - 1) * self.size
            rows = [{"productId": {"main": str(100 + i)}, "timeUs": {"main": "2026-07-01"}, "clickCnt": {"main": 1}}
                    for i in range(lo, min(lo + self.size, self.rows))] if d.get("page") else [{"timeUs": {"main": "2026-07-01"}, "cost": {"main": 2.5}}]
            return Answer(200, json.dumps({"ok": True, "data": {"queryResult": {"totalRows": self.rows if d.get("page") else 1, "detailList": rows}}}))
        if u.path.endswith("subjectList.htm"):
            if form.get("_csrf_token_") != TOKEN:
                return Answer(403, "forbidden")
            params = json.loads(form["params"])
            page = params["pagination"]["nextPage"]
            items = [{"id": 1300000000 + i, "tradeId": 1300000000 + i, "secTradeId": "s%d" % i, "createTime": 1790000000000, "feedbackType": 1,
                      "subject": "private-fixture", "sender": {"countryCode": "US", "name": "private-fixture", "chatToken": "secret-fixture"}}
                     for i in range((page - 1) * 20, min(page * 20, 25))]
            return Answer(200, json.dumps({"code": 200, "data": {"pagination": {"totalCount": 25, "currentPage": page}, "list": [items]}}).replace('"', "&quot;"))
        if u.path.endswith("subjectListExtraInfo.htm"):
            ids = json.loads(form["params"])["tradeIds"]
            body = {"code": 200, "data": {"list": {str(i): {"latestMessage": {"content": 'he said "hi"'}, "sender": {"userNewLevel": "L%d" % (i % 4), "companyName": "private-fixture"}} for i in ids}}}
            return Answer(200, json.dumps(body, separators=(",", ":")).replace('"', "&quot;"))
        if "mydata" in u.netloc:
            q = parse_qs(u.query)
            n = int(q.get("pageNO", ["1"])[0])
            data = [{"id": n, "subject": "x"}] if n <= 2 else []
            return Answer(200, json.dumps({"successed": True, "value": {"products": {"data": data, "recordCount": 2}}}))
        if u.path.endswith("allList"):
            d = json.loads(form["data"])
            items = [{"id": 900 + d["page"], "properties": {"maxPrice": 1.5, "optimizeTarget": "whole_site_fb"}, "title": "fixture"}] if d["page"] == 1 else []
            return Answer(200, json.dumps({"ok": True, "data": {"51": items}}))
        return Answer(200, json.dumps({"ok": True, "data": {}}))


def http(site):
    return at.Http(COOKIES, opener=site)


PAGES = {p["key"]: p for p in raw.load_pages()}


def params(key, **kw):
    pg = PAGES[key]
    return {"key": key, "url": pg["url"], "start": "2026-07-01", "end": "2026-07-31", "maxPages": 30, "rangeReplay": bool(pg.get("rangeReplay")),
            "rangeTables": kw.get("tables", []), "rangeResume": {}, "extra": kw.get("extra", []), "inquiryList": bool(pg.get("inquiryList"))}


class ApiTransport(unittest.TestCase):
    def test_inquiry_list_pages_allowlist_levels_and_token_never_saved(self):
        site = FakeSite()
        res = at.capture(PAGES["inquiries"], params("inquiries"), COOKIES, LOGIN_ID, http=http(site))
        self.assertEqual(res["status"], "ok")
        self.assertEqual([r["replay"]["page"] for r in res["records"]], [1, 2])
        items = [i for r in res["records"] for i in json.loads(r["text"])["list"]]
        self.assertEqual(len(items), 25)
        self.assertEqual(items[0]["buyerCountry"], "US")
        self.assertEqual({i["buyerLevel"] for i in items}, {"L0", "L1", "L2", "L3"})
        saved = json.dumps(res)
        for v in ("private-fixture", "secret-fixture", TOKEN):
            self.assertNotIn(v, saved)
        self.assertTrue(all(c[2].get("_csrf_token_") == TOKEN for c in site.calls if c[1].endswith(".htm") and c[0] == "POST"))

    def test_ads_range_pages_until_total_rows_with_www2_csrf(self):
        site = FakeSite(rows=5, size=2)
        tables = [t for t in PAGES["ads-report"]["rangeTables"] if t["key"] in ("ai.product", "ai.product_cost", "ai.conv_detail")]
        tables = [{**t, "pageSize": 2} if t["key"].startswith("ai.product") else t for t in tables]
        res = at.capture(PAGES["ads-report"], params("ads-report", tables=tables), COOKIES, LOGIN_ID, http=http(site))
        self.assertEqual(res["status"], "ok", res.get("detail"))
        cost = [r for r in res["records"] if (r["replay"] or {}).get("tag") == "ai.product_cost"]
        self.assertEqual({r["replay"]["table"] for r in cost}, {"component_ggs_offline_product@whole_site"})  # kept apart from the manual table
        self.assertEqual(len(cost), 3)
        prod = [r for r in res["records"] if (r["replay"] or {}).get("tag") == "ai.product"]
        self.assertEqual([r["replay"]["page"] for r in prod], [1, 2, 3])
        sent = [json.loads(c[2]["data"]) for c in site.calls if c[1].endswith("component/data")]
        self.assertTrue(all(d["orders"][0] == {"field": "timeUs", "mode": "desc"} for d in sent if d.get("page")))  # unsorted pages overlap
        self.assertEqual(next(d for d in sent if d.get("page") and d["tableName"].endswith("_product"))["orders"][1], {"field": "productId", "mode": "desc"})
        conv = next(r for r in res["records"] if (r["replay"] or {}).get("tag") == "ai.conv_detail")
        self.assertNotIn("private-fixture", conv["text"])
        self.assertIn("convUserName", conv["text"])  # key name only, in fieldNames
        self.assertNotIn(TOKEN, json.dumps(res))
        self.assertEqual(raw.range_state(res["records"])["component_ggs_whole_site_offline_product[]"]["complete"], True)

    def test_same_report_rows_as_the_browser_records(self):
        """API records written by write_page read back through report.Dump like browser records."""
        site = FakeSite(rows=5, size=2)
        tables = [{**t, "pageSize": 2} for t in PAGES["ads-report"]["rangeTables"] if t["key"] == "ai.product"]
        res = at.capture(PAGES["ads-report"], params("ads-report", tables=tables), COOKIES, LOGIN_ID, http=http(site))
        inq = at.capture(PAGES["inquiries"], params("inquiries"), COOKIES, LOGIN_ID, http=http(FakeSite()))
        with tempfile.TemporaryDirectory() as d:
            raw.write_page(res, d)
            raw.write_page(inq, d)
            dump = report.Dump([d], "2026-07-01", "2026-07-31")
            self.assertEqual(len(dump.ad_tables["component_ggs_whole_site_offline_product"]), 5)
            self.assertEqual(len(dump.inquiries_all), 25)
            self.assertEqual(sorted({report.buyer_level(i) for i in dump.inquiries_all}), ["L0", "L1", "L2", "L3"])
            self.assertEqual(raw.scan([os.path.join(d, "raw", "ads-report.jsonl"), os.path.join(d, "raw", "inquiries.jsonl")]), [])

    def test_paged_page_load_call_and_extra_stop_on_empty_page(self):
        res = at.capture(PAGES["ads-campaigns"], params("ads-campaigns"), COOKIES, LOGIN_ID, http=http(FakeSite()))
        pages = [r for r in res["records"] if r["url"].endswith("allList")]
        self.assertEqual(len(pages), 1)  # 1 row < page size 10: done
        with tempfile.TemporaryDirectory() as d:
            raw.write_page(res, d)
            self.assertEqual(list(report.Dump([d]).campaigns), ["901"])
        x = {"tag": "product_stats.month", "url": "https://mydata.alibaba.com/self/.json", "params": {"iName": "x"}, "pageParam": "pageNO", "maxPages": 20}
        res = at.capture(PAGES["analytics-product"], params("analytics-product", extra=[x]), COOKIES, LOGIN_ID, http=http(FakeSite()))
        self.assertEqual([r["replay"]["page"] for r in res["records"]], [1, 2, 3])

    def test_login_page_is_session_expired_and_punish_is_reported(self):
        res = at.capture(PAGES["ads-report"], params("ads-report"), COOKIES, LOGIN_ID, http=http(FakeSite(login_after=1)))
        self.assertEqual(res["status"], "session_expired")
        self.assertTrue(raw.login_expired(res))
        res = at.capture(PAGES["ads-report"], params("ads-report"), COOKIES, LOGIN_ID, http=http(FakeSite(punish=True)))
        self.assertEqual(res["status"], "api_blocked")
        self.assertIn("component/data", res["detail"])

    def test_page_without_catalog_entry_and_wrong_seller(self):
        self.assertEqual(at.capture(PAGES["orders"], params("orders"), COOKIES, LOGIN_ID)["status"], "no_api")
        self.assertEqual(at.capture(PAGES["ads-report"], params("ads-report"), COOKIES, "someone-else")["status"], "wrong_account")


if __name__ == "__main__":
    unittest.main()
