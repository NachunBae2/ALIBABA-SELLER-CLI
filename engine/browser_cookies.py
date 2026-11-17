"""Read the alibaba.com cookies of a Chrome / Edge profile (or the Aside browser) into plain dicts.

  Cookie = {name, value, domain, path, secure, httpOnly, expires (unix s, -1 = session), sameSite}

  macOS    <profile>/Cookies SQLite, values "v10" + AES-128-CBC. Key = PBKDF2-SHA1(keychain
           "<Browser> Safe Storage", "saltysalt", 1003, 16), IV = 16 spaces.
  Windows  <profile>\\Network\\Cookies, values "v10"/"v11" + AES-256-GCM (nonce 12, tag 16).
           Key = DPAPI(Local State os_crypt.encrypted_key minus "DPAPI").
           "v20" (Chrome 127+ app-bound encryption) is not decryptable here: read_via_cdp starts
           the browser itself headless on a copy of the profile and asks it over CDP instead.
  both     cookie DB meta version >= 24 prefixes every plaintext with SHA256(host_key) (32 bytes).

Values only ever live in memory; nothing here prints them.
"""
import base64
import ctypes
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

import aes
import secret_store

HERE = os.path.dirname(os.path.abspath(__file__))
SAMESITE = {-1: "", 0: "None", 1: "Lax", 2: "Strict"}
WIN_EPOCH_OFFSET = 11644473600  # seconds from 1601-01-01 (Chrome time) to 1970-01-01


class CookieError(secret_store.StoreError):
    """User-facing failure. code = process exit code (3 not signed in, 64 ambiguous, 69 locked)."""

    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


class AppBoundError(CookieError):
    """v20 values: only the browser itself can decrypt them (CDP fallback)."""


class LockedError(CookieError):
    """The browser holds the cookie file open (Windows): CDP fallback, else close it."""


