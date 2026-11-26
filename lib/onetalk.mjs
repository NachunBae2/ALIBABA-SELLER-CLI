import { fileURLToPath } from "node:url";
import { pythonCmd } from "./platform.mjs";
// OneTalk inbox commands.
// API commands (api/op): the CLI calls OneTalk's own HTTP endpoints with the account's saved login
// cookies (engine/onetalk_cli.mjs, no browser, no clicks): reply, send-card, send-file, send-product,
// template send/list, tags, conversations, messages, mark-read. A send goes out right away and the
// conversation is read back over the API to confirm the new message. --dry-run = plan only (nothing
// sent; template send still looks the template up, read-only, to show it exists and its length).
// inspect reads messages/customer-panel products; draft generates Copilot content over HTTP.
// Every send (ok or failed) appends one line to <account outDir>/inquiry-replies.jsonl (sendlog.mjs).
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { appendSend } from "./sendlog.mjs";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
export const SEND_ENV = "ALIBABA_SELLER_ALLOW_SEND";
export const SEND_ENV_VALUE = "owner-approved";
export class Refused extends Error {
  constructor(message) { super(message); this.code = 77; }
}

export function sendGate(spec, opt, env = process.env) {
  if (opt.send && !spec.write) throw new Refused("--send 는 보내는 명령에만 쓴다");
  if (opt.send && env[SEND_ENV] !== SEND_ENV_VALUE)
    throw new Refused(`실발송 거부: --send 와 환경변수 ${SEND_ENV}=${SEND_ENV_VALUE} 가 둘 다 있어야 한다`);
}

// command -> {action, write: sends or changes something for the buyer, conversation: needs a target,
//             item: log label of a send}
export const ONETALK = {
  reply: { action: "reply", api: "text", write: true, conversation: true, item: "reply", help: "답장 --text '…' | --text-file f.txt" },
  "send-card": { action: "send-card", api: "card", write: true, conversation: true, item: "card", help: "명함 보내기(Exchange name card)" },
  "send-file": { action: "send-file", api: "file", write: true, conversation: true, item: "file", help: "파일 보내기 --path x.pdf (이미지는 사진으로 감)" },
  "send-product": { action: "send-product", api: "product", write: true, conversation: true, item: "product", help: "상품 카드 보내기 --product-id N | --product '검색어'" },
  buyer: { action: "buyer", op: "buyer", write: false, conversation: true, help: "바이어 등급·levelSource·비식별 정보 조회(응답 행 없음: no_buyer_info)" },
  conversations: { action: "conversations", op: "conversations", write: false, help: "대화 목록(최근 순) [--count N] [--cursor T]: buyerAliId·level(없으면 null)·levelSource·안읽음 수·마지막 메시지 종류/시각" },
  messages: { action: "messages", op: "messages", write: false, conversation: true, help: "대화 읽기(읽음 처리 안 함) [--count N] [--with-text: 글 표시, 이메일·전화 가림]" },
  "mark-read": { action: "mark-read", op: "mark-read", write: true, conversation: true, item: "mark-read", help: "문의 읽음 처리 (--inquiry-id N | --trade-id N)" },
  "mark-unread": { action: "mark-unread", op: "mark-unread", write: true, conversation: true, item: "mark-unread", help: "문의 안읽음 표시 (--inquiry-id N | --trade-id N)" },
  "template send": { action: "template-send", api: "template", dryOp: "template-find", write: true, conversation: true, item: "template", help: "빠른 답장 템플릿 보내기 --template '이름'(정확히, 아니면 그 말로 시작·포함하는 1개)" },
  "tag add": { action: "tag-add", op: "tag-add", write: true, conversation: true, item: "tag-add", help: "대화에 태그 달기 --tag '이름' (내가 만든 태그·별표)" },
  "tag remove": { action: "tag-remove", op: "tag-remove", write: true, conversation: true, item: "tag-remove", help: "대화에서 태그 빼기 --tag '이름'" },
  inspect: { action: "inspect", op: "inspect", write: false, conversation: true, help: "API 조회: 본 상품(고객 패널)·조회 메시지 수·최근 대화 끝부분(이메일·전화 가림) [--count N]" },
  draft: { action: "draft", op: "draft", write: false, conversation: true, help: "AI 초안 API 생성만(전송 안 함) [--draft-kind reply|summary]" },
  "template list": { action: "template-list", op: "templates", write: false, help: "빠른 답장 템플릿 목록(id·이름)" },
  "tags list": { action: "tags-list", op: "tags", write: false, help: "계정 태그 목록(이름·종류·색)" },
};

