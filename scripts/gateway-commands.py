#!/usr/bin/env python3
"""Write accio/gateway-server/commands.json: the read commands the gateway server may run.

Source = lib/cli.mjs READ_COMMANDS (asked of node, so the object literal is parsed, not pattern-matched).
Every entry in that table is a read; write commands are not in it at all, so the server cannot run them.
The node test test/gateway-server.test.mjs checks the file against the table, so a change in either place
fails the suite until the file is rewritten.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASK = 'import("./lib/cli.mjs").then((m) => console.log(JSON.stringify(Object.keys(m.READ_COMMANDS))))'


def read_commands(node=None):
    out = subprocess.run([node or "node", "-e", ASK], cwd=ROOT, capture_output=True, text=True, check=True)
    return sorted(set(json.loads(out.stdout.strip().splitlines()[-1])))


def main(argv=None):
    out = os.path.join(ROOT, "accio", "gateway-server", "commands.json")
    names = read_commands()
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(names, fh, ensure_ascii=False, indent=0)
        fh.write("\n")
    print("written: %s (%d commands)" % (out, len(names)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
