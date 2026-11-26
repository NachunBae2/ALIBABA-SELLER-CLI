#!/usr/bin/env python3
"""The gateway server without a gateway: a fake alibaba-data CLI (test/fixtures/gateway-fake-cli.py)."""
import json
import os
import threading
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), "accio", "gateway-server", "mcp_server.py")
FAKE_CLI = os.path.join(HERE, "fixtures", "gateway-fake-cli.py")
sys.path.insert(0, os.path.dirname(SERVER))
import mcp_server as srv  # noqa: E402


def _temp_server():
    """A copy of the server in its own folder with commands.json (and config.json written by the caller)."""
    tmp = tempfile.mkdtemp(prefix="gw-srv-")
    server = os.path.join(tmp, "mcp_server.py")
    shutil.copy(SERVER, server)
    return tmp, server


def _conf(tmp, cli=FAKE_CLI):
    path = os.path.join(tmp, "config.json")
    with open(path, "w") as fh:
        json.dump({"SELLER_CLI": cli, "NODE": sys.executable}, fh)
    return path


def _ask(proc, line, want=True):
    proc.stdin.write(json.dumps(line) + "\n")
    proc.stdin.flush()
    if not want:
        return None
    # a reader thread, not select(): Windows select() takes sockets only, not pipes
    got = []
    t = threading.Thread(target=lambda: got.append(proc.stdout.readline()), daemon=True)
    t.start()
    t.join(60)
    assert got and got[0], "server gave no answer"
    return json.loads(got[0])


def _inner(answer):
    # in-process dispatch returns the tool result dict; the stdio server wraps it in a JSON-RPC answer
    if "result" in answer:
        answer = answer["result"]
    return json.loads(answer["content"][0]["text"])


CALL = lambda i, name, **args: {"jsonrpc": "2.0", "id": i, "method": "tools/call",  # noqa: E731
                                "params": {"name": name, "arguments": args}}


class TestDispatch(unittest.TestCase):
    """dispatch() in-process: allowlist, accounts, dates, what reaches the CLI (no subprocess)."""

    def setUp(self):
        self.tmp, _server = _temp_server()
        with open(os.path.join(self.tmp, "commands.json"), "w") as fh:
            json.dump(["ads report", "ads tables", "analytics products"], fh)
        srv._READ = None
        self.p = [patch.object(srv, "HERE", self.tmp), patch.object(srv, "RUNS", os.path.join(self.tmp, "runs")),
                  patch.object(srv, "_config", lambda: {"SELLER_CLI": FAKE_CLI, "NODE": sys.executable}),
                  patch.object(srv, "started", wraps=srv.started), patch.object(srv, "WAIT", 5)]
        for x in self.p:
            x.start()
        srv.JOBS.clear()

    def tearDown(self):
        for x in self.p:
            x.stop()
        srv._READ = None
        srv.JOBS.clear()

    def test_write_and_unknown_commands_are_refused_before_any_run(self):
        for cmd in ("ads set-bid", "inquiries reply", "store summary --write", "", "ads report; rm -rf /"):
            self.assertEqual(_inner(srv.read({"command": cmd}))["reason"], "not_a_read_command", cmd)
        self.assertFalse(srv.JOBS)
        srv.started.assert_not_called()

    def test_blocked_accounts_and_bad_account_names(self):
        for acct in ("wonnischoice", "linea-shop", "Wonnischoice", "a b"):
            self.assertEqual(_inner(srv.read({"command": "ads report", "account": acct}))["reason"], "account_refused", acct)
        self.assertFalse(srv.JOBS)

    def test_dates_must_be_yyyy_mm_dd(self):
        self.assertEqual(_inner(srv.read({"command": "ads report", "start": "2026-7-1"}))["reason"], "bad_date")
        self.assertFalse(srv.JOBS)

    def test_allowed_read_starts_the_cli_with_transport_api(self):
        out = _inner(srv.read({"command": "analytics products", "account": "thedozers", "start": "2026-07-01", "end": "2026-09-30"}))
        self.assertEqual(out["state"], "done")  # the fake CLI answers in a moment
        self.assertEqual(out["rc"], 0)
        job, argv = srv.started.call_args[0][:2]
        self.assertRegex(job, r"^[0-9a-f]{12}$")
        self.assertEqual(argv[:4], [sys.executable, FAKE_CLI, "analytics", "products"])
        self.assertEqual(argv[argv.index("--transport") + 1], "api")
        self.assertEqual(argv[argv.index("--start") + 1], "2026-07-01")
        self.assertEqual(argv[argv.index("--end") + 1], "2026-09-30")
        self.assertEqual(argv[argv.index("--account") + 1], "thedozers")
        self.assertIn("--json", argv)

    def test_default_account_is_thedozers(self):
        srv.read({"command": "ads report"})
        self.assertIn("thedozers", srv.started.call_args[0][1])

    def test_missing_cli_file_is_an_error_not_a_run(self):
        with patch.object(srv, "_config", lambda: {"SELLER_CLI": os.path.join(self.tmp, "nope.mjs"), "NODE": sys.executable}):
            self.assertEqual(_inner(srv.read({"command": "ads report"}))["reason"], "cli_missing")
        self.assertFalse(srv.JOBS)

    def test_missing_commands_file_refuses_everything(self):
        os.remove(os.path.join(self.tmp, "commands.json"))
        srv._READ = None
        self.assertEqual(_inner(srv.read({"command": "ads report"}))["reason"], "not_a_read_command")

    def test_unknown_tool_is_an_error_answer(self):
        out = srv.dispatch({"method": "tools/call", "params": {"name": "anything_else", "arguments": {}}})
        self.assertTrue(out["isError"])
        self.assertEqual(_inner(out)["reason"], "unknown_tool")


