import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import aes  # noqa: E402
import browser_cookies as bc  # noqa: E402
import cookie_store as cs  # noqa: E402
import secret_store  # noqa: E402
import seller_central_raw as raw  # noqa: E402

HOST = ".alibaba.com"
VALUE = "fixture-cookie-value"
# MAC_V10 = "v10" + AES-128-CBC(key = PBKDF2-SHA1(b"fixture-safe-storage-password", b"saltysalt", 1003, 16),
#   iv = 16 spaces, PKCS7(SHA256(".alibaba.com") + b"fixture-cookie-value")); cross-checked with `openssl enc -d -aes-128-cbc`.
MAC_V10 = bytes.fromhex("7631309f782053f3639b1f17e9f43848ea01140abaef8089f003d125c4402719f53aff744b33f5437709ad93360e4d10"
                        "fb20aae8e824893eababdaec82fef869bd4de6")
# WIN_V10 = "v10" + nonce b"fixture-nonc" + AES-256-GCM(key = bytes(range(32)), same plaintext) + tag.
WIN_V10 = bytes.fromhex("763130666978747572652d6e6f6e63deaab5fa2ebc8ed3b4e4b877453c4d12959b3e1bbc44c456bb6b9c537e9a2470d8"
                        "3d5bc0ec132562ec3298958eb7f6e5c6b96fd3d7593dc586049825ecafa5ca3bb73fbb")
MAC_SCHEME = ("cbc", bc.mac_key(b"fixture-safe-storage-password"))
WIN_SCHEME = ("gcm", bytes(range(32)))
SECRET = "SECRETVALUE-do-not-print-7f3a"
LOGIN_ID = "fixture-seller-id"


def cookie(name, value, domain=HOST, expires=-1, **kw):
    return {"name": name, "value": value, "domain": domain, "path": "/", "secure": True, "httpOnly": True,
            "expires": expires, "sameSite": "", **kw}


SIGNED_IN = [cookie("cookie2", SECRET), cookie("ali_apache_track", "mt=1|mid=" + LOGIN_ID)]


class AesVectors(unittest.TestCase):
    def test_fips197_block(self):
        pt = bytes.fromhex("00112233445566778899aabbccddeeff")
        for key, ct in (("000102030405060708090a0b0c0d0e0f", "69c4e0d86a7b0430d8cdb78070b4c55a"),
                        ("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f", "8ea2b7ca516745bfeafc49904b496089")):
            a = aes.AES(bytes.fromhex(key))
            self.assertEqual(a.encrypt_block(pt).hex(), ct)
            self.assertEqual(a.decrypt_block(bytes.fromhex(ct)), pt)

    def test_sp800_38a_cbc_aes128_decrypt(self):
        ct = bytes.fromhex("7649abac8119b246cee98e9b12e9197d5086cb9b507219ee95db113a917678b2")
        pt = aes.cbc_decrypt(bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c"), bytes.fromhex("000102030405060708090a0b0c0d0e0f"), ct, unpad=False)
        self.assertEqual(pt.hex(), "6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51")

    def test_gcm_spec_case16_aes256(self):
        key = bytes.fromhex("feffe9928665731c6d6a8f9467308308feffe9928665731c6d6a8f9467308308")
        iv = bytes.fromhex("cafebabefacedbaddecaf888")
        aad = bytes.fromhex("feedfacedeadbeeffeedfacedeadbeefabaddad2")
        pt = bytes.fromhex("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a721c3c0c95956809532fcf0e2449a6b525"
                           "b16aedf5aa0de657ba637b39")
        out = aes.gcm_encrypt(key, iv, pt, aad)
        self.assertEqual(out.hex(), "522dc1f099567d07f47f37a32a84427d643a8cdcbfe5c0c97598a2bd2555d1aa8cb08e48590dbb3da7b08b1056828838"
                                    "c5f61e6393ba7a0abcc9f662" + "76fc6ece0f4e1768cddf8853bb2d551b")
        self.assertEqual(aes.gcm_decrypt(key, iv, out, aad), pt)
        with self.assertRaises(ValueError):
            aes.gcm_decrypt(key, iv, out[:-1] + bytes([out[-1] ^ 1]), aad)


class CookieValues(unittest.TestCase):
    def test_mac_v10_with_and_without_host_prefix(self):
        self.assertEqual(bc.decrypt_value(MAC_V10, HOST, 24, MAC_SCHEME), VALUE)
        pt = aes.cbc_decrypt(MAC_SCHEME[1], b" " * 16, MAC_V10[3:])
        self.assertEqual(pt[:32], hashlib.sha256(HOST.encode()).digest())

    def test_windows_v10_gcm(self):
        self.assertEqual(bc.decrypt_value(WIN_V10, HOST, 24, WIN_SCHEME), VALUE)
        self.assertEqual(bc.decrypt_value(b"v11" + WIN_V10[3:], HOST, 24, WIN_SCHEME), VALUE)

    def test_mac_v11_is_not_the_keychain_key(self):
        with self.assertRaises(bc.AppBoundError):
            bc.decrypt_value(b"v11" + MAC_V10[3:], HOST, 24, MAC_SCHEME)

    def test_v20_goes_to_cdp(self):
        with self.assertRaises(bc.AppBoundError):
            bc.decrypt_value(b"v20" + WIN_V10[3:], HOST, 24, WIN_SCHEME)

    def test_local_state_key_strips_dpapi_prefix(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "Local State"), "w") as fh:
                json.dump({"os_crypt": {"encrypted_key": __import__("base64").b64encode(b"DPAPIwrapped").decode()}}, fh)
            self.assertEqual(bc.win_key_from_local_state(d, unprotect=lambda b: b.upper()), b"WRAPPED")

    @unittest.skipUnless(os.name == "nt", "DPAPI exists only on Windows")
    def test_dpapi_round_trip(self):
        blob = bc.dpapi_protect(b"fixture")
        self.assertNotEqual(blob, b"fixture")
        self.assertEqual(bc.dpapi_unprotect(blob), b"fixture")


