"""OneTalk (seller messenger) internal HTTP API, called directly with the account's saved login cookies.

No browser and no clicks: the same endpoints the OneTalk web page calls.

  onetalk.alibaba.com   POST form {params: <json>, _csrf: <token from /csrf/getToken.htm>}
    /message/pageListRecentConversation.htm  {count, limitTimeStamp}            conversation list (cursor)
    /message/listRecentMessage.htm           {contactAliId, pointTimeStamp, forward:false, count}
    /message/sendMessage.htm                 {messageType, content, chatType, extParam, chatToken,
                                              receiverAliId:[buyer, self]}       one message
  acs.h.alibaba.com     mtop h5 (sign = md5(_m_h5_tk token & t & appKey & data))
    mtop.alibaba.interaction.clouddisk.prepareSendFileWithGroup   upload policy for one file
    mtop.alibaba.interaction.clouddisk.buildFileRelationWithGroup file card for the uploaded file

Message types (messageType): 1 text, 57 name card (cardType 1), 2000 product card (cardType 2000),
53 file card (cardType 12), 60 image.

TLS: the send endpoints answer a non-browser TLS handshake (Node fetch, curl) with a 302 to
/help/maintenance.html. curl_cffi with impersonate="chrome" gets the real answer, so it is required
(`pip install curl_cffi`).

Cookies come from cookie_store.load_cookies (the account's saved login; `alibaba-data login`).
Cookie values, csrf and h5 tokens stay in this process: never printed, logged or written.

Pipe CLI (one JSON object on stdin, one JSON line on stdout):
  python3 onetalk_api.py <op> --account A [--config accounts.json] < request.json
  ops: conversations | messages | send | mark-read | mark-unread
  target: {"buyerAliId"} (OneTalk contact id) or {"inquiryId"} / {"tradeId"} (looked up in the inquiry list)

  message.alibaba.com   POST form {_csrf_token_: <token from /message/default.htm>, params: <json>}
    /message/ajax/feedback/subjectList.htm   inquiry list page: id, secTradeId, hasUnread, sender ids
    /message/ajax/feedback/markRead.htm      {readStatus: read|unread, data:[{secTradeId}], listMode}
"""
import hashlib
import html
import json
import mimetypes
import os
import re
import sys
import time
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OT = "https://onetalk.alibaba.com"
MC = "https://message.alibaba.com"
ACS = "https://acs.h.alibaba.com/h5"
APP_KEY = "24889839"
HEADERS = {"origin": OT, "referer": OT + "/message/weblitePWA.htm"}
TYPES = {"text": 1, "card": 57, "product": 2000, "file": 53, "image": 60}


