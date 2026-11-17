# 테스트 전용 파일 — CLI 실행에 쓰이지 않음. 안의 ID·금액·클릭 수는 동작 확인용 예시 값이며 실제 셀러·고객 데이터가 아님.
import io
import json
import os
import subprocess
import sys
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
import accio_gateway as ag  # noqa: E402
import secret_store  # noqa: E402

PW_OLD, PW_NEW = "old-fixture-password", "new-fixture-password"


class FakeStore:
    def __init__(self):
        self.v = {}

    def set_secret(self, s, a, v):
        self.v[(s, a)] = v
        return "fake"

    def get_secret(self, s, a):
        return self.v.get((s, a))


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def gateway(app_pw, store, handler):
    files = lambda: [("/x/accounts/123", {"url": "http://localhost:4097", "username": "phoenix", "password": app_pw[0], "createdAt": "z"})]
    seen = []

    def opener(req, timeout=0):
        seen.append(req)
        return handler(req)
    return ag.Gateway(opener=opener, files=files, store=store), seen


def tools_body():
    return Resp(json.dumps({"success": True, "data": {"tools": [
        {"name": "query_ggs_shop_summary", "meta": {"toolkit": "alibaba-ggs"}},
        {"name": "ggs_new_publish_product", "meta": {"toolkit": "alibaba-ggs"}},
        {"name": "shopee_x", "meta": {"toolkit": "shopee"}}], "status": {"managerState": "ready"}}}).encode())


def run(argv, g):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err), patch.object(ag, "connectors", return_value=[{"label": "A", "connect_id": "alibaba:1"}]):
        code = ag.main(argv, gateway=g)
    return code, out.getvalue(), err.getvalue()


class Accio(unittest.TestCase):
    def test_login_copies_app_password_to_store_and_never_prints_it(self):
        store, app = FakeStore(), [PW_OLD]
        g, seen = gateway(app, store, lambda req: tools_body())
        code, out, err = run(["login"], g)
        self.assertEqual(code, 0)
        self.assertEqual(store.v[("aliops-accio-gateway", "phoenix")], PW_OLD)
        self.assertNotIn(PW_OLD, out + err)
        self.assertEqual(json.loads(out)["seller_tools"], 2)

    def test_401_rereads_app_password_once_then_retries(self):
        store, app = FakeStore(), [PW_OLD]
        store.set_secret("aliops-accio-gateway", "phoenix", PW_OLD)
        app[0] = PW_NEW  # app restarted

        def handler(req):
            if req.get_header("Authorization").endswith(__import__("base64").b64encode(b"phoenix:" + PW_OLD.encode()).decode()):
                raise urllib.error.HTTPError(req.full_url, 401, "x", {}, None)
            return tools_body()
        g, seen = gateway(app, store, handler)
        code, out, err = run(["status"], g)
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 2)
        self.assertEqual(store.v[("aliops-accio-gateway", "phoenix")], PW_NEW)
        self.assertNotIn(PW_NEW, out + err)

    def test_write_tools_refused_before_any_request(self):
        store = FakeStore()
        store.set_secret("aliops-accio-gateway", "phoenix", PW_OLD)
        g, seen = gateway([PW_OLD], store, lambda req: self.fail("no request"))
        for name in ("ggs_new_publish_product", "save_or_update_ggs_auto_quote_setting", "icbu_ads_showcase_mutate", "unknown_tool_x"):
            code, out, err = run(["call", name], g)
            self.assertEqual(code, 65, name)
        self.assertEqual(seen, [])

    def test_shortcut_call_unwraps_content_and_sends_connect_id(self):
        store = FakeStore()
        store.set_secret("aliops-accio-gateway", "phoenix", PW_OLD)
        body = {"success": True, "data": {"content": [{"type": "text", "text": json.dumps({"success": True, "data": {"pv": 5}})}]}}
        g, seen = gateway([PW_OLD], store, lambda req: Resp(json.dumps(body).encode()))
        code, out, err = run(["shop", "summary", "--arg", "statisticsType=day"], g)
        self.assertEqual(code, 0, err)
        sent = json.loads(seen[-1].data)
        self.assertEqual(sent, {"name": "query_ggs_shop_summary", "arguments": {"statisticsType": "day"}, "trace": {"connectId": "alibaba:1"}})
        self.assertEqual(json.loads(out)["data"]["pv"], 5)

    def test_missing_required_arg_explained_before_any_call_and_rfq_paging_defaulted(self):
        store = FakeStore()
        store.set_secret("aliops-accio-gateway", "phoenix", PW_OLD)
        tools = {"success": True, "data": {"tools": [
            {"name": "global_hot_selling_products", "inputSchema": {"required": ["query"], "properties": {"query": {"description": "keywords"}}}},
            {"name": "rfq-quote-history-search", "inputSchema": {"required": ["pageSize", "currentPage"], "properties": {}}}]}}
        calls = []

        def handler(req):
            if req.data:
                calls.append(json.loads(req.data))
                return Resp(json.dumps({"success": True, "data": {"content": [{"type": "text", "text": "{}"}]}}).encode())
            return Resp(json.dumps(tools).encode())
        g, seen = gateway([PW_OLD], store, handler)
        feats = {"global_hot_selling_products": {"rw": "읽기"}, "rfq-quote-history-search": {"rw": "읽기"}}
        with patch.object(ag, "load_features", return_value=feats):
            code, out, err = run(["market", "hot-products"], g)
            self.assertEqual(code, 64)
            self.assertIn("--arg query=<값>", err)
            self.assertEqual(calls, [])
            code, out, err = run(["rfq", "history"], g)
        self.assertEqual(code, 0, err)
        self.assertEqual(calls[0]["arguments"], {"pageSize": 20, "currentPage": 1})

    def test_buyer_data_tools_not_written_to_file(self):
        store = FakeStore()
        store.set_secret("aliops-accio-gateway", "phoenix", PW_OLD)
        g, seen = gateway([PW_OLD], store, lambda req: self.fail("no request"))
        code, out, err = run(["conversations", "--out", "x.json"], g)
        self.assertEqual(code, 65)

    def test_features_table_lists_seller_tools_with_results(self):
        code, out, err = run(["features", "--tested", "됨", "--format", "json"], None)
        rows = json.loads(out)
        self.assertGreater(len(rows), 50)
        self.assertTrue(all(r["toolkit"].startswith("alibaba") for r in rows))
        self.assertIn("alibaba-accio shop summary", [r["command"] for r in rows])


