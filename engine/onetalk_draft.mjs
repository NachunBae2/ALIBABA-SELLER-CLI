// Copilot's read/generate-only SSE contract, observed in eva-copilot/0.0.35/index.js.
// No editor state, send endpoint, or assistant-history save endpoint is touched.
import { ONETALK, MESSAGE } from "./onetalk_api.mjs";

export function parseDraftStream(text) {
  let draft = "", ended = false, events = 0;
  for (const block of String(text).replace(/\r\n/g, "\n").split(/\n\n+/)) {
    const data = block.split("\n").filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart()).join("\n");
    if (!data) continue;
    let event;
    try { event = JSON.parse(data); } catch { throw new Error("draft: invalid SSE JSON"); }
    events++;
    if (Number(event.code) !== 200) throw new Error("draft: response code " + String(event.code ?? "missing"));
    if (event.message === "end") { ended = true; break; }
    let chunk;
    try { chunk = typeof event.data === "string" ? JSON.parse(event.data || "{}") : event.data; }
    catch { throw new Error("draft: invalid content JSON"); }
    if (typeof chunk?.modelRspContent === "string") draft += chunk.modelRspContent;
    // Model reasoning, tokens and the rest of the response are never returned.
  }
  return { draft, ended, events };
}

export async function generateDraft(client, conv, inquiry, messages, kind = "reply", { now = Date.now } = {}) {
  const action = { reply: "replySug", summary: "summary" }[kind];
  if (!action) throw Object.assign(new Error("--draft-kind reply|summary"), { code: 64 });
  const latest = messages.slice().sort((a, b) => Number(b.sendTime) - Number(a.sendTime))[0];
  if (!latest?.messageId || !conv.cid || !inquiry?.secTradeId) return { status: "draft_unavailable", reason: "초안에 필요한 문의·대화·최근 메시지 없음" };
  const summary = await client.form(MESSAGE + "/message/ajax/feedback/querySummary.htm", { secTradeId: inquiry.secTradeId });
  if (String(summary?.code) !== "200") throw new Error("querySummary: " + String(summary?.code ?? "no code"));
  const buyerLoginId = summary.data?.contact?.loginId;
  if (!buyerLoginId) return { status: "draft_unavailable", reason: "querySummary contact.loginId 없음" };
  const requestId = String(now());
  const context = { continueSession: true, modelSessionId: conv.cid, questionDesc: {
    contactAliId: Number(conv.contactAliId), userContent: action, responseId: requestId,
    endMsgId: String(latest.messageId).replace(/\.PNM$/, "") + ".PNM", endMsgTimestamp: latest.sendTime,
    buyerLoginId, ...(kind === "reply" ? { previousContext: "" } : {}) } };
  const protocol = { service: "merchant_assistant", question: "copilot", transferProtocol: "0", requestId,
    deviceId: client.deviceId, context: JSON.stringify(context) };
  const text = await client.request(ONETALK + "/reception/aiCopilot?" + new URLSearchParams({ protocolParam: JSON.stringify(protocol) }),
    { headers: { accept: "text/event-stream" }, signal: AbortSignal.timeout(65000) });
  const result = parseDraftStream(text);
  if (!result.ended) return { status: "draft_incomplete", reason: "SSE end 이벤트 없음", events: result.events };
  if (!result.draft.trim()) return { status: "draft_empty", reason: "API 초안 내용 없음", events: result.events };
  return { status: "ok", draft: result.draft, draftKind: kind, events: result.events, sent: false, transport: "api", asideCalls: 0, collectionTabOpens: 0 };
}