class TestProcess(unittest.TestCase):
    """The real stdio server: notification silence, tools/list, running job, result, rc, stderr."""

    def test_large_answer_is_left_in_runs_file_instead_of_a_cut_string(self):
        tmp, server = _temp_server()
        conf = _conf(tmp)
        with open(os.path.join(tmp, "commands.json"), "w") as fh:
            json.dump(["ads report"], fh)
        with open(server) as fh:
            text = fh.read()
        with open(server, "w") as fh:
            fh.write(text.replace("WAIT = 25", "WAIT = 2"))
        env = dict(os.environ, SELLER_GW_CONFIG=conf, FAKE_WAIT_MS="3000", FAKE_PAD="300000")
        proc = subprocess.Popen([sys.executable, server], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=env)
        try:
            _ask(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
            first = _inner(_ask(proc, CALL(2, "seller_central_read", command="ads report", account="thedozers")))
            self.assertEqual(first["state"], "running")
            done = _inner(_ask(proc, CALL(3, "seller_central_result", job_id=first["job_id"])))
            self.assertEqual(done["state"], "done")
            self.assertNotIn("stdout", done)  # the gateway would cut this string in the middle
            self.assertTrue(done["result_file"].endswith(first["job_id"] + ".json"))
            with open(done["result_file"]) as fh:
                full = json.load(fh)
            self.assertEqual(len(full["data"]["account_day"][0]["pad"]), 300000)
            self.assertIn('"account_day"', full["stdout"])
        finally:
            proc.stdin.close()
            proc.wait(timeout=30)

    def test_running_then_result_and_rc(self):
        tmp, server = _temp_server()
        conf = _conf(tmp)
        with open(os.path.join(tmp, "commands.json"), "w") as fh:
            json.dump(["ads report"], fh)
        # shorter in-server wait, so the read answers "running" and the job finishes while we poll
        with open(server) as fh:
            text = fh.read()
        text = text.replace("WAIT = 25", "WAIT = 2")
        with open(server, "w") as fh:
            fh.write(text)
        env = dict(os.environ, SELLER_GW_CONFIG=conf, FAKE_WAIT_MS="3000", FAKE_ACCOUNT_rc="3")
        proc = subprocess.Popen([sys.executable, server], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=env)
        try:
            hello = _ask(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
            self.assertEqual(hello["result"]["protocolVersion"], "2025-06-18")
            self.assertEqual(hello["result"]["serverInfo"]["name"], "seller-central-screen-api")
            _ask(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"}, want=False)
            tools = _ask(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            self.assertEqual([t["name"] for t in tools["result"]["tools"]],
                             ["seller_central_read", "seller_cli", "seller_ads_locked", "seller_central_result"])
            first = _inner(_ask(proc, CALL(3, "seller_central_read", command="ads report", account="thedozers")))
            self.assertEqual(first["state"], "running")
            self.assertRegex(first["job_id"], r"^[0-9a-f]{12}$")
            done = _inner(_ask(proc, CALL(4, "seller_central_result", job_id=first["job_id"])))
            self.assertEqual(done["state"], "done")
            self.assertEqual(done["rc"], 3)  # the command rc travels unchanged
            self.assertFalse(done["success"])
            self.assertEqual(done["data"]["account_day"], [{"date": "2026-07-01"}])
            self.assertIn('"account_day"', done["stdout"])  # the CLI's own stdout, untouched
            self.assertEqual(done["passed"]["mode"], "list")
            self.assertEqual(done["passed"]["rows"], 1)
            self.assertEqual(_inner(_ask(proc, CALL(5, "seller_central_result", job_id="not-a-job"))), {"success": False, "reason": "bad_job_id"})
            refused = _ask(proc, CALL(6, "seller_central_read", command="ads set-bid"))
            self.assertTrue(refused["result"]["isError"])
        finally:
            proc.stdin.close()
            proc.wait(timeout=30)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
