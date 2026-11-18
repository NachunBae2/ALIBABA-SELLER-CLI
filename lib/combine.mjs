import { lstatSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

const SCHEMA = "alibaba-seller-combine/1";
const KINDS = ["products", "keywords", "daily"];
const BUNDLE = "번들 기간", CLOSED = "마감 주/달", SNAPSHOT = "스냅샷";
const RATE = "비율(0~1)", CHANGE = "비율(1=100% 변화)";
const TEXT = "텍스트", ID = "ID", DATE = "날짜(PST)";
const USD = "USD", TIMES = "회", PEOPLE = "명", COUNT = "건";
const nonnumeric = new Set([TEXT, ID, DATE]);
const cmp = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const text = (value) => typeof value === "string" && value.trim() ? value : null;
const id = (value) => {
  if (typeof value === "number" && !Number.isSafeInteger(value)) return null;
  const raw = typeof value === "number" ? String(value) : text(value)?.trim();
  return raw && /^\d+$/.test(raw) ? raw : null;
};
const keyword = (value) => text(value)?.trim().toLowerCase() ?? null;
function date(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const time = new Date(value + "T00:00:00Z");
  return Number.isFinite(+time) && time.toISOString().slice(0, 10) === value ? value : null;
}
function number(value) {
  if (typeof value === "string") {
    value = value.trim().replace(/^(?:US\$|USD\s*|\$)/, "").trim();
    if (!/^[+-]?(?:(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value)) return null;
    value = Number(value.replaceAll(",", ""));
  }
  return Number.isFinite(value) ? value : null;
}

// Match the report engine's decimal summing of reported numbers, including USD cents.
function add(values) {
  const parts = values.map(value => {
    const [decimal, exponent = "0"] = String(value).split("e");
    const fraction = (decimal.split(".")[1] || "").length;
    return [BigInt(decimal.replace(".", "")), Number(exponent) - fraction];
  });
  const exp = parts.reduce((min, [, exponent]) => Math.min(min, exponent), 0);
  const integer = parts.reduce((sum, [digits, exponent]) => sum + digits * 10n ** BigInt(exponent - exp), 0n);
  return number(Number(`${integer}e${exp}`));
}
function sum(rows, select) {
  const values = rows.map(row => number(select(row)));
  return values.length && values.every(value => value !== null) ? add(values) : null;
}

class Bundle {
  constructor(dir) {
    this.dir = path.resolve(dir);
    const manifest = JSON.parse(readFileSync(path.join(this.dir, "manifest.json"), "utf8"));
    if (manifest?.schema !== "alibaba-seller-bundle/1" || !Array.isArray(manifest.commands) ||
        !date(manifest.period?.start) || !date(manifest.period?.end) || manifest.period.start > manifest.period.end) {
      throw new Error("유효한 alibaba-seller-bundle/1 manifest가 필요하다");
    }
    this.period = manifest.period;
    this.commands = new Map(manifest.commands.map(entry => [entry.command, entry]));
    this.results = new Map();
    this.notes = new Set();
    this.sources = new Map();
  }
  table(command, table) {
    if (!this.results.has(command)) {
      const entry = this.commands.get(command);
      const slug = command.replaceAll(" ", "_");
      let result = null;
      if (!entry || entry.status === "skipped") this.notes.add(`${command} 없음`);
      else if (entry.rc !== 0 || entry.status !== "ok") this.notes.add(`${command} 실패`);
      else if (entry.slug !== slug) this.notes.add(`${command} 실패: manifest slug 불일치`);
      else {
        try {
          result = JSON.parse(readFileSync(path.join(this.dir, slug, "result.json"), "utf8"));
          if (!result || typeof result !== "object" || Array.isArray(result)) {
            this.notes.add(`${command} 실패: result.json 형식 오류`);
            result = null;
          }
        } catch (error) {
          this.notes.add(`${command} ${error.code === "ENOENT" ? "없음: result.json" : "실패: result.json 읽기"}`);
        }
      }
      this.results.set(command, result);
    }
    const result = this.results.get(command);
    if (result == null) return null;
    const rows = result[table];
    if (!Array.isArray(rows) || rows.some(row => !row || typeof row !== "object" || Array.isArray(row))) {
      this.notes.add(`${command} / ${table} 표 없음 또는 형식 오류`);
      return null;
    }
    this.sources.set(`${command}/${table}`, { command, table, rows: rows.length });
    if (!rows.length) this.notes.add(`${command} / ${table} 0행: 키별 0으로 해석하지 않음`);
    return rows;
  }
  inRange(day) { return day && this.period.start <= day && day <= this.period.end; }
  excluded(count, reason) { if (count) this.notes.add(`${reason}: ${count}행`); }
}

function group(bundle, rows, keyOf, reason) {
  const result = new Map();
  let excluded = 0;
  for (const row of rows || []) {
    const key = keyOf(row);
    if (key === null) { excluded++; continue; }
    if (!result.has(key)) result.set(key, []);
    result.get(key).push(row);
  }
  bundle.excluded(excluded, reason);
  return result;
}

const adMetrics = [
  ["impressions", "노출", TIMES, "impressions", "impsCnt", "노출 규모 비교"],
  ["clicks", "클릭", TIMES, "clicks", "clickCnt", "클릭 유입 규모 비교"],
  ["spend_usd", "광고비", USD, "spend", "cost/costStr", "광고비 규모 비교"],
  ["inquiries", "문의", COUNT, "inquiries", "mcCnt", "광고 보고서가 집계한 문의 규모 비교"],
];
const analyzerMetrics = [
  ["impressions", "노출", TIMES, "views", "상품별 검색 노출 규모 비교"],
  ["clicks", "클릭", TIMES, "clicks", "상품별 클릭 규모 비교"],
  ["visitors", "방문자", PEOPLE, "visitors", "주·달 방문자 수의 합; 여러 기간의 동일 방문자는 중복"],
  ["inquiries", "문의", COUNT, "inquiries", "Analyzer가 집계한 문의 규모 비교"],
  ["favorites", "찜", TIMES, "add_to_favorite", "관심 저장 빈도 비교"],
  ["shares", "공유", TIMES, "shared", "공유 빈도 비교"],
  ["compares", "비교 추가", TIMES, "add_to_compare", "비교에 추가한 빈도 비교"],
  ["inquiry_buyers", "문의 구매자", PEOPLE, "inquiry_buyers", "주·달 문의 구매자 수의 합; 기간 간 중복 가능"],
];

class Combined {
  constructor(bundle, kind) {
    this.bundle = bundle;
    this.kind = kind;
    this.fields = {};
    this.rows = [];
    this.key = [];
  }
  field(name, ko, unit, source, period, judge) {
    this.fields[name] = { ko, unit, source, period, judge };
  }
  identity(name, ...info) { this.key.push(name); this.field(name, ...info); }
  ratio(name, ko, unit, num, den, judge) {
    const n = this.fields[num], d = this.fields[den];
    this.field(name, ko, unit, `${num} / ${den}`, n.period, judge);
    this.field(name + "_num", ko + " 분자", n.unit, n.source, n.period, n.judge);
    this.field(name + "_den", ko + " 분모", d.unit, d.source, d.period, d.judge);
    this.field(name + "_reason", ko + " 미계산 사유", TEXT, `${num} / ${den}`, n.period, "빈값과 실제 0을 구분");
    (this.ratios ??= []).push([name, num, den]);
  }
  row(values) {
    for (const [name, num, den] of this.ratios || []) {
      const n = values[num] ?? null, d = values[den] ?? null;
      const reason = d === null ? "분모 값 없음" : d === 0 ? "분모 0" : n === null ? "분자 값 없음" : null;
      values[name] = reason ? null : number(n / d);
      values[name + "_num"] = n;
      values[name + "_den"] = d;
      values[name + "_reason"] = reason ?? (values[name] === null ? "계산 범위 초과" : null);
    }
    this.rows.push(Object.fromEntries(Object.keys(this.fields).map(k => [k, values[k] ?? null])));
  }
  result() {
    const { bundle, kind, key, fields, rows } = this;
    const missing = Object.keys(fields).filter(k => !k.endsWith("_reason")).map(k =>
      [k, rows.reduce((count, row) => count + (row[k] === null), 0)]).filter(([, n]) => n);
    for (const [field, count] of missing) {
      bundle.notes.add(`${field}: ${count}행 값 없음 (키 미일치·원천 누락·계산 불가; 0으로 대체하지 않음)`);
    }
    return { schema: SCHEMA, kind, bundle: bundle.dir, period: bundle.period, key, fields, rows,
      notes: [...bundle.notes], sources: [...bundle.sources.values()] };
  }
}

function declareAds(out, prefix, table, rawWord = false) {
  for (const [name, ko, unit, field, raw, judge] of adMetrics) {
    const columns = rawWord && name === "inquiries" ? "cvr3/businessLeadsCnt" : raw;
    const wordProjection = name === "inquiries" ? "" : ` 또는 keywords/keywords/${name === "spend_usd" ? "cost_usd" : field}`;
    const label = rawWord && name === "inquiries" ? "전환(문의·TM 등)" : ko;
    out.field(prefix + name, (prefix === "search_term_" ? "광고 검색어 " : rawWord ? "광고 키워드 " : "광고 ") + label, unit,
      rawWord ? `ads tables/${table}/${columns}${wordProjection}`
        : `ads report/${table}/${field} (${raw})`, BUNDLE,
      rawWord && name === "inquiries" ? "Word Report 전환 집계; 다른 문의 표의 수와 합산하지 않음" : judge);
  }
}
function economics(out, prefix, ratioPrefix = "", wordReport = false) {
  out.ratio(ratioPrefix + "cpc", "클릭당 광고비", USD, prefix + "spend_usd", prefix + "clicks", "클릭 1회 확보 비용 비교");
  const basis = wordReport ? "Word Report 전환(문의·TM 등)" : "광고표 문의";
  out.ratio(ratioPrefix + "cost_per_inquiry", (wordReport ? "전환당" : "문의당") + " 광고비", USD,
    prefix + "spend_usd", prefix + "inquiries", basis + " 1건당 비용 비교");
}
function adValues(rows, prefix) {
  return Object.fromEntries(adMetrics.map(([name, , , field]) => [prefix + name, sum(rows, r => r[field])]));
}
function declareProductID(out) {
  out.identity("product_id", "셀러센터 상품 ID", ID, "원천 product_id/productId 또는 문의 inflow", BUNDLE, "숫자 문자열로만 결합; 제목으로 추정하지 않음");
}

function products(bundle) {
  const out = new Combined(bundle, "products");
  declareProductID(out);
  out.field("product_title", "상품 제목", TEXT, "analytics products/analytics_products/product_title → ads report/product_day/raw.subject → inquiries by-product/inquiry_by_product_counts/product_title", BUNDLE,
    "원천에 있는 제목 표시; 결합 키가 아님");
  declareAds(out, "ad_", "product_day");
  for (const [name, ko, unit, field, judge] of analyzerMetrics) {
    out.field("analyzer_" + name, "Product Analyzer " + ko, unit, `analytics products/analytics_products/${field}`, CLOSED, judge);
  }
  for (const [name, ko, unit] of [["covered_start", "집계 시작일", DATE], ["covered_end", "집계 종료일", DATE], ["granularity", "주기", TEXT]]) {
    out.field("analyzer_" + name, "Product Analyzer " + ko, unit, "analytics products/analytics_products/" + name, CLOSED, "번들 요청 기간과 실제 합산 범위를 구분");
  }
  out.field("inquiry_count", "상품 ID가 있는 문의 수", COUNT, "inquiries list/inquiries/product_id 또는 inquiries by-product/inquiry_by_product_counts/inflow,inquiries", BUNDLE, "전체 문의 목록의 직접 연결만 셈; 광고 기여를 뜻하지 않음");
  out.field("order_count", "상품에 연결된 고유 주문 수", COUNT, "orders list/orders-list/product_id|productId|productInfo.productId, baseInfo.id", SNAPSHOT, "상품 ID가 명시된 주문만 셈; 기간 주문 수가 아님");
  out.field("order_count_reason", "주문 수 빈값의 사유", TEXT, "orders list/orders-list", SNAPSHOT, "미수집과 연결 불가를 구분");
  economics(out, "ad_");
  out.ratio("ad_ctr", "광고 클릭률", RATE, "ad_clicks", "ad_impressions", "광고 노출 대비 클릭 비교");
  out.ratio("analyzer_inquiry_rate", "Product Analyzer 문의율", RATE, "analyzer_inquiries", "analyzer_visitors", "Analyzer 방문자 대비 문의 비교");

  const ads = group(bundle, bundle.table("ads report", "product_day"), r =>
    bundle.inRange(date(r.date)) ? id(r.product_id) : null, "product_day 날짜 또는 상품ID 없음/범위 밖 제외");
  const analyzer = group(bundle, bundle.table("analytics products", "analytics_products"), r => id(r.product_id), "analytics_products 상품ID 없거나 유효하지 않아 제외");
  if (analyzer.size) bundle.notes.add("Product Analyzer는 기존 집계표 값 그대로 사용: 원천의 빈값을 기존 보고서가 0으로 변환했는지는 번들에서 검증 불가.");
  const list = bundle.table("inquiries list", "inquiries");
  const inquiries = list === null
    ? group(bundle, bundle.table("inquiries by-product", "inquiry_by_product_counts"), r => id(r.inflow), "상품별 문의의 비상품 유입 제외")
    : group(bundle, list, r => bundle.inRange(date(r.us_date)) ? id(r.product_id) : null, "문의 목록 날짜 또는 직접 상품ID 없음/범위 밖 제외");
  const orders = bundle.table("orders list", "orders-list");
  const orderProduct = (r) => id(r.product_id) ?? id(r.productId) ?? id(r["productInfo.productId"]);
  const orderID = (r) => text(r["baseInfo.id"]) ?? id(r["baseInfo.id"]);
  const hasOrderProduct = orders?.some(r => orderProduct(r));
  const ordersByProduct = group(bundle, orders, orderProduct, "주문 목록에 유효한 상품ID 없는 행 제외");
  if (orders && !hasOrderProduct) bundle.notes.add("주문 목록에 상품ID 없음");
  for (const pid of union(ads, analyzer, inquiries, ordersByProduct)) {
    const a = ads.get(pid) || [], p = analyzer.get(pid) || [], q = inquiries.get(pid) || [], o = ordersByProduct.get(pid) || [];
    const stats = single(bundle, p, "analytics_products 중복 상품행 제외");
    const orderIds = o.map(orderID);
    out.row({ product_id: pid,
      product_title: text(stats.product_title) ?? a.map(r => text(r.raw?.subject)).find(Boolean) ?? q.map(r => text(r.product_title)).find(Boolean),
      ...adValues(a, "ad_"),
      ...Object.fromEntries(analyzerMetrics.map(([name, , , field]) => ["analyzer_" + name, number(stats[field])])),
      analyzer_covered_start: date(stats.covered_start), analyzer_covered_end: date(stats.covered_end), analyzer_granularity: text(stats.granularity),
      inquiry_count: list === null ? sum(q, r => r.inquiries) : q.length || null,
      order_count: orderIds.length && orderIds.every(Boolean) ? new Set(orderIds).size : null,
      order_count_reason: orders === null ? "orders list 실패/없음" : !hasOrderProduct ? "주문 목록에 상품ID 없음"
        : !o.length ? "상품ID와 일치하는 주문 행 없음" : orderIds.some(value => !value) ? "주문ID 누락" : null,
    });
  }
  return out.result();
}

const union = (...maps) => [...new Set(maps.flatMap(m => [...m.keys()]))].sort(cmp);
function single(bundle, rows, reason) {
  if (rows.length === 1) return rows[0];
  bundle.excluded(rows.length, reason);
  return {};
}

const WORD_TABLE = "component_ggs_offline_keyword";
const TERM_TABLE = "component_ggs_offline_searchword";
function adsWords(bundle, table, fallback) {
  const raw = bundle.table("ads tables", "table_" + table);
  if (raw?.length) {
    // Range replays can coexist with the UI's default tab in ads tables.
    const seeded = r => typeof r._query_tag === "string" && r._query_tag.includes(".seed.");
    const tagged = raw.some(r => r._query_tag && !seeded(r) && (r._query_granularity ?? "DAY") === "DAY" && date(r.timeUs));
    return raw.map(r => ({
      keyword: text(r[table === TERM_TABLE ? "searchWord" : "keyword"]) ?? text(r.word) ??
        text(r[table === TERM_TABLE ? "keyword" : "searchWord"]),
      product_id: r.productId, day: date(r.timeUs),
      impressions: r.impsCnt, clicks: r.clickCnt, spend: number(r.cost) ?? number(r.costStr),
      inquiries: number(r.cvr3) ?? number(r.businessLeadsCnt),
      eligible: (r._query_granularity ?? "DAY") === "DAY" && !seeded(r) && (!tagged || Boolean(r._query_tag)),
    }));
  }
  const projected = (fallback || []).filter(r => r.source === "ads " + table);
  if (projected.length) bundle.notes.add(`keywords/keywords (ads ${table}): 문의 필드 없음; 광고 문의당 비용 계산 불가`);
  // The projection drops campaign/filter identities, so same-key daily rows cannot be safely added.
  const identity = r => JSON.stringify([keyword(r.keyword), id(r.product_id), date(r.period)]);
  const counts = new Map();
  for (const r of projected) counts.set(identity(r), (counts.get(identity(r)) ?? 0) + 1);
  return projected.map(r => {
    const ambiguous = counts.get(identity(r)) > 1;
    if (ambiguous) bundle.notes.add(`keywords/keywords (ads ${table}): 같은 키·날짜에 복수 행, 캠페인·필터 구분 불가; 합계 null`);
    return { ...r, day: date(r.period), inquiries: null, eligible: true, impressions: ambiguous ? null : r.impressions,
      clicks: ambiguous ? null : r.clicks, spend: ambiguous ? null : r.cost_usd };
  });
}

function keywords(bundle) {
  const out = new Combined(bundle, "keywords");
  out.identity("keyword", "정규화한 키워드", TEXT, "광고 Word Report·Product Analyzer·Keyword Index 키워드", BUNDLE, "소문자·앞뒤 공백 제거 후 정확히 일치할 때만 결합");
  declareProductID(out);
  out.field("keyword_raw", "원천 키워드 표기", TEXT, "광고 Word Report·Product Analyzer·Keyword Index 키워드", BUNDLE, "정규화 전 관측 표기 하나; 키로 쓰지 않음");
  declareAds(out, "ad_", "table_" + WORD_TABLE, true);
  declareAds(out, "search_term_", "table_" + TERM_TABLE, true);
  economics(out, "ad_", "", true);
  economics(out, "search_term_", "search_term_", true);
  const effectPath = "keywords/keywords (analytics-product keywordEffect) 또는 analytics products/analytics_product_keywords";
  for (const [name, ko, field] of [["impressions", "노출", "impressions|views"], ["clicks", "클릭", "clicks"]]) {
    out.field("analyzer_" + name, "Product Analyzer 유입 검색어 " + ko, TIMES, effectPath + "/" + field, CLOSED, "상품에 연결된 검색어의 유입 규모 비교");
  }
  out.field("analyzer_periods", "Product Analyzer 유입 검색어 집계 기간 목록", TEXT, effectPath + "/period", CLOSED, "실제 합산된 주 시작일 또는 월; 쉼표 구분");
  out.field("search_index", "Keyword Index 검색 지수", "지수", "keywords/keyword_index/search_index (pv)", SNAPSHOT, "같은 nd에서 검색 수요 비교; 실검색 횟수 아님");
  out.field("seller_scale", "Keyword Index 판매자 규모", "셀러 수", "keywords/keyword_index/seller_scale (resultCnt)", SNAPSHOT, "같은 nd의 경쟁 규모 비교");
  out.field("keyword_index_nd", "Keyword Index 상대 기간", TEXT, "keywords/keyword_index/nd", SNAPSHOT, "번들 기간과 별개; 같은 nd끼리 비교");
  out.field("keyword_index_observed_at", "번들 keywords 명령 종료 시각", TEXT, "manifest/keywords/finished_at", SNAPSHOT, "번들 생성 시각의 근거; 캐시의 수집 시각이나 지수의 집계 종료일은 아님");

  const fallback = bundle.table("keywords", "keywords");
  const index = bundle.table("keywords", "keyword_index");
  const keyOf = r => {
    const k = keyword(r.keyword), pid = id(r.product_id);
    return k && (r.product_id == null || r.product_id === "" || pid)
      ? JSON.stringify([k, pid]) : null;
  };
  const byAds = [WORD_TABLE, TERM_TABLE].map(table => group(bundle, adsWords(bundle, table, fallback), r =>
    r.eligible && bundle.inRange(r.day) ? keyOf(r) : null, `${table} 범위·키 또는 조회 필터가 맞지 않는 행 제외`));
  const keywordEffect = (fallback || []).filter(r => r.source === "analytics-product keywordEffect");
  const effect = keywordEffect.length ? keywordEffect : bundle.table("analytics products", "analytics_product_keywords");
  const byAnalyzer = group(bundle, effect, r => id(r.product_id) ? keyOf(r) : null, "Product Analyzer 유입 검색어 키 미확인 제외");
  if (byAnalyzer.size) bundle.notes.add("유입 검색어 수치는 기존 Product Analyzer 집계표 그대로 사용; 원천의 빈값이 기존 보고서에서 0으로 변환됐는지는 검증 불가.");
  bundle.excluded((fallback || []).filter(r => r.source === "analytics-keyword getKeywords").length, "Keyword Analyzer getKeywords: 상품ID 없는 별도 계정 통계 제외");
  const byIndex = group(bundle, index, r => keyword(r.keyword), "Keyword Index 키워드 없음 제외");
  const keys = new Set(union(...byAds, byAnalyzer));
  const words = new Set([...keys].map(key => JSON.parse(key)[0]));
  for (const k of byIndex.keys()) if (!words.has(k)) keys.add(JSON.stringify([k, null]));
  if (keys.size) bundle.notes.add("상품ID 없는 광고·Index 단독 행은 product_id=null. 지수는 키워드 기준이라 상품 간 합산 금지. Optional Keywords와 Search Terms 광고 수치도 합산 금지.");
  for (const key of [...keys].sort(cmp)) {
    const [k, pid] = JSON.parse(key);
    const [wordRows, termRows] = byAds.map(map => map.get(key) || []);
    const effectRows = byAnalyzer.get(key) || [], indexRows = byIndex.get(k) || [];
    const periods = [...new Set(effectRows.map(r => text(r.period)).filter(Boolean))].sort(cmp);
    const indexValue = (values, column) => {
      const distinct = new Set(values.filter(value => value !== null));
      if (distinct.size > 1) bundle.notes.add(`Keyword Index ${column}: 정규화 키워드 중 값이 상충한 경우 제외`);
      return distinct.size === 1 ? [...distinct][0] : null;
    };
    const nd = indexValue(indexRows.map(r => text(r.nd)), "nd");
    const sameWindow = nd && indexRows.every(r => r.nd === nd);
    out.row({ keyword: k, product_id: pid, keyword_raw: (wordRows[0] ?? termRows[0] ?? effectRows[0] ?? indexRows[0]).keyword,
      ...adValues(wordRows, "ad_"), ...adValues(termRows, "search_term_"),
      analyzer_impressions: sum(effectRows, r => r.impressions ?? r.views), analyzer_clicks: sum(effectRows, r => r.clicks),
      analyzer_periods: periods.length ? periods.join(",") : null,
      search_index: sameWindow ? indexValue(indexRows.map(r => number(r.search_index)), "search_index") : null,
      seller_scale: sameWindow ? indexValue(indexRows.map(r => number(r.seller_scale)), "seller_scale") : null,
      keyword_index_nd: nd, keyword_index_observed_at: indexRows.length ? text(bundle.commands.get("keywords")?.finished_at) : null,
    });
  }
  return out.result();
}

const storeMetrics = [
  ["page_views", "전체 페이지 조회", TIMES, "pvLong.value", "요약표 전체 페이지 조회 규모 비교"],
  ["visitors", "전체 방문자", PEOPLE, "uvLong.value", "요약표 전체 일별 방문자 규모 비교"],
  ["minisite_page_views", "미니사이트 페이지 조회", TIMES, "minisitePv.value", "전체 조회와 별개로 미니사이트 유입 확인"],
  ["minisite_visitors", "미니사이트 방문자", PEOPLE, "minisiteUv.value", "전체 방문자와 별개로 미니사이트 유입 확인"],
];

function daily(bundle) {
  const out = new Combined(bundle, "daily");
  out.identity("date", "미국 태평양 집계 일자", DATE, "ads report/account_day/date · inquiries list/us_date · store summary/statDate.value", BUNDLE,
    "광고·문의 Pacific 날짜; 상점은 서버 날짜 표기 그대로 결합(시간대 미제공)");
  declareAds(out, "ad_", "account_day");
  for (const [name, ko, unit, field, judge] of storeMetrics) out.field("store_" + name, "상점 " + ko, unit, "store summary/store_summary/" + field, BUNDLE, judge);
  out.field("inquiry_count", "일별 문의 수", COUNT, "inquiries list/inquiries/us_date 또는 inquiries stats/inquiry_stats/us_date", BUNDLE, "해당 날짜의 목록 문의 행 수; 광고 문의와 중복 가능");
  economics(out, "ad_");
  out.ratio("ad_ctr", "광고 클릭률", RATE, "ad_clicks", "ad_impressions", "광고 노출 대비 클릭 비교");
  const dayKey = (value) => bundle.inRange(date(value)) ? value : null;
  const ads = group(bundle, bundle.table("ads report", "account_day"), r => dayKey(r.date), "account_day 날짜 미확인/범위 밖 제외");
  const store = group(bundle, bundle.table("store summary", "store_summary"), r => {
    const stat = dayKey(r["statDate.value"]);
    return stat && (r._query_day == null || stat === r._query_day) ? stat : null;
  }, "store_summary 날짜 미확인/조회일과 불일치/범위 밖 제외");
  const list = bundle.table("inquiries list", "inquiries");
  const inquiries = list === null ? group(bundle, bundle.table("inquiries stats", "inquiry_stats")?.filter(r => r.dimension === "us_date"),
    r => dayKey(r.value), "문의 통계 날짜 미확인/범위 밖 제외")
    : group(bundle, list, r => dayKey(r.us_date), "문의 목록 날짜 미확인/범위 밖 제외");
  if (store.size) bundle.notes.add("상점 일별 statDate에는 시간대가 없어 서버 날짜 표기만 일치시킴.");
  if (ads.size) bundle.notes.add("광고 계정 일별 행의 합이며 상품 일별 합으로 대체하지 않음.");
  for (const day of union(ads, store, inquiries)) {
    const storeRow = single(bundle, store.get(day) || [], "store_summary 같은 날짜의 중복 행 제외");
    const q = inquiries.get(day) || [];
    out.row({ date: day, ...adValues(ads.get(day) || [], "ad_"),
      ...Object.fromEntries(storeMetrics.map(([name, , , field]) => ["store_" + name, number(storeRow[field])])),
      inquiry_count: list === null ? sum(q, r => r.inquiries) : q.length || null,
    });
  }
  return out.result();
}

const builders = { products, keywords, daily };
function compare(dirA, dirB) {
  const key = ["table_kind", "product_id", "keyword", "date"];
  const fields = { table_kind: { ko: "조합 표 종류", unit: TEXT, source: "combine kind", period: "A·B 번들 기간", judge: "종류별 원래 키 사용; 나머지 키는 null" } };
  const rows = [], notes = new Set(), sources = new Map();
  let a, b;
  for (const kind of KINDS) {
    a = builders[kind](dirA());
    b = builders[kind](dirB());
    for (const k of a.key) fields[k] = a.fields[k];
    const values = Object.entries(a.fields).filter(([k]) => !a.key.includes(k));
    for (const [k, spec] of values) {
      for (const side of ["a", "b"]) fields[`${kind}_${k}_${side}`] = { ...spec, ko: spec.ko + " " + side.toUpperCase(), period: side.toUpperCase() + ": " + spec.period };
      if (nonnumeric.has(spec.unit)) continue;
      const base = `${kind}_${k}`;
      const operands = `${base}_b, ${base}_a`;
      fields[base + "_delta"] = { ...spec, ko: spec.ko + " 차이", source: operands, period: "A·B: " + spec.period, judge: "B − A; 두 값이 모두 있을 때만 비교" };
      fields[base + "_change_rate"] = { ...fields[base + "_delta"], ko: spec.ko + " 변화율", unit: CHANGE, judge: "(B − A) / A; A=0 또는 값 없음이면 null" };
      fields[base + "_reason"] = { ...fields[base + "_delta"], ko: spec.ko + " 비교 불가 사유", unit: TEXT, judge: "누락과 기준값 0을 구분" };
    }
    for (const [side, table] of [["a", a], ["b", b]]) {
      table.notes.forEach(note => notes.add(`${side.toUpperCase()} ${kind}: ${note}`));
      table.sources.forEach(source => sources.set(`${side}/${source.command}/${source.table}`, { ...source, side }));
    }
    const index = (table) => new Map(table.rows.map(row => [JSON.stringify(table.key.map(k => row[k])), row]));
    const aa = index(a), bb = index(b);
    for (const id of union(aa, bb)) {
      const x = aa.get(id), y = bb.get(id);
      const row = { table_kind: kind, ...Object.fromEntries(a.key.map(k => [k, (x ?? y)[k]])) };
      for (const [k, spec] of values) {
        const base = `${kind}_${k}`;
        const av = row[base + "_a"] = x?.[k] ?? null;
        const bv = row[base + "_b"] = y?.[k] ?? null;
        if (nonnumeric.has(spec.unit)) continue;
        const comparable = !["search_index", "seller_scale"].includes(k) || x?.keyword_index_nd === y?.keyword_index_nd;
        const delta = row[base + "_delta"] = av !== null && bv !== null && comparable ? add([bv, -av]) : null;
        row[base + "_change_rate"] = delta !== null && av !== 0 ? number(delta / av) : null;
        row[base + "_reason"] = av === null || bv === null ? "A 또는 B 값 없음"
          : !comparable ? "Keyword Index nd 불일치" : av === 0 ? "분모 A=0"
            : row[base + "_change_rate"] === null ? "계산 범위 초과" : null;
      }
      rows.push(row);
    }
  }
  return { schema: SCHEMA, kind: "compare", bundle: a.bundle, bundle_b: b.bundle, period: { a: a.period, b: b.period },
    key, fields, rows: rows.map(r => Object.fromEntries(Object.keys(fields).map(k => [k, r[k] ?? null]))),
    notes: [...notes], sources: [...sources.values()] };
}

function csv({ fields, rows }) {
  const escape = value => {
    const s = String(value ?? "");
    return /[",\r\n]/.test(s) ? '"' + s.replaceAll('"', '""') + '"' : s;
  };
  const header = Object.keys(fields);
  return [header.join(","), ...rows.map(r => header.map(k => escape(r[k])).join(","))].join("\r\n") + "\r\n";
}

function save(result, json, csvText) {
  const dir = path.join(result.bundle, "combine");
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  if (!lstatSync(dir).isDirectory()) throw new Error("combine/ 경로는 일반 디렉터리여야 한다");
  const staging = mkdtempSync(path.join(dir, ".tmp-"));
  try {
    for (const [ext, contents] of [["json", json], ["csv", csvText]]) {
      const file = result.kind + "." + ext;
      writeFileSync(path.join(staging, file), contents, { mode: 0o600 });
      renameSync(path.join(staging, file), path.join(dir, file));
    }
  } finally {
    rmSync(staging, { recursive: true, force: true });
  }
}

export function combineMain(args, opt, log) {
  const [kind] = args;
  if (args.length !== 1 || ![...KINDS, "compare"].includes(kind)) throw new Error("combine products|keywords|daily|compare 필요");
  if (opt.from.length !== 1 || !opt.from[0]) throw new Error("combine --from <번들 폴더> 한 개 필요");
  if (kind === "compare" ? !opt["from-b"] : opt["from-b"] != null) throw new Error("--from-b <번들2>는 combine compare에서만 필수");
  if (opt.json && opt.csv) throw new Error("--json과 --csv는 함께 쓸 수 없다");
  const result = kind === "compare" ? compare(() => new Bundle(opt.from[0]), () => new Bundle(opt["from-b"]))
    : builders[kind](new Bundle(opt.from[0]));
  const json = JSON.stringify(result, null, 2) + "\n", csvText = csv(result);
  save(result, json, csvText);
  log((opt.csv ? csvText : json).trimEnd());
  return 0;
}
