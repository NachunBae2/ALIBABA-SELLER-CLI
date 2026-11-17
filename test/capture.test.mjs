import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../engine/capture.js", import.meta.url), "utf8");
const begin = source.indexOf("const sanitizeInquiryResponse =");
const end = source.indexOf("\nconst page =", begin);
const sanitize = Function(source.slice(begin, end) + ";return sanitizeInquiryResponse")();
const sanitizeAllowed = Function(source.slice(begin, end) + ";return sanitizeAllowedTable")();
const pages = JSON.parse(readFileSync(new URL("../engine/pages.json", import.meta.url), "utf8"));

const item = {
  id: 1,
  tradeId: 2,
  feedbackType: 1,
  targetIds: ["123"],
  sender: { name: "private-fixture", countryCode: "KR" },
  subject: "private-fixture",
  body: "private-fixture",
  chatToken: "secret-fixture",
};

test("retains allowed metadata and drops buyer originals before persistence", () => {
  const result = sanitize(JSON.stringify({ code: 200, data: { list: [[item]] } }));
  assert.deepEqual(result.list, [{
    id: 1, tradeId: 2, feedbackType: 1, targetIds: ["123"], buyerCountry: "KR",
  }]);
  assert(!JSON.stringify(result).includes("private-fixture"));
  assert(!JSON.stringify(result).includes("secret-fixture"));
  assert(!source.includes("inquiryRaw"));
  assert(source.includes("page.evaluate(sanitizeInquiryResponse, res.text)"));
});

test("keeps short buyer-level codes and key names only, never sender free text", () => {
  const withLevel = { ...item, sender: { ...item.sender, buyerLevel: "L3", nickName: "private-fixture", email: "private-fixture@x.test", levelText: "private fixture text" } };
  const result = sanitize(JSON.stringify({ data: { list: [withLevel] } }));
  assert.deepEqual(result.list[0].buyerAttrs, { "sender.buyerLevel": "L3" });
  assert(result.fieldNames.includes("sender.buyerLevel"));
  assert(result.fieldNames.includes("sender.email"));
  assert(!JSON.stringify(result).includes("private-fixture"));
  assert(!JSON.stringify(result).includes("private fixture text"));
});

test("flattens groups, rejects invalid IDs, and handles missing sender", () => {
  const result = sanitize(JSON.stringify({ data: { list: [[{ id: 1 }], { id: 2 }, { id: "bad" }, null] } }));
  assert.deepEqual(result.list, [{ id: 1, buyerCountry: null }, { id: 2, buyerCountry: null }]);
});

test("repairs unescaped quotes inside embedded product cards", () => {
  const broken = JSON.stringify({ data: { list: [{ ...item, properties: JSON.stringify({ productId: "123" }) }] } })
    .replaceAll('\\"', '"');
  assert.equal(sanitize(broken).list.length, 1);
});

test("fails closed on unrecoverable input", () => {
  assert.deepEqual(sanitize("not json"), { code: null, pagination: null, list: [], parsed: false, fieldNames: [] });
});

test("preserves large inquiry IDs exactly", () => {
  const result = sanitize('{"data":{"list":[{"id":1601234567890123456}]}}');
  assert.equal(result.list[0].id, "1601234567890123456");
});

test("capture script parses as an async function", () => {
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  assert.doesNotThrow(() => new AsyncFunction(source.replace("__PARAMS__", "{}")));
});

test("repairs bare quotes inside a product-card string field", () => {
  const raw = '{"code":200,"data":{"pagination":{"totalCount":2},"list":[[{"id":1,"tradeId":11,"feedbackType":1,"targetIds":[100],"subject":"x","card":{"properties":"{"selectedSKUs":"[]"}"}},{"id":2,"tradeId":12,"feedbackType":6,"targetIds":[999]}]]}}';
  const result = sanitize(raw);
  assert.equal(result.parsed, true);
  assert.deepEqual(result.list.map((entry) => entry.id), [1, 2]);
});

test("conversion detail rows keep only the allowlisted fields (no buyer name, avatar, profile, chat, member ids)", () => {
  const conv = pages.pages.find((p) => p.key === "ads-report").rangeTables.find((t) => t.table === "component_ggs_ad_shop_conv_detail");
  const forbidden = { convUserName: { main: "private-fixture" }, convUserAvatar: { main: "https://x.test/private-fixture.jpg" }, convUserDetailUrl: { main: "https://x.test/private-fixture" },
    chatContent: { main: "private-fixture" }, senderMbrSeq: { main: "private-fixture" }, receiverMbrSeq: { main: "private-fixture" }, fbOrderUrl: { main: "private-fixture" }, fbId: { main: "private-fixture" } };
  const row = { timeUs: { main: "2026-07-01" }, productId: { main: "100" }, fbProdId: { main: "101" }, countryId: { main: "US" }, fbType: { main: "Inquiry" }, buyerLevel: { main: "L3" }, ...forbidden };
  const out = JSON.parse(sanitizeAllowed(JSON.stringify({ data: { queryResult: { totalRows: 1, detailList: [row] }, debug: "private-fixture" } }), conv.allow));
  assert.deepEqual(Object.keys(out.data.queryResult.detailList[0]).sort(), ["buyerLevel", "countryId", "fbProdId", "fbType", "productId", "timeUs"]);
  assert.equal(out.data.queryResult.totalRows, 1);
  assert.deepEqual(out.data.queryResult.fieldNames, Object.keys(row).sort());  // key names only, values never
  assert(!JSON.stringify(out).includes("private-fixture"));
  for (const k of Object.keys(forbidden)) assert(!conv.allow.includes(k), k);
  assert.deepEqual(JSON.parse(sanitizeAllowed("not json", conv.allow)), { sanitized: true, parsed: false });
});
