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
import glob
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import secret_store  # noqa: E402

FEATURES = os.path.join(os.path.dirname(HERE), "accio", "features.json")
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
    "shop diagnosis": ("query_store_diagnosis_daily_odps", "일간 유입 진단 --arg dailyScenarioCode=… (값은 schema 로)"),
    "shop merchant": ("query_ggs_merchant_info", "상점(판매자) 정보"),
    "shop star-rating": ("query_ggs_star_rating_overview", "별점 개요"),
    "shop risk": ("query_ggs_risk_detected_stat", "리스크 감지 통계"),
    "conversations": ("query_recent_conversation", "최근 문의 대화 목록(읽기만, 읽음 처리 안 함)"),
    "messages": ("query_conversation_msg_timeRange", "한 대화의 메시지(시간 범위) --arg …"),
    "products zero-effect": ("ggs_diagnosis_list_zero_effect_products", "효과 0 상품 목록"),
    "products low-score": ("ggs_diagnosis_list_low_score_products", "점수 낮은 상품 목록"),
    "products by-name": ("ggs_list_products_by_name", "상품 이름으로 찾기 --arg …"),
    "product info": ("ggs_product_query_information", "상품 정보 --arg …"),
    "product category": ("ggs_predict_category", "카테고리 예측 --arg …"),
    "market hot-products": ("global_hot_selling_products", "글로벌 인기 상품"),
    "market opportunity": ("data_advisor_opportunity_discovery", "시장 기회"),
    "market country-rank": ("data_advisor_industry_country_rank", "업종 국가 순위"),
    "market trend": ("data_advisor_industry_market_trend", "업종 시장 추세"),
    "market detail": ("data_advisor_industry_market_detail", "업종 시장 상세"),
    "market buyer-profile": ("data_advisor_industry_buyer_profile", "업종 바이어 프로필"),
    "rfq history": ("rfq-quote-history-search", "RFQ 견적 이력"),
}


# shortcut -> (wrapper key, default arguments). --arg values go inside the wrapper.
SHORTCUT_ARGS = {
    "conversations": ("request", {"domain": "icbu", "count": 20}),
    "messages": (None, {"domain": "icbu"}),
    "shop summary": (None, {"statisticsType": "week"}),
    "shop trends": (None, {"statisticsType": "week"}),
}


class GatewayError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


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
        self.url, self.user = (g.get("url") or "http://127.0.0.1:4097").rstrip("/"), g.get("username") or "phoenix"

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

    def call(self, name, arguments, connect_id):
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
        return inner


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
                     "command": ("alibaba-accio " + short[r["tool"]]) if r["tool"] in short else ("alibaba-accio call " + r["tool"]) if read else "(안 부름)"})
    emit_rows(rows, a.format)
    return 0


def main(argv=None, gateway=None):
    p = argparse.ArgumentParser(prog="alibaba-accio")
    p.add_argument("words", nargs="*")
    p.add_argument("--arg", action="append", help="key=value (value JSON if it parses)")
    p.add_argument("--args", help="JSON object of tool arguments")
    p.add_argument("--connector", help="connected Alibaba account (connect id or label part) when there are several")
    p.add_argument("--search")
    p.add_argument("--tested", help="features: filter by 2026-11-16 result prefix (됨, 안 됨, 미호출, 미시험)")
    p.add_argument("--read-only", action="store_true")
    p.add_argument("--format", choices=("table", "json", "csv"), default="table")
    p.add_argument("--out", help="write the tool result JSON here (not for 문의·바이어 tools)")
    a = p.parse_args(argv)
    feats = load_features()
    w = a.words
    cmd = " ".join(w[:2]) if " ".join(w[:2]) in SHORTCUTS else (w[0] if w else "help")
    try:
        if cmd == "help":
            print(usage())
            return 0
        if cmd == "features":
            return cmd_features(a, feats)
        g = gateway or Gateway()
        if cmd == "login":
            where = g.login()
            tools, st = g.tools()
            seller = [t for t in tools if (t.get("meta") or {}).get("toolkit") in SELLER_TOOLKITS]
            print(json.dumps({"login": "ok", "secret_store": where, "gateway": g.url, "accio_account": os.path.basename(g.account_dir),
                              "tools_total": len(tools), "seller_tools": len(seller)}, ensure_ascii=False))
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
            if cmd in SHORTCUT_ARGS:
                wrap, base = SHORTCUT_ARGS[cmd]
                args = {wrap: {**base, **args}} if wrap else {**base, **args}
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


def usage():
    lines = ["alibaba-accio (Accio Work 게이트웨이, GGS 셀러 계정 기준) <명령> [--arg k=v …] [--args JSON] [--format table|json|csv]",
             "  login                Accio Work 앱의 게이트웨이 비밀번호를 OS 비밀 저장소에 넣음(값 출력 없음)",
             "  status               게이트웨이 연결·세션·연결된 알리바바 계정·도구 수",
             "  features             기능 표: 도구·읽기/쓰기·2026-11-16 실호출 결과·명령 [--search x] [--tested 됨] [--read-only]",
             "  tools                지금 게이트웨이의 셀러 도구 목록(호출 가능 여부) [--search x]",
             "  schema <도구>         도구 설명·인자(inputSchema)·호출 가능 여부",
             "  call <도구>           읽기 도구 하나 호출 (쓰기·발송·광고 변경·결제·생성 도구는 거부, 종료 코드 65)"]
    lines += ["  %-20s %s" % (k, v[1]) for k, v in SHORTCUTS.items()]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