BROWSERS = {
    "chrome": {"label": "Chrome", "mac": "Google/Chrome", "keychain": "Chrome Safe Storage",
               "win": r"Google\Chrome\User Data",
               "mac_exe": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
               "win_exe": [r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
                           r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
                           r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"]},
    "edge": {"label": "Edge", "mac": "Microsoft Edge", "keychain": "Microsoft Edge Safe Storage",
             "win": r"Microsoft\Edge\User Data",
             "mac_exe": ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
             "win_exe": [r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe",
                         r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"]},
    # Aside is Chromium: its profiles keep the same cookie DB, keyed by "Aside Safe Storage".
    # Its repl has no browser-context cookie API (page.context() is absent), so the file is the only source.
    "aside": {"label": "Aside", "mac": "Aside", "keychain": "Aside Safe Storage", "win": None,
              "mac_exe": ["/Applications/Aside.app/Contents/MacOS/Aside"], "win_exe": []},
}


def browser_spec(name):
    if name not in BROWSERS:
        raise CookieError("--browser 는 %s" % "|".join(BROWSERS), 64)
    return BROWSERS[name]


def user_data_dir(name):
    spec = browser_spec(name)
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/" + spec["mac"])
    if os.name == "nt" and spec["win"]:
        return os.path.join(os.environ.get("LOCALAPPDATA", ""), spec["win"])
    raise CookieError("%s 쿠키 읽기는 %s 만 된다" % (spec["label"], "macOS·Windows" if spec["win"] else "macOS"), 1)


def cookie_file(udd, profile):
    for rel in (os.path.join("Network", "Cookies"), "Cookies"):
        p = os.path.join(udd, profile, rel)
        if os.path.isfile(p):
            return p
    return None


def profiles(name, udd=None):
    """Profile folder names that have a cookie DB, Default first."""
    udd = udd or user_data_dir(name)
    if not os.path.isdir(udd):
        return []
    names = [d for d in os.listdir(udd) if (d == "Default" or d.startswith("Profile ")) and cookie_file(udd, d)]
    return sorted(names, key=lambda d: (d != "Default", int(d.split()[-1]) if d.split()[-1].isdigit() else 0))


def is_alibaba(host):
    h = (host or "").lstrip(".").lower()
    return h == "alibaba.com" or h.endswith(".alibaba.com")


# -- keys ---------------------------------------------------------------------------
def mac_key(password):
    return hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, 16)


def mac_keychain_key(name):
    service = browser_spec(name)["keychain"]
    r = secret_store.security(["find-generic-password", "-w", "-s", service], fallback="브라우저 자신에게 CDP 로 받는 경로(--via-cdp)")
    if r.returncode != 0 or not r.stdout.strip():
        raise CookieError("키체인 항목 '%s' 을 못 읽음(그 브라우저를 한 번 켜서 로그인했는지 확인)" % service, 1)
    return mac_key(r.stdout.strip().encode())


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = _Blob(len(data), buf), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise CookieError("DPAPI %s 실패(같은 Windows 사용자로 실행해야 한다)" % ("암호화" if protect else "복호"), 1)
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def dpapi_protect(data):
    return _dpapi(data, True)


def dpapi_unprotect(data):
    return _dpapi(data, False)


def win_key_from_local_state(udd, unprotect=dpapi_unprotect):
    with open(os.path.join(udd, "Local State"), encoding="utf-8") as fh:
        enc = base64.b64decode(json.load(fh)["os_crypt"]["encrypted_key"])
    if enc[:5] != b"DPAPI":
        raise CookieError("Local State 키 형식을 모름", 1)
    return unprotect(enc[5:])


# -- values -------------------------------------------------------------------------
def decrypt_value(enc, host, version, scheme):
    """scheme = ("cbc", 16-byte key) on macOS, ("gcm", 32-byte key) on Windows."""
    prefix, (mode, key) = bytes(enc[:3]), scheme
    if prefix == b"v20":
        raise AppBoundError("v20(앱 바인딩 암호화) 쿠키", 1)
    if mode == "cbc" and prefix == b"v11":
        # Measured with Chrome 158 on macOS: a newly made profile writes v11, and neither the
        # "Chrome Safe Storage" password nor anything derived from it opens it.
        raise AppBoundError("v11(맥 크롬 새 프로필 암호화, 키체인 비밀번호로 안 열림) 쿠키", 1)
    wrong_key = CookieError("복호화 키가 맞지 않음(키체인·Local State 키가 이 쿠키 파일의 것이 아니다)", 69)
    try:
        if mode == "cbc" and prefix == b"v10":
            pt = aes.cbc_decrypt(key, b" " * 16, bytes(enc[3:]))
        elif mode == "gcm" and prefix in (b"v10", b"v11"):
            pt = aes.gcm_decrypt(key, bytes(enc[3:15]), bytes(enc[15:]))
        else:
            raise CookieError("모르는 쿠키 암호 형식", 1)
    except ValueError:  # bad padding (CBC) or tag mismatch (GCM)
        raise wrong_key from None
    if version >= 24:
        # The DB says every plaintext starts with SHA256(host_key): a mismatch means the wrong key.
        if pt[:32] != hashlib.sha256(host.encode()).digest():
            raise wrong_key
        pt = pt[32:]
    try:
        return pt.decode("utf-8")
    except UnicodeDecodeError:
        raise wrong_key from None


def chrome_time(us):
    return -1 if not us else round(us / 1e6 - WIN_EPOCH_OFFSET, 3)


def read_db(path, scheme):
    """Alibaba cookies of one cookie DB. Reads a temp copy (works while the browser runs on macOS;
    on Windows a running browser locks the file -> LockedError)."""
    tmp = tempfile.mkdtemp(prefix="asc-ck-")
    try:
        copy = os.path.join(tmp, "Cookies")
        try:
            shutil.copyfile(path, copy)
        except PermissionError:
            raise LockedError("브라우저가 켜져 있어 쿠키 파일이 잠겨 있다", 69)
        con = sqlite3.connect(copy)
        try:
            row = con.execute("select value from meta where key='version'").fetchone()
            version = int(row[0]) if row else 0
            rows = con.execute("select host_key, name, value, encrypted_value, path, expires_utc, is_secure, is_httponly, samesite "
                               "from cookies where host_key like '%alibaba.com'").fetchall()
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out = []
    for host, name, value, enc, cpath, exp, sec, http, ss in rows:
        if not is_alibaba(host):
            continue
        out.append({"name": name, "value": value or (decrypt_value(enc, host, version, scheme) if enc else ""),
                    "domain": host, "path": cpath, "secure": bool(sec), "httpOnly": bool(http),
                    "expires": chrome_time(exp), "sameSite": SAMESITE.get(ss, "")})
    return out


def read_profile(name, profile, udd=None):
    """Alibaba cookies of <browser>/<profile> straight from its SQLite file. udd = another user data dir."""
    udd = udd or user_data_dir(name)
    path = cookie_file(udd, profile)
    if not path:
        raise CookieError("%s 프로필 '%s' 에 쿠키 파일이 없다 (있는 프로필: %s)" % (browser_spec(name)["label"], profile, ", ".join(profiles(name, udd)) or "없음"), 64)
    if sys.platform == "darwin":
        scheme = ("cbc", mac_keychain_key(name))
    else:
        scheme = ("gcm", win_key_from_local_state(udd))
    return read_db(path, scheme)


# -- CDP fallback ---------------------------------------------------------------------
def browser_exe(name):
    spec = browser_spec(name)
    cands = spec["mac_exe"] if sys.platform == "darwin" else [os.path.expandvars(p) for p in spec["win_exe"]]
    exe = next((p for p in cands if os.path.isfile(p)), None)
    if not exe:
        raise CookieError("%s 실행 파일을 못 찾음: %s" % (spec["label"], ", ".join(cands)), 1)
    return exe


def from_cdp(c):
    return {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c.get("path", "/"),
            "secure": bool(c.get("secure")), "httpOnly": bool(c.get("httpOnly")),
            "expires": -1 if c.get("session") or (c.get("expires") or -1) < 0 else c["expires"],
            "sameSite": c.get("sameSite") or ""}


def read_via_cdp(name, profile, udd=None):
    """Start the browser headless on a temp copy of the profile (Local State + cookie DB +
    Preferences) and read Storage.getCookies over CDP: the browser decrypts its own cookies,
    v20 included. Values come back on the helper's stdout pipe only."""
    udd = udd or user_data_dir(name)
    src = cookie_file(udd, profile)
    if not src:
        raise CookieError("%s 프로필 '%s' 에 쿠키 파일이 없다" % (browser_spec(name)["label"], profile), 64)
    exe = browser_exe(name)
    tmp = tempfile.mkdtemp(prefix="asc-cdp-")
    try:
        dst_profile = os.path.join(tmp, "Default")
        dst = os.path.join(dst_profile, os.path.relpath(src, os.path.join(udd, profile)))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.copyfile(src, dst)
            for rel_src, rel_dst in (("Local State", os.path.join(tmp, "Local State")),
                                     (os.path.join(profile, "Preferences"), os.path.join(dst_profile, "Preferences"))):
                if os.path.isfile(os.path.join(udd, rel_src)):
                    shutil.copyfile(os.path.join(udd, rel_src), rel_dst)
        except PermissionError:
            raise LockedError("%s 가 켜져 있어 쿠키 파일을 복사할 수 없다: %s 를 완전히 닫고 다시 실행" % ((browser_spec(name)["label"],) * 2), 69)
        node = os.environ.get("ALIBABA_SELLER_NODE") or shutil.which("node") or "node"
        r = subprocess.run([node, os.path.join(HERE, "cdp_cookies.mjs"), exe, tmp], capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            raise CookieError("CDP 쿠키 읽기 실패: %s" % (r.stderr.strip().splitlines() or ["?"])[-1][:200], 1)
        got = json.loads(r.stdout)
        if not got["total"]:
            # measured 2026-11-17 on macOS Chrome: it loads the copied store, then deletes every cookie
            raise CookieError("%s 가 복사본 프로필에서 쿠키를 0개 돌려줌(macOS 크롬은 복사본 쿠키를 지운다): "
                              "키체인을 풀고 기본 경로(--via-cdp 없이)로 다시" % browser_spec(name)["label"], 69)
        return [from_cdp(c) for c in got["cookies"] if is_alibaba(c.get("domain"))]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
