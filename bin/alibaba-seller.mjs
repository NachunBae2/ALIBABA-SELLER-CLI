#!/usr/bin/env node
import { main } from "../lib/cli.mjs";

try {
  process.exitCode = main(process.argv.slice(2));
} catch (e) {
  console.error("오류: " + e.message);
  process.exitCode = 64;
}
