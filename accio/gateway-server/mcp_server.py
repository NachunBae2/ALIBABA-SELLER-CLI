#!/usr/bin/env python3
"""seller-central screen-api: Accio gateway custom MCP server (read only).

Gateway /mcp/custom/servers -> this server -> the alibaba-data CLI -> seller-center screen APIs
(the same requests the seller-center pages send, engine/pages.json). Auth = the CLI's saved seller
session. Which commands it may run comes from commands.json (made from lib/cli.mjs READ_COMMANDS,
no write command in it); accounts in BLOCKED_ACCOUNTS are refused here as well.

Layout next to this file (alibaba-accio attach writes it under ~/.aliops-ops/seller-gw):
  config.json     {"SELLER_CLI": "<alibaba-data.mjs of a copy outside ~/Documents>", "NODE": "<node>"}
                  the file beats env: Accio keeps the env it registered, and a CLI under ~/Documents
                  hangs on TCC (SELLER_GW_CONFIG points elsewhere, for tests)
  commands.json   ["ads quality", ...] read commands only
  runs/<job>.json finished job results (the gateway gives one tools/call 30 s)

Tools: seller_central_read{command,account,start,end} -> result, or {state:"running",job_id} when it
does not finish within WAIT seconds; seller_central_result{job_id} -> result (still running: wait again).
The CLI runs with --transport api, so a gateway call never loops back into the gateway.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
WAIT = 25  # the gateway gives one tools/call 30 s
INLINE_LIMIT = 200000  # the gateway cuts a tool result near 400k chars: bigger answers go to runs/<job>.json
DEFAULT_ACCOUNT = "thedozers"
BLOCKED_ACCOUNTS = ("wonnischoice", "linea")
ACCOUNT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
JOB_ID = re.compile(r"^[0-9a-f]{12}$")
RUNS = os.path.join(HERE, "runs")

TOOLS = [{
  "name": "seller_central_read",
  "description": "셀러센터 화면 데이터 읽기(광고·상품분석·문의 통계 등). command = alibaba-data 읽기 명령(예: 'ads report', 'analytics products', 'inquiries stats') — 목록은 commands.json. 읽기만. 25초 안에 못 끝나면 state=running 과 job_id 를 돌려주고 seller_central_result 로 이어 받는다.",
  "inputSchema": {"type": "object", "properties": {
    "command": {"type": "string"}, "account": {"type": "string"},
    "start": {"type": "string"}, "end": {"type": "string"},
    "format": {"type": "string", "enum": ["json", "csv", "table"]},
    "out": {"type": "string"}},
    "required": ["command"], "additionalProperties": False}},
 {"name": "seller_central_result",
  "description": "seller_central_read 가 30초 안에 못 끝내고 job_id 를 돌려줬을 때, 그 결과를 받는다(최대 25초 대기, 아직이면 state=running).",
  "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"], "additionalProperties": False}}]

JOBS = {}
JOBS_LOCK = threading.Lock()
_READ = None


def _config():
    path = os.environ.get("SELLER_GW_CONFIG") or os.path.join(HERE, "config.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def read_commands():
    """The read allowlist (commands.json next to this file). Missing list = every command refused."""
    global _READ
    if _READ is None:
        try:
            with open(os.path.join(HERE, "commands.json"), encoding="utf-8") as fh:
                _READ = frozenset(x.strip() for x in json.load(fh) if isinstance(x, str) and x.strip())
        except (OSError, ValueError):
            _READ = frozenset()
    return _READ


def cli_argv():
    conf = _config()
    cli = conf.get("SELLER_CLI") or os.environ.get("SELLER_CLI", "")
    node = conf.get("NODE") or os.environ.get("SELLER_NODE") or "/opt/homebrew/bin/node"
    return node, cli


def result(value, error=False):
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": error}


def started(job, argv, command):
    """-> (job, answer). Registers the job and runs the CLI on a thread; wait() gives the answer or running."""
    with JOBS_LOCK:
        JOBS[job] = {"state": "running", "started": time.time(), "command": command, "argv": argv}
    threading.Thread(target=_run, args=(job, argv), daemon=True).start()
    return job, wait(job)


def read(args):
    command = " ".join(str(args.get("command") or "").split())
    account = str(args.get("account") or DEFAULT_ACCOUNT).strip()
    if command not in read_commands():
        return result({"success": False, "reason": "not_a_read_command", "command": command}, True)
    if any(b in account for b in BLOCKED_ACCOUNTS) or not ACCOUNT.match(account):
        return result({"success": False, "reason": "account_refused"}, True)
    node, cli = cli_argv()
    if not cli or not os.path.isfile(cli):
        return result({"success": False, "reason": "cli_missing", "detail": "alibaba-accio attach"}, True)
    argv = [node, cli, *command.split(" "), "--account", account, "--transport", "api"]
    fmt = str(args.get("format") or "json")  # the CLI's own output contract: json / csv / table
    if fmt not in ("json", "csv", "table"):
        return result({"success": False, "reason": "bad_format", "field": "format"}, True)
    if fmt != "table":
        argv.append("--" + fmt)
    if args.get("out"):
        argv += ["--out", str(args["out"])]
    for key in ("start", "end"):
        if args.get(key):
            if not DATE.match(str(args[key])):
                return result({"success": False, "reason": "bad_date", "field": key}, True)
            argv += ["--" + key, str(args[key])]
    job = uuid.uuid4().hex[:12]
    return started(job, argv, command)[1]


def _pattern(text):
    """stderr joined with \\n so the caller can find these lines again in its own run."""
    return "\\n".join(x.strip() for x in text.splitlines() if x.strip())[-1500:]


def _count(data):
    """(mode, n) of the rows in one CLI JSON answer, so the caller can compare two runs.
    'list' = len of the first row list found, 'flat' = items of a flat row object."""
    if isinstance(data, list):
        return "list", len(data)
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return "list", len(v)
            if isinstance(v, dict):
                mode, n = _count(v)
                if mode == "list":
                    return mode, n
        if data and all(not isinstance(v, (list, dict)) for v in data.values()):
            return "flat", len(data)
    return "none", 0


def _run(job, argv):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=1800, cwd=os.path.dirname(os.path.dirname(argv[1])))
        try:
            data = json.loads(proc.stdout) if proc.stdout.strip() else None
        except ValueError:
            data = None
        # what this run answered (row mode + count, stderr lines): the caller compares it with its own
        # run and drops the lines the CLI printed the same way, so nothing shows twice
        passed = {"mode": _count(data)[0], "rows": _count(data)[1], "stderr": _pattern(proc.stderr)}
        out = {"success": proc.returncode == 0, "rc": proc.returncode, "data": data,
               "stdout": proc.stdout, "stderr": proc.stderr[-20000:], "passed": passed}
    except subprocess.TimeoutExpired:
        out = {"success": False, "rc": None, "reason": "timeout_1800s", "data": None}
    except OSError as e:
        out = {"success": False, "rc": None, "reason": type(e).__name__, "data": None}
    with JOBS_LOCK:
        info = dict(JOBS.get(job) or {})
    full = dict(out, state="done", job_id=job, command=info.get("command"),
                seconds=round(time.time() - (info.get("started") or time.time()), 1))
    answer = full
    runs_file = os.path.join(RUNS, job + ".json")
    if len(out.get("stdout") or "") > INLINE_LIMIT or len(json.dumps(out.get("data"))) > INLINE_LIMIT:
        # The gateway cuts a tool result when it is large (a truncated string is invalid JSON).
        # Keep the whole answer in runs/<job>.json and answer with where it is.
        answer = {k: full[k] for k in ("state", "success", "rc", "stderr", "passed", "command", "seconds", "job_id")}
        answer["result_file"] = runs_file
    # write the file before state=done: a poller that sees done reads a complete file (atomic replace)
    try:
        os.makedirs(RUNS, exist_ok=True)
        tmp = runs_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(full, fh, ensure_ascii=False)
        os.replace(tmp, runs_file)
    except OSError:
        if "result_file" in answer:  # no file, no usable answer: say so instead of pointing nowhere
            answer = dict(answer)
            answer.pop("result_file", None)
            answer.update({"success": False, "rc": None, "reason": "runs_file_failed"})
    with JOBS_LOCK:
        info.update(full)
        info["_answer"] = answer
        JOBS[job] = info
    return answer


def wait(job):
    end = time.time() + WAIT
    while time.time() < end:
        with JOBS_LOCK:
            info = JOBS.get(job)
        if info and info.get("state") == "done":
            return result(info.get("_answer") or info, not info.get("success"))
        time.sleep(0.5)
    with JOBS_LOCK:
        info = JOBS.get(job)
    if not info:
        return result({"success": False, "reason": "unknown_job", "job_id": job}, True)
    return result({"success": True, "state": "running", "job_id": job, "command": info.get("command"),
                   "seconds": round(time.time() - (info.get("started") or time.time()), 1),
                   "next": "seller_central_result 에 같은 job_id"})


def dispatch(req):
    method = req.get("method")
    if method == "initialize":
        version = (req.get("params") or {}).get("protocolVersion")
        return {"protocolVersion": version if version in ("2024-11-05", "2025-03-26", "2025-06-18") else "2024-11-05",
                "capabilities": {"tools": {}}, "serverInfo": {"name": "seller-central-screen-api", "version": "0.2.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        params = req.get("params") or {}
        args = params.get("arguments") or {}
        name = params.get("name")
        if name == "seller_central_result":
            job = str(args.get("job_id") or "")
            if not JOB_ID.match(job):
                return result({"success": False, "reason": "bad_job_id"}, True)
            return wait(job)
        if name != "seller_central_read":
            return result({"success": False, "reason": "unknown_tool", "tool": name}, True)
        return read(args)
    raise KeyError("method_not_found")


def main():
    os.umask(0o077)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if not isinstance(req, dict) or "id" not in req:
            continue  # notifications (notifications/initialized) have no answer
        try:
            out = {"jsonrpc": "2.0", "id": req["id"], "result": dispatch(req)}
        except KeyError:
            out = {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32601, "message": "Method not found"}}
        except Exception as e:  # noqa: BLE001 — one bad call must not end the server
            out = {"jsonrpc": "2.0", "id": req["id"], "result": result({"success": False, "reason": type(e).__name__}, True)}
        print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
