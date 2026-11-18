import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { main } from "../lib/cli.mjs";

const CLI = fileURLToPath(new URL("../bin/alibaba-data.mjs", import.meta.url));
const period = { start: "2026-01-01", end: "2026-01-03", days: 3, mode: "explicit" };
const started_at = "2026-01-12T00:00:00Z", finished_at = "2026-01-12T00:01:00Z";
function fixture(t, results, span = period, statuses = {}) {
  const dir = mkdtempSync(path.join(tmpdir(), "seller-combine-test-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const commands = Object.entries(results).map(([command, result]) => {
    const slug = command.replaceAll(" ", "_");
    const { rc = 0, status = rc ? "failed" : "ok" } = statuses[command] || {};
    mkdirSync(path.join(dir, slug));
    writeFileSync(path.join(dir, slug, "result.json"), JSON.stringify(result));
    return { command, slug, rc, status, started_at, finished_at, error: rc ? "synthetic error" : null,
      period: result.period?.[0] ?? null, files: [slug + "/result.json"],
      tables: Object.fromEntries(Object.entries(result).map(([table, rows]) => [table, rows.length])) };
  });
  writeFileSync(path.join(dir, "manifest.json"), JSON.stringify({ schema: "alibaba-seller-bundle/1", account: "fixture",
    period: span, started_at, finished_at, cli_version: "0.0.0", commands,
    totals: Object.fromEntries(["ok", "failed", "skipped"].map(k => [k, commands.filter(c => c.status === k).length])) }));
  return dir;
}
function run(kind, dir, ...opts) {
  let output = "";
  const forbidden = () => { throw new Error("combine must not run a collector or subprocess"); };
  assert.equal(main(["combine", kind, "--from", dir, ...opts], {
    scope: "data", run: forbidden, capture: forbidden, log: (s) => { output += s; },
  }), 0);
  if (opts.includes("--csv")) return output;
  const result = JSON.parse(output);
  assert.deepEqual(JSON.parse(readFileSync(path.join(dir, "combine", kind + ".json"), "utf8")), result);
  assert.equal(result.schema, "alibaba-seller-combine/1");
  assert.deepEqual(result.key, { products: ["product_id"], keywords: ["keyword", "product_id"], daily: ["date"],
    compare: ["table_kind", "product_id", "keyword", "date"] }[kind]);
  for (const row of result.rows) assert.deepEqual(Object.keys(row), Object.keys(result.fields));
  for (const field of Object.values(result.fields)) {
    assert.deepEqual(Object.keys(field), ["ko", "unit", "source", "period", "judge"]);
    for (const value of Object.values(field)) assert.ok(typeof value === "string" && value.trim());
  }
  return result;
}
const productAd = (product_id, date, spend, impressions, clicks, inquiries) =>
  ({ product_id, date, spend, impressions, clicks, inquiries, raw: { subject: "Ad title" } });
const wordTable = "table_component_ggs_offline_keyword";
const termTable = "table_component_ggs_offline_searchword";
const word = { keyword: " KEY Word ", productId: 10, timeUs: "2026-01-01", impsCnt: 40, clickCnt: 4, cost: "US$ 2.00", cvr3: 1 };

