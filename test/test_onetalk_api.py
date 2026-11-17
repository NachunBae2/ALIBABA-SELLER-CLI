# 테스트 전용 파일 — CLI 실행에 쓰이지 않음. 안의 ID·금액은 동작 확인용 예시 값이며 실제 셀러·고객 데이터가 아님.
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("onetalk_api", Path(__file__).resolve().parent.parent / "engine" / "onetalk_api.py")
ot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ot)

SELF, BUYER, ACCOUNT = "4000000000009", "4000000000001", "5000000001"
CONV = {"contactAliId": BUYER, "selfAliId": SELF, "contactAccountId": ACCOUNT, "chatToken": "tok-fixture", "chatType": 1, "unreadCount": 0,
        "latestMessage": {"messageType": 1, "messageSendType": "rec", "sendTime": 1789000000000}}
INQ = {"id": 1000000000077, "tradeId": 7000000001, "secTradeId": "sec-fixture-1", "hasUnread": True, "sender": {"accountId": int(ACCOUNT), "name": "Fixture Buyer"}}


class Resp:
    def __init__(self, body, status=200, headers=None):
        self.status_code, self.headers = status, headers or {}
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """Answers the OneTalk, message-center and mtop endpoints from in-memory state; records every call."""

    def __init__(self, inquiry_unread=True, echo=True):
        self.calls, self.sent, self.inq_unread, self.echo = [], [], inquiry_unread, echo
        self.history = [{"messageId": "m1", "messageType": 1, "messageSendType": "rec", "content": "Hi, mail me at a.b@example.com or +1 555 010 0199"}]
        self.cookies = {"_m_h5_tk": "h5fixture_1789000000000"}

    def post(self, url, data=None, params=None, headers=None, allow_redirects=True, multipart=None):
        self.calls.append(("POST", url, data, params))
        path = url.split(".com", 1)[1] if ".com" in url else url
        if path == "/csrf/getToken.htm":
            return Resp({"token": "csrf-fixture"})
        if path == "/message/ajax/feedback/subjectList.htm":
            row = dict(INQ, hasUnread=self.inq_unread, subject='He said "hi" &amp; left')
            return Resp(json.dumps({"code": 200, "data": {"list": [[row]], "pagination": {"totalCount": 1}}}).replace('\\"hi\\"', '&quot;hi&quot;'))
        p = json.loads(data["params"]) if data and "params" in data else {}
        if path == "/message/ajax/feedback/markRead.htm":
            self.marked = p
            self.inq_unread = p["readStatus"] == "unread"
            return Resp('{&quot;code&quot;:200,&quot;data&quot;:&quot;ALL_SUCCESS&quot;}')
        if path == "/message/pageListRecentConversation.htm":
            return Resp({"code": 200, "data": {"dataList": [dict(CONV, contactAliId="4000000000002", contactAccountId="5000000002"), CONV], "hasMore": False}})
        if path == "/message/listRecentMessage.htm":
            return Resp({"code": 200, "data": {"messageList": list(self.history)}})
        if path == "/message/sendMessage.htm":
            self.sent.append(p)
            if self.echo:
                self.history.insert(0, {"messageId": "m%d" % (len(self.history) + 1), "messageType": p["messageType"], "messageSendType": "send",
                                        "content": p["content"] if isinstance(p["content"], str) else json.dumps(p["content"])})
            return Resp({"code": 200, "data": {"messageID": "new"}})
        if "/h5/" in url:
            api = params["api"]
            if api.endswith("readstatus.set"):
                self.inq_unread = False
            return Resp({"ret": ["SUCCESS::ok"], "data": {"api": api}})
        raise AssertionError("unexpected POST " + url)

    def get(self, url, params=None, headers=None, allow_redirects=True):
        self.calls.append(("GET", url, None, params))
        if url.endswith("/message/default.htm"):
            return Resp("<script>var cfg = { csrfTokenName: '_csrf_token_', csrfTokenValue: '00000000-0000-4000-8000-000000000000' };</script>")
        if "/h5/" in url:
            if params["api"].endswith("readstatus.set"):
                self.inq_unread = False
            return Resp({"ret": ["SUCCESS::ok"], "data": {}})
        raise AssertionError("unexpected GET " + url)


def api(**kw):
    return ot.OneTalk(FakeSession(**kw), clock=lambda: 1789000000000)