class SqliteProfile(unittest.TestCase):
    def test_reads_only_alibaba_rows_and_decrypts(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "Cookies")
            con = sqlite3.connect(path)
            con.execute("create table meta(key text, value text)")
            con.execute("insert into meta values('version', '24')")
            con.execute("create table cookies(host_key text, name text, value text, encrypted_value blob, path text, "
                        "expires_utc integer, is_secure integer, is_httponly integer, samesite integer)")
            rows = [(".alibaba.com", "xman_t", "", MAC_V10, "/", 13400000000000000, 1, 1, 1),
                    ("i.alibaba.com", "plain", "visible", b"", "/", 0, 0, 0, -1),
                    (".example.com", "other", "", MAC_V10, "/", 0, 0, 0, 0),
                    ("notalibaba.com", "lookalike", "x", b"", "/", 0, 0, 0, 0)]
            con.executemany("insert into cookies values(?,?,?,?,?,?,?,?,?)", rows)
            con.commit()
            con.close()
            got = {c["name"]: c for c in bc.read_db(path, MAC_SCHEME)}
        self.assertEqual(sorted(got), ["plain", "xman_t"])
        self.assertEqual(got["xman_t"]["value"], VALUE)
        self.assertEqual(got["xman_t"]["sameSite"], "Lax")
        self.assertAlmostEqual(got["xman_t"]["expires"], 13400000000 - bc.WIN_EPOCH_OFFSET)
        self.assertEqual(got["plain"]["expires"], -1)

    def test_profile_listing(self):
        with tempfile.TemporaryDirectory() as d:
            for p, rel in (("Profile 2", "Cookies"), ("Default", os.path.join("Network", "Cookies")), ("System Profile", "Cookies"), ("Profile 10", "Cookies")):
                os.makedirs(os.path.dirname(os.path.join(d, p, rel)), exist_ok=True)
                open(os.path.join(d, p, rel), "w").close()
            self.assertEqual(bc.profiles("chrome", d), ["Default", "Profile 2", "Profile 10"])
            self.assertTrue(bc.cookie_file(d, "Default").endswith(os.path.join("Network", "Cookies")))


class Session(unittest.TestCase):
    def test_identity(self):
        self.assertEqual(cs.identity(SIGNED_IN, LOGIN_ID), "ok")
        self.assertEqual(cs.identity(SIGNED_IN, "another-seller"), "wrong_account")
        self.assertEqual(cs.identity([cookie("cookie2", "x", expires=1)] + SIGNED_IN[1:], LOGIN_ID), "signed_out")

    def test_login_expired(self):
        self.assertTrue(cs.is_login_expired("https://login.alibaba.com/newlogin/icbuLogin.htm", 200, ""))
        self.assertTrue(cs.is_login_expired("https://i.alibaba.com/x", 200, '{"code":"NOT_LOGIN"}'))
        self.assertTrue(cs.is_login_expired("https://i.alibaba.com/x", 401, ""))
        self.assertFalse(cs.is_login_expired("https://i.alibaba.com/x", 200, '{"result":{"data":true}}'))

    def test_cookie_header_scopes_by_domain_and_expiry(self):
        jar = SIGNED_IN + [cookie("other", "o", domain="i.alibaba.com"), cookie("old", "o", expires=1)]
        h = cs.cookie_header(jar, "https://business.alibaba.com/index/isLogin")
        self.assertIn("cookie2=", h)
        self.assertNotIn("other=", h)
        self.assertNotIn("old=", h)

    def test_runner_uses_this_store(self):
        self.assertIs(raw.cookie_store, cs)
        with patch.object(cs, "load_cookies", return_value=SIGNED_IN):
            session = raw.choose_runner(raw.parse_args(["--account", "fixture"]), {"name": "fixture", "loginId": LOGIN_ID})
        self.assertEqual((session["runner"], session["cookies"]), ("chrome", SIGNED_IN))
        self.assertTrue(raw.login_expired({"status": "ok", "finalUrl": "https://i.alibaba.com/", "records": [{"url": "u", "status": 200, "text": "NOT_LOGIN"}]}))


