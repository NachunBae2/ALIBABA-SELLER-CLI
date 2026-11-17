// 테스트 전용 파일 — CLI 실행에 쓰이지 않음. 안의 ID·금액·클릭 수는 동작 확인용 예시 값이며 실제 셀러·고객 데이터가 아님.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../engine/capture.js", import.meta.url), "utf8");
const begin = source.indexOf("const sanitizeInquiryResponse =");
const end = source.indexOf("\nconst page =", begin);
const sanitize = Function(source.slice(begin, end) + ";return sanitizeInquiryResponse")();
const pages = JSON.parse(readFileSync(new URL("../engine/pages.json", import.meta.url), "utf8"));

// Data collection tests moved to test_http.py; capture.js is only the OneTalk lookup adapter.

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
  assert(source.includes("page.evaluate(sanitizeInquiryResponse, { text: res.text, lookup: P.lookup || null })"));
});

test("OneTalk lookup: the target's match keys come out beside the saved list, never inside it", () => {
  const target = { id: 1000000000077, tradeId: 7000000001, secTradeId: "s7000000001", messageId: 3000000000099, lastestReplyTime: 1789000000000,
    sender: { accountId: 555000111, alitalkId: "ali-fixture-555", countryCode: "US", name: "private-fixture", chatToken: "secret-fixture" } };
  const other = { id: 1000000000088, tradeId: 7000000002, sender: { accountId: 999000111 } };
  const res = sanitize({ text: JSON.stringify({ code: 200, data: { list: [[other, target]] } }), lookup: { inquiryId: "1000000000077" } });
  assert.deepEqual(res.lookup.aliIds, ["555000111", "ali-fixture-555"]);
  assert.ok(res.lookup.cids.includes("3000000000099") && res.lookup.cids.includes("s7000000001"));
  assert.deepEqual(res.lookup.times, [1789000000000]);
  assert.deepEqual(res.lookup.country, ["US"]);
  const saved = JSON.stringify({ ...res, lookup: undefined });
  for (const v of ["555000111", "ali-fixture-555", "private-fixture", "secret-fixture"]) assert(!saved.includes(v), v);
  assert(!("lookup" in sanitize(JSON.stringify({ data: { list: [[target]] } }))));
  assert.equal(sanitize({ text: JSON.stringify({ data: { list: [[other]] } }), lookup: { tradeId: "123" } }).lookup, null);
  // the capture script deletes it before the record is built and prints it after the file is written
  assert(source.indexOf("delete san.lookup") < source.indexOf("text: JSON.stringify(san)"));
  assert(source.lastIndexOf("await save();") < source.indexOf('console.log("LOOKUP "'));
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
