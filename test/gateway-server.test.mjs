// test-only: the gateway server's command list against lib/cli.mjs (no network, no gateway).
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { READ_COMMANDS, GATEWAY_READS } from "../lib/cli.mjs";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const listed = JSON.parse(readFileSync(path.join(root, "accio", "gateway-server", "commands.json"), "utf8"));

test("gateway commands.json = every alibaba-data read, nothing else", () => {
  assert.deepEqual([...listed].sort(), Object.keys(READ_COMMANDS).sort());
  assert.equal(new Set(listed).size, listed.length, "duplicate command");
  assert.ok(listed.every((c) => c === c.trim().replace(/\s+/g, " ")), "normalized names only");
  assert.deepEqual(listed.filter((c) => !READ_COMMANDS[c]), [], "every gateway read is a READ_COMMANDS entry");
  for (const c of GATEWAY_READS) assert.ok(READ_COMMANDS[c], c);
});

test("write commands are not reachable through the gateway list", () => {
  const writes = /(^|\s)(send|reply|set|create|update|delete|publish|mark|pay|bid)\b/;
  assert.deepEqual(listed.filter((c) => writes.test(c)), []);
});
