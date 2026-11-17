"""Seller-center session from a browser's cookies: capture, encrypted storage, status, relogin.

Stored record (one per account key, never printed):
  {"v": 1, "source": {"browser": "chrome|edge|aside", "profile": "Default", "route": "sqlite|cdp|aside|live",
                      "userDataDir"?: dir, "signIn"?: true},
   "store": "keychain|file600|dpapi", "savedAt": iso, "cookies": [Cookie, ...]}
  keychain  macOS: ~/.aliops-ops/seller-report/cookies/<key>.bin = AES-256-GCM, key (32 random bytes) in
            the keychain item "alibaba-seller-cookies"/<key> (the JSON itself is too long for
            `security -i`, whose input line stops near 4 KB).
  file600   keychain did not answer (locked / no GUI session), or no keychain (Linux):
            <cookie dir>/<key>.json, plain JSON readable by the owner only (chmod 600).
  dpapi     Windows: %APPDATA%\\alibaba-seller-cli\\cookies\\<key>.bin = DPAPI (current user).
  Loading reads whichever file exists, the newest first.

Pipe-only CLI for a parent process (cookie values on stdout: never type these in a terminal):
  python3 cookie_store.py load <account key> [--config accounts.json]      # exit 2 when nothing is stored
  python3 cookie_store.py refresh <account key> [--config accounts.json]   # re-read the source browser first
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import unquote, urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aes  # noqa: E402
import browser_cookies as bc  # noqa: E402
import secret_store  # noqa: E402
from secret_store import StoreError  # noqa: E402

SERVICE = "alibaba-seller-cookies"
# Session cookies Alibaba sets only for a signed-in member (httpOnly, so Aside's document.cookie never sees them).
LOGIN_COOKIES = {"cookie2", "xman_t", "xman_us_t"}
ISLOGIN_URL = "https://business.alibaba.com/index/isLogin"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
NOT_LOGIN = re.compile(r"(?i)not[_ ]?login|need[_ ]?login|session[_ ]?(?:expired|timeout)")


# -- storage --------------------------------------------------------------------------
def cookie_dir():
    if os.environ.get("ALIBABA_SELLER_COOKIE_DIR"):
        return os.environ["ALIBABA_SELLER_COOKIE_DIR"]
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "alibaba-seller-cli", "cookies")
    return os.path.expanduser("~/.aliops-ops/seller-report/cookies")


def cookie_path(name, ext=".bin"):
    secret_store._check(name)
    return os.path.join(cookie_dir(), name + ext)


def _file_key(name, create):
    hexkey = secret_store.get_secret(SERVICE, name)
    if not hexkey and create:
        hexkey = os.urandom(32).hex()
        secret_store.set_secret(SERVICE, name, hexkey)
    return bytes.fromhex(hexkey) if hexkey else None


def _seal(name, data):
    if os.name == "nt":
        return b"DP1" + bc.dpapi_protect(data)
    nonce = os.urandom(12)
    return b"GC1" + nonce + aes.gcm_encrypt(_file_key(name, True), nonce, data)


def _unseal(name, blob):
    if blob[:3] == b"DP1":
        return bc.dpapi_unprotect(blob[3:])
    if blob[:3] == b"GC1":
        key = _file_key(name, False)
        if not key:
            raise StoreError("쿠키 파일의 키가 키체인에 없다: login --browser 로 다시 저장")
        return aes.gcm_decrypt(key, blob[3:15], blob[15:])
    raise StoreError("쿠키 파일 형식을 모름")


def _write600(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def save(name, cookies, source):
    rec = {"v": 1, "source": source, "savedAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "cookies": cookies}
    if os.name == "nt" or sys.platform == "darwin":
        rec["store"] = "dpapi" if os.name == "nt" else "keychain"
        try:
            _write600(cookie_path(name), _seal(name, json.dumps(rec, ensure_ascii=False).encode("utf-8")))
            if os.path.exists(cookie_path(name, ".json")):
                os.remove(cookie_path(name, ".json"))
            return rec
        except secret_store.KeychainTimeout as e:
            print("%s\n→ 쿠키를 본인만 읽는 파일(권한 600)에 저장한다: %s" % (e, cookie_path(name, ".json")), file=sys.stderr)
    rec["store"] = "file600"
    _write600(cookie_path(name, ".json"), json.dumps(rec, ensure_ascii=False).encode("utf-8"))
    return rec


def load_record(name):
    """The newest stored record (.bin keychain/DPAPI, or .json file600) or None."""
    paths = sorted((p for p in (cookie_path(name), cookie_path(name, ".json")) if os.path.isfile(p)), key=os.path.getmtime, reverse=True)
    for i, path in enumerate(paths):
        with open(path, "rb") as fh:
            blob = fh.read()
        try:
            return json.loads(blob if path.endswith(".json") else _unseal(name, blob).decode("utf-8"))
        except secret_store.KeychainTimeout:
            if i + 1 == len(paths):
                raise
        except ValueError:
            if i + 1 == len(paths):
                raise StoreError("저장된 쿠키를 못 풂(키가 바뀜?): login --browser 로 다시 저장")
    return None


# -- what the cookies say -------------------------------------------------------------
def alive(cookies, now=None):
    now = time.time() if now is None else now
    return [c for c in cookies if c.get("value") and (c.get("expires", -1) < 0 or c["expires"] > now)]


def identity(cookies, login_id, now=None):
    """'ok' | 'signed_out' (no live login cookie) | 'wrong_account' (loginId in no cookie value)."""
    live = alive(cookies, now)
    if not any(c["name"] in LOGIN_COOKIES for c in live):
        return "signed_out"
    blob = "\n".join(unquote(c["value"]).lower() for c in live)
    return "ok" if login_id.strip().lower() in blob else "wrong_account"


def cookie_header(cookies, url, now=None):
    u = urlparse(url)
    host, path = (u.hostname or "").lower(), u.path or "/"
    sent = []
    for c in alive(cookies, now):
        d = c["domain"].lower()
        if not (host == d.lstrip(".") or host.endswith("." + d.lstrip("."))):
            continue
        if not path.startswith(c.get("path") or "/") or (c.get("secure") and u.scheme != "https"):
            continue
        sent.append("%s=%s" % (c["name"], c["value"]))
    return "; ".join(sent)


def is_login_expired(final_url, status, text):
    host = (urlparse(final_url or "").hostname or "").lower()
    if host.startswith(("login.", "passport.")):
        return True
    return status == 401 or bool(NOT_LOGIN.search((text or "")[:4000]))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def http_get(url, cookie):
    """-> (final url, status, body text). One GET, redirects not followed (Location = final url)."""
    req = urllib.request.Request(url, headers={"Cookie": cookie, "User-Agent": UA, "Accept": "application/json, text/plain, */*",
                                               "Referer": "https://i.alibaba.com/"})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=30) as r:
            return r.geturl(), r.status, r.read(200000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location")
        return (urljoin(url, loc) if loc else url), e.code, e.read(20000).decode("utf-8", "replace")


# -- capture --------------------------------------------------------------------------
def read_browser(browser, profile, via_cdp=False, aside_account="u0", udd=None):
    """-> (cookies, route). SQLite first; v20, a locked file or a keychain that does not answer
    falls back to CDP (the browser decrypts its own cookies)."""
    if browser == "aside":
        return bc.read_via_aside(aside_account), "aside"
    if via_cdp:
        return bc.read_via_cdp(browser, profile, udd), "cdp"
    try:
        return bc.read_profile(browser, profile, udd), "sqlite"
    except (bc.AppBoundError, bc.LockedError, secret_store.KeychainTimeout) as e:
        print("%s → 브라우저 자신에게 CDP 로 받는다" % e, file=sys.stderr)
        return bc.read_via_cdp(browser, profile, udd), "cdp"


STATE_KO = {"ok": "로그인 쿠키 있음, 셀러 일치", "signed_out": "로그인 쿠키 없음", "wrong_account": "로그인 쿠키 있음, 셀러 불일치"}


def choose_profile(browser, login_id, via_cdp=False, udd=None):
    """The one profile signed in as login_id -> (profile, cookies, route)."""
    found = []
    for p in bc.profiles(browser, udd):
        cookies, route = read_browser(browser, p, via_cdp, udd=udd)
        found.append((p, identity(cookies, login_id), cookies, route))
    ok = [f for f in found if f[1] == "ok"]
    listing = "\n".join("  %s: %s" % (p, STATE_KO[s]) for p, s, _, _ in found) or "  (쿠키 파일이 있는 프로필 없음)"
    if len(ok) > 1:
        raise bc.CookieError("이 셀러로 로그인된 프로필이 여럿: --profile 로 하나를 고른다\n" + listing, 64)
    if not ok:
        raise bc.CookieError("%s 어느 프로필에도 이 셀러 로그인이 없다: 그 브라우저에서 셀러센터에 먼저 로그인\n%s"
                             % (bc.browser_spec(browser)["label"], listing), 3)
    return ok[0][0], ok[0][2], ok[0][3]


def profile_home():
    """Dedicated browser profiles for login --sign-in, one folder per account key."""
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "alibaba-seller-cli", "chrome-profiles")
    return os.path.expanduser("~/.aliops-ops/seller-report/chrome-profiles")


SIGN_IN_KO = {
    "captcha": "로그인 중 슬라이더·캡차가 떴다(드래그 1회 시도 뒤에도 남음)",
    "two_factor": "로그인 중 2단계 인증 코드를 요구한다",
    "still_on_login": "비밀번호를 넣었지만 로그인 화면에서 넘어가지 않는다(비밀번호가 바뀌었거나 추가 확인)",
    "login_form_not_found": "로그인 화면에서 이메일 칸을 못 찾음",
    "password_box_not_found": "이메일 다음 단계에서 비밀번호 칸을 못 찾음",
    "wrong_account": "로그인된 셀러가 계정 설정 loginId 와 다르다",
}
CDP_RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cdp_runner.mjs")


def sign_in(acc, browser, run=None):
    """Sign the dedicated profile <profile_home>/<key> in with the keychain password: the real
    browser in an off-screen window, driven over CDP with engine/login.js. -> (user data dir, cookies
    the signed-in browser read out itself). The password goes keychain -> this process -> the runner's
    stdin -> the password box only; cookies come back on the runner's stdout pipe only."""
    secret_store._check(acc["name"])
    udd = os.path.join(profile_home(), acc["name"])
    os.makedirs(udd, mode=0o700, exist_ok=True)
    svc, user = acc.get("keychainService"), acc.get("keychainAccount")
    if not svc or not user:
        raise bc.CookieError("계정 설정에 keychainService·keychainAccount 가 없다(README 4단계)", 3)
    pw = secret_store.get_secret(svc, user)
    if not pw:
        raise bc.CookieError("키체인에 비밀번호가 없다: %s / 계정 설정의 keychainAccount" % svc, 3)
    with open(os.path.join(os.path.dirname(CDP_RUNNER), "login.js"), encoding="utf-8") as fh:
        stdin = json.dumps({"cookies": [], "script": fh.read().replace("__PARAMS__", json.dumps({"email": user, "pw": pw, "loginId": acc["loginId"], "dumpCookies": True}))})
    node = os.environ.get("ALIBABA_SELLER_NODE") or shutil.which("node") or "node"
    try:
        proc = (run or subprocess.run)([node, CDP_RUNNER, "--browser", browser, "--profile-dir", udd, "--timeout", "150000"],
                   input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=200)
        lines = ((proc.stdout or "") + (proc.stderr or "")).replace(pw, "***").splitlines()
    except subprocess.TimeoutExpired:
        lines = []
    finally:
        del pw, stdin
    dump = next((l[8:] for l in lines if l.startswith("COOKIES ")), None)
    text = "\n".join(l for l in lines if not l.startswith("COOKIES "))
    status = next((l[7:].strip() for l in text.splitlines() if l.startswith("STATUS ")), None)
    if not status and "BOTBLOCK " in text:
        status = "captcha"
    if status in ("signed_in", "already_signed_in"):
        if dump is None:
            raise bc.CookieError("로그인은 됐지만 브라우저가 쿠키를 넘겨주지 않았다", 69)
        return udd, [bc.from_cdp(c) for c in json.loads(dump) if bc.is_alibaba(c.get("domain"))]
    if status:
        raise bc.CookieError("%s. 그 프로필(%s)을 화면에 띄워 직접 통과한 뒤 같은 명령을 다시" % (SIGN_IN_KO.get(status, status), udd), 3)
    raise bc.CookieError("자동 로그인 실행 실패: %s" % (text.strip().splitlines() or ["응답 없음(시간 초과)"])[-1][:200], 69)


