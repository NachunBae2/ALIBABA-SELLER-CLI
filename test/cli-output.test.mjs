// Offline fixtures only: no seller IDs, amounts, cookies, or network requests.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { READ_COMMANDS, gatewayEligible, parseArgs } from "../lib/cli.mjs";

// Exercise real child-process descriptors, replacing only the Python engine/report.
const harness = `
  import { main } from ${JSON.stringify(new URL("../lib/cli.mjs", import.meta.url).href)};
  import { spawnSync } from "node:child_process";
  import { mkdirSync, writeFileSync } from "node:fs";
  import path from "node:path";
  process.exitCode = main(JSON.parse(process.argv[1]), {
    scope: "data",
    gatewayRead() { throw new Error("acci_no_gateway"); },
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
    ...(argv[0] === "store" || argv[0] === "dump" ? ["--transport", "api"] : []),
  ])], { encoding: "utf8", env: { ...process.env, ALIBABA_SELLER_PYTHON: "python3" } });
}

const period = ["--start", "2026-06-01", "--end", "2026-06-03"];
// These fixtures run in place of the engine, so the gateway path is off: any --transport accio then
// falls back with one 안내 line (the transport itself is tested in gateway-transport.test.mjs).
const offGateway = "acci_no_gateway";
for (const command of [...Object.keys(READ_COMMANDS), "dump"]) {
  test(`${command} --json keeps collection progress off stdout`, (t) => {
    const spec = READ_COMMANDS[command];
    const query = spec?.discovery
      ? (command === "keywords index-detail" ? ["--keyword", "fixture widget"] : []) : period;
    const result = runFixture(t, [...command.split(" "), ...query, "--json", "--seed", "fixture"]);
    assert.equal(result.status, 0, result.stderr);
    const payload = JSON.parse(result.stdout);
    if (command === "dump") assert.ok(payload.windows.length);
    else assert.deepEqual(payload, { report: spec.liveRead ?? spec.report, rows: [] });
    if (spec?.liveRead) {
      assert.doesNotMatch(result.stderr, /ok  records=0|"out":/);
    } else {
      assert.match(result.stderr, /ok  records=0/);
      assert.match(result.stderr, /"out":/);
    }
    assert.match(result.stderr, /engine diagnostic/);
    if (command === "keywords") assert.match(result.stderr, /keyword-index  ok/);
    // Real gateway I/O is live in the CLI, so every fixture run is given a gateway that refuses. A read
    // the gateway is not routed (`gatewayEligible` false) never asks and stays silent; the eligible ones
    // print one 안내 line and take api (the gateway itself is tested in gateway-transport.test.mjs).
    const eligible = gatewayEligible(command, parseArgs([...command.split(" "), ...query, "--json", "--seed", "fixture"]).opt);
    assert.equal((result.stderr.match(/안내: 게이트웨이 경유 실패/g) || []).length, eligible ? 1 : 0);
    if (eligible) assert.match(result.stderr, new RegExp(`안내: 게이트웨이 경유 실패\\(${offGateway}\\)`));
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
  // --max probes the date filter through the engine directly; that one call is api, so no 안내 line.
  assert.equal(result.stderr.match(/안내: 게이트웨이 경유 실패/g), null);
});

test("human-readable final output remains on stdout", (t) => {
  const result = runFixture(t, ["inquiries", "list", ...period]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "Human-readable report\n");
  assert.match(result.stderr, /inquiries  ok/);
});
