#!/usr/bin/env python3
"""alibaba-accio engine: Accio Work local gateway (GGS seller account), read tools only.

Gateway = the one the Accio Work desktop app runs for its agents:
  GET  <url>/mcp/proxy/tools                      tool catalog
  POST <url>/mcp/proxy  {name, arguments, trace:{connectId}}   tool call
  auth = HTTP Basic <username>:<password>. The app writes url/username/password to
  ~/.accio/accounts/<accio account>/.accio/runtime/gateway-cli.json at every start (the password
  changes when the app restarts). `login` copies the password into the OS secret store
  (macOS keychain / Windows DPAPI, service aliops-accio-gateway); calls read it from there and,
  on HTTP 401, copy it again from the app's file and retry once. No Aside, no browser.

Never called: tools that write, send, publish, change ads/bids/budgets/campaigns, pay or spend
credits. The read/write call per tool comes from accio/features.json (live check 2026-11-16) and,
for tools not in it, from the tool name.
"""
import argparse
import base64
import csv
import datetime
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import secret_store  # noqa: E402

FEATURES = os.path.join(os.path.dirname(HERE), "accio", "features.json")
GW_DIR = os.path.join(os.path.dirname(HERE), "accio", "gateway-server")
COMMANDS = os.path.join(GW_DIR, "commands.json")
GW_HOME = "~/.aliops-ops/seller-gw"  # CLI copy + server files the gateway process runs (outside ~/Documents)
SECRET_SERVICE = "aliops-accio-gateway"
SELLER_TOOLKITS = ("alibaba", "alibaba-ggs")
EXIT_AUTH, EXIT_NO_LOGIN, EXIT_REFUSED, EXIT_USAGE = 2, 3, 65, 64

WRITE_WORDS = ("create", "update", "set", "save", "delete", "submit", "publish", "edit", "send", "mutate", "add",
               "remove", "upload", "generate", "optimize", "apply", "bind", "unbind", "cancel", "pay", "recharge",
               "confirm", "reply", "batch", "draft", "import", "modify", "change", "start", "stop", "enable",
               "disable", "invite", "sign", "bid", "budget", "charge", "order", "purchase", "buy", "write", "post")
READ_START = ("query", "get", "list", "fetch", "search", "find", "is", "retrieve", "check")
PRIVATE_AREAS = ("문의", "바이어")  # buyer data: stdout only, never --out

# shortcut -> (tool, help). Every tool here was "됨" in the 2026-11-16 live check on a test account.
SHORTCUTS = {
    "shop summary": ("query_ggs_shop_summary", "상점 요약(노출·클릭·P4P·자연·방문·문의) --arg statisticsType=day|week|month [--arg startDate=… --arg endDate=…]"),
    "shop trends": ("query_ggs_shop_trends", "상점 추세"),
    "shop report": ("query_ggs_monthly_diagnosis_report", "월간 진단 보고서(유입 출처별 바이어·쇼케이스 사용)"),
    "shop metrics": ("query_ggs_metrics_detail", "상점 지표 상세"),
    "shop diagnosis": ("query_store_diagnosis_daily_odps", "일간 진단(기본 유입) [--arg dailyScenarioCode=AGENTIC_DIAGNOSIS_TRAFFIC_D|_PRODUCT_D|_OPPORTUNITIES_D|_STAR_RANK_D]"),
    "shop merchant": ("query_ggs_merchant_info", "상점(판매자) 정보"),
    "shop star-rating": ("query_ggs_star_rating_overview", "별점 개요"),
    "shop risk": ("query_ggs_risk_detected_stat", "리스크 감지 통계"),
    "conversations": ("query_recent_conversation", "최근 문의 대화 목록(읽기만, 읽음 처리 안 함) [--arg limitTimeStamp=<앞 결과 nextTimeStamp>]"),
    "messages": ("query_conversation_msg_timeRange", "한 대화의 메시지 --arg conversationId=… --arg startTime=<ms> --arg endTime=<ms> (최근 30일만)"),
    "products zero-effect": ("ggs_diagnosis_list_zero_effect_products", "효과 0 상품 목록"),
    "products low-score": ("ggs_diagnosis_list_low_score_products", "상품 점수 범위로 상품 목록 [--arg minScore=0 --arg maxScore=5] (기본 0~5 = 전체)"),
    "products by-name": ("ggs_list_products_by_name", "상품 이름 단어로 찾기 --arg productName=…"),
    "product info": ("ggs_product_query_information", "상품 정보 --arg productId=… [--arg queryType=trunk|copy|draft|draftFirst] [--arg componentList='[…]']"),
    "product category": ("ggs_predict_category", "카테고리 예측 --arg imageInfo='[\"<이미지 URL>\"]' [--arg textInfo='[…]']"),
    "market hot-products": ("global_hot_selling_products", "글로벌 인기 상품 --arg query=… [--arg platform=amazon|alibaba|1688 …]"),
    "market opportunity": ("data_advisor_opportunity_discovery", "시장 기회"),
    "market country-rank": ("data_advisor_industry_country_rank", "업종 국가 순위 --arg cateId=… [--arg rankType=1|2|3|4]"),
    "market trend": ("data_advisor_industry_market_trend", "업종 시장 추세 --arg cateId=…"),
    "market detail": ("data_advisor_industry_market_detail", "업종 시장 상세 --arg cateId=…"),
    "market buyer-profile": ("data_advisor_industry_buyer_profile", "업종 바이어 프로필 --arg cateId=… [--arg indexName=cate_total|visitor_country|buyers_identity]"),
    "rfq history": ("rfq-quote-history-search", "RFQ 견적 이력"),
}


