#!/usr/bin/env node
import { main } from "../lib/cli.mjs";

try {
  process.exitCode = main(process.argv.slice(2), { scope: "data" });
} catch (e) {
  console.error((e.code === 77 ? "거부: " : "오류: ") + e.message);
  process.exitCode = e.code === 77 ? 77 : 64;
}