class FakeKeychain:
    def __init__(self):
        self.items = {}

    def get(self, service, account):
        return self.items.get((service, account))

    def set(self, service, account, value):
        self.items[(service, account)] = value
        return "keychain"


class Stored(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cfg = os.path.join(self.dir, "accounts.json")
        with open(self.cfg, "w") as fh:
            json.dump({"fixture": {"loginId": LOGIN_ID}}, fh)
        kc = FakeKeychain()
        for p in (patch.dict(os.environ, {"ALIBABA_SELLER_COOKIE_DIR": os.path.join(self.dir, "cookies")}),
                  patch.object(secret_store, "get_secret", kc.get), patch.object(secret_store, "set_secret", kc.set)):
            p.start()
            self.addCleanup(p.stop)

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = raw.main(["--account", "fixture", "--config", self.cfg, *args])
        return code, out.getvalue(), err.getvalue()

    def test_login_and_status_never_print_values(self):
        with patch.object(bc, "read_profile", return_value=SIGNED_IN):
            code, out, err = self.run_main("--browser-login", "chrome", "--profile", "Default")
        self.assertEqual(code, 0)
        summary = json.loads(out)
        self.assertEqual((summary["cookies"], summary["source"], summary["route"]), (2, "chrome:Default", "sqlite"))
        with patch.object(cs, "http_get", return_value=("https://business.alibaba.com/index/isLogin", 200, '{"result":{"data":true}}')):
            code2, out2, err2 = self.run_main("--session-status")
        self.assertEqual((code2, json.loads(out2)["session"]), (0, "valid"))
        with patch.object(bc, "read_profile", return_value=[cookie("cookie2", SECRET)]):
            code3, out3, err3 = self.run_main("--browser-login", "chrome", "--profile", "Default")
        self.assertEqual(code3, 3)
        for text in (out, err, out2, err2, out3, err3):
            self.assertNotIn(SECRET, text)
        for name in os.listdir(os.path.join(self.dir, "cookies")):
            with open(os.path.join(self.dir, "cookies", name), "rb") as fh:
                stored = fh.read()
            if name.endswith(".bin"):
                self.assertNotIn(SECRET.encode(), stored)

    def test_status_none_and_expired(self):
        self.assertEqual(cs.session_status({"name": "fixture", "loginId": LOGIN_ID})["session"], "none")
        cs.save("fixture", SIGNED_IN, {"browser": "chrome", "profile": "Default", "route": "sqlite"})
        st = cs.session_status({"name": "fixture", "loginId": LOGIN_ID}, fetch=lambda u, c: ("https://login.alibaba.com/x", 302, ""))
        self.assertEqual(st["session"], "expired")
        self.assertNotIn(SECRET, json.dumps(st))

    def test_pipe_cli_load(self):
        cs.save("fixture", SIGNED_IN, {"browser": "chrome", "profile": "Default", "route": "sqlite"})
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(sys.stdout, "isatty", return_value=False, create=True):
            self.assertEqual(cs.main(["load", "fixture", "--config", self.cfg]), 0)
        self.assertEqual(json.loads(out.getvalue()), SIGNED_IN)
        os.remove(cs.cookie_path("fixture") if os.path.exists(cs.cookie_path("fixture")) else cs.cookie_path("fixture", ".json"))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cs.main(["load", "fixture", "--config", self.cfg]), 2)
            self.assertEqual(cs.main(["refresh", "fixture", "--config", self.cfg]), 2)

    def test_keychain_timeout_stores_owner_only_file_and_newest_wins(self):
        with patch.object(cs, "_seal", side_effect=secret_store.KeychainTimeout("timeout")), contextlib.redirect_stderr(io.StringIO()) as err:
            rec = cs.save("fixture", SIGNED_IN, {"browser": "chrome", "profile": "Default", "route": "cdp"})
        self.assertEqual(rec["store"], "file600")
        path = cs.cookie_path("fixture", ".json")
        self.assertIn(path, err.getvalue())
        if os.name != "nt":
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(cs.load_record("fixture")["store"], "file600")
        with open(cs.cookie_path("fixture"), "wb") as fh:
            fh.write(b"XX1older")
        os.utime(cs.cookie_path("fixture"), (1, 1))
        self.assertEqual(cs.load_cookies({"name": "fixture"}), SIGNED_IN)


