import { fileURLToPath } from "node:url";
import { asideCmd, pythonCmd, SPAWN_SHELL } from "./platform.mjs";
// OneTalk inbox commands. Running a command opens that conversation (an unread one is marked read,
// which is normal: answering an inquiry means opening it) and sends right away.
// --dry-run = preview only: the script finds the popup, the conversation row, the input and the tool
// icons and reports their positions, with zero clicks or typing (an unread conversation stays unread).
// Every send (ok or failed) appends one line to <account outDir>/inquiry-replies.jsonl (sendlog.mjs).
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { appendSend } from "./sendlog.mjs";

// command -> {action, write: sends or changes something for the buyer, conversation: needs a target,
//             opens: read-only but opens a panel or the conversation, item: log label of a send}
export const ONETALK = {
  reply: { action: "reply", write: true, conversation: true, item: "reply", help: "답장 --text '…' | --text-file f.txt" },
  "send-card": { action: "send-card", write: true, conversation: true, item: "card", help: "명함 보내기" },
  "send-file": { action: "send-file", write: true, conversation: true, item: "file", help: "파일 보내기 --path x.pdf" },
  "send-product": { action: "send-product", write: true, conversation: true, item: "product", help: "상품 카드 보내기 --product-id N" },
  "template send": { action: "template-send", write: true, conversation: true, item: "template", help: "빠른 답장 템플릿 보내기 --template '이름'" },
  "tag add": { action: "tag-add", write: true, conversation: true, item: "tag-add", help: "대화에 태그 달기 --tag '이름'" },
  "tag remove": { action: "tag-remove", write: true, conversation: true, item: "tag-remove", help: "대화에서 태그 빼기 --tag '이름'" },
  inspect: { action: "inspect", write: false, opens: true, conversation: true, help: "대화 열어 확인만: 본 상품(고객 패널)·대화 칸 수·최근 대화 끝부분(이메일·전화 가림, 출력만)" },
  draft: { action: "draft", write: false, opens: true, conversation: true, help: "AI 초안 생성만(전송 안 함) [--button copilot|'draft summary'|'content enhancer']" },
  "template list": { action: "template-list", write: false, opens: true, help: "템플릿 이름 목록(패널을 열어 읽음)" },
  "tags list": { action: "tags-list", write: false, help: "태그 이름·색 목록(읽기)" },
  "ui probe": { action: "ui-probe", write: false, opens: true, help: "툴바 아이콘 hover 툴팁·역할, 더보기 메뉴 항목, 대화 행 속성 이름" },
};

// -> "dry-run" | "open" | "send". No approval step: the command itself is the instruction.
export function gate(spec, opt) {
  if (opt["dry-run"]) return "dry-run";
  if (spec.write) return "send";
  return spec.opens ? "open" : "dry-run";
}

// Find the target in a sanitized inquiry list (raw/inquiries.jsonl of a dump). -> {ids, unread} | null
export function findConversation(dumpDir, { inquiryId, tradeId }) {
  const file = path.join(dumpDir, "raw", "inquiries.jsonl");
  if (!existsSync(file)) return null;
  for (const line of readFileSync(file, "utf8").split("\n")) {
    if (!line.trim()) continue;
    const rec = JSON.parse(line);
    for (const it of rec?.json?.list || []) {
      const ids = [it.id, it.tradeId, it.secTradeId, it.feedbackId].filter((x) => x != null).map(String);
      if ((inquiryId && ids.includes(String(inquiryId))) || (tradeId && (String(it.tradeId) === String(tradeId) || String(it.secTradeId) === String(tradeId)))) {
        return { ids, unread: !!(it.hasUnread || Number(it.unreadCount) > 0) };
      }
    }
  }
  return null;
}

export function readText(opt) {
  if (opt["text-file"]) return readFileSync(opt["text-file"], "utf8");
  return opt.text ?? null;
}

export function buildParams(spec, opt, mode, conversation) {
  return {
    action: spec.action, mode, ids: conversation ? conversation.ids : [], match: conversation ? conversation.keys || null : null,
    text: spec.action === "reply" && mode === "send" ? readText(opt) : null,
    textLength: spec.action === "reply" ? (readText(opt) || "").length : 0, filePath: opt.path ? path.resolve(opt.path) : null,
    productId: opt["product-id"] ?? null, template: opt.template ?? null, tag: opt.tag ?? null, draftButton: opt.button ?? null,
    shot: !!opt.shot,
    // send-file: Aside only opens files inside its own session folder, so the file travels as bytes (send mode only)
    ...(spec.action === "send-file" && mode === "send" && opt.path ? fileParams(opt.path) : {}),  // Aside saves under its session folder ./artifacts/onetalk-sendbox.png
  };
}

const MIME = { pdf: "application/pdf", jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", gif: "image/gif", webp: "image/webp",
  xls: "application/vnd.ms-excel", xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", doc: "application/msword",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ppt: "application/vnd.ms-powerpoint",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation", txt: "text/plain", csv: "text/csv", zip: "application/zip", mp4: "video/mp4" };
export function fileParams(file) {
  const ext = path.extname(file).slice(1).toLowerCase();
  return { fileName: path.basename(file), fileMime: MIME[ext] || "application/octet-stream", fileB64: readFileSync(file).toString("base64") };
}

