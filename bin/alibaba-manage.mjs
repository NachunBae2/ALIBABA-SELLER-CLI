#!/usr/bin/env node
// alibaba-manage: 광고 밖 셀러센트럴 조작(dry-run 기본, --yes 로만 전송). The work is in engine/manage_write.py.
import { runManage } from "../lib/manage_write.mjs";

process.exitCode = runManage(process.argv.slice(2));
