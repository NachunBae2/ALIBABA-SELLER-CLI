#!/usr/bin/env python3
"""Run the repo's ad-analysis read commands and print the headline rows.

  analyze.py --account <key> --start YYYY-MM-DD --end YYYY-MM-DD --out DIR [--top 10]
  analyze.py --from <dump folder> [--from ...] [--start ... --end ...] --out DIR

Each table comes from one `alibaba-data` read command (ads quality, ads products,
ads conversions, bids, keywords). All numbers come from the CLI's CSVs. This skill
lives inside the CLI repo (skills/alibaba-ad-analysis), so it calls the repo's own
bin/alibaba-data.mjs.
"""
import argparse
import csv
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
REPO_BIN = os.path.normpath(os.path.join(HERE, "..", "..", "..", "bin", "alibaba-data.mjs"))

# (table file, CLI command words, headline columns) — one CLI run each, --out DIR/<table>
TABLES = [
    ("buyer_quality", ["ads", "quality"],
     ("level", "campaign_kind", "l1plus_click_pct", "clicks", "spend_usd", "inquiries_cvr3", "cpc_usd",
      "conversion_rate", "cost_per_inquiry_usd")),
    ("product_ad_performance", ["ads", "products"],
     ("product_id", "l1plus_click_pct", "clicks", "inquiries_cvr3", "conversion_rate", "cost_per_inquiry_usd")),
    ("conversions", ["ads", "conversions"],
     ("ad_product_id", "buyer_level", "channel", "converted_scope", "converted_product_id")),
    ("bid_analysis", ["bids"],
     ("level", "campaign_name", "keyword", "bid_usd", "cost_per_inquiry_usd")),
    ("keyword_seeds", ["keywords"],
     ("keyword", "score", "search_volume", "competition", "bid_usd", "impressions", "clicks", "ctr_pct")),
]


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
    p.add_argument("--out", required=True, help="output folder for the CSVs")
    p.add_argument("--top", type=int, default=10)
    a = p.parse_args(argv)
    if not a.account and not a.dirs:
        p.error("--account (live, uses Aside) or --from <dump folder> (offline)")
    tail = [["--account", a.account]] if a.account else [["--from", d] for d in a.dirs]
    for k in ("start", "end", "days"):
        if getattr(a, k):
            tail.append(["--" + k, getattr(a, k)])
    for table, words, cols in TABLES:
        folder = os.path.join(a.out, table)
        os.makedirs(folder, exist_ok=True)
        cmd = cli() + words + ["--out", folder, "--csv"] + sum(tail, [])
        code = subprocess.run(cmd, stdout=subprocess.DEVNULL).returncode
        if code != 0:
            return code
        print("## %s.csv (%s, top %d)" % (table, " ".join(words), a.top))
        print(" | ".join(cols))
        for r in rows(os.path.join(folder, table + ".csv"), a.top):
            print(" | ".join(r.get(c, "") for c in cols))
        print()
    print("files: " + a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