export function validate(spec, opt) {
  if (spec.conversation && !opt["inquiry-id"] && !opt["trade-id"] && !opt["buyer-ali-id"]) throw new Error("대화 지정: --inquiry-id N | --trade-id N | --buyer-ali-id N");
  const need = { reply: () => readText(opt), "send-file": () => opt.path, "send-product": () => opt["product-id"], "template-send": () => opt.template, "tag-add": () => opt.tag, "tag-remove": () => opt.tag }[spec.action];
  if (need && !need()) throw new Error(`${spec.action}: 필요한 값이 없다 (${spec.help})`);
  if (spec.action === "send-file" && !existsSync(opt.path)) throw new Error(`파일 없음: ${opt.path}`);
}

// Script source goes to Aside on stdin, so typed text never shows up in a process list.
export function runScript(asideAccount, params, { spawn = spawnSync } = {}) {
  const engine = path.join(path.dirname(path.dirname(fileURLToPath(import.meta.url))), "engine", "onetalk.js");
  const src = readFileSync(engine, "utf8").replace("const P = __PARAMS__;", "const P = " + JSON.stringify(params) + ";");
  const input = "await eval(" + JSON.stringify("(async () => {\n" + src + "\n})()") + ")\n";
  const r = spawn(asideCmd(), ["--host", "local", "--account", asideAccount, "repl"],{ input, encoding: "utf8", timeout: 170000, shell: SPAWN_SHELL });
  const text = (r.stdout || "") + (r.stderr || "");
  const line = text.split("\n").find((l) => l.startsWith("RESULT "));
  return line ? JSON.parse(line.slice(7)) : { status: "aside_failed", detail: redact(text.slice(-300), params) };
}

// Typed text and lookup keys never reach output, even inside an error tail.
export function redact(text, params) {
  const m = params.match || {};
  const secrets = [params.text, ...(m.aliIds || []), ...(m.cids || []), ...(m.names || []), ...(m.times || []).map(String)].filter((v) => v && String(v).length >= 2);
  return secrets.reduce((t, v) => t.replaceAll(String(v), "***"), text);
}

// Look the conversation up through the read-only inquiry list dump (temp folder, removed after).
// The target's match keys (sender ali ids, conversation ids, times, country) come back on the
// engine's stdout pipe as one LOOKUP line and stay in this process's RAM: never on disk or printed.
export function readLookup(stdout) {
  const line = String(stdout || "").split("\n").find((l) => l.startsWith("LOOKUP "));
  if (!line) return null;
  try { return JSON.parse(line.slice(7)); } catch (e) { return null; }
}

export function lookupConversation({ opt, account, configPath, enginePath, spawn = spawnSync }) {
  const target = { inquiryId: opt["inquiry-id"], tradeId: opt["trade-id"] };
  // --buyer-ali-id = the buyer's OneTalk ali id (row data-ali-id; alibaba-accio conversations shows it as contactAliId):
  // no inquiry-list lookup needed.
  if (opt["buyer-ali-id"] && !target.inquiryId && !target.tradeId) return { ids: [], unread: null, keys: { aliIds: [String(opt["buyer-ali-id"])] } };
  if (opt.from?.length) return findConversation(opt.from[0], target);
  const dir = mkdtempSync(path.join(tmpdir(), "alibaba-seller-inq-"));
  try {
    const args = [enginePath, "--account", account.name, "--config", configPath, "--pages", "inquiries", "--out", dir,
      "--max-pages", String(opt["max-pages"] || 30)];
    if (target.inquiryId) args.push("--lookup-inquiry-id", String(target.inquiryId));
    if (target.tradeId) args.push("--lookup-trade-id", String(target.tradeId));
    const [exe, ...pre] = pythonCmd();
    const r = spawn(exe, [...pre, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "inherit"], timeout: 600000 });
    if (r.status !== 0) throw new Error(`문의 목록 조회 실패(종료 코드 ${r.status})`);
    const found = findConversation(dir, target);
    return found && { ...found, keys: readLookup(r.stdout) };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
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

export function onetalkMain(spec, opt, { account, configPath, enginePath, spawn, log = console.log, flow = null, conversation = null }) {
  validate(spec, opt);
  // panel commands (template list, ui probe, draft …) open the given conversation too, so its toolbar is on screen
  const target = opt["inquiry-id"] || opt["trade-id"] || opt["buyer-ali-id"];
  if ((spec.conversation || target) && !conversation) {
    conversation = lookupConversation({ opt, account, configPath, enginePath, ...(spawn ? { spawn } : {}) });
    if (!conversation) throw new Error("문의 목록에서 그 대화를 못 찾음(--max-pages 를 늘리거나 id 확인)");
  }
  const mode = gate(spec, opt);
  const res = runScript(account.asideAccount, buildParams(spec, opt, mode, conversation), { spawn });
  if (res.shotPng) {  // --shot: the message pane picture (no header, no contact list) goes next to where the command ran
    const file = path.resolve(`onetalk-${spec.action}-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "")}.png`);
    writeFileSync(file, Buffer.from(res.shotPng, "base64"));
    delete res.shotPng;
    res.shot = file;
  }
  log(JSON.stringify(res, null, 1));
  if (mode === "send") logSend(spec, opt, account, res, { flow });
  else if (spec.write) log("dry-run: 미리보기만, 아무것도 보내지 않았다(--dry-run 빼면 바로 보냄)");
  return res.status === "ok" ? 0 : 1;
}