test("products joins explicit IDs and preserves advertising sums, ratios and reasons", t => {
  const dir = fixture(t, {
    "ads report": { product_day: [
      productAd(10, "2026-01-01", 0.1, 40, 2, 0), productAd(" 10 ", "2026-01-02", 0.2, 60, 3, 0),
      productAd("11", "2026-01-01", 2, 0, 0, null), productAd(11, "2026-01-03", "", 0, 0, 2),
      productAd("10", "2026-01-04", 900, 900, 900, 900),
    ] },
    "analytics products": { analytics_products: [
      { product_id: "10", product_title: "Analyzer title", views: 120, clicks: 5, visitors: 8, inquiries: 2, add_to_favorite: 0,
        covered_start: "2025-12-28", covered_end: "2026-01-03", granularity: "week" },
      { product_id: "9007199254740993", visitors: 0, inquiries: 0 },
    ] },
    "inquiries list": { inquiries: [
      { product_id: "10", us_date: period.start, inquiry_count: 100 }, { product_id: 10, us_date: period.end },
      { product_id: "", matched_products: "10", us_date: period.start },
    ] },
    "orders list": { "orders-list": [{ "baseInfo.id": "01", "productInfo.productCount": 10 }] },
  });
  const result = run("products", dir);
  assert.deepEqual(result.period, period);
  assert.deepEqual(result.rows.map(r => r.product_id), ["10", "11", "9007199254740993"]);
  const [ad, missing, analyzer] = result.rows;
  assert.equal(ad.product_title, "Analyzer title");
  assert.equal(ad.ad_spend_usd, 0.3);
  assert.deepEqual([ad.cpc, ad.cpc_num, ad.cpc_den, ad.cpc_reason], [0.06, 0.3, 5, null]);
  assert.deepEqual([ad.ad_ctr, ad.ad_ctr_num, ad.ad_ctr_den], [0.05, 5, 100]);
  assert.deepEqual([ad.cost_per_inquiry, ad.cost_per_inquiry_num, ad.cost_per_inquiry_den, ad.cost_per_inquiry_reason], [null, 0.3, 0, "분모 0"]);
  assert.equal(ad.analyzer_favorites, 0);
  assert.equal(ad.analyzer_inquiry_rate, 0.25);
  assert.equal(ad.analyzer_covered_start, "2025-12-28");
  assert.equal(ad.inquiry_count, 2);
  assert.equal(ad.order_count, null);
  assert.equal(ad.order_count_reason, "주문 목록에 상품ID 없음");
  assert.ok(result.notes.includes("주문 목록에 상품ID 없음"));
  assert.equal(missing.ad_spend_usd, null, "do not sum only the available rows");
  assert.equal(missing.cpc_reason, "분모 0");
  assert.equal(missing.cost_per_inquiry_reason, "분모 값 없음");
  assert.equal(analyzer.inquiry_count, null);
  assert.equal(analyzer.analyzer_inquiry_rate_den, 0);
  assert.ok(result.sources.some(s => s.command === "ads report" && s.table === "product_day" && s.rows === 5));
});

test("failed and skipped outputs are ignored; only explicit order IDs and inquiry product keys join", t => {
  const dir = fixture(t, {
    "ads report": { product_day: [productAd("10", period.start, 12, 120, 0, 0)] },
    "analytics products": { analytics_products: [{ product_id: "11", views: 120 }] },
    "inquiries by-product": { inquiry_by_product_counts: [{ inflow: "10", inquiries: 3, product_title: "Inquiry title" }, { inflow: "TM 유입", inquiries: 7 }] },
    "orders list": { "orders-list": [
      { "baseInfo.id": 1, "productInfo.productId": "10" }, { "baseInfo.id": "1", productId: 10 },
      { "baseInfo.id": 2, product_id: "10" }, { product_id: "12" },
    ] },
  }, period, { "ads report": { rc: 3, status: "ok" }, "analytics products": { status: "skipped" } });
  const out = run("products", dir);
  assert.deepEqual(out.rows.map(r => r.product_id), ["10", "12"]);
  assert.deepEqual(out.rows.map(r => [r.order_count, r.order_count_reason]), [[2, null], [null, "주문ID 누락"]]);
  assert.equal(out.rows[0].inquiry_count, 3);
  assert.equal(out.rows[0].ad_spend_usd, null);
  assert.equal(out.rows[0].analyzer_clicks, null);
  assert.ok(out.notes.includes("ads report 실패"));
  assert.ok(out.notes.includes("analytics products 없음"));
  assert.ok(out.notes.includes("inquiries list 없음"));
  assert.ok(out.sources.every(s => !["ads report", "analytics products"].includes(s.command)));
});

