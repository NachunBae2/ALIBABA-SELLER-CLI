# 테스트 전용 파일 — CLI 실행에 쓰이지 않음. 가짜 게이트웨이 응답만 씀(실값 없음).
"""alibaba-accio attach/detach: POST or PUT, what goes to the gateway, /test result, files it writes."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
import accio_gateway as ag  # noqa: E402


class FakeGateway:
    """Stands in for ag.Gateway: records requests, answers like the gateway does (list / PUT / POST / test)."""

    def __init__(self, listed=(), test=None):
        self.listed, self.calls, self.test_answer = list(listed), [], test or {"connected": True, "toolCount": 2}

    def custom_servers(self):
        return self.listed

    def custom_find(self, name):
        return next((s for s in self.listed if s.get("name") == name), None)

    def custom_upsert(self, body):
        method = "PUT" if self.custom_find(body["name"]) else "POST"
        self.calls.append((method, body))
        return method, {}

    def custom_update(self, name, patch):
        self.calls.append(("PUT", dict(patch, name=name)))
        return {}

    def custom_test(self, name):
        self.calls.append(("TEST", name))
        return self.test_answer


class TestAttach(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gw-attach-")
        self.home = os.path.join(self.tmp, "seller-gw")
        # a stand-in "repo" with the two server files, so nothing touches the real checkout
        repo = os.path.join(self.tmp, "repo")
        shutil.copytree(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "accio", "gateway-server"),
                        os.path.join(repo, "accio", "gateway-server"))
        self.repo, self.cli = repo, os.path.join(self.tmp, "cli")
        self.p = [patch.object(ag, "HERE", os.path.join(repo, "engine")), patch.object(ag, "worktree", lambda r, c: "worktree")]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_time_posts_a_local_server_and_reports_the_tool_count(self):
        g = FakeGateway()
        out = ag.attach(g, "seller-gw", self.cli, home=self.home)
        self.assertEqual(g.calls[0][0], "POST")
        body = g.calls[0][1]
        self.assertEqual(body["name"], "seller-gw")
        # the gateway launches type=local entries and takes command as the argv array; a string
        # command with args is not started (/test answers Failed to parse URL)
        self.assertEqual(body["type"], "local")
        self.assertEqual(body["command"], [sys.executable, os.path.join(self.home, "server", "mcp_server.py")])
        self.assertTrue(body["enabled"])
        self.assertEqual(body["environment"]["SELLER_CLI"], os.path.join(self.cli, "bin", "alibaba-data.mjs"))
        self.assertEqual(body["environment"]["SELLER_GW_CONFIG"], os.path.join(self.home, "config.json"))
        self.assertEqual(g.calls[1], ("TEST", "seller-gw"))
        self.assertEqual((out["method"], out["connected"], out["toolCount"]), ("POST", True, 2))
        with open(os.path.join(self.home, "config.json")) as fh:  # what the server actually reads
            self.assertEqual(json.load(fh)["SELLER_CLI"], os.path.join(self.cli, "bin", "alibaba-data.mjs"))
        self.assertTrue(os.path.exists(os.path.join(self.home, "server", "mcp_server.py")))
        self.assertTrue(os.path.exists(os.path.join(self.home, "server", "commands.json")))

    def test_already_listed_puts_instead_of_posting(self):
        g = FakeGateway(listed=[{"name": "seller-gw"}, {"name": "someone-else"}])
        out = ag.attach(g, "seller-gw", self.cli, home=self.home)
        self.assertEqual(out["method"], "PUT")
        self.assertEqual([c[0] for c in g.calls], ["PUT", "TEST"])
        self.assertEqual(g.calls[0][1]["name"], "seller-gw")  # one PUT, for this server only

    def test_not_connected_is_an_error(self):
        g = FakeGateway(test={"connected": False, "toolCount": 0, "raw": {"connected": False}})
        with self.assertRaises(ag.GatewayError) as ctx:
            ag.attach(g, "seller-gw", self.cli, home=self.home)
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("connected=false", str(ctx.exception))

    def test_detach_only_switches_the_entry_off(self):
        g = FakeGateway(listed=[{"name": "seller-gw"}])
        self.assertEqual(ag.detach(g, "seller-gw"), {"server": "seller-gw", "enabled": False})
        self.assertEqual(g.calls, [("PUT", {"enabled": False, "name": "seller-gw"})])


class TestAttachCommand(unittest.TestCase):
    def test_attach_word_prints_json_and_exits_0(self):
        fake = FakeGateway()
        with patch.object(ag, "attach", lambda g, name, cli: {"server": name, "method": "POST", "connected": True, "toolCount": 2}):
            out, code = _run(["attach"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"server": "seller-gw", "method": "POST", "connected": True, "toolCount": 2})

    def test_failed_attach_exits_1_with_a_message(self):
        def boom(g, name, cli):
            raise ag.GatewayError(1, "/test 가 connected=false")
        with patch.object(ag, "attach", boom):
            _err, code = _run(["attach"], FakeGateway(), stderr=True)
        self.assertEqual(code, 1)
        self.assertIn("connected=false", _err)


def _run(words, g, stderr=False):
    out = __import__("io").StringIO()
    err = __import__("io").StringIO()
    with patch("sys.stdout", out), patch("sys.stderr", err):
        code = ag.main(words, gateway=g)
    return ((err if stderr else out).getvalue(), code)


class WorktreeFollowsHead(unittest.TestCase):
    """Re-attach after a new commit moves the existing CLI copy to the attaching checkout's HEAD."""

    def test_existing_copy_moves_to_new_head(self):
        import subprocess
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        repo, cli = os.path.join(tmp, "repo"), os.path.join(tmp, "copy")
        git = lambda *a, cwd=repo: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=cwd,
                                                  check=True, capture_output=True, text=True).stdout.strip()
        os.makedirs(repo)
        git("init", "-q")
        with open(os.path.join(repo, "f"), "w") as fh:
            fh.write("1")
        git("add", "f"); git("commit", "-qm", "one")
        self.assertEqual(ag.worktree(repo, cli), "worktree+")
        with open(os.path.join(repo, "f"), "w") as fh:
            fh.write("2")
        git("commit", "-qam", "two")
        self.assertEqual(ag.worktree(os.path.realpath(repo), os.path.realpath(cli)), "worktree")
        self.assertEqual(git("rev-parse", "HEAD", cwd=cli), git("rev-parse", "HEAD"))


if __name__ == "__main__":
    unittest.main()
