#!/usr/bin/env python3
"""Fake alibaba-data CLI for the gateway server tests: prints a fixed JSON table and exits with $FAKE_ACCOUNT_rc.

Never touches the network. argv: <command…> --account X --transport api --json [--start …] [--end …]
"""
import json
import os
import sys
import time

req = {"argv": sys.argv[1:]}
if os.environ.get("FAKE_WAIT_MS"):
    time.sleep(int(os.environ["FAKE_WAIT_MS"]) / 1000)
row = {"date": "2026-07-01"}
if os.environ.get("FAKE_PAD"):
    row["pad"] = "x" * int(os.environ["FAKE_PAD"])
print(json.dumps({"account_day": [row], "argv": sys.argv[1:], "fake": True}, ensure_ascii=False))
rc = int(os.environ.get("FAKE_ACCOUNT_rc") or 0)
if rc:
    print("안내: rc %d (가짜)" % rc, file=sys.stderr)
sys.exit(rc)
