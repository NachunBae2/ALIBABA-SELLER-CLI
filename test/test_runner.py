"""Runner choice and the one-time cookie re-read in seller_central_raw (fake cookie_store, fake browser)."""
import importlib.util
import io
import json
import re
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("seller_raw_runner", Path(__file__).resolve().parent.parent / "engine" / "seller_central_raw.py")
raw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(raw)

SECRET = "cookie-value-fixture-7q2w"  # synthetic
COOKIES = [{"name": "fixture_sid", "value": SECRET, "domain": ".alibaba.com", "path": "/"}]
FRESH = [{"name": "fixture_sid", "value": SECRET + "-fresh", "domain": ".alibaba.com", "path": "/"}]


def fake_store(cookies=COOKIES, refreshed=FRESH):
    m = types.ModuleType("cookie_store")
    m.calls = []
    m.load_cookies = lambda acc: m.calls.append(("load", acc["name"])) or cookies
    m.refresh_from_browser = lambda acc: m.calls.append(("refresh", acc["name"])) or refreshed
    m.is_login_expired = lambda url, status, text: "not login" in (text or "")
    return m


def browser(*final_urls, blocked=False):
    """subprocess.run stand-in: writes capture.js's out file with the next finalUrl, records each call."""
    calls = []
    urls = list(final_urls)

    def run(cmd, input=None, **kw):
        calls.append({"cmd": cmd, "input": input})
        src = json.loads(input)["script"] if input else cmd[-1]
        if blocked:
            return subprocess.CompletedProcess(cmd, 5, 'BOTBLOCK {"reason":"slider","url":"https://www.alibaba.com/punish","headed":true}\n', "")
        out_file = json.loads(re.search(r"const P = (\{.*?\});\n", src).group(1))["outFile"]
        url = urls.pop(0) if len(urls) > 1 else urls[0]
        status = "session_expired" if "login." in url else "ok"
        Path(out_file).write_text(json.dumps({"key": "home", "status": status, "finalUrl": url, "records": [], "notes": []}))
        return subprocess.CompletedProcess(cmd, 0, "STATUS %s\n" % status, "")
    return run, calls


class Runner(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cfg = Path(self.dir.name) / "accounts.json"
        self.cfg.write_text(json.dumps({"fixture": {"loginId": "fixture"}}))

    def tearDown(self):
        sys.modules.pop("cookie_store", None)
        self.dir.cleanup()

    def main(self, *extra, store=None, run=None):
        if store is not None:
            sys.modules["cookie_store"] = store
        out, err = io.StringIO(), io.StringIO()
        with patch.object(raw.subprocess, "run", side_effect=run), redirect_stdout(out), redirect_stderr(err):
            code = raw.main(["--account", "fixture", "--config", str(self.cfg), "--pages", "home", "--out", str(Path(self.dir.name) / "o"), *extra])
        return code, out.getvalue(), err.getvalue()

    def test_expired_cookies_are_reread_once_then_the_page_runs(self):
        store = fake_store()
        run, calls = browser("https://login.alibaba.com/newlogin", "https://i.alibaba.com/home")
        code, out, err = self.main("--runner", "chrome", store=store, run=run)
        self.assertEqual(code, 0)
        self.assertEqual(store.calls, [("load", "fixture"), ("refresh", "fixture")])
        self.assertEqual([c["cmd"][1:] for c in calls], [[raw.CDP_RUNNER, "--browser", "chrome"]] * 2)
        self.assertEqual([json.loads(c["input"])["cookies"] for c in calls], [COOKIES, FRESH])
        for c in calls:
            self.assertNotIn(SECRET, " ".join(c["cmd"]))
        self.assertNotIn(SECRET, out + err)

    def test_still_expired_after_one_reread_is_exit_3_with_its_own_reason(self):
        store = fake_store()
        run, calls = browser("https://login.alibaba.com/newlogin")
        code, out, err = self.main("--runner", "edge", store=store, run=run)
        self.assertEqual(code, raw.EXIT_LOGIN)
        self.assertEqual(len(calls), 2)
        self.assertEqual(store.calls.count(("refresh", "fixture")), 1)
        self.assertIn("still expired after one re-read", err)
        self.assertNotIn(SECRET, out + err)

    def test_api_not_login_answer_counts_as_expired(self):
        sys.modules["cookie_store"] = fake_store()
        self.assertTrue(raw.login_expired({"status": "ok", "finalUrl": "https://i.alibaba.com/", "records": [{"url": "u", "status": 200, "text": '{"msg":"not login"}'}]}))
        self.assertFalse(raw.login_expired({"status": "ok", "finalUrl": "https://i.alibaba.com/", "records": []}))
        self.assertTrue(raw.login_expired({"status": "ok", "finalUrl": "https://passport.alibaba.com/x"}))

    def test_auto_uses_chrome_only_with_saved_cookies(self):
        args = raw.parse_args(["--account", "fixture"])
        acc = raw.load_account(self.cfg, "fixture")
        sys.modules["cookie_store"] = fake_store()
        self.assertEqual(raw.choose_runner(args, acc)["runner"], "chrome")
        sys.modules["cookie_store"] = fake_store(cookies=None)
        self.assertEqual(raw.choose_runner(args, acc)["runner"], "aside")
        sys.modules["cookie_store"] = None  # module not installed yet
        with redirect_stderr(io.StringIO()):
            self.assertEqual(raw.choose_runner(args, acc)["runner"], "aside")
        self.assertEqual(raw.choose_runner(args, {**acc, "runner": "aside"})["runner"], "aside")

    def test_explicit_browser_without_saved_cookies_needs_login(self):
        run, calls = browser("https://i.alibaba.com/home")
        code, out, err = self.main("--runner", "chrome", store=fake_store(cookies=None), run=run)
        self.assertEqual((code, calls), (raw.EXIT_LOGIN, []))
        self.assertIn("login --account fixture --browser chrome", err)

    def test_aside_runner_keeps_the_aside_command(self):
        run, calls = browser("https://i.alibaba.com/home")
        with patch.dict(raw.os.environ, {"ALIBABA_SELLER_ASIDE": "aside-fixture", "ALIBABA_SELLER_ASIDE_HOME": self.dir.name}):
            code, _, _ = self.main("--runner", "aside", run=run)
        self.assertEqual(code, 0)
        self.assertEqual(calls[0]["cmd"][:5], ["aside-fixture", "--host", "local", "--account", "u0"])
        self.assertIsNone(calls[0]["input"])

    def test_bot_check_is_reported_not_hidden(self):
        run, _ = browser("x", blocked=True)
        code, out, err = self.main("--runner", "chrome", store=fake_store(), run=run)
        self.assertEqual(code, raw.EXIT_BLOCKED)
        self.assertIn("bot_blocked", out)
        self.assertIn("bot check", err)

    def test_unknown_runner_in_account_is_refused(self):
        self.cfg.write_text(json.dumps({"fixture": {"loginId": "fixture", "runner": "firefox"}}))
        with self.assertRaises(SystemExit):
            raw.load_account(self.cfg, "fixture")


if __name__ == "__main__":
    unittest.main()
