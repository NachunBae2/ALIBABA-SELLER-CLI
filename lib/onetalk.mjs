import { fileURLToPath } from "node:url";
import { pythonCmd } from "./platform.mjs";
import { runRepl } from "./runner.mjs";
// Remaining browser-only OneTalk commands. Default is a dry run: finds the popup, conversation row,
// the input and the tool icons and reports their positions, with zero clicks or typing.
// Writing to a buyer needs both --send and ALIBABA_SELLER_ALLOW_SEND=owner-approved.
// Opening an unread conversation marks it read, so an unread target needs --allow-mark-read.
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

export const SEND_ENV = "ALIBABA_SELLER_ALLOW_SEND";
export const SEND_ENV_VALUE = "owner-approved";
export const EXIT_REFUSED = 77;

// command -> {action, write: sends or changes something for the buyer, conversation: needs a target,
//             opens: read-only but needs a panel or conversation opened (--allow-open)}
export const ONETALK = {
  "template send": { action: "template-send", write: true, conversation: true, help: "빠른 답장 템플릿 보내기 --template '이름'" },
  "tag add": { action: "tag-add", write: true, conversation: true, help: "대화에 태그 달기 --tag '이름'" },
  "tag remove": { action: "tag-remove", write: true, conversation: true, help: "대화에서 태그 빼기 --tag '이름'" },
  draft: { action: "draft", write: false, opens: true, conversation: true, help: "AI 초안 생성만(전송 안 함) [--button copilot|'draft summary'|'content enhancer'] --allow-open" },
  "template list": { action: "template-list", write: false, opens: true, help: "템플릿 이름 목록 (--allow-open 이면 패널을 열어 읽음)" },
  "tags list": { action: "tags-list", write: false, help: "태그 이름·색 목록(읽기)" },
  "ui probe": { action: "ui-probe", write: false, opens: true, help: "툴바 아이콘 hover 툴팁·역할, 더보기 메뉴 항목(--allow-open), 대화 행 속성 이름" },
};

export class Refused extends Error {
  constructor(message) { super(message); this.code = EXIT_REFUSED; }
}

// Checks that need no lookup: run before any engine or Aside call.
export function sendGate(spec, opt, env) {
  if (opt.send && !spec.write) throw new Refused("--send 는 보내는 명령에만 쓴다");
  if (opt.send && env[SEND_ENV] !== SEND_ENV_VALUE) throw new Refused(`실발송 거부: --send 와 환경변수 ${SEND_ENV}=${SEND_ENV_VALUE} 가 둘 다 있어야 한다`);
}

// -> "dry-run" | "open" | "send"; throws Refused.
export function gate(spec, opt, env, conversation) {
  sendGate(spec, opt, env);
  const mode = opt.send ? "send" : opt["allow-open"] && (spec.opens || spec.write) ? "open" : "dry-run";
  // A dry run never opens the conversation (0 clicks), so an unread one stays unread: only open/send need the flag.
  if (mode !== "dry-run" && conversation && conversation.unread && !opt["allow-mark-read"]) throw new Refused("안 읽은 대화라 열면 읽음 처리된다: --allow-mark-read 없이는 거부 (dry-run 은 열지 않으므로 그냥 됨)");
  return mode;
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

export function buildParams(spec, opt, mode, conversation) {
  return {
    action: spec.action, mode, ids: conversation ? conversation.ids : [], match: conversation ? conversation.keys || null : null,
    template: opt.template ?? null, tag: opt.tag ?? null, draftButton: opt.button ?? null,
  };
}

export function validate(spec, opt) {
  if (spec.conversation && !opt["inquiry-id"] && !opt["trade-id"]) throw new Error("대화 지정: --inquiry-id N 또는 --trade-id N");
  const need = { "template-send": () => opt.template, "tag-add": () => opt.tag, "tag-remove": () => opt.tag }[spec.action];
  if (need && !need()) throw new Error(`${spec.action}: 필요한 값이 없다 (${spec.help})`);
}

// Script source goes to Aside on stdin, so typed text never shows up in a process list.
export function runScript(asideAccount, params, { spawn = spawnSync, runner = "aside", account, configPath } = {}) {
  const engine = path.join(path.dirname(path.dirname(fileURLToPath(import.meta.url))), "engine", "onetalk.js");
  const src = readFileSync(engine, "utf8").replace("const P = __PARAMS__;", "const P = " + JSON.stringify(params) + ";");
  const input = "await eval(" + JSON.stringify("(async () => {\n" + src + "\n})()") + ")\n";
  const r = runRepl({ asideAccount, input, runner, account, configPath, spawn });
  const text = (r.stdout || "") + (r.stderr || "");
  const line = text.split("\n").find((l) => l.startsWith("RESULT "));
  return line ? JSON.parse(line.slice(7)) : { status: r.runner + "_failed", detail: redact(text.slice(-300), params) };
}

// Lookup keys never reach output, even inside an error tail.
export function redact(text, params) {
  const m = params.match || {};
  const secrets = [...(m.aliIds || []), ...(m.cids || []), ...(m.times || []).map(String)].filter((v) => v && String(v).length >= 4);
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
  if (opt.from?.length) return findConversation(opt.from[0], target);
  const dir = mkdtempSync(path.join(tmpdir(), "alibaba-seller-inq-"));
  try {
    const args = [enginePath, "--account", account.name, "--config", configPath, "--pages", "inquiries", "--out", dir,
      "--max-pages", String(opt["max-pages"] || 30), ...(opt.runner ? ["--runner", opt.runner] : [])];
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

export function onetalkMain(spec, opt, { account, configPath, enginePath, env = process.env, spawn, log = console.log }) {
  validate(spec, opt);
  sendGate(spec, opt, env);
  let conversation = null;
  if (spec.conversation) {
    conversation = lookupConversation({ opt, account, configPath, enginePath, ...(spawn ? { spawn } : {}) });
    if (!conversation) throw new Error("문의 목록에서 그 대화를 못 찾음(--max-pages 를 늘리거나 id 확인)");
  }
  const mode = gate(spec, opt, env, conversation);
  const res = runScript(account.asideAccount, buildParams(spec, opt, mode, conversation), { spawn, runner: opt.runner || account.runner || "auto", account: account.name, configPath });
  log(JSON.stringify(res, null, 1));
  if (mode !== "send" && spec.write) log("dry-run: 아무것도 보내지 않았다. 실제 실행은 --send 와 " + SEND_ENV + "=" + SEND_ENV_VALUE);
  return res.status === "ok" ? 0 : 1;
}
