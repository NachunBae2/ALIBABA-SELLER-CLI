// 자식 명령 대역: `node bundle-child.mjs <명령어…> --account A --json --out DIR`
// --json 이면 result.json 이 될 stdout 을 쓰고, --out 폴더에 CSV 하나를 남긴다. 네트워크 없음.
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const argv = process.argv.slice(2);
const dir = argv.includes("--out") ? argv[argv.indexOf("--out") + 1] : null;
const slug = dir ? path.basename(dir) : "child";
if (dir) {
  mkdirSync(dir, { recursive: true });
  writeFileSync(path.join(dir, `${slug}.csv`), "a,b\n1,2\n");
}
if (process.env.BUNDLE_CHILD_FAIL && argv.join(" ").startsWith(process.env.BUNDLE_CHILD_FAIL)) {
  process.stderr.write("fixture child: server error\n");
  process.exit(5);
}
const rows = [{ period: "2026-11-09~2026-11-15", note: slug }];
process.stdout.write(`${JSON.stringify({ [`${slug}_rows`]: rows })}\n`);
process.stderr.write(`fixture child: ${slug}\n`);