class SecretStore(unittest.TestCase):
    def test_mac_value_goes_on_stdin_hex_not_argv(self):
        with patch.object(secret_store.sys, "platform", "darwin"), \
             patch.object(secret_store.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as r:
            secret_store.set_secret("svc", "acct", "s3cret value")
        argv, kw = r.call_args.args[0], r.call_args.kwargs
        self.assertEqual(argv, ["security", "-i"])
        self.assertNotIn("s3cret", kw["input"])
        self.assertIn("s3cret value".encode().hex(), kw["input"])

    def test_windows_dpapi_value_on_stdin_path_in_env(self):
        with patch.object(secret_store.sys, "platform", "win32"), patch.object(secret_store.os, "name", "nt"), \
             patch.object(secret_store.os, "makedirs"), \
             patch.object(secret_store.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as r:
            self.assertEqual(secret_store.set_secret("svc", "acct", "s3cret"), "dpapi")
        argv, kw = r.call_args.args[0], r.call_args.kwargs
        self.assertEqual(argv[0], "powershell")
        self.assertNotIn("s3cret", " ".join(argv))
        self.assertEqual(kw["input"], "s3cret\n")
        self.assertTrue(kw["env"]["ASC_SECRET_PATH"].endswith("svc@acct.dpapi"))

    def test_bad_names_refused(self):
        with self.assertRaises(secret_store.StoreError):
            secret_store.get_secret("svc; rm", "a")


class ReadDefaults(unittest.TestCase):
    def test_model_list_default_and_override(self):
        class G:
            account_dir='/fake'
            calls=[]
            def tools(self):
                return ([{'name':'icbu_product_3d_model_list','inputSchema':{'required':['request'],'properties':{'request':{'type':'object'}}}}],{})
            def call(self,name,args,connector):
                self.calls.append(args)
                return {'items':[]}
        for argv,expected in [([],{'currentPage':1,'pageSize':10}),(['--args','{"request":{"currentPage":2,"pageSize":5}}'],{'currentPage':2,'pageSize':5})]:
            g=G()
            code,out,err=run(['call','icbu_product_3d_model_list',*argv],g)
            self.assertEqual(code,0,err)
            self.assertEqual(g.calls[-1],{'request':expected})

    def test_verified_auth_refusal_visible_in_features(self):
        code,out,err=run(['features','--search','query_user_category','--format','json'],None)
        self.assertEqual(code,0)
        self.assertIn('-32003',json.loads(out)[0]['verified_result'])


if __name__ == "__main__":
    unittest.main()
