#!/usr/bin/env node
import { main } from "../lib/cli.mjs";

try {
  process.exitCode = await main(process.argv.slice(2), { scope: "inquiry" });
} catch (e) {
  console.error((e.code === 77 ? "거부: " : "오류: ") + e.message);
  process.exitCode = e.code === 77 ? 77 : 64;
}
