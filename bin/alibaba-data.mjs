#!/usr/bin/env node
import { main } from "../lib/cli.mjs";

try {
  // 비동기 명령(bundle 등)은 promise 를 돌려준다 — 풀어서 종료 코드로 쓴다.
  const r = main(process.argv.slice(2), { scope: "data" });
  const code = typeof r?.then === "function" ? (await r) : r;
  // main() 은 이미 숫자 종료 코드를 돌려준다. 엉뚱한 값은 usage 오류로 본다.
  process.exitCode = Number.isInteger(code) ? code : 0;
} catch (e) {
  console.error((e.code === 77 ? "거부: " : "오류: ") + e.message);
  process.exitCode = e.code === 77 ? 77 : 64;
}
