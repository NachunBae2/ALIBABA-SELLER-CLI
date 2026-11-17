// Inquiry-handling flows: flows/<name>.json = conditions + ordered steps, run through the OneTalk commands.
//   when:  country / notCountry (ISO-2 lists), viewedProduct ("any" | "none" | [ids]), spam (false = skip spam,
//          the default), buyerLevel (list of level codes), firstInquiry (true | false). Missing key = any.
//   steps: check (read customer-panel products through HTTP), reply {text}, card, product {productId}, file {path}. Text and ids take {product_id},
//          {product_title}, {country}; a step whose placeholder has no value, or whose file is missing, is skipped
//          and reported. Each send is logged (sendlog.mjs); an inquiry that already got this flow is skipped.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ONETALK, onetalkMain, runApi } from "./onetalk.mjs";
import { flowAlreadySent } from "./sendlog.mjs";

export const FLOWS_DIR = path.join(path.dirname(path.dirname(fileURLToPath(import.meta.url))), "flows");
const STEP_COMMAND = { reply: "reply", card: "send-card", product: "send-product", file: "send-file" };

export function loadFlows(dir = FLOWS_DIR) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir).filter((f) => f.endsWith(".json")).sort()
    .map((f) => ({ ...JSON.parse(readFileSync(path.join(dir, f), "utf8")), name: f.replace(/\.json$/, ""), file: path.join(dir, f) }));
}

// row = one inquiries-list row (report.py inquiry-list); facts.products = product ids seen after check.
// -> null when it matches, else the reason it does not.
export function whyNot(flow, row, facts = {}, { includeSpam = false } = {}) {
  const w = flow.when || {};
  const up = (xs) => (xs || []).map((x) => String(x).toUpperCase());
  const country = String(row.buyer_country || "").toUpperCase();
  const spam = row.spam === true || row.spam === "True" || row.spam === "true";
  if (spam && !includeSpam && w.spam !== true) return "스팸 표시(기본 건너뜀, --include-spam)";
  if (w.spam === true && !spam) return "스팸 아님";
  if (w.country?.length && !up(w.country).includes(country)) return `국가 ${country || "-"} 조건 밖`;
  if (w.notCountry?.length && up(w.notCountry).includes(country)) return `국가 ${country} 제외`;
  if (w.buyerLevel?.length && !w.buyerLevel.map(String).includes(String(row.buyer_level || ""))) return `바이어 등급 ${row.buyer_level || "-"} 조건 밖`;
  if (w.firstInquiry != null) {
    const first = !(row.history_inquiry === true || row.history_inquiry === "True" || Number(row.inquiry_count) > 1);
    if (w.firstInquiry !== first) return w.firstInquiry ? "첫 문의 아님" : "첫 문의";
  }
  if (!facts.products) return null;
  const products = facts.products;
  if (w.viewedProduct === "any" && !products.length) return "본 상품 없음";
  if (w.viewedProduct === "none" && products.length) return "본 상품 있음";
  if (Array.isArray(w.viewedProduct) && !products.some((p) => w.viewedProduct.map(String).includes(String(p.id)))) return "본 상품 조건 밖";
  return null;
}

export function fill(template, vars) {
  let missing = null;
  const out = String(template).replace(/\{(\w+)\}/g, (_, k) => { if (vars[k] == null || vars[k] === "") missing = k; return vars[k] ?? ""; });
  return { value: out, missing };
}

const needsCheck = (flow) => (flow.steps || []).some((s) => s.do === "check") || flow.when?.viewedProduct != null ||
  JSON.stringify(flow.steps || []).includes("{product_");

// Product ids the list row knows (product inquiries) + customer-panel API products.
function productsOf(row, panel) {
  const out = [];
  if (row.product_id) out.push({ id: String(row.product_id), title: String(row.viewed_products || "").replace(/^\d+\s*/, "") });
  for (const p of panel || []) if (!out.some((o) => o.id === p.id)) out.push({ id: p.id, title: p.title });
  return out;
}