class OneTalkApiTest(unittest.TestCase):
    def setUp(self):
        p = patch.object(ot.time, "sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)
    def test_h5_sign_is_md5_of_token_time_appkey_data(self):
        want = hashlib.md5(("tk&1&%s&{}" % ot.APP_KEY).encode()).hexdigest()
        self.assertEqual(ot.h5_sign("tk", "1", "{}"), want)

    def test_send_card_by_buyer_ali_id_posts_type_57_and_reads_it_back(self):
        a = api()
        out = ot.run("send", {"buyerAliId": BUYER, "kind": "card"}, a)
        self.assertEqual(out["status"], "ok")
        body = a.s.sent[0]
        self.assertEqual((body["messageType"], body["content"]["cardType"]), (57, 1))
        self.assertEqual(body["receiverAliId"], [int(BUYER), int(SELF)])
        self.assertEqual(body["chatToken"], "tok-fixture")
        self.assertEqual(out["readback"], {"messageType": 57, "seen": True})

    def test_product_card_and_text_are_confirmed_by_their_own_message_in_the_readback(self):
        a = api()
        self.assertEqual(ot.run("send", {"buyerAliId": BUYER, "kind": "product", "productId": "900000001"}, a)["readback"]["productSeen"], True)
        out = ot.run("send", {"buyerAliId": BUYER, "kind": "text", "text": "Thanks for your inquiry."}, a)
        self.assertEqual((out["status"], out["readback"]["textSeen"]), ("ok", True))
        self.assertNotIn("Thanks", json.dumps(out))  # the result never carries the text

    def test_a_send_the_conversation_does_not_show_is_sent_not_confirmed(self):
        with patch.object(ot.time, "sleep", lambda s: None):
            out = ot.send_and_confirm(api(echo=False), CONV, "card", {}, sleep=lambda s: None)
        self.assertEqual(out["status"], "sent_not_confirmed")

    def test_inquiry_id_resolves_the_conversation_through_the_sender_account_id(self):
        a = api()
        out = ot.run("messages", {"inquiryId": "1000000000077", "count": 5}, a)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["conversation"]["buyerAliId"], BUYER)
        self.assertNotIn("text", out["messages"][0])
        with_text = ot.run("messages", {"inquiryId": "1000000000077", "withText": True}, a)["messages"][0]["text"]
        self.assertNotIn("example.com", with_text)
        self.assertNotIn("555", with_text)
        self.assertIn("[email]", with_text)

    def test_unknown_inquiry_and_unknown_buyer(self):
        self.assertEqual(ot.run("send", {"inquiryId": "42", "kind": "card"}, api())["status"], "inquiry_not_found")
        self.assertEqual(ot.run("send", {"buyerAliId": "1", "kind": "card"}, api())["status"], "conversation_not_found")

    def test_mark_read_and_unread_flip_the_inquiry_and_read_the_row_back(self):
        a = api()
        out = ot.run("mark-read", {"inquiryId": "1000000000077"}, a)
        self.assertEqual(out, {"status": "ok", "readback": {"hasUnreadBefore": True, "hasUnreadAfter": False}})
        self.assertEqual(a.s.marked, {"readStatus": "read", "data": [{"secTradeId": "sec-fixture-1"}], "listMode": "REC_AGGREGATED"})
        out = ot.run("mark-unread", {"tradeId": "7000000001"}, a)
        self.assertEqual(out, {"status": "ok", "readback": {"hasUnreadBefore": False, "hasUnreadAfter": True}})
        with self.assertRaises(ot.ApiError):
            ot.run("mark-read", {"buyerAliId": BUYER}, a)

    def test_conversation_list_shows_no_names_or_tokens(self):
        out = ot.run("conversations", {"count": 2}, api())
        self.assertEqual(out["count"], 2)
        self.assertNotIn("tok-fixture", json.dumps(out))
        self.assertEqual(out["conversations"][1]["buyerAliId"], BUYER)

    def test_loose_json_repairs_entity_decoded_quotes(self):
        self.assertEqual(ot.loose_json('{"a":"He said &quot;hi&quot;"}'), {"a": 'He said "hi"'})

    def test_file_message_card_and_image(self):
        info = {"fileCardUrl": "https://x/card?ctime=1&version=2&picWidth=10&picHeight=20", "materialType": "pdf", "md5": "d" * 32, "nodeName": "catalog.pdf",
                "nodeSize": 1000, "id": 5, "parentId": 6, "redirectFileUrl": "https://x/f"}
        t, c = ot.file_message(info, "application/pdf")
        self.assertEqual((t, c["cardType"], c["params"]["name"], c["params"]["ctime"]), (53, 12, "catalog.pdf", "1"))
        t, c = ot.file_message(dict(info, materialType="jpg"), "image/jpeg")
        self.assertEqual((t, c["width"], c["height"]), (60, 10, 20))

    def test_redirect_to_login_is_exit_code_3(self):
        a = ot.OneTalk(FakeSession())
        a.s.post = lambda *x, **k: Resp("", 302, {"location": "https://login.alibaba.com/?x=1"})
        with self.assertRaises(ot.ApiError) as e:
            a.csrf()
        self.assertEqual(e.exception.code, 3)

    def test_main_prints_one_json_line_and_never_the_cookie(self):
        out = io.StringIO()
        with patch.object(ot, "load_cookies", lambda acc, cfg: [{"name": "_m_h5_tk", "value": "SECRETCOOKIE_1"}]), \
             patch.object(ot, "session_from_cookies", lambda c: FakeSession()), \
             patch.object(sys, "stdin", io.StringIO(json.dumps({"count": 1}))), patch.object(sys, "stdout", out):
            code = ot.main(["conversations", "--account", "fixture"])
        self.assertEqual(code, 0)
        self.assertEqual(len(out.getvalue().strip().split("\n")), 1)
        self.assertNotIn("SECRETCOOKIE", out.getvalue())


if __name__ == "__main__":
    unittest.main()
