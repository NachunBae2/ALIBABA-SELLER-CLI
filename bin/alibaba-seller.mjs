#!/usr/bin/env node
import { main } from "../lib/cli.mjs";

try {
  const result = main(process.argv.slice(2));
  if (result && typeof result.then === "function") {
    result.then((code) => { process.exitCode = code; },
      (e) => { console.error((e.code === 77 ? "거부: " : "오류: ") + e.message); process.exitCode = e.code === 77 ? 77 : 64; });
  } else {
    process.exitCode = result;
  }
} catch (e) {
  console.error((e.code === 77 ? "거부: " : "오류: ") + e.message);
  process.exitCode = e.code === 77 ? 77 : 64;
}