export function runFlowOn(flow, row, opt, ctx) {
  const { account, log, spawn } = ctx;
  const inquiryId = String(row.inquiry_id);
  const report = { inquiryId, flow: flow.name, steps: [] };
  if (!opt["dry-run"] && flowAlreadySent(account, flow.name, inquiryId)) return { ...report, skipped: "이 문의에 이 플로우를 이미 보냄(로그)" };
  const early = whyNot(flow, row, {}, { includeSpam: opt["include-spam"] }); // product conditions wait for the check
  if (early) return { ...report, skipped: early };
  const base = { "inquiry-id": inquiryId, ...(opt["dry-run"] ? { "dry-run": true } : {}) };
  let panel = [];
  if (needsCheck(flow)) {
    // Dry runs still read facts; only the later write steps become plans.
    const res = (ctx.inspect || runApi)("inspect", { inquiryId, maxPages: Number(opt["max-pages"]) || 30 },
      account, ctx.configPath, spawn ? { spawn } : {});
    panel = res.found?.products;
    report.steps.push({ do: "check", status: res.status, products: panel?.map((p) => p.id) ?? null,
      reason: res.found?.productsReason ?? res.error ?? null, transport: res.transport });
    if (res.status !== "ok" || !Array.isArray(panel)) return { ...report, stopped: "고객 패널 상품 조회 실패" };
  }
  const products = productsOf(row, panel);
  const why = whyNot(flow, row, { products }, { includeSpam: opt["include-spam"] });
  if (why) return { ...report, skipped: why };
  const vars = { product_id: products[0]?.id, product_title: products[0]?.title, country: row.buyer_country };
  for (const step of flow.steps || []) {
    const cmd = STEP_COMMAND[step.do];
    if (!cmd) continue; // check ran above; legacy open is a no-op
    const o = { ...base };
    if (step.do === "reply") { const f = fill(step.text || "", vars); if (f.missing) { report.steps.push({ do: step.do, skipped: `{${f.missing}} 값 없음` }); continue; } o.text = f.value; }
    if (step.do === "product") { const f = fill(step.productId || "{product_id}", vars); if (f.missing) { report.steps.push({ do: step.do, skipped: `{${f.missing}} 값 없음` }); continue; } o["product-id"] = f.value; }
    if (step.do === "file") {
      const f = fill(step.path || "", vars);
      const p = f.value && path.resolve(path.dirname(flow.file || "."), f.value);
      if (f.missing || !p || !existsSync(p)) { report.steps.push({ do: step.do, skipped: "파일 없음" }); continue; }
      o.path = p;
    }
    const code = onetalkMain(ONETALK[cmd], o, { ...ctx, flow: flow.name, log: ctx.quiet ? () => {} : log });
    report.steps.push({ do: step.do, result: code === 0 ? "success" : "fail" });
    if (code !== 0) { report.stopped = step.do + " 실패, 나머지 단계 중단"; break; }
  }
  return report;
}

export function flowMain(args, opt, ctx) {
  const { log, err = console.error } = ctx;
  const flows = loadFlows(opt["flows-dir"] || ctx.flowsDir);
  if (args[0] === "list" || !args.length) {
    log(flows.length ? flows.map((f) => `${f.name.padEnd(28)} ${f.description || ""}`).join("\n") : "플로우 없음(flows/*.json)");
    return 0;
  }
  if (args[0] !== "run") { err("flow list | flow run <이름> (--inquiry-id N | --unanswered)"); return 64; }
  const flow = flows.find((f) => f.name === args[1]);
  if (!flow) { err(`플로우 없음: ${args[1]} (flow list)`); return 64; }
  if (!opt["inquiry-id"] && !opt.unanswered) { err("대상: --inquiry-id N 또는 --unanswered"); return 64; }
  const rows = ctx.listInquiries(opt);
  const targets = opt["inquiry-id"] ? rows.filter((r) => String(r.inquiry_id) === String(opt["inquiry-id"]))
    : rows.filter((r) => String(r.reply_status) === "0");
  if (!targets.length) { err(opt["inquiry-id"] ? "문의 목록에 그 문의가 없음(--days 를 늘리기)" : "답장 안 한 문의 없음"); return opt["inquiry-id"] ? 1 : 0; }
  let failed = 0;
  for (const row of targets) {
    const r = runFlowOn(flow, row, opt, ctx);
    if (r.stopped) failed++;
    log(JSON.stringify(r));
  }
  return failed ? 1 : 0;
}
