import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { main } from "../lib/cli.mjs";

function fixture() {
  const dir = mkdtempSync(path.join(tmpdir(), "seller-features-"));
  const token = path.join(dir, "oauth.json");
  const config = path.join(dir, "accounts.json");
  writeFileSync(token, JSON.stringify({ access_token: "abcd-secret", refresh_token: "refresh-secret",
    expires_at: "2026-11-26T01:00:00.000Z", scope: "product.read" }));
  writeFileSync(config, JSON.stringify({ fixture: { loginId: "fixture", openApiTokenFile: token,
    openApiClientId: "fake-client", openApiClientSecret: "fake-secret", openApiScopes: ["product.read", "store.read"] } }));
  return { config, token };
}

test("auth status redacts tokens and reports expiry/consent", async () => {
  const f = fixture(), out = [];
  assert.equal(await main(["auth", "status", "--account", "fixture", "--config", f.config],
    { log: (x) => out.push(x), now: Date.parse("2026-11-26T00:00:00Z") }), 0);
  assert.match(out[0], /abcd…/);
  assert.doesNotMatch(out[0], /abcd-secret|refresh-secret|fake-secret/);
  assert.match(out[0], /"seconds_remaining": 3600/);
  assert.match(out[0], /store.read/);
});

test("auth refresh uses injected fixed response and keeps refresh token secret", async () => {
  const f = fixture(), out = [];
  const rc = await main(["auth", "refresh", "--account", "fixture", "--config", f.config], {
    log: (x) => out.push(x), now: Date.parse("2026-11-26T00:00:00Z"),
    oauthRequest: async (body) => { assert.equal(body.grant_type, "refresh_token"); return { access_token: "new1-secret", expires_in: 7200, scope: "product.read store.read" }; },
  });
  assert.equal(rc, 0);
  assert.doesNotMatch(out[0], /new1-secret|refresh-secret/);
  assert.equal(JSON.parse(readFileSync(f.token)).refresh_token, "refresh-secret");
});

test("shipping and minisite reads use offline fixed responses", async () => {
  const f = fixture(), out = [];
  const featureRead = async (cmd) => cmd === "minisite pages"
    ? [{ pageId: 1, name: "Home", published: true }]
    : [{ template: "Global", countries: ["US"], carriers: ["DHL", "EMS"] },
       { template: "Economy", countries: ["CA"], carriers: ["POST"] }];
  assert.equal(await main(["logistics", "ems", "--account", "fixture", "--config", f.config], { log: x => out.push(x), featureRead }), 0);
  assert.match(out.pop(), /Global/);
  assert.equal(await main(["minisite", "pages", "--account", "fixture", "--config", f.config], { log: x => out.push(x), featureRead }), 0);
  assert.match(out.pop(), /published/);
});

test("every new command has focused help", () => {
  for (const command of ["auth status", "auth refresh", "auth check-consent", "logistics coverage", "logistics ems", "minisite pages"]) {
    const out = [];
    assert.equal(main([...command.split(" "), "--help"], { log: x => out.push(x) }), 0);
    assert.match(out[0], new RegExp(command.replace(" ", "\\s")));
  }
});