export function readText(opt) {
  if (opt["text-file"]) return readFileSync(opt["text-file"], "utf8");
  return opt.text ?? null;
}

export function validate(spec, opt) {
  if (spec.action === "draft" && opt["draft-kind"] && !["reply", "summary"].includes(opt["draft-kind"])) throw new Error("--draft-kind reply|summary");
  if (spec.action.startsWith("mark-") && !opt["inquiry-id"] && !opt["trade-id"]) throw new Error(`${spec.action}: 문의 지정 필요 --inquiry-id N | --trade-id N`);
  if (spec.conversation && !opt["inquiry-id"] && !opt["trade-id"] && !opt["buyer-ali-id"]) throw new Error("대화 지정: --inquiry-id N | --trade-id N | --buyer-ali-id N");
  const need = { reply: () => readText(opt), "send-file": () => opt.path, "send-product": () => opt["product-id"] || opt.product, "template-send": () => opt.template, "tag-add": () => opt.tag, "tag-remove": () => opt.tag }[spec.action];
  if (need && !need()) throw new Error(`${spec.action}: 필요한 값이 없다 (${spec.help})`);
  if (spec.action === "send-file" && !existsSync(opt.path)) throw new Error(`파일 없음: ${opt.path}`);
}

// Typed text and lookup keys never reach output, even inside an error tail.
export function redact(text, params) {
  const m = params.match || {};
  const secrets = [params.text, ...(m.aliIds || []), ...(m.cids || []), ...(m.names || []), ...(m.times || []).map(String)].filter((v) => v && String(v).length >= 2);
  return secrets.reduce((t, v) => t.replaceAll(String(v), "***"), text);
}

// One send = one log line (sent or failed). The reply body is logged; buyer name and contact never are.
export function logSend(spec, opt, account, res, { flow = null, now = new Date() } = {}) {
  return appendSend(account, {
    at: now.toISOString(), account: account.name, inquiryId: String(opt["inquiry-id"] ?? ""), tradeId: opt["trade-id"] ? String(opt["trade-id"]) : null,
    buyerAliId: opt["buyer-ali-id"] ? String(opt["buyer-ali-id"]) : null,
    flow, item: spec.item, text: spec.action === "reply" ? readText(opt) : spec.action === "template-send" ? opt.template : null,
    file: opt.path ? path.basename(opt.path) : null, productId: opt["product-id"] ? String(opt["product-id"]) : null, tag: opt.tag ?? null,
    result: res.status === "ok" ? "success" : "fail", status: res.status, readback: res.readback || null,
  });
}

// API request for engine/onetalk_cli.mjs. Reply text and file path travel on stdin only.
export function apiRequest(spec, opt) {
  const req = {};
  if (opt["buyer-ali-id"]) req.buyerAliId = String(opt["buyer-ali-id"]);
  if (opt["inquiry-id"]) req.inquiryId = String(opt["inquiry-id"]);
  if (opt["trade-id"]) req.tradeId = String(opt["trade-id"]);
  if (opt["max-pages"]) req.maxPages = Number(opt["max-pages"]);
  if (opt.count) req.count = Number(opt.count);
  if (opt.cursor) req.cursor = Number(opt.cursor);
  if (opt["with-text"]) req.withText = true;
  if (spec.action === "draft" && opt["draft-kind"]) req.draftKind = opt["draft-kind"];
  if (opt.tag) req.tag = String(opt.tag);
  if (spec.api) {
    req.kind = spec.api;
    if (spec.api === "text") req.text = readText(opt);
    if (spec.api === "product") {
      if (opt["product-id"]) req.productId = String(opt["product-id"]);
      if (opt.product) req.product = String(opt.product);
    }
    if (spec.api === "file") req.path = path.resolve(opt.path);
    if (spec.api === "template") req.template = String(opt.template);
  }
  return req;
}

export function apiPlan(spec, req) {
  const { text, path: file, ...rest } = req;
  return { status: "dry_run", op: spec.op || "send", ...rest, ...(text != null ? { textLength: text.length } : {}), ...(file ? { file: path.basename(file) } : {}) };
}

// Saved login cookies (engine/cookie_store.py, written by `alibaba-data login`) go straight from
// that process's stdout pipe to the API engine's stdin: never printed, never on disk here.
export function loadCookies(account, configPath, { spawn = spawnSync } = {}) {
  const store = path.join(ROOT, "engine", "cookie_store.py");
  const missing = { status: "error", error: `저장된 로그인 쿠키 없음: alibaba-data login --account ${account.name}` };
  const [exe, ...pre] = pythonCmd();
  const r = spawn(exe, [...pre, store, "load", account.name, "--config", configPath], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 120000 });
  let cookies = null;
  try { cookies = JSON.parse(String(r.stdout || "").trim().split("\n").pop()); } catch (e) { cookies = null; }
  if (r.status === 0 && Array.isArray(cookies) && cookies.length) return { cookies };
  return { fail: Object.defineProperty(missing, "exitCode", { value: 2, enumerable: false }) };
}

