# 테스트 전용 파일 — CLI 실행에 쓰이지 않음. 안의 ID·금액·클릭 수는 동작 확인용 예시 값이며 실제 셀러·고객 데이터가 아님.
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("seller_raw", Path(__file__).resolve().parent.parent / "engine" / "seller_central_raw.py")
raw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(raw)


def record(page, size, count, total):
  return {
    "replay": {"kind": "range", "page": page},
    "body": urlencode({"data": json.dumps({"tableName": "products", "filters": [], "page": {"size": size}})}),
    "json": {"data": {"queryResult": {"detailList": [{} for _ in range(count)], "totalRows": total}}},
  }


class CollectorSafety(unittest.TestCase):
  def test_resume_retains_progress_and_completes_last_page(self):
    first = record(1, 3, 3, 7)
    self.assertEqual(raw.range_resume([first])["products[]"], {"nextPage": 2, "covered": 3, "complete": False})
    state = raw.range_resume([first, record(2, 3, 3, 7), record(3, 3, 1, 7)])
    self.assertEqual(state["products[]"], {"nextPage": 4, "covered": 7, "complete": True})

  def test_empty_response_before_total_does_not_skip_page(self):
    state = raw.range_resume([record(1, 3, 3, 7), record(2, 3, 0, 7)])
    self.assertEqual(state["products[]"]["nextPage"], 2)
    self.assertFalse(state["products[]"]["complete"])

  def test_empty_table_is_complete_and_nonrange_is_ignored(self):
    self.assertTrue(raw.range_resume([record(1, 500, 0, 0)])["products[]"]["complete"])
    self.assertEqual(raw.range_resume([{"replay": {"kind": "page"}}]), {})

  def test_incomplete_ranges_lists_short_and_never_reached_tables(self):
    pg = {"rangeTables": [{"key": "a", "table": "products", "filters": []}, {"key": "b", "table": "keywords", "filters": []}]}
    a = type("A", (), {"tables": ""})()
    state = raw.range_state([{**record(1, 3, 3, 7), "text": None}])
    self.assertEqual(raw.incomplete_ranges(state, pg, a), {"products[]", "keywords[]"})
    text_rec = dict(record(1, 3, 3, 3)); text_rec["text"] = json.dumps(text_rec.pop("json"))
    self.assertEqual(raw.incomplete_ranges(raw.range_state([text_rec]), pg, a), {"keywords[]"})

  def test_profile_validation_rejects_blank_identity_and_invalid_profile(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "accounts.json"
      for account in [{"loginId": ""}, {"loginId": "fixture", "asideAccount": "../other"}]:
        path.write_text(json.dumps({"fixture": account}))
        with self.assertRaises(SystemExit):
          raw.load_account(path, "fixture")
      path.write_text(json.dumps({"fixture": {"loginId": "fixture", "asideAccount": "u1"}}))
      self.assertEqual(raw.load_account(path, "fixture")["asideAccount"], "u1")

  def test_login_secret_uses_stdin_not_process_arguments(self):
    password = "fixture-password-not-real"
    browser = subprocess.CompletedProcess([], 0, "STATUS signed_in\n", "")
    account = {"loginId": "fixture", "asideAccount": "u1", "keychainService": "fixture", "keychainAccount": "fixture"}
    with patch.object(raw.secret_store, "get_secret", return_value=password), \
         patch.object(raw.subprocess, "run", side_effect=[browser]) as run:
      self.assertEqual(raw.login(account), "signed_in")
      call = run.call_args_list[0]
      self.assertNotIn(password, " ".join(call.args[0]))
      self.assertIn(password, call.kwargs["input"])
      self.assertIn("u1", call.args[0])
      self.assertNotIn("--file", call.args[0])


import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
import inquiry_attribution  # noqa: E402


class Redact(unittest.TestCase):
    def test_query_and_form_tokens(self):
        x = "a?page=1&_csrf_token_=9a77-dd&ctoken=abc&_tb_token_=q1&lang=en"
        y = raw.REDACT.sub(r"\1***", x)
        self.assertNotIn("9a77", y)
        self.assertNotIn("abc", y)
        self.assertEqual([r for r in raw.SECRET_RES if r.search(y)], [])
        self.assertTrue(any(r.search(x) for r in raw.SECRET_RES))

    def test_nested_json_stays_valid(self):
        line = json.dumps({"u": json.dumps({"p": "ctoken=abc"}), "b": json.dumps({"_csrf_token": "qq"})})
        y = raw.REDACT_JSON.sub(r"\1***", raw.REDACT.sub(r"\1***", line))
        json.loads(y)
        self.assertNotIn("abc", y)
        self.assertNotIn("qq", y)


class Attribution(unittest.TestCase):
    def test_product_link_kept_tm_unlinked(self):
        d = tempfile.mkdtemp()
        raw = os.path.join(d, "raw")
        os.makedirs(raw)
        # 2026-07-01 12:00 US Pacific = 19:00 UTC
        t = 1782932400000
        with open(os.path.join(raw, "inquiries.jsonl"), "w") as fh:
            fh.write(json.dumps({"replay": {"kind": "inquiry"}, "json": {"list": [
                {"id": 1, "createTime": t, "feedbackType": 1, "targetIds": [100], "source": "main_app_detail_sendInquiry", "appFrom": "Android"},
                {"id": 2, "createTime": t, "feedbackType": 6, "targetIds": [555], "source": "CONTACT_MKT_IMOpportunity", "appFrom": "web_weblite"},
            ]}}) + "\n")
        with open(os.path.join(raw, "ads-report.jsonl"), "w") as fh:
            fh.write(json.dumps({"replay": {"kind": "range", "table": "component_ggs_whole_site_offline_product"}, "json": {"data": {"queryResult": {"detailList": [
                {"productId": {"main": "200"}, "timeUs": {"main": "2026-07-01"}, "mcCnt": {"main": "0"}, "atmSessionCnt": {"main": "1"}, "clickCnt": {"main": "3"}, "impsCnt": {"main": "40"}},
            ]}}}}) + "\n")
        self.assertEqual(inquiry_attribution.main([d]), 0)
        rows = {r["inquiry_id"]: r for r in csv_rows(os.path.join(d, "analysis", "lead_sources.csv"))}
        self.assertEqual(rows["1"]["lead_type"], "상품 문의")
        self.assertEqual(rows["1"]["product_id"], "100")
        self.assertEqual(rows["1"]["device"], "앱")
        self.assertEqual(rows["2"]["lead_type"], "TM/IM 대화")
        self.assertNotIn("tm_ad_day_candidates", rows["2"])
        self.assertEqual(rows["2"]["ad"], "광고 연결 기록 없음")
        pairs = csv_rows(os.path.join(d, "analysis", "product_inquiries_ad_match.csv"))
        self.assertEqual([r["product_id"] for r in pairs], ["100"])
        inflow = {r["inquiry_id"]: r["inflow"] for r in csv_rows(os.path.join(d, "analysis", "inquiry_by_product.csv"))}
        self.assertEqual(inflow, {"1": "100", "2": "TM 유입"})


def csv_rows(path):
    import csv
    with open(path) as fh:
        return list(csv.DictReader(fh))



if __name__ == "__main__":
  unittest.main()
