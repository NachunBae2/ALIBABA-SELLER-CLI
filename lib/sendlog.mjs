// Send log: every OneTalk send (ok or failed) is one JSON line in <account outDir>/inquiry-replies.jsonl.
// Fields: at, account, inquiryId, tradeId, flow, item (reply|card|product|file|template|tag-add|tag-remove),
// text (reply body or template name), file (base name), productId, tag, result (success|fail), status,
// readback (booleans from the chat pane). Buyer name and contact are never written.
import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

export const SEND_LOG = "inquiry-replies.jsonl";

export function sendLogPath(account) {
  const base = account.outDir || path.join(homedir(), "30_Assets", account.name, "seller-report");
  return path.join(base, SEND_LOG);
}

export function appendSend(account, entry) {
  const file = sendLogPath(account);
  mkdirSync(path.dirname(file), { recursive: true });
  appendFileSync(file, JSON.stringify(entry) + "\n", "utf8");
  return file;
}

export function readSends(account, { days = null, now = new Date() } = {}) {
  const file = sendLogPath(account);
  if (!existsSync(file)) return [];
  const since = days ? now.getTime() - Number(days) * 86400000 : -Infinity;
  return readFileSync(file, "utf8").split("\n").filter((l) => l.trim()).map((l) => { try { return JSON.parse(l); } catch (e) { return null; } })
    .filter((e) => e && Date.parse(e.at) >= since);
}

// A flow already ran on this inquiry when the log has a successful line with that flow name.
export function flowAlreadySent(account, flow, inquiryId) {
  return readSends(account).some((e) => e.flow === flow && String(e.inquiryId) === String(inquiryId) && e.result === "success");
}

export function formatSends(rows) {
  if (!rows.length) return "보낸 기록 없음";
  return rows.map((e) => [e.at, e.inquiryId, e.flow || "-", e.item, e.result, e.file || e.productId || e.tag || "",
    e.text ? JSON.stringify(e.text.length > 80 ? e.text.slice(0, 80) + "…" : e.text) : ""].join("\t")).join("\n");
}