# shortcut -> inputs it cannot run without (cateId: from `products by-name` leafId or `product category --arg imageInfo=…`; product category needs imageInfo or textInfo and the tool itself says so)
SHORTCUT_NEEDS = {
    "products by-name": ("productName",), "product info": ("productId",),
    "market hot-products": ("query",), "market country-rank": ("cateId",), "market trend": ("cateId",),
    "market detail": ("cateId",), "market buyer-profile": ("cateId",),
}


# shortcut -> (wrapper key, default arguments). --arg values go inside the wrapper.
SHORTCUT_ARGS = {
    "conversations": ("request", {"domain": "icbu", "count": 20}),
    "messages": (None, {"domain": "icbu"}),
    "shop summary": (None, {"statisticsType": "week"}),
    "shop diagnosis": (None, {"dailyScenarioCode": "AGENTIC_DIAGNOSIS_TRAFFIC_D"}),
    "shop trends": (None, {"statisticsType": "week"}),
    "rfq history": (None, {"pageSize": 20, "currentPage": 1}),
    "products low-score": (None, {"minScore": 0, "maxScore": 5}),
    "product info": (None, {"queryType": "trunk", "componentList": ["productTitle", "images", "attr", "sku", "moq", "ladderPrices",
                                                                    "priceUnit", "productKeywords", "pkgWeight", "pkgMeasure"]}),
    "market opportunity": ("sceneTermQueryParam", {"currentPage": 1, "pageSize": 20}),
    "market country-rank": ("industryRankQueryParam", {"rankType": "1"}),
    "market buyer-profile": ("industryPortraitQueryParam", {"indexName": "cate_total"}),
}


# Inputs the gateway needs although the tool's inputSchema does not mark them required
# (without cateId the country-rank tool answers "countryRankQueryParam为空").


def missing_required(schema, args, prefix=""):
    """Required inputs (inputSchema `required`, nested objects too) absent from args -> [(path, description)]."""
    out = []
    props = (schema or {}).get("properties") or {}
    for k in (schema or {}).get("required") or []:
        if k not in args or args[k] in (None, ""):
            out.append((prefix + k, str((props.get(k) or {}).get("description", ""))))
    for k, sub in props.items():
        if isinstance(sub, dict) and sub.get("type") == "object" and isinstance(args.get(k), dict):
            out += missing_required(sub, args[k], prefix + k + ".")
    return out


class GatewayError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


class TransientToolFailure(Exception):
    pass


def accio_home():
    return os.environ.get("ACCIO_HOME") or os.path.join(os.path.expanduser("~"), ".accio")


def app_files():
    """-> list of (accio account dir, gateway-cli.json dict), newest first."""
    out = []
    for p in glob.glob(os.path.join(accio_home(), "accounts", "*", ".accio", "runtime", "gateway-cli.json")):
        try:
            with open(p, encoding="utf-8") as fh:
                g = json.load(fh)
        except (OSError, ValueError):
            continue
        out.append((os.path.dirname(os.path.dirname(os.path.dirname(p))), g))
    out.sort(key=lambda x: x[1].get("createdAt") or "", reverse=True)
    return out


def connectors(account_dir):
    try:
        with open(os.path.join(account_dir, "connected-accounts.json"), encoding="utf-8") as fh:
            ca = json.load(fh)
    except (OSError, ValueError):
        return []
    return [{"label": a.get("label") or "", "connect_id": "alibaba:%s" % (a.get("metadata") or {}).get("connector_id", "")}
            for a in (ca.get("alibaba") or {}).get("accounts") or [] if (a.get("metadata") or {}).get("connector_id")]