def capture(acc, browser, profile=None, via_cdp=False, udd=None, signin=False):
    """Read the browser's cookies, check they are this seller's, store them. -> stored record.
    signin: sign the dedicated profile in (sign_in) and keep the cookies that browser read out itself."""
    if signin:
        (udd, cookies), profile, route = sign_in(acc, browser), "Default", "live"
    elif browser == "aside":
        profile = acc.get("asideAccount", "u0")
        cookies, route = read_browser(browser, profile, aside_account=profile)
    elif profile:
        cookies, route = read_browser(browser, profile, via_cdp, udd=udd)
    else:
        profile, cookies, route = choose_profile(browser, acc["loginId"], via_cdp, udd)
    state = identity(cookies, acc["loginId"])
    if state == "signed_out":
        raise bc.CookieError("%s:%s 에 셀러센터 로그인 쿠키가 없다: 그 브라우저에서 셀러센터에 먼저 로그인" % (browser, profile), 3)
    if state == "wrong_account":
        raise bc.CookieError("%s:%s 는 계정 설정 loginId 와 다른 셀러로 로그인돼 있다" % (browser, profile), 3)
    source = {"browser": browser, "profile": profile, "route": route}
    if udd:
        source["userDataDir"] = udd
    if signin:
        source["signIn"] = True
    return save(acc["name"], cookies, source)