test("keywords retain exact join scope, split overlapping ad views, and do not sum index snapshots", t => {
  const dir = fixture(t, {
    "ads tables": {
      [wordTable]: [
        { ...word, _query_tag: "ai.keyword" },
        { ...word, keyword: "key word", clickCnt: 2, cost: 1, cvr3: 0, _query_tag: "manual.keyword" },
        { ...word, _query_tag: "manual.keyword", _query_granularity: "ALL" },
        { ...word, _query_tag: "manual.keyword.seed.key" },
        { ...word, _query_tag: "" },
        { ...word, productId: "", cvr3: "", businessLeadsCnt: "2", _query_tag: "ai.keyword" },
      ],
      [termTable]: [{ ...word, keyword: "different bid word", searchWord: "key word", cvr3: 0 }],
    },
    keywords: {
      keywords: [{ keyword: " Key Word ", product_id: "10", source: "analytics-product keywordEffect", period: "2026-01", impressions: 100, clicks: 10 },
        { keyword: "KEY  WORD", product_id: "10", source: "analytics-product keywordEffect", period: "2026-01", impressions: 20, clicks: 0 },
        { keyword: "ignored", source: "analytics-keyword getKeywords", impressions: 1000 }],
      keyword_index: [{ keyword: "key word", seed: "key", nd: "30d", search_index: 25, seller_scale: 5 },
        { keyword: "KEY WORD ", seed: "word", nd: "30d", search_index: 25, seller_scale: 5 },
        { keyword: "index only", nd: "30d", search_index: 100 },
        { keyword: "conflict", nd: "30d", search_index: 1 }, { keyword: "CONFLICT ", nd: "7d", search_index: 2 }],
    },
  });
  const out = run("keywords", dir);
  assert.equal(out.rows.length, 5);
  const [row, unlinked] = out.rows.filter(r => r.keyword === "key word");
  assert.deepEqual([row.product_id, row.keyword_raw, row.ad_impressions, row.ad_clicks, row.ad_spend_usd, row.ad_inquiries],
    ["10", " KEY Word ", 80, 6, 3, 1]);
  assert.deepEqual([row.cpc, row.cpc_num, row.cpc_den, row.cost_per_inquiry], [0.5, 3, 6, 3]);
  assert.deepEqual([row.search_term_spend_usd, row.search_term_cost_per_inquiry, row.search_term_cost_per_inquiry_reason], [2, null, "분모 0"]);
  assert.deepEqual([row.analyzer_impressions, row.analyzer_clicks, row.analyzer_periods], [100, 10, "2026-01"]);
  assert.deepEqual([row.search_index, row.keyword_index_nd, row.keyword_index_observed_at], [25, "30d", finished_at]);
  assert.deepEqual([unlinked.product_id, unlinked.ad_inquiries, unlinked.analyzer_impressions], [null, 2, null]);
  assert.equal(out.rows.find(r => r.keyword === "index only").product_id, null);
  assert.equal(out.rows.find(r => r.keyword === "conflict").search_index, null);
  assert.ok(out.notes.some(n => n.includes("nd:") && n.includes("상충")));
});

test("keywords fall back to projected word rows without inventing advertising inquiries", t => {
  const dir = fixture(t, {
    "ads tables": { [wordTable]: [word] },
    keywords: { keywords: ["key WORD", "repeat", " REPEAT "].map(keyword => ({
      source: "ads component_ggs_offline_keyword", product_id: "", keyword,
      period: "2026-01-01", impressions: 10, clicks: 2, cost_usd: 4,
    })), keyword_index: [] },
    "analytics products": { analytics_product_keywords: [{ keyword: "key word", product_id: "10", period: "2025-12-28", views: 200, clicks: 4 }] },
  }, period, { "ads tables": { rc: 3 } });
  const out = run("keywords", dir);
  const [analyzer, ad, ambiguous] = out.rows;
  assert.deepEqual([analyzer.product_id, analyzer.ad_clicks, analyzer.analyzer_impressions], ["10", null, 200]);
  assert.deepEqual([ad.product_id, ad.ad_spend_usd, ad.cpc, ad.ad_inquiries, ad.cost_per_inquiry_reason],
    [null, 4, 2, null, "분모 값 없음"]);
  assert.ok(out.notes.includes("ads tables 실패"));
  assert.deepEqual([ambiguous.keyword, ambiguous.ad_clicks, ambiguous.cpc], ["repeat", null, null]);
  assert.ok(out.notes.some(n => n.includes("캠페인·필터 구분 불가")));
});

test("daily joins only observed server date labels and preserves raw zero versus missing", t => {
  const dir = fixture(t, {
    "ads report": { account_day: [
      { date: period.start, spend: 2, clicks: 4, impressions: 10, inquiries: 0 },
      { date: period.start, spend: 1, clicks: 1, impressions: 2, inquiries: 0 },
      { date: "2026-01-02", spend: 0, clicks: 0, impressions: 0, inquiries: 0 },
      { date: "2026-01-32", spend: 10 },
    ] },
    "store summary": { store_summary: [
      { _query_day: period.start, "statDate.value": period.start, "pvLong.value": 55, "uvLong.value": "" },
      { _query_day: period.start, "statDate.value": "2026-01-02", "uvLong.value": 99 },
    ] },
    "inquiries stats": { inquiry_stats: [
      { dimension: "us_date", value: period.start, inquiries: 7 },
      { dimension: "buyer_country", value: period.start, inquiries: 99 },
      { dimension: "us_date", value: period.end, inquiries: 1 },
    ] },
  });
  const out = run("daily", dir);
  const [day, next, inquiryOnly] = out.rows;
  assert.deepEqual([day.ad_spend_usd, day.ad_clicks, day.store_page_views, day.store_visitors, day.inquiry_count], [3, 5, 55, null, 7]);
  assert.deepEqual([next.date, next.ad_spend_usd, next.store_visitors, next.inquiry_count], ["2026-01-02", 0, null, null]);
  assert.deepEqual([inquiryOnly.date, inquiryOnly.ad_spend_usd, inquiryOnly.inquiry_count], [period.end, null, 1]);
});

