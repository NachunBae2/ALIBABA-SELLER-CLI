// 테스트 전용 파일 — CLI 실행에 쓰이지 않음. 안의 ID·금액·클릭 수는 동작 확인용 예시 값이며 실제 셀러·고객 데이터가 아님.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../engine/capture.js", import.meta.url), "utf8");
const begin = source.indexOf("const sanitizeInquiryResponse =");
const end = source.indexOf("\nconst page =", begin);
const sanitize = Function(source.slice(begin, end) + ";return sanitizeInquiryResponse")();
const sanitizeAllowed = Function(source.slice(begin, end) + ";return sanitizeAllowedTable")();
const captureLogs = Function(source.slice(begin, end) + ";return captureOperationLogs")();
const captureTargets = Function(source.slice(begin, end) + ";return captureCampaignTargets")();
const pages = JSON.parse(readFileSync(new URL("../engine/pages.json", import.meta.url), "utf8"));

test("campaign target reads use observed list identities and only verified Shop paths", async () => {
  const paths = pages.pages.find(p => p.key === "ads-campaigns").campaignTargetReads;
  const cap = [{url: "/api/campaign/type/allList", method: "POST", body: "type=normal&data=%7B%7D", text: JSON.stringify({data: {
    shop: [{id: 9001, productLineId: 110106}, {id: 9001, productLineId: 110106}],
    other: [{id: 8001, productLineId: 1}]}})}];
  const calls = [];
  const records = await captureTargets(cap, paths, async req => {
    calls.push(req); assert.equal(req.method, "POST");
    const body = new URLSearchParams(req.body);
    assert.equal(body.get("data"), "{}");
    assert.deepEqual(JSON.parse(body.get("ads")), {productLineId: 110106});
    return {status: 200, text: JSON.stringify({ok: true, data: []})};
  }, () => 60000);
  assert.deepEqual(calls.map(r => new URL(r.url).pathname), ["/api/campaign/9001/adgroup/batch", "/api/9001/select/product/query"]);
  assert.equal(records.length, 2);
  assert(records.every(r => r.replay.campaignId === "9001" && r.keep));
  assert.deepEqual(await captureTargets([], paths, () => {throw Error("no template")}, () => 60000), []);
});

test("operation logs reuse observed read template, clamp retention and paginate", async () => {
  const cap = [{method: "POST", url: "/api/log/interval", body: "type=normal&token=fixture",
    text: JSON.stringify({data: {maxDate: "2026-11-16"}})}];
  const calls = [];
  const result = await captureLogs(cap, {queries: [{productLineId: 1, logMajor: "campaign"}]},
    "2026-06-01", "2026-11-30", 30, async req => {
      const body = new URLSearchParams(req.body), data = JSON.parse(body.get("data"));
      calls.push(data);
      assert.equal(req.url, "https://www2.alibaba.com/api/log");
      assert.equal(req.method, "POST");
      assert.equal(body.get("type"), "normal");
      assert.equal(body.get("token"), "fixture");
      return {status: 200, text: JSON.stringify({ok: true, totalCount: 101,
        data: Array.from({length: data.pageIndex === 1 ? 100 : 1}, () => ({gmtCreate: "2026-10-01 12:00:00"}))})};
    }, () => 60000);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].dateBegin, "2026-09-16");
  assert.equal(calls[0].dateEnd, "2026-11-16");
  assert.equal(result.records[1].replay.page, 2);
  assert(result.records.every(r => r.keep && r.replay.kind === "ads-history"));
  const expired = await captureLogs(cap, {queries: [{}]}, "2026-06-01", "2026-08-01", 30,
    () => {throw Error("must not query an expired window");}, () => 60000);
  assert.equal(expired.records.length, 0);
  const missing = await captureLogs([], {}, "2026-10-01", "2026-11-01", 30, () => {}, () => 60000);
  assert.match(missing.notes[0], /no observed/);
});

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