def summary(rec):
    src = rec["source"]
    return {"cookies": len(rec["cookies"]), "domains": len({c["domain"].lstrip(".") for c in rec["cookies"]}),
            "source": "%s:%s" % (src["browser"], src["profile"]), "route": src["route"], "store": rec["store"], "savedAt": rec["savedAt"],
            **({"userDataDir": src["userDataDir"]} if src.get("userDataDir") else {}), **({"signIn": True} if src.get("signIn") else {})}


# -- contract used by the data commands -------------------------------------------------
def load_cookies(acc):
    rec = load_record(acc["name"])
    return rec["cookies"] if rec else None


def refresh_from_browser(acc):
    """Re-read the stored source. A --sign-in source opens its dedicated profile again, which renews
    a live session or signs in once more."""
    rec = load_record(acc["name"])
    src = (rec or {}).get("source")
    if not src:
        raise StoreError("쿠키 출처 없음: 먼저 login --account %s --browser chrome|edge|aside" % acc["name"])
    return capture(acc, src["browser"], src["profile"], via_cdp=src.get("route") == "cdp",
                   udd=src.get("userDataDir"), signin=bool(src.get("signIn")))["cookies"]


def session_status(acc, fetch=None):
    """{"session": valid|expired|wrong_account|none|unknown, ...}: one read GET, no values."""
    out = {"session": "none", "checked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    rec = load_record(acc["name"])
    if not rec:
        return out
    out.update(summary(rec))
    state = identity(rec["cookies"], acc["loginId"])
    if state != "ok":
        out["session"] = "expired" if state == "signed_out" else state
        return out
    final_url, status, text = (fetch or http_get)(ISLOGIN_URL, cookie_header(rec["cookies"], ISLOGIN_URL))
    out["http"] = status
    if is_login_expired(final_url, status, text):
        out["session"] = "expired"
        return out
    try:
        data = (json.loads(text).get("result") or {}).get("data")
    except (ValueError, AttributeError):
        data = None
    out["session"] = "valid" if data is True else "expired" if data is False else "unknown"
    return out


def main(argv):
    import argparse
    p = argparse.ArgumentParser(description="pipe-only: stored seller-center cookies as one JSON line")
    p.add_argument("op", choices=("load", "refresh"))
    p.add_argument("account")
    p.add_argument("--config", default=os.path.expanduser("~/.aliops-ops/seller-report/accounts.json"))
    a = p.parse_args(argv)
    if sys.stdout.isatty():
        print("파이프 전용 명령이다(쿠키 값을 화면에 안 찍는다): 상태는 alibaba-data session status", file=sys.stderr)
        return 64
    import seller_central_raw
    acc = seller_central_raw.load_account(a.config, a.account)
    try:
        cookies = load_cookies(acc) if a.op == "load" else refresh_from_browser(acc)
    except StoreError as e:
        print("오류: %s" % e, file=sys.stderr)
        return 2 if type(e) is StoreError else e.code
    if not cookies:
        print("저장된 쿠키 없음: login --account %s --browser chrome|edge|aside" % a.account, file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(cookies, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
