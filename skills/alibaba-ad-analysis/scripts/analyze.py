#!/usr/bin/env python3
"""Run `alibaba-data ads analyze` and print the headline rows.

  analyze.py --account <key> --start YYYY-MM-DD --end YYYY-MM-DD [--out DIR] [--top 10]
  analyze.py --from <dump folder> [--from ...] [--start ... --end ...] [--out DIR]

All numbers come from the CLI's CSVs. This skill lives inside the CLI repo
(skills/alibaba-ad-analysis), so it calls the repo's own bin/alibaba-data.mjs.
"""
import argparse
import csv
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
REPO_BIN = os.path.normpath(os.path.join(HERE, "..", "..", "..", "bin", "alibaba-data.mjs"))


def cli():
    if os.environ.get("ALIBABA_DATA_CLI"):
        return ["node", os.environ["ALIBABA_DATA_CLI"]]
    if os.path.exists(REPO_BIN):
        return ["node", REPO_BIN]
    found = shutil.which("alibaba-data")
    if found:
        return [found]
    raise SystemExit("alibaba-data CLI not found: git clone https://github.com/NachunBae2/ALIBABA-SELLER-CLI.git, npm link (or set ALIBABA_DATA_CLI)")


def rows(path, n):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))[:n]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--account")
    p.add_argument("--from", dest="dirs", action="append", default=[])
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--days")
    p.add_argument("--out", required=True, help="output folder for the CSVs and summary.md")
    p.add_argument("--top", type=int, default=10)
    a = p.parse_args(argv)
    if not a.account and not a.dirs:
        p.error("--account (live, uses Aside) or --from <dump folder> (offline)")
    cmd = cli() + ["ads", "analyze", "--out", a.out, "--csv"]
    cmd += ["--account", a.account] if a.account else sum((["--from", d] for d in a.dirs), [])
    for k in ("start", "end", "days"):
        if getattr(a, k):
            cmd += ["--" + k, getattr(a, k)]
    code = subprocess.run(cmd, stdout=subprocess.DEVNULL).returncode
    if code != 0:
        return code
    with open(os.path.join(a.out, "summary.md")) as fh:
        print(fh.read())
    cols = ("product_id", "l1plus_click_pct", "spend_estimated_usd", "spend_actual_usd", "clicks", "inquiries_cvr3", "cpc_usd",
            "conversion_rate", "cost_per_inquiry_usd", "cost_basis", "l3plus_share_pct", "conv_detail_count", "bid_usd")
    print("## product_ad_performance.csv (top %d by inquiries)" % a.top)
    print(" | ".join(cols))
    for r in rows(os.path.join(a.out, "product_ad_performance.csv"), a.top):
        print(" | ".join(r.get(c, "") for c in cols))
    print("\nfiles: " + ", ".join(sorted(f for f in os.listdir(a.out))) + "  (" + a.out + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