export function runApi(op, req, account, configPath, { spawn = spawnSync } = {}) {
  const { cookies, fail } = loadCookies(account, configPath, { spawn });
  if (fail) return fail;
  const r = spawn(process.execPath, [path.join(ROOT, "engine", "onetalk_cli.mjs"), op],
    { input: JSON.stringify({ cookies, request: req }), encoding: "utf8", stdio: ["pipe", "pipe", "pipe"], timeout: 300000 });
  const line = String(r.stdout || "").trim().split("\n").pop();
  try { return Object.defineProperty(JSON.parse(line), "exitCode", { value: r.status, enumerable: false }); } catch (e) {
    return { status: "api_failed", detail: redact(String(r.stderr || r.error || "").slice(-300), { text: req.text, match: { aliIds: [req.buyerAliId] } }) };
  }
}

// --transport accio: conversations, messages, buyer from the Accio Work gateway's IM tools
// (engine/accio_gateway.py inquiry <op>, same JSON as onetalk_cli; what the tools lack stays null).
export const ACCIO_OPS = ["conversations", "messages", "buyer"];

export function accioRefusal(spec, opt) {
  if (!ACCIO_OPS.includes(spec.op)) return spec.write ? "보내기·표시 바꾸기는 게이트웨이로 하지 않는다(쓰기 도구 안 부름)" : "게이트웨이에 맞는 읽기 도구 없음";
  if (opt["inquiry-id"] || opt["trade-id"]) return "게이트웨이에 문의 목록 도구가 없어 --buyer-ali-id 로만 대화를 찾는다";
  if (opt["with-text"]) return "게이트웨이 메시지에 유형(messageType)이 없어 글 메시지만 골라 가릴 수 없다";
  return null;
}

export function runAccio(op, req, { spawn = spawnSync } = {}) {
  const [exe, ...pre] = pythonCmd();
  const r = spawn(exe, [...pre, path.join(ROOT, "engine", "accio_gateway.py"), "inquiry", op],
    { input: JSON.stringify({ request: req }), encoding: "utf8", stdio: ["pipe", "pipe", "pipe"], timeout: 300000 });
  const line = String(r.stdout || "").trim().split("\n").pop();
  try { return Object.defineProperty(JSON.parse(line), "exitCode", { value: r.status, enumerable: false }); } catch (e) {
    return { status: "accio_failed", detail: String(r.stderr || r.error || "").slice(-300) };
  }
}

function apiMain(spec, opt, { account, configPath, spawn, log, flow }) {
  const req = apiRequest(spec, opt);
  if (opt["dry-run"]) {
    // a dry run with a read-only lookup (template send: does the template exist, how long is it)
    const found = spec.dryOp ? runApi(spec.dryOp, req, account, configPath, spawn ? { spawn } : {}) : null;
    log(JSON.stringify(found ? { ...apiPlan(spec, req), lookup: found } : apiPlan(spec, req), null, 1));
    if (found && found.status !== "ok") return [2, 64].includes(found.exitCode) ? found.exitCode : 1;
    if (spec.write) log("dry-run: 미리보기만, 아무것도 보내지 않았다(--dry-run 빼면 바로 보냄)");
    return 0;
  }
  const res = opt.transport === "accio" ? runAccio(spec.op, req, spawn ? { spawn } : {})
    : runApi(spec.op || "send", req, account, configPath, spawn ? { spawn } : {});
  log(JSON.stringify(res, null, 1));
  if (spec.write) logSend(spec, opt, account, res, { flow });
  if (spec.op === "buyer" && res.status === "no_buyer_info" && res.exitCode === 0) return 0;
  return res.status === "ok" ? 0 : [2, 64].includes(res.exitCode) ? res.exitCode : 1;  // login missing or expired, bad argument
}

export function onetalkMain(spec, opt, { account, configPath, enginePath, spawn, log = console.log, err = console.error, flow = null, conversation = null, env = process.env }) {
  // This gate intentionally precedes validation, cookie load and inquiry lookup.
  sendGate(spec, opt, env);
  if (opt.transport === "accio") {
    const why = accioRefusal(spec, opt);
    if (why) { err(`--transport accio 안 됨(${spec.action}): ${why}`); return 64; }
  }
  validate(spec, opt);
  return apiMain(spec, opt, { account, configPath, spawn, log, flow });
}