class Gateway:
    def __init__(self, opener=urllib.request.urlopen, files=app_files, store=secret_store):
        self.opener, self.files, self.store = opener, files, store
        found = self.files()
        if not found:
            raise GatewayError(EXIT_NO_LOGIN, "Accio Work 앱 게이트웨이 정보 없음(%s/accounts/*/.accio/runtime/gateway-cli.json): Accio Work 앱을 켜고 로그인" % accio_home())
        self.account_dir, g = found[0]
        self.url, self.user = (os.environ.get("ACCIO_GATEWAY_URL") or g.get("url") or "http://127.0.0.1:4097").rstrip("/"), g.get("username") or "phoenix"

    def login(self):
        """Copy the app's gateway password into the secret store. Value never printed."""
        pw = self.files()[0][1].get("password")
        if not pw:
            raise GatewayError(EXIT_NO_LOGIN, "게이트웨이 비밀번호 없음: Accio Work 앱 재시작")
        where = self.store.set_secret(SECRET_SERVICE, self.user, pw)
        return where

    def _auth(self):
        pw = self.store.get_secret(SECRET_SERVICE, self.user)
        if not pw:
            raise GatewayError(EXIT_NO_LOGIN, "로그인 안 됨: alibaba-accio login")
        return "Basic " + base64.b64encode(("%s:%s" % (self.user, pw)).encode()).decode()

    def _req(self, method, path, body=None, retried=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Authorization": self._auth(), "Accept": "application/json", "Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 401 and not retried:  # app restarted -> new password in its file
                self.login()
                return self._req(method, path, body, retried=True)
            raise GatewayError(EXIT_AUTH if e.code == 401 else 1, "게이트웨이 HTTP %s" % e.code) from None
        except urllib.error.URLError as e:
            raise GatewayError(1, "게이트웨이 연결 안 됨(%s): Accio Work 앱이 켜져 있는지 확인" % self.url) from None

    def tools(self):
        d = self._req("GET", "/mcp/proxy/tools")
        data = d.get("data") or {}
        return data.get("tools") or [], data.get("status") or {}

    # Custom MCP servers (POST /mcp/custom/servers; the gateway makes their tools callable at
    # POST /mcp/proxy {name} like its own). Never edit custom.json on disk: other sessions keep
    # their servers there. GET returns the raw list (field names come from the gateway).
    CUSTOM = "/mcp/custom/servers"

    def custom_servers(self):
        d = self._req("GET", self.CUSTOM)
        data = d.get("data") if isinstance(d, dict) else None
        for key in ("servers", "list", "items"):
            if isinstance(data, dict) and isinstance(data.get(key), list):
                return data[key]
        return data if isinstance(data, list) else []

    @staticmethod
    def custom_name(server):
        return str((server or {}).get("name") or (server or {}).get("serverName") or "")

    def custom_find(self, name):
        return next((s for s in self.custom_servers() if self.custom_name(s) == name), None)

    def custom_upsert(self, body):
        """-> (method, answer): PUT when the gateway already lists this server name, else POST."""
        if self.custom_find(body["name"]) is not None:
            return "PUT", self._req("PUT", "%s/%s" % (self.CUSTOM, body["name"]), body)
        return "POST", self._req("POST", self.CUSTOM, body)

    def custom_update(self, name, patch):
        return self._req("PUT", "%s/%s" % (self.CUSTOM, name), patch)

    def custom_test(self, name):
        d = self._req("POST", "%s/%s/test" % (self.CUSTOM, name), {})
        data = d.get("data") if isinstance(d, dict) else None
        out = data if isinstance(data, dict) else (d if isinstance(d, dict) else {})
        connected = bool(out.get("connected"))
        count = out.get("toolCount", out.get("tools"))
        if not isinstance(count, int):
            count = len(count) if isinstance(count, list) else None
        return {"connected": connected, "toolCount": count, "raw": out}

    def call(self, name, arguments, connect_id, attempts=3):
        """Read tools only, so a transient upstream failure is retried (attempts total)."""
        for left in range(attempts - 1, -1, -1):
            try:
                return self._call_once(name, arguments, connect_id)
            except TransientToolFailure as e:
                if not left:
                    raise GatewayError(1, "도구 실패(결과 없음, %d번 시도): %s" % (attempts, e)) from None

    def _call_once(self, name, arguments, connect_id):
        env = self._req("POST", "/mcp/proxy", {"name": name, "arguments": arguments, "trace": {"connectId": connect_id}})
        if env.get("success") is False:
            raise GatewayError(1, "게이트웨이 거절: %s" % str(env.get("errorMsg") or env.get("message") or env.get("errorCode"))[:200])
        data = env.get("data") or {}
        content = data.get("content")
        text = next((c.get("text") for c in content if isinstance(c, dict) and c.get("type") == "text"), None) if isinstance(content, list) else content
        try:
            inner = json.loads(text) if isinstance(text, str) else text
        except ValueError:
            inner = text
        if data.get("isError"):
            raise GatewayError(1, "도구 오류: %s" % (json.dumps(inner, ensure_ascii=False)[:300] if not isinstance(inner, str) else inner[:300]))
        if isinstance(inner, dict) and inner.get("success") is False:
            raise GatewayError(1, "도구 실패(결과 없음): %s" % (" ".join(
                str(x) for x in (inner.get("errorCode") or inner.get("code"), inner.get("errorMsg") or inner.get("message")) if x) or "success=false"))
        info = inner.get("payload").get("error_info") if isinstance(inner, dict) and isinstance(inner.get("payload"), dict) else None
        if info and info != "success":  # global_hot_selling_products: upstream search fails intermittently
            raise TransientToolFailure("payload.error_info=%s" % info)
        if isinstance(inner, dict) and isinstance(inner.get("data"), str):  # GGS tools send data as a JSON string
            try:
                decoded = json.loads(inner["data"])
            except ValueError:
                decoded = None
            if isinstance(decoded, (list, dict)):
                inner = {**inner, "data": decoded}
        return inner


# alibaba-inquiry conversations|messages|buyer --transport accio: the same JSON engine/onetalk_cli.mjs
# prints, from the gateway's IM tools (read only). Live check 2026-11-17 against the cookie API on one
# account: ids, unread counts, times, buyer level and country equal. The tool rows do not give chat type,
# message type, joining years or potential score (null here), list one-to-one chats only, leave system
# notices out of a message list, and page conversations 20 at a time whatever count is asked.
CONV_PAGE = 20


def _iso_ms(text):
    try:
        return int(datetime.datetime.fromisoformat(str(text)).timestamp() * 1000)
    except ValueError:
        return None


def accio_conv_brief(c):
    """query_recent_conversation row -> onetalk_cli convBrief + level, without names, logins or e-mail."""
    role = c.get("latestMessageSenderRole")
    level = c.get("contactLevel")
    return {"buyerAliId": str(c.get("contactAliId", "")), "unread": c.get("unreadMessageCount"),
            "modifyTime": _iso_ms(c.get("conversationModifyTime")), "chatType": None, "lastType": None,
            "lastSendType": {"seller": "send", "buyer": "rec"}.get(role), "lastTime": c.get("latestMessageTimestamp"),
            "level": level, "levelSource": "contactLevel" if level is not None else "no_level_field"}


def _conv_page(call, cursor=None):
    req = {"domain": "icbu"}
    if cursor:
        req["limitTimeStamp"] = int(cursor)
    d = (call("query_recent_conversation", {"request": req}) or {}).get("data") or {}
    return d.get("conversations") or [], d.get("nextTimeStamp"), bool(d.get("hasMore"))


def _find_conv(call, buyer_ali_id, max_pages=40):
    cursor = None
    for _ in range(max_pages):
        rows, cursor, more = _conv_page(call, cursor)
        hit = next((c for c in rows if str(c.get("contactAliId")) == str(buyer_ali_id)), None)
        if hit or not more or not cursor:
            return hit
    return None


def inquiry_op(call, op, req, now=time.time):
    """call(tool, arguments) -> tool result. -> the onetalk_cli result dict for op."""
    if op == "conversations":
        # The tool ignores count and answers pages of 20 conversations (group chats left out), so
        # count is rounded up to whole pages: the same window as the cookie API's first `count` rows.
        pages, cursor, rows, more = -(-max(1, int(req.get("count") or 50)) // CONV_PAGE), req.get("cursor"), [], True
        for _ in range(pages):
            page, cursor, more = _conv_page(call, cursor)
            rows += page
            if not more or not cursor:
                break
        return {"status": "ok", "count": len(rows), "hasMore": more, "nextCursor": cursor,
                "conversations": [accio_conv_brief(c) for c in rows]}
    if op not in ("messages", "buyer"):
        raise GatewayError(EXIT_USAGE, "--transport accio 안 됨: %s" % op)
    if not req.get("buyerAliId") or req.get("inquiryId") or req.get("tradeId"):
        raise GatewayError(EXIT_USAGE, "--transport accio 는 --buyer-ali-id 로만 대화를 찾는다(게이트웨이에 문의 목록 도구 없음)")
    conv = _find_conv(call, req["buyerAliId"], int(req.get("maxPages") or 40))
    if not conv:
        return {"status": "conversation_not_found"}
    if op == "buyer":
        # From the conversation row: get_buyer_basic_info disagreed with the cookie API on level and
        # country for some buyers (live check), the row's contactLevel / contactCountry did not.
        brief = accio_conv_brief(conv)
        buyer = {"buyerAliId": brief["buyerAliId"], "level": brief["level"], "levelSource": brief["levelSource"]}
        if conv.get("contactCountry"):
            buyer["countryCode"] = conv["contactCountry"]
        return {"status": "ok", "buyer": buyer}
    d = (call("query_conversation_msg", {"request": {"conversationId": conv["conversationId"], "selfAliId": conv["sellerAliId"],
                                                     "limitTimeStamp": int(now() * 1000), "forward": False,
                                                     "count": int(req.get("count") or 20), "domain": "icbu"}}) or {}).get("data") or {}
    self_id = str(conv["sellerAliId"])
    msgs = [{"messageId": m.get("messageId"), "messageType": None, "sendType": "send" if str(m.get("senderAliId")) == self_id else "rec",
             "sendTime": m.get("timestamp"), "read": m.get("isRead")} for m in d.get("messages") or []]
    return {"status": "ok", "count": len(msgs), "conversation": accio_conv_brief(conv), "messages": msgs}


def cmd_inquiry(op, stdin_text, make_gateway):
    """`accio_gateway.py inquiry <op>` (request JSON on stdin) -> one JSON line, exit code like onetalk_cli
    (2 = gateway login missing or refused, 64 = bad argument, 1 = anything else)."""
    try:
        req = json.loads(stdin_text or "{}").get("request") or {}
        g = make_gateway()
        cons = connectors(g.account_dir)
        if len(cons) != 1:
            raise GatewayError(EXIT_USAGE, "Accio 앱에 연결된 알리바바 계정 %d개(하나여야 함)" % len(cons))
        feats = load_features()

        def call(name, args):
            if not is_read(name, feats):
                raise GatewayError(EXIT_REFUSED, "안 부름: %s 는 읽기 도구가 아니다" % name)
            return g.call(name, args, cons[0]["connect_id"])
        res = inquiry_op(call, op, req)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    except (GatewayError, secret_store.StoreError) as e:
        print(json.dumps({"status": "error", "error": str(e)[:300]}, ensure_ascii=False))
        code = getattr(e, "code", 1)
        return 2 if code in (EXIT_AUTH, EXIT_NO_LOGIN) else EXIT_USAGE if code == EXIT_USAGE else 1


def load_features():
    with open(FEATURES, encoding="utf-8") as fh:
        return {r["tool"]: r for r in json.load(fh)["tools"]}


def name_class(name):
    toks = [t for t in re.split(r"[_\-]+|(?<=[a-z])(?=[A-Z])", name.lower()) if t]
    if any(t in WRITE_WORDS for t in toks):
        return "쓰기"
    return "읽기" if toks and toks[0] in READ_START else "모름"


def is_read(name, feats):
    f = feats.get(name)
    rw = (f or {}).get("rw") or name_class(name)
    return rw == "읽기" and name_class(name) != "쓰기"


def parse_args_kv(a):
    args = json.loads(a.args) if a.args else {}
    if not isinstance(args, dict):
        raise GatewayError(EXIT_USAGE, "--args 는 JSON 객체")
    for kv in a.arg or []:
        k, _, v = kv.partition("=")
        if not k:
            raise GatewayError(EXIT_USAGE, "--arg 는 key=value")
        try:
            args[k] = json.loads(v)
        except ValueError:
            args[k] = v
    return args


def emit_rows(rows, fmt):
    if fmt == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    elif fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else ["empty"])
        w.writeheader()
        w.writerows(rows)
        sys.stdout.write(buf.getvalue())
    else:
        if not rows:
            print("(0행)")
            return
        cols = list(rows[0].keys())
        width = {c: min(48, max(len(c), *(len(str(r.get(c, ""))) for r in rows))) for c in cols}
        print("  ".join(c.ljust(width[c]) for c in cols))
        for r in rows:
            print("  ".join(str(r.get(c, ""))[:48].ljust(width[c]) for c in cols))


def cmd_features(a, feats):
    rows = []
    short = {v[0]: k for k, v in SHORTCUTS.items()}
    for r in feats.values():
        if a.search and a.search.lower() not in r["tool"].lower():
            continue
        if a.tested and not r["tested_20261116"].startswith(a.tested):
            continue
        read = is_read(r["tool"], feats)
        if a.read_only and not read:
            continue
        rows.append({"area": r["area"], "tool": r["tool"], "toolkit": r["toolkit"], "rw": r["rw"] or name_class(r["tool"]),
                     "tested_20261116": r["tested_20261116"],
                     "verified_result": r.get("verified_result", ""),
                     "command": ("alibaba-accio " + short[r["tool"]]) if r["tool"] in short else ("alibaba-accio call " + r["tool"]) if read else "(안 부름)"})
    emit_rows(rows, a.format)
    return 0


def main(argv=None, gateway=None):
    p = argparse.ArgumentParser(prog="alibaba-accio", add_help=False)
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("words", nargs="*")
    p.add_argument("--arg", action="append", help="key=value (value JSON if it parses)")
    p.add_argument("--args", help="JSON object of tool arguments")
    p.add_argument("--connector", help="connected Alibaba account (connect id or label part) when there are several")
    p.add_argument("--search")
    p.add_argument("--tested", help="features: filter by 2026-11-16 result prefix (됨, 안 됨, 미호출, 미시험)")
    p.add_argument("--read-only", action="store_true")
    p.add_argument("--format", choices=("table", "json", "csv"), default="table")
    p.add_argument("--out", help="write the tool result JSON here (not for 문의·바이어 tools)")
    p.add_argument("--cli", dest="cli_copy", help="attach: the CLI copy the server runs (default ~/.aliops-ops/seller-gw/cli)")
    a = p.parse_args(argv)
    feats = load_features()
    w = a.words
    cmd = "help" if a.help else " ".join(w[:2]) if " ".join(w[:2]) in SHORTCUTS else (w[0] if w else "help")
    try:
        if cmd == "help":
            print(usage())
            return 0
        if cmd == "features":
            return cmd_features(a, feats)
        if cmd == "inquiry":  # internal: alibaba-inquiry --transport accio
            return cmd_inquiry(w[1] if len(w) > 1 else "", sys.stdin.read(), lambda: gateway or Gateway())
        g = gateway or Gateway()
        if cmd == "login":
            where = g.login()
            tools, st = g.tools()
            seller = [t for t in tools if (t.get("meta") or {}).get("toolkit") in SELLER_TOOLKITS]
            print(json.dumps({"login": "ok", "secret_store": where, "gateway": g.url, "accio_account": os.path.basename(g.account_dir),
                              "tools_total": len(tools), "seller_tools": len(seller)}, ensure_ascii=False))
            return 0
        if cmd == "attach":
            return cmd_attach(g, w[1] if len(w) > 1 else "seller-gw", a)
        if cmd == "detach":
            print(json.dumps(detach(g, w[1] if len(w) > 1 else "seller-gw"), ensure_ascii=False))
            return 0
        if cmd == "status":
            tools, st = g.tools()
            seller = [t for t in tools if (t.get("meta") or {}).get("toolkit") in SELLER_TOOLKITS]
            print(json.dumps({"gateway": g.url, "session": "ok", "accio_account": os.path.basename(g.account_dir),
                              "alibaba_accounts": [c["label"] for c in connectors(g.account_dir)],
                              "tools_total": len(tools), "seller_tools": len(seller), "catalog_state": st.get("managerState")}, ensure_ascii=False))
            return 0
        if cmd == "tools":
            tools, _ = g.tools()
            rows = []
            for t in tools:
                kit = (t.get("meta") or {}).get("toolkit")
                if kit not in SELLER_TOOLKITS or (a.search and a.search.lower() not in t["name"].lower()):
                    continue
                f = feats.get(t["name"]) or {}
                rows.append({"tool": t["name"], "toolkit": kit, "rw": f.get("rw") or name_class(t["name"]),
                             "tested_20261116": f.get("tested_20261116", "미시험"), "callable": "예" if is_read(t["name"], feats) else "아니오"})
            emit_rows(rows, a.format)
            return 0
        if cmd == "schema":
            name = w[1] if len(w) > 1 else ""
            tools, _ = g.tools()
            t = next((x for x in tools if x["name"] == name), None)
            if not t:
                raise GatewayError(EXIT_USAGE, "도구 없음: %s (alibaba-accio tools --search …)" % name)
            print(json.dumps({"tool": name, "callable": is_read(name, feats), "description": t.get("description", ""),
                              "inputSchema": t.get("inputSchema")}, ensure_ascii=False, indent=1))
            return 0
        if cmd in SHORTCUTS or cmd == "call":
            name = SHORTCUTS[cmd][0] if cmd in SHORTCUTS else (w[1] if len(w) > 1 else "")
            if not name:
                raise GatewayError(EXIT_USAGE, "call <도구 이름>")
            if not is_read(name, feats):
                raise GatewayError(EXIT_REFUSED, "안 부름: %s 는 읽기 도구가 아니다(쓰기·발송·광고 변경·결제·생성 금지)" % name)
            area = (feats.get(name) or {}).get("area", "")
            if a.out and area in PRIVATE_AREAS:
                raise GatewayError(EXIT_REFUSED, "%s 도구 결과는 바이어 정보라 파일로 저장하지 않는다(--out 빼고 화면으로만)" % area)
            cons = connectors(g.account_dir)
            if a.connector:
                cons = [c for c in cons if a.connector in (c["connect_id"], ) or a.connector.lower() in c["label"].lower()]
            if len(cons) != 1:
                raise GatewayError(EXIT_USAGE, "알리바바 연결 계정 %d개: --connector <이름 일부> 로 하나 고르기 (%s)" % (len(cons), ", ".join(c["label"] for c in connectors(g.account_dir))))
            args = parse_args_kv(a)
            if name == "icbu_product_3d_model_list":
                args.setdefault("request", {"currentPage": 1, "pageSize": 10})
            if cmd in SHORTCUT_ARGS:
                wrap, base = SHORTCUT_ARGS[cmd]
                args = {wrap: {**base, **args}} if wrap else {**base, **args}
            if cmd == "messages":
                # defaults: the most recent conversation, the last 7 days (the tool keeps 30 days)
                now_ms = int(time.time() * 1000)
                args.setdefault("endTime", now_ms)
                args.setdefault("startTime", now_ms - 7 * 86400000)
                if not args.get("conversationId"):
                    rec = g.call("query_recent_conversation", {"request": {"domain": "icbu", "count": 1}}, cons[0]["connect_id"])
                    convs = ((rec or {}).get("data") or {}).get("conversations") or []
                    if not convs:
                        raise GatewayError(EXIT_USAGE, "최근 대화 없음: --arg conversationId=… 로 지정")
                    args["conversationId"] = convs[0]["conversationId"]
            tools, _ = g.tools()
            tool = next((x for x in tools if x["name"] == name), None)
            miss = missing_required((tool or {}).get("inputSchema"), args)
            # inputs the tool rejects without (the schema does not mark them required)
            inner = args.get(SHORTCUT_ARGS[cmd][0]) if cmd in SHORTCUT_ARGS and SHORTCUT_ARGS[cmd][0] else args
            miss += [(k, "필수") for k in SHORTCUT_NEEDS.get(cmd, ()) if (inner or {}).get(k) in (None, "")]
            if miss:
                how = "alibaba-accio %s" % (cmd if cmd in SHORTCUTS else "call " + name)
                raise GatewayError(EXIT_USAGE, "필수 인자 없음 — 호출 안 함:\n" + "\n".join(
                    "  %s: %s" % (k, d[:120]) for k, d in miss) + "\n예: %s %s   (전체 인자: alibaba-accio schema %s)" % (
                    how, " ".join("--arg %s=<값>" % k.split(".")[-1] for k, _ in miss), name))
            res = g.call(name, args, cons[0]["connect_id"])
            text = json.dumps(res, ensure_ascii=False, indent=1)
            if a.out:
                with open(a.out, "w", encoding="utf-8") as fh:
                    fh.write(text + "\n")
                print("written: " + a.out)
            else:
                print(text)
            return 0
        raise GatewayError(EXIT_USAGE, "모르는 명령: %s\n%s" % (" ".join(w), usage()))
    except GatewayError as e:
        print("오류: %s" % e, file=sys.stderr)
        return e.code
    except secret_store.StoreError as e:
        print("오류: %s" % e, file=sys.stderr)
        return 1


def gateway_commands():
    with open(COMMANDS, encoding="utf-8") as fh:
        return json.load(fh)


def custom_server_body(name, cli_copy, home=GW_HOME, node=None):
    """The body POST /mcp/custom/servers takes: a local server that runs accio/gateway-server/mcp_server.py
    with the CLI copy outside ~/Documents (a process Accio starts hangs on files there, TCC).
    The gateway's own shape is what `GET /mcp/custom/servers` reports for a working entry: type "local"
    and command as the argv array (a string command with args is not launched: /test answers
    `Failed to parse URL from :`)."""
    home = os.path.abspath(os.path.expanduser(home))
    server_dir = os.path.join(home, "server")  # attach copies mcp_server.py + commands.json here
    env = {"SELLER_CLI": cli_copy, "SELLER_GW_CONFIG": os.path.join(home, "config.json")}
    if node:
        env["SELLER_NODE"] = node
    return {"name": name, "type": "local", "command": [sys.executable, os.path.join(server_dir, "mcp_server.py")],
            "environment": env, "enabled": True}


def attach(g, name="seller-gw", cli_copy=None, home=GW_HOME):
    """Make the gateway serve seller_central_read/result: this repo's HEAD -> <home>/cli (git worktree),
    the server files -> <home>/server, config -> <home>/config.json, then register/PUT and /test.
    -> dict for the command output. Raises GatewayError when the test does not connect."""
    repo = os.path.dirname(HERE)
    home = os.path.expanduser(home)
    cli = os.path.abspath(os.path.expanduser(cli_copy or os.path.join(home, "cli")))
    dest = os.path.join(home, "server")
    os.makedirs(dest, exist_ok=True)
    if cli_copy is None:
        worktree(repo, cli)
    for f in ("mcp_server.py", "commands.json"):
        shutil.copyfile(os.path.join(repo, "accio", "gateway-server", f), os.path.join(dest, f))
    with open(os.path.join(home, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"SELLER_CLI": os.path.join(cli, "bin", "alibaba-data.mjs")}, fh, ensure_ascii=False)
    body = custom_server_body(name, os.path.join(cli, "bin", "alibaba-data.mjs"), home)
    method, _ = g.custom_upsert(body)
    test = g.custom_test(name)
    out = {"server": name, "method": method, "cli": cli, "connected": test["connected"], "toolCount": test["toolCount"],
           "commands": len(gateway_commands())}
    if not test["connected"]:
        raise GatewayError(1, "/test 가 connected=false: %s" % json.dumps(test["raw"], ensure_ascii=False)[:300])
    return out


def detach(g, name="seller-gw"):
    """Keep the registration but switch it off (DELETE answers 409)."""
    g.custom_update(name, {"enabled": False})
    return {"server": name, "enabled": False}


def worktree(repo, cli):
    """<cli> = this repo at HEAD, as a git worktree (a plain copy when it is already a git checkout)."""
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    listed = subprocess.run(["git", "-C", repo, "worktree", "list", "--porcelain"], capture_output=True, text=True).stdout
    for block in listed.split("\n\n"):
        if ("worktree %s" % cli) in block:
            # move the copy to the attaching checkout's HEAD (its own HEAD would keep the old code)
            subprocess.run(["git", "-C", cli, "checkout", "--quiet", "--force", "--detach", head], check=True)
            return "worktree"
    if os.path.exists(os.path.join(cli, ".git")):
        return "existing"
    os.makedirs(os.path.dirname(cli) or ".", exist_ok=True)
    subprocess.run(["git", "-C", repo, "worktree", "add", "--detach", cli, head], check=True)
    return "worktree+"


def cmd_attach(g, name, a):
    try:
        print(json.dumps(attach(g, name, a.cli_copy), ensure_ascii=False))
        return 0
    except subprocess.CalledProcessError as e:
        raise GatewayError(1, "attach: git 명령 실패(%s): %s" % (e.cmd, (e.stderr or b"").decode("utf-8", "replace")[:300] if isinstance(e.stderr, bytes) else str(e.stderr)[:300])) from None


def usage():
    lines = ["alibaba-accio (선택: Accio Work 게이트웨이, GGS 셀러 계정 기준) <명령> [--arg k=v …] [--args JSON] [--format table|json|csv]",
             "  login                Accio Work 앱의 게이트웨이 비밀번호를 OS 비밀 저장소에 넣음(값 출력 없음)",
             "  attach [이름]        이 레포 HEAD 사본·서버를 ~/.aliops-ops/seller-gw 로 놓고 게이트웨이에 등록(PUT/POST) 후 /test 로 connected·toolCount 확인 [--cli 경로]",
             "  detach [이름]        등록을 enabled:false 로 (DELETE 는 409)",
             "  status               게이트웨이 연결·세션·연결된 알리바바 계정·도구 수",
             "  features             기능 표: 도구·읽기/쓰기·2026-11-16 실호출 결과·명령 [--search x] [--tested 됨] [--read-only]",
             "  tools                지금 게이트웨이의 셀러 도구 목록(호출 가능 여부) [--search x]",
             "  schema <도구>         도구 설명·인자(inputSchema)·호출 가능 여부",
             "  call <도구>           읽기 도구 하나 호출 (쓰기·발송·광고 변경·결제·생성 도구는 거부, 종료 코드 65)"]
    lines += ["  %-20s %s" % (k, v[1]) for k, v in SHORTCUTS.items()]
    return "\n".join(lines)


if __name__ == "__main__":
    import signal
    if hasattr(signal, "SIGPIPE"):  # `| head` ends quietly; not on Windows
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main())
