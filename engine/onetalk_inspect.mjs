// Read the customer panel using the same HTTP contract as api_session.py.
// HTML is fetched for its scoped token only; scripts and read-status actions never run.
import { readFileSync } from "node:fs";

const profile = JSON.parse(readFileSync(new URL("./pages.json", import.meta.url), "utf8"))
  .pages.find((p) => p.key === "inquiries").inquiryProfile;

export function parseProfile(text) {
  const body = String(text).replace(/^\s*\/\*[^]*?\*\/\s*/, "");
  const jsonp = /^\s*(?:typeof\s+[\w$.]+\s*===?\s*['"]function['"]\s*&&\s*)?[\w$.]+\(([^]*)\)\s*;?\s*$/.exec(body);
  try { return JSON.parse(jsonp ? jsonp[1] : body); }
  catch { throw new Error("queryCustomerInfo: invalid JSON/JSONP"); }
}

export async function customerProducts(client, inquiry) {
  const absent = (reason) => ({ products: null, reason });
  if (!inquiry?.secTradeId || !inquiry?.tradeId) return absent("고객 패널 조회용 문의 tradeId/secTradeId 없음");
  const summary = await client.form(profile.summaryUrl, { secTradeId: inquiry.secTradeId });
  if (String(summary?.code) !== "200") throw new Error("querySummary: " + String(summary?.code ?? "no code"));
  const data = summary.data;
  if (!data?.contact?.accountIdEncrypt) return absent("querySummary contact 없음");
  const html = await client.request(profile.detailUrl + "?" + new URLSearchParams({ imInquiryId: inquiry.tradeId }));
  const token = /window\.KHTAccessToken\s*=\s*['"]([^'"]+)['"]/.exec(html)?.[1];
  if (!token) return absent("상세 HTML KHTAccessToken 없음");
  const query = new URLSearchParams({ buyerAccountId: data.contact.accountIdEncrypt, secTradeId: data.secTradeId,
    buyerLoginId: "", secReqToken: token, clientType: "", lang: "en_US", callback: "profileRead" });
  // These anti-CSRF parameters are also supplied by the Python HTTP collector.
  for (const name of ["ctoken", "_tb_token_", "p_csrf"]) {
    const value = client.jar?.get(name);
    if (value) query.set(name, decodeURIComponent(value));
  }
  const origin = new URL(profile.url).origin;
  const result = parseProfile(await client.request(profile.url + "?" + query, { headers: {
    referer: origin + "/", origin, "x-requested-with": "XMLHttpRequest", accept: "application/json, text/plain, */*" } }));
  const buyer = result?.data?.data?.buyerInfo;
  if (!buyer) return absent("queryCustomerInfo buyerInfo 없음");
  const visits = buyer.buyerShopBehaviorInfo?.productVisitHistory;
  if (!Array.isArray(visits)) return absent("queryCustomerInfo buyerShopBehaviorInfo/productVisitHistory 없음");
  return { products: visits.map((v) => ({ id: v.productId == null ? null : String(v.productId), title: v.productName ?? null })),
    reason: visits.length ? null : "productVisitHistory 0행" };
}
