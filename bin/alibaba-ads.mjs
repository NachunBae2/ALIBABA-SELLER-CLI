#!/usr/bin/env node
// alibaba-ads: 광고 조작(dry-run 기본, --yes 로만 반영). The work is in engine/ads_write.py.
import { runAds } from "../lib/ads_write.mjs";

process.exitCode = runAds(process.argv.slice(2));
