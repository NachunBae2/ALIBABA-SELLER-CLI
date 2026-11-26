import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

export const FEATURE_HELP = {
  "auth status": "OAuth 토큰 상태·만료까지 남은 시간·refresh 가능 여부 (토큰은 앞 4자만 표시)",
  "auth refresh": "저장된 refresh token으로 OAuth 토큰 갱신 (브라우저 로그인 없음)",
  "auth check-consent": "필수 scope와 저장된 동의 scope를 비교해 재동의 필요 여부 확인",
  "logistics coverage": "배송 템플릿별 국가·운송사 커버리지 읽기",
  "logistics ems": "EMS가 포함된 배송 템플릿 읽기",
  "minisite pages": "미니사이트 페이지 목록과 게시 상태 읽기",
};

const tokenPath = (account, configPath) => account.openApiTokenFile || path.join(path.dirname(configPath), `${account.name}-openapi-token.json`);
const safeToken = (value) => value ? `${String(value).slice(0, 4)}…` : "없음";
const secondsLeft = (expiresAt, now) => expiresAt ? Math.floor((Date.parse(expiresAt) - now) / 1000) : null;

// 최소 이식: aliops core/alibaba-openapi.mjs와 같은 저장 토큰/refresh-token grant 계약.
export async function authCommand(action, { account, configPath, log, now = Date.now(), oauthRequest }) {
  const file = tokenPath(account, configPath);
  if (!existsSync(file)) throw new Error(`OAuth 토큰 저장소 없음: ${file}`);
  let token = JSON.parse(readFileSync(file, "utf8"));
  if (action === "refresh") {
    if (!token.refresh_token) throw new Error("refresh 불가: 저장된 refresh token 없음");
    const request = oauthRequest || (async (body) => {
      const response = await fetch(account.openApiTokenEndpoint || "https://oauth.alibaba.com/token", {
        method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: new URLSearchParams(body),
      });
      if (!response.ok) throw new Error(`OAuth refresh 실패: HTTP ${response.status}`);
      return response.json();
    });
    const received = await request({ grant_type: "refresh_token", refresh_token: token.refresh_token,
      client_id: account.openApiClientId, client_secret: account.openApiClientSecret });
    const expiresAt = new Date(now + Number(received.expires_in || 0) * 1000).toISOString();
    token = { ...token, ...received, expires_at: expiresAt, refresh_token: received.refresh_token || token.refresh_token };
    writeFileSync(file, JSON.stringify(token, null, 2) + "\n", { mode: 0o600 });
  }
  const required = account.openApiScopes || [];
  const granted = new Set(String(token.scope || "").split(/[ ,]+/).filter(Boolean));
  const missing = required.filter((scope) => !granted.has(scope));
  const left = secondsLeft(token.expires_at, now);
  const result = { access_token: safeToken(token.access_token), expires_at: token.expires_at || null,
    seconds_remaining: left, expired: left != null ? left <= 0 : null, refresh_available: !!token.refresh_token,
    consent_required: missing.length > 0, missing_scopes: missing };
  log(JSON.stringify(result, null, 2));
  return result.consent_required && action === "check-consent" ? 3 : 0;
}

export function featureReadCommand(command, data, log) {
  const rows = Array.isArray(data) ? data : data.rows || [];
  const selected = command === "logistics ems"
    ? rows.filter((row) => (row.carriers || []).some((carrier) => /EMS/i.test(String(carrier)))) : rows;
  log(JSON.stringify(selected, null, 2));
  return 0;
}