test("compare outer-joins all three keys and emits differences and rate operands", t => {
  const span = { ...period, start: "2026-01-02", end: "2026-01-04", mode: "relative" };
  const common = { keyword: "key word", nd: "30d", search_index: 50 };
  const a = fixture(t, {
    "ads report": { account_day: [{ date: "2026-01-02", spend: 2 }], product_day: [productAd(10, period.start, 0, 100, 1, 1), productAd(12, period.start, 2, 20, 1, 1)] },
    keywords: { keywords: [], keyword_index: [common] },
  });
  const b = fixture(t, {
    "ads report": { account_day: [{ date: "2026-01-02", spend: 3 }], product_day: [productAd("10", span.end, 3, 150, 2, 1), productAd(13, span.end, 0, 50, 1, 1)] },
    keywords: { keywords: [], keyword_index: [{ ...common, keyword: " KEY WORD ", search_index: 60, nd: "7d" }] },
  }, span);
  const out = run("compare", a, "--from-b", b, "--json");
  assert.equal(out.bundle_b, b);
  assert.deepEqual(out.period, { a: period, b: span });
  assert.equal(out.rows.length, 5);
  const [matched, aOnly, bOnly, indexed, day] = out.rows;
  assert.equal(matched.table_kind, "products");
  assert.equal(matched.product_id, "10");
  assert.deepEqual([matched.products_ad_spend_usd_a, matched.products_ad_spend_usd_b, matched.products_ad_spend_usd_delta,
    matched.products_ad_spend_usd_change_rate, matched.products_ad_spend_usd_reason], [0, 3, 3, null, "분모 A=0"]);
  assert.equal(matched.products_ad_impressions_delta, 50);
  assert.equal(matched.products_ad_impressions_change_rate, 0.5);
  assert.deepEqual([aOnly.product_id, aOnly.products_ad_spend_usd_b, aOnly.products_ad_spend_usd_delta], ["12", null, null]);
  assert.deepEqual([bOnly.product_id, bOnly.products_ad_spend_usd_a, bOnly.products_ad_spend_usd_delta], ["13", null, null]);
  assert.deepEqual([indexed.keyword, indexed.keywords_search_index_delta, indexed.keywords_search_index_reason], ["key word", null, "Keyword Index nd 불일치"]);
  assert.deepEqual([day.date, day.daily_ad_spend_usd_change_rate], ["2026-01-02", 0.5]);
  assert.ok(out.sources.some(s => s.side === "a") && out.sources.some(s => s.side === "b"));
  assert.equal(readFileSync(path.join(a, "combine", "compare.csv"), "utf8").trimEnd().split("\r\n").length, out.rows.length + 1);
});

test("CLI emits CSV with the JSON row count, escaping text and retaining an empty header", t => {
  const title = 'A, "test"\nsecond line';
  const dir = fixture(t, { "analytics products": { analytics_products: [{ product_id: "10", product_title: title }, { product_id: "11" }] } });
  const result = run("products", dir, "--json");
  const proc = spawnSync(process.execPath, [CLI, "combine", "products", "--from=" + dir, "--csv"], { encoding: "utf8" });
  assert.equal(proc.status, 0, proc.stderr);
  const saved = readFileSync(path.join(dir, "combine", "products.csv"), "utf8");
  assert.equal(proc.stdout.trimEnd(), saved.trimEnd());
  assert.ok(saved.includes('"A, ""test""\nsecond line"'));
  assert.equal(saved.split("\r\n").filter(Boolean).length - 1, result.rows.length);
  assert.equal(run("daily", dir).rows.length, 0);
  assert.match(run("daily", dir, "--csv"), /^date,[^\r\n]+$/);
});

test("CLI requires a bundle, from-b only on compare, and rejects a raw dump manifest", t => {
  const dir = fixture(t, {});
  assert.throws(() => main(["combine", "products"]), /--from/);
  assert.throws(() => main(["combine", "compare", "--from", dir]), /--from-b/);
  assert.throws(() => main(["combine", "daily", "--from", dir, "--from-b", dir]), /--from-b/);
  assert.throws(() => main(["combine", "daily", "--from", dir, "--json", "--csv"]), /함께/);
  writeFileSync(path.join(dir, "manifest.json"), JSON.stringify({ pages: [] }));
  assert.throws(() => main(["combine", "products", "--from", dir]), /alibaba-seller-bundle/);
});