class KeychainTimeout(unittest.TestCase):
    def test_security_that_never_answers_fails_fast_with_guidance(self):
        with patch.object(secret_store, "SECURITY_CMD", [sys.executable, "-c", "import time; time.sleep(60)"]), \
                patch.dict(os.environ, {"ALIBABA_SELLER_KEYCHAIN_TIMEOUT": "0.5"}):
            t = time.time()
            with self.assertRaises(secret_store.KeychainTimeout) as e:
                secret_store.security(["find-generic-password", "-w", "-s", "x"], fallback="CDP")
            self.assertLess(time.time() - t, 10)
        self.assertEqual(e.exception.code, 69)
        self.assertIn("unlock-keychain", str(e.exception))
        self.assertIn("CDP", str(e.exception))

    def test_cookie_read_falls_back_to_cdp(self):
        with patch.object(bc, "read_profile", side_effect=secret_store.KeychainTimeout("t")), \
                patch.object(bc, "read_via_cdp", return_value=SIGNED_IN) as cdp, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cs.read_browser("chrome", "Default"), (SIGNED_IN, "cdp"))
        cdp.assert_called_once_with("chrome", "Default", None)

    def test_password_login_exits_69(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "accounts.json")
            with open(cfg, "w") as fh:
                json.dump({"fixture": {"loginId": LOGIN_ID, "keychainService": "svc", "keychainAccount": "acct"}}, fh)
            with patch.object(secret_store, "get_secret", side_effect=secret_store.KeychainTimeout("t")), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(raw.main(["--account", "fixture", "--config", cfg, "--login"]), 69)
            self.assertIn("keychain_timeout", out.getvalue())


PASSWORD = "fixture-password-never-printed-4c1d"


