// Offline fixtures only: no seller IDs, amounts, cookies, or network requests.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { READ_COMMANDS } from "../lib/cli.mjs";

// Exercise real child-process descriptors, replacing only the Python engine/report.
const harness = `
  import { main } from ${JSON.stringify(new URL("../lib/cli.mjs", import.meta.url).href)};
  import { spawnSync } from "node:child_process";
  import { mkdirSync, writeFileSync } from "node:fs";
  import path from "node:path";
  process.exitCode = main(JSON.parse(process.argv[1]), {
    scope: "data",
    run(cmd, args, opts) {
      const collecting = args[0].endsWith("seller_central_raw.py");
      let output;
      if (collecting) {
        if (args[args.indexOf("--transport") + 1] !== "api") throw new Error("default collection must use api");
        const dir = args[args.indexOf("--out") + 1];
        mkdirSync(path.join(dir, "raw"), { recursive: true });
        if (args.includes("--skip-tables")) {
          writeFileSync(path.join(dir, "manifest.json"), JSON.stringify({ generatedAt: new Date().toISOString(), pages: [] }));
          writeFileSync(path.join(dir, "raw", "ads-report.jsonl"), JSON.stringify({
            url: "https://fixture.invalid/api/data/dynamic/filter/data",
            json: { data: { queryResult: [{ sceneType: "offline_effect_report", beginDateTime: "2020-01-01", endDateTime: "2020-01-03" }] } }
          }));
        }
        output = args[args.indexOf("--pages") + 1] + "  ok  records=0\\n" + JSON.stringify({ out: dir }) + "\\n";
      } else {
        output = args[args.indexOf("--format") + 1] === "json"
          ? JSON.stringify({ report: args[1], rows: [] }) + "\\n"
          : "Human-readable report\\n";
      }
      return spawnSync(process.execPath, ["-e",
        "process.stdout.write(" + JSON.stringify(output) + "); process.stderr.write('engine diagnostic\\\\n');"
      ], opts).status;
    }
  });
`;

function runFixture(t, argv) {
  const dir = mkdtempSync(path.join(tmpdir(), "cli-output-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const config = path.join(dir, "accounts.json");
  writeFileSync(config, JSON.stringify({ fixture: { loginId: "fixture", outDir: dir } }));
  return spawnSync(process.execPath, ["--input-type=module", "-e", harness, JSON.stringify([
    ...argv, "--account", "fixture", "--config", config, "--fresh",
  ])], { encoding: "utf8", env: { ...process.env, ALIBABA_SELLER_PYTHON: "python3" } });
}

const period = ["--start", "2026-06-01", "--end", "2026-06-03"];
for (const command of [...Object.keys(READ_COMMANDS), "dump"]) {
  test(`${command} --json keeps collection progress off stdout`, (t) => {
    const result = runFixture(t, [...command.split(" "), ...period, "--json", "--seed", "fixture"]);
    assert.equal(result.status, 0, result.stderr);
    const payload = JSON.parse(result.stdout);
    if (command === "dump") assert.ok(payload.windows.length);
    else assert.deepEqual(payload, { report: READ_COMMANDS[command].report, rows: [] });
    assert.match(result.stderr, /ok  records=0/);
    assert.match(result.stderr, /"out":/);
    assert.match(result.stderr, /engine diagnostic/);
    if (command === "keywords") assert.match(result.stderr, /keyword-index  ok/);
  });
}

test("multi-window ads daily --json is one parseable JSON value", (t) => {
  const result = runFixture(t, ["ads", "daily", "--level", "campaign", "--start", "2026-06-01", "--end", "2026-11-15", "--json"]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).report, "ads-daily");
  assert.ok((result.stderr.match(/"out":/g) || []).length > 1);
});

test("--max date discovery also leaves stdout as one JSON value", (t) => {
  const result = runFixture(t, ["ads", "report", "--max", "--json"]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).report, "ads-report");
  assert.match(result.stderr, /sellercentral-bounds-/);
});

test("human-readable final output remains on stdout", (t) => {
  const result = runFixture(t, ["inquiries", "list", ...period]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "Human-readable report\n");
  assert.match(result.stderr, /inquiries  ok/);
});