class ApiError(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


def now_ms():
    return int(time.time() * 1000)


def h5_sign(token, t, data):
    return hashlib.md5(("%s&%s&%s&%s" % (token, t, APP_KEY, data)).encode("utf-8")).hexdigest()


def loose_json(text):
    """The inquiry list answers HTML-entity encoded JSON whose decoded strings can hold bare quotes:
    escape the quote just before each parse error until it parses (same repair as capture.js)."""
    v = html.unescape(text or "")
    for _ in range(5000):
        try:
            return json.loads(v)
        except json.JSONDecodeError as e:
            q = v.rfind('"', 0, e.pos)
            if q <= 0:
                break
            v = v[:q] + "\\" + v[q:]
    raise ApiError("문의 목록 응답을 읽지 못함")


def session_from_cookies(cookies, factory=None):
    if factory is None:
        try:
            from curl_cffi import requests as cr
        except ImportError:
            raise ApiError("curl_cffi 가 필요하다(OneTalk 발송 API 는 브라우저 TLS 만 받음): pip install curl_cffi", 69)
        factory = lambda: cr.Session(impersonate="chrome")  # noqa: E731
    s = factory()
    for c in cookies:
        s.cookies.set(c["name"], c["value"], domain=c.get("domain") or ".alibaba.com", path=c.get("path") or "/")
    return s


class OneTalk:
    def __init__(self, session, clock=now_ms):
        self.s = session
        self.clock = clock
        self._csrf = None
        self._mc_csrf = None

    # -- onetalk.alibaba.com -----------------------------------------------------------
    def csrf(self):
        if not self._csrf:
            r = self.s.post(OT + "/csrf/getToken.htm", headers=HEADERS)
            tok = self._json(r).get("token")
            if not tok:
                raise ApiError("OneTalk csrf 없음: 로그인 쿠키가 만료됐을 수 있다(alibaba-data login)", 3)
            self._csrf = tok
        return self._csrf

    def call(self, path, params):
        r = self.s.post(OT + path, data={"params": json.dumps(params, separators=(",", ":"), ensure_ascii=False), "_csrf": self.csrf()},
                        headers=HEADERS, allow_redirects=False)
        j = self._json(r)
        if str(j.get("code")) != "200":
            raise ApiError("%s: %s %s" % (path, j.get("code"), j.get("message") or j.get("data") or ""))
        return j.get("data")

    @staticmethod
    def _json(r):
        if 300 <= r.status_code < 400:
            loc = (r.headers.get("location") or "").split("?")[0]
            if "login" in loc:
                raise ApiError("로그인 필요(쿠키 만료): alibaba-data login", 3)
            raise ApiError("OneTalk 이 %s 로 보냄(%s)" % (r.status_code, loc))
        try:
            return r.json()
        except ValueError:
            raise ApiError("OneTalk 응답이 JSON 아님(HTTP %s)" % r.status_code)

    def conversations(self, count=50, cursor=None):
        d = self.call("/message/pageListRecentConversation.htm", {"count": count, "limitTimeStamp": cursor or self.clock()})
        return d.get("dataList") or [], d.get("nextTimeStamp"), bool(d.get("hasMore"))

    def find(self, ids, key="contactAliId", max_pages=40):
        """First conversation whose `key` is one of `ids` (one id or a list)."""
        want = {str(i) for i in (ids if isinstance(ids, (list, tuple, set)) else [ids]) if i not in (None, "")}
        cursor = None
        for _ in range(max_pages):
            rows, cursor, more = self.conversations(50, cursor)
            for c in rows:
                if str(c.get(key)) in want:
                    return c
            if not more or not cursor:
                return None
        return None

    # -- message.alibaba.com (inquiry list) ----------------------------------------------
    def mc_csrf(self):
        if not self._mc_csrf:
            r = self.s.get(MC + "/message/default.htm", allow_redirects=False)
            if 300 <= r.status_code < 400:
                raise ApiError("로그인 필요(쿠키 만료): alibaba-data login", 3)
            m = re.search(r"csrfToken\w*\s*:\s*'([0-9a-fA-F-]{20,})'", r.text or "")
            if not m:
                raise ApiError("메시지센터 csrf 없음: 로그인 쿠키가 만료됐을 수 있다(alibaba-data login)", 3)
            self._mc_csrf = m.group(1)
        return self._mc_csrf

    def mc_post(self, path, params):
        r = self.s.post(MC + path, allow_redirects=False,
                        data={"_csrf_token_": self.mc_csrf(), "postId": str(self.clock()), "params": json.dumps(params, separators=(",", ":"))},
                        headers={"x-requested-with": "XMLHttpRequest", "origin": MC, "referer": MC + "/message/default.htm"})
        if 300 <= r.status_code < 400:
            raise ApiError("로그인 필요(쿠키 만료): alibaba-data login", 3)
        j = loose_json(r.text)
        if str(j.get("code")) != "200":
            raise ApiError("%s: %s %s" % (path, j.get("code"), j.get("message") or ""))
        return j.get("data")

    def mark_inquiry(self, sec_trade_id, status):
        """The message center's Mark as read / unread button."""
        return self.mc_post("/message/ajax/feedback/markRead.htm", {"readStatus": status, "data": [{"secTradeId": sec_trade_id}], "listMode": "REC_AGGREGATED"})

    def inquiries(self, page=1, size=20):
        """One page of the inquiry list (newest contact first). -> (items, totalCount)"""
        params = {"listType": "all", "pagination": {"nextPage": page, "pageSize": size}, "filter": {"isShowAtm": False},
                  "order": {"order": "desc", "orderBy": "latest_contact_time"}, "search": {}}
        d = self.mc_post("/message/ajax/feedback/subjectList.htm", params) or {}
        items = [x for g in d.get("list") or [] for x in (g if isinstance(g, list) else [g]) if isinstance(x, dict)]
        return items, (d.get("pagination") or {}).get("totalCount")

    def find_inquiry(self, inquiry_id=None, trade_id=None, max_pages=30):
        for page in range(1, max_pages + 1):
            items, total = self.inquiries(page)
            for it in items:
                ids = {str(it.get(k)) for k in ("id", "tradeId", "secTradeId", "feedbackId") if it.get(k) not in (None, "")}
                if (inquiry_id and str(inquiry_id) in ids) or (trade_id and str(trade_id) in {str(it.get("tradeId")), str(it.get("secTradeId"))}):
                    return it
            if not items or (total and page * 20 >= int(total)):
                return None
        return None

    def messages(self, conv, count=20, before=None):
        d = self.call("/message/listRecentMessage.htm", {"contactAliId": conv["contactAliId"], "pointTimeStamp": before or self.clock(), "forward": False, "count": count})
        return d.get("messageList") or []

    def send(self, conv, message_type, content):
        body = {"messageType": message_type, "content": content, "chatType": conv.get("chatType", 1), "extParam": {},
                "chatToken": conv["chatToken"], "receiverAliId": [int(conv["contactAliId"]), int(conv["selfAliId"])]}
        return self.call("/message/sendMessage.htm", body) or {}

    # -- mtop --------------------------------------------------------------------------
    def _h5_token(self):
        for c in self.s.cookies.jar if hasattr(self.s.cookies, "jar") else []:
            if c.name == "_m_h5_tk":
                return c.value.split("_")[0]
        v = self.s.cookies.get("_m_h5_tk") if hasattr(self.s.cookies, "get") else None
        return (v or "").split("_")[0]

    def mtop(self, api, data, v="1.0", post=False):
        d = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        for attempt in range(2):
            t = str(self.clock())
            q = {"jsv": "2.7.2", "appKey": APP_KEY, "t": t, "sign": h5_sign(self._h5_token(), t, d), "api": api, "v": v,
                 "type": "originaljson", "dataType": "json", "timeout": "20000"}
            url = "%s/%s/%s/" % (ACS, api.lower(), v)
            if post:
                r = self.s.post(url, params=q, data={"data": d}, headers=HEADERS)
            else:
                r = self.s.get(url, params=dict(q, data=d), headers=HEADERS)
            j = r.json()
            ret = ";".join(j.get("ret") or [])
            if ret.startswith("SUCCESS"):
                return j.get("data") or {}
            if attempt == 0 and "TOKEN" in ret:  # first call sets _m_h5_tk; sign again with it
                continue
            raise ApiError("%s: %s" % (api, ret))
        raise ApiError("%s: token" % api)

    # -- file --------------------------------------------------------------------------
    def upload(self, conv, path, mime_factory=None):
        with open(path, "rb") as fh:
            blob = fh.read()
        name = os.path.basename(path)
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        md5 = hashlib.md5(blob).hexdigest()
        scene = json.dumps({"sceneType": "1", "idType": "2", "from": str(conv["selfAliId"]), "to": str(conv["contactAliId"])}, separators=(",", ":"))
        prep = self.mtop("mtop.alibaba.interaction.clouddisk.prepareSendFileWithGroup",
                         {"appKey": "OneChat", "fileName": "%s.%s" % (md5, ext), "scene": scene, "isToken": False, "chatToken": conv["chatToken"]})
        res = prep.get("result") or {}
        if str(prep.get("code")) not in ("200", "None") and not res:
            raise ApiError("파일 업로드 준비 실패: %s %s" % (prep.get("code"), prep.get("msg")))
        limit = res.get("allowSendFileMaxSize")
        if limit and len(blob) > int(limit):
            raise ApiError("파일이 너무 큼: %d 바이트 > 한도 %s" % (len(blob), limit), 65)
        if not res.get("fileIsExist"):
            pol = res.get("policyDTO") or {}
            key = "%s%s.%s" % (res.get("uploadFileDir") or "", md5, ext)
            fields = {"key": key, "policy": pol.get("encodedPolicy"), "OSSAccessKeyId": pol.get("accessid"),
                      "success_action_status": "200", "callback": pol.get("callbackBody"), "signature": pol.get("signature")}
            mp = (mime_factory or _curl_mime)()
            for k, val in fields.items():
                mp.addpart(name=k, data=str(val or "").encode("utf-8"))
            mp.addpart(name="file", filename=name, content_type=mimetypes.guess_type(name)[0] or "application/octet-stream", data=blob)
            up = self.s.post(pol.get("host"), multipart=mp)
            # 203 + CallbackFailed: the bytes are stored, only OSS's callback to Alibaba failed. The web page
            # treats it as uploaded too and builds the file card next.
            if up.status_code != 200 and not (up.status_code == 203 and "CallbackFailed" in (up.text or "")):
                raise ApiError("파일 업로드 실패(HTTP %s)" % up.status_code)
        node = json.dumps({"nodeName": name, "nodeSize": len(blob), "materialType": ext, "md5": md5}, separators=(",", ":"))
        rel = self.mtop("mtop.alibaba.interaction.clouddisk.buildFileRelationWithGroup",
                        {"appKey": "OneChat", "scene": scene, "node": node, "params": json.dumps({"source": "", "needCardUrl": True}, separators=(",", ":")),
                         "chatToken": conv["chatToken"]})
        info = rel.get("result") or {}
        if not info.get("fileCardUrl"):
            raise ApiError("파일 카드 생성 실패: %s %s" % (rel.get("code"), rel.get("msg")))
        info.setdefault("nodeName", name)
        info.setdefault("nodeSize", len(blob))
        info.setdefault("materialType", ext)
        info.setdefault("md5", md5)
        return info, (mimetypes.guess_type(name)[0] or "")


def _curl_mime():
    from curl_cffi import CurlMime
    return CurlMime()


def file_message(info, mime):
    """(messageType, content) for an uploaded file: image bubble for images, file card otherwise."""
    q = {k: v[0] for k, v in parse_qs(urlparse(info.get("fileCardUrl") or "").query).items()}
    ext = str(info.get("materialType") or "")
    if mime.startswith("image/"):
        return TYPES["image"], {"height": int(info.get("height") or q.get("picHeight") or 0), "width": int(info.get("width") or q.get("picWidth") or 0),
                                "url": info.get("redirectFileUrl") or "", "isOriginal": 1, "size": int(info.get("nodeSize") or 0),
                                "md5": info.get("md5"), "fileId": info.get("fileId") or "%s.%s" % (info.get("md5"), ext), "suffix": ext}
    params = {"type": "12", "ctime": str(q.get("ctime") or ""), "version": str(q.get("version") or ""), "extensionType": ext,
              "id": str(info.get("id") or ""), "parentId": str(info.get("parentId") or ""), "md5": str(info.get("md5") or ""),
              "url": info.get("redirectFileUrl") or "", "name": info.get("nodeName") or "", "size": str(info.get("nodeSize") or ""),
              "thumbnailUrl": info.get("thumbnailUrl") or "", "downloadUrl": info.get("downloadUrl") or ""}
    return TYPES["file"], {"cardType": 12, "params": params}


def card_message():
    return TYPES["card"], {"cardType": 1, "params": {"type": "1", "showCertification": "true", "showCompanyName": "true", "showEmailAddress": "true"}}


def product_message(product_id, clock=now_ms):
    pid = str(product_id)
    return TYPES["product"], {"cardType": 2000, "params": {"type": "2000", "ids": pid, "ctime": str(clock())}}


EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")


def mask(text):
    """Text for the screen: emails and phone-like digit runs hidden."""
    return PHONE.sub("[phone]", EMAIL.sub("[email]", text or ""))


def brief(m, with_text=False):
    """A message: type, direction, time, id; the text only on request, masked and cut to 300 characters."""
    out = {"messageId": m.get("messageId"), "messageType": m.get("messageType"), "sendType": m.get("messageSendType"), "sendTime": m.get("sendTime"),
           "read": m.get("contactRead")}
    if with_text and m.get("messageType") == TYPES["text"] and isinstance(m.get("content"), str):
        out["text"] = mask(m["content"])[:300]
    return out


def conv_brief(c):
    """A conversation row without names or tokens: what the CLI may print. buyerAliId is the target
    key the send commands take (--buyer-ali-id)."""
    lm = c.get("latestMessage") or {}
    return {"buyerAliId": str(c.get("contactAliId") or ""), "unread": c.get("unreadCount"), "modifyTime": c.get("modifyTime"), "chatType": c.get("chatType"),
            "lastType": lm.get("messageType"), "lastSendType": lm.get("messageSendType"), "lastTime": lm.get("sendTime")}


def send_and_confirm(api, conv, kind, req, sleep=None):
    """Send one message, then read the conversation back. -> result dict (no text, no ids of the buyer)."""
    sleep = sleep or time.sleep
    before = api.messages(conv, 5)
    top0 = before[0].get("messageId") if before else None
    if kind == "text":
        text = req.get("text") or ""
        if not text.strip():
            raise ApiError("보낼 글이 비었다", 64)
        mtype, content = TYPES["text"], text
    elif kind == "card":
        mtype, content = card_message()
    elif kind == "product":
        if not req.get("productId"):
            raise ApiError("--product-id 필요", 64)
        mtype, content = product_message(req["productId"], api.clock)
    elif kind == "file":
        info, mime = api.upload(conv, req["path"])
        mtype, content = file_message(info, mime)
    else:
        raise ApiError("모르는 종류: %s" % kind, 64)
    sent = api.send(conv, mtype, content)
    found = None
    for _ in range(4):
        sleep(1.5)
        after = api.messages(conv, 5)
        ids = [m.get("messageId") for m in after]
        new = after[:ids.index(top0)] if top0 in ids else after  # newest first: everything above the old top
        found = next((m for m in new if m.get("messageType") == mtype and m.get("messageSendType") == "send"), None)
        if found:
            break
    rb = {"messageType": mtype, "seen": bool(found)}
    if found and kind == "text":
        rb["textSeen"] = text.strip()[:60] in (found.get("content") or "")
    if found and kind == "product":
        rb["productSeen"] = str(req["productId"]) in json.dumps(found)
    return {"status": "ok" if found and rb.get("textSeen", True) and rb.get("productSeen", True) else "sent_not_confirmed",
            "sentMessage": bool(sent.get("messageID")), "readback": rb, "message": brief(found) if found else None}


def load_cookies(account, config):
    try:
        import cookie_store
        import seller_central_raw
    except ImportError as e:
        raise ApiError("저장 로그인 쿠키 모듈 없음(%s): alibaba-data login 이 있는 버전이 필요하다" % e.name, 69)
    acc = seller_central_raw.load_account(config, account)
    cookies = cookie_store.load_cookies(acc)
    if not cookies:
        raise ApiError("저장된 로그인 쿠키 없음: alibaba-data login --account %s" % account, 3)
    return cookies


SENDER_IDS = ("accountId", "alitalkId", "vaccountId")


def resolve(api, req):
    """-> (conversation | None, inquiry | None). Target = buyerAliId (OneTalk contact id) or
    inquiryId / tradeId (inquiry list row; its sender's account id is the conversation's contactAccountId)."""
    inq = None
    if req.get("inquiryId") or req.get("tradeId"):
        inq = api.find_inquiry(req.get("inquiryId"), req.get("tradeId"), int(req.get("maxPages") or 30))
        if not inq:
            return None, None
    if req.get("buyerAliId"):
        return api.find(req["buyerAliId"]), inq
    if inq:
        snd = inq.get("sender") or {}
        return api.find([snd.get(k) for k in SENDER_IDS], key="contactAccountId"), inq
    raise ApiError("대화 지정: --inquiry-id N | --trade-id N | --buyer-ali-id N", 64)


def mark_read(api, inq, status="read"):
    """Mark the inquiry read or unread (message center button), then read its list row back."""
    if not inq or not inq.get("secTradeId"):
        raise ApiError("읽음 처리는 문의 지정이 필요하다: --inquiry-id N | --trade-id N", 64)
    before = bool(inq.get("hasUnread"))
    api.mark_inquiry(inq["secTradeId"], status)
    after = api.find_inquiry(inquiry_id=inq.get("id"))
    unread_after = bool(after and after.get("hasUnread"))
    ok = after is not None and unread_after == (status == "unread")
    return {"status": "ok" if ok else "sent_not_confirmed", "readback": {"hasUnreadBefore": before, "hasUnreadAfter": unread_after}}


def run(op, req, api):
    if op == "conversations":
        rows, cursor, more = api.conversations(int(req.get("count") or 50), req.get("cursor"))
        return {"status": "ok", "count": len(rows), "hasMore": more, "nextCursor": cursor, "conversations": [conv_brief(c) for c in rows]}
    if op not in ("messages", "send", "mark-read", "mark-unread"):
        raise ApiError("모르는 명령: %s" % op, 64)
    if op.startswith("mark-") and not (req.get("inquiryId") or req.get("tradeId")):
        raise ApiError("읽음 처리는 문의 지정이 필요하다: --inquiry-id N | --trade-id N", 64)
    conv, inq = resolve(api, req)
    if (req.get("inquiryId") or req.get("tradeId")) and not inq:
        return {"status": "inquiry_not_found"}
    if op in ("mark-read", "mark-unread"):
        return mark_read(api, inq, "read" if op == "mark-read" else "unread")
    if not conv:
        return {"status": "conversation_not_found"}
    if op == "messages":
        ms = api.messages(conv, int(req.get("count") or 20))
        return {"status": "ok", "count": len(ms), "conversation": conv_brief(conv), "messages": [brief(m, bool(req.get("withText"))) for m in ms]}
    return send_and_confirm(api, conv, req["kind"], req)


def main(argv):
    import argparse
    p = argparse.ArgumentParser(description="OneTalk API (request JSON on stdin, result JSON on stdout)")
    p.add_argument("op")
    p.add_argument("--account", required=True)
    p.add_argument("--config", default=os.path.expanduser("~/.aliops-ops/seller-report/accounts.json"))
    a = p.parse_args(argv)
    try:
        req = json.loads(sys.stdin.read() or "{}")
        api = OneTalk(session_from_cookies(load_cookies(a.account, a.config)))
        out = run(a.op, req, api)
    except ApiError as e:
        out, code = {"status": "error", "error": str(e)}, e.code
    except Exception as e:  # network or unexpected shape: one line, no request data
        out, code = {"status": "error", "error": "%s: %s" % (type(e).__name__, str(e)[:200])}, 1
    else:
        code = 0
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