class SignIn(unittest.TestCase):
    """login --sign-in: a dedicated profile signed in by engine/login.js, then read from its cookie file."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cfg = os.path.join(self.dir, "accounts.json")
        with open(self.cfg, "w") as fh:
            json.dump({"fixture": {"loginId": LOGIN_ID, "keychainService": "svc", "keychainAccount": "acct"}}, fh)
        kc = FakeKeychain()
        kc.items[("svc", "acct")] = PASSWORD
        self.runs = []
        live = [{"name": c["name"], "value": c["value"], "domain": c["domain"], "path": "/", "expires": -1, "session": True} for c in SIGNED_IN]
        self.reply = "COOKIES " + json.dumps(live + [{"name": "x", "value": "y", "domain": ".example.com"}]) + "\nSTATUS signed_in\n"
        for p in (patch.dict(os.environ, {"ALIBABA_SELLER_COOKIE_DIR": os.path.join(self.dir, "cookies")}),
                  patch.object(cs, "profile_home", return_value=os.path.join(self.dir, "profiles")),
                  patch.object(secret_store, "get_secret", kc.get), patch.object(secret_store, "set_secret", kc.set),
                  patch.object(cs.subprocess, "run", self.fake_run)):
            p.start()
            self.addCleanup(p.stop)
        self.udd = os.path.join(self.dir, "profiles", "fixture")

    def fake_run(self, argv, **kw):
        self.runs.append((argv, kw["input"]))
        return type("P", (), {"stdout": self.reply.replace("__PW__", PASSWORD), "stderr": "", "returncode": 0})()

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = raw.main(["--account", "fixture", "--config", self.cfg, *args])
        return code, out.getvalue(), err.getvalue()

    def test_signs_in_keeps_the_live_browser_cookies_and_remembers_the_profile(self):
        with patch.object(bc, "read_profile") as rp:
            code, out, err = self.run_main("--browser-login", "chrome", "--sign-in")
        self.assertEqual(code, 0, err)
        rp.assert_not_called()
        summary = json.loads(out)
        self.assertEqual((summary["cookies"], summary["route"], summary["userDataDir"], summary["signIn"]), (2, "live", self.udd, True))
        self.assertEqual(cs.load_cookies({"name": "fixture"})[0]["value"], SECRET)
        (argv, stdin), = self.runs
        self.assertEqual(argv[1:], [cs.CDP_RUNNER, "--browser", "chrome", "--profile-dir", self.udd, "--timeout", "150000"])
        self.assertNotIn(PASSWORD, " ".join(argv))
        self.assertIn(PASSWORD, json.loads(stdin)["script"])  # only on the runner's stdin
        self.assertIn('"dumpCookies": true', json.loads(stdin)["script"])
        self.assertTrue(os.path.isdir(self.udd))
        for text in (out, err):
            self.assertNotIn(PASSWORD, text)
            self.assertNotIn(SECRET, text)
        # refresh: same dedicated profile, signed in once more
        acc = {"name": "fixture", "loginId": LOGIN_ID, "keychainService": "svc", "keychainAccount": "acct"}
        self.assertEqual([c["name"] for c in cs.refresh_from_browser(acc)], ["cookie2", "ali_apache_track"])
        self.assertEqual(len(self.runs), 2)
        self.assertEqual(self.runs[1][0][self.runs[1][0].index("--profile-dir") + 1], self.udd)
        self.reply = "STATUS signed_in\n"  # signed in, but no cookies handed over
        with self.assertRaises(bc.CookieError) as e:
            cs.refresh_from_browser(acc)
        self.assertEqual(e.exception.code, 69)

    def test_blocked_sign_in_is_exit_3_with_korean_reason_and_no_password(self):
        for reply, word in (("STATUS captcha\n", "캡차"), ('BOTBLOCK {"reason":"slider"}\n', "캡차"),
                            ("STATUS two_factor\n", "2단계"), ("echoed __PW__\nSTATUS still_on_login\n", "로그인 화면")):
            self.reply = reply
            with patch.object(bc, "read_profile") as rp:
                code, out, err = self.run_main("--browser-login", "chrome", "--sign-in")
            self.assertEqual(code, 3, reply)
            self.assertIn(word, out + err)
            self.assertIn(self.udd, out + err)
            self.assertNotIn(PASSWORD, out + err)
            rp.assert_not_called()
        self.reply = "cdp_runner: Chrome/Edge not found __PW__\n"
        code, out, err = self.run_main("--browser-login", "chrome", "--sign-in")
        self.assertEqual(code, 69)
        self.assertNotIn(PASSWORD, out + err)

    def test_user_data_dir_and_aside_guard(self):
        other = os.path.join(self.dir, "other-chrome")
        with patch.object(bc, "profiles", return_value=["Default"]) as pr, patch.object(bc, "read_profile", return_value=SIGNED_IN) as rp:
            code, out, err = self.run_main("--browser-login", "chrome", "--user-data-dir", other)
        self.assertEqual(code, 0, err)
        pr.assert_called_once_with("chrome", other)
        rp.assert_called_once_with("chrome", "Default", other)
        self.assertEqual(json.loads(out)["userDataDir"], other)
        self.assertNotIn("signIn", json.loads(out))
        code, out, err = self.run_main("--browser-login", "aside", "--sign-in")
        self.assertNotEqual(code, 0)
        self.assertIn("--sign-in", out + err)
        self.assertEqual(self.runs, [])

    def test_missing_password_is_exit_3(self):
        with open(self.cfg, "w") as fh:
            json.dump({"fixture": {"loginId": LOGIN_ID, "keychainService": "svc", "keychainAccount": "nobody"}}, fh)
        code, out, err = self.run_main("--browser-login", "chrome", "--sign-in")
        self.assertEqual(code, 3)
        self.assertIn("키체인", out + err)
        self.assertEqual(self.runs, [])


class AsideRoute(unittest.TestCase):
    def test_reads_cookies_line_from_repl(self):
        line = "COOKIES " + json.dumps([{"name": "cookie2", "value": SECRET, "domain": ".alibaba.com", "path": "/", "expires": -1, "session": True},
                                        {"name": "x", "value": "y", "domain": ".example.com"}])
        seen = {}

        def run(argv, **kw):
            seen["argv"], seen["input"] = argv, kw["input"]
            return type("P", (), {"stdout": "noise\n" + line + "\n", "returncode": 0})()
        got = bc.read_via_aside("u1", run=run)
        self.assertEqual([c["name"] for c in got], ["cookie2"])
        self.assertEqual(seen["argv"][1:], ["--host", "local", "--account", "u1", "repl"])
        self.assertIn("context().cookies()", seen["input"])
        with self.assertRaises(bc.CookieError):
            bc.read_via_aside("u1", run=lambda *a, **k: type("P", (), {"stdout": "", "returncode": 1})())


if __name__ == "__main__":
    unittest.main()
