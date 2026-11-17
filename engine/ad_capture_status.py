"""Validate read-only advertising responses without exposing server payloads."""
import math


def response_error(record):
    status = record.get("status")
    if record.get("error") or status is not None and (not isinstance(status, (int, float)) or not 200 <= status < 300):
        return "광고 요청 실패(HTTP/네트워크); 다시 수집 필요"
    payload = record.get("json")
    if not isinstance(payload, dict):
        return "광고 응답 JSON 없음; 다시 수집 필요"
    if payload.get("ok") is False or payload.get("success") is False or payload.get("captureError"):
        return "광고 API 실패 응답; 다시 수집 필요"
    data = payload.get("data")
    query = data.get("queryResult") if isinstance(data, dict) else None
    if not isinstance(query, dict) or not isinstance(query.get("detailList"), list):
        return "광고 응답 queryResult.detailList 없음; 0행 확인 불가"
    total = query.get("totalRows")
    if isinstance(total, bool) or not isinstance(total, (int, float)) or not math.isfinite(total) or total < 0 or int(total) != total:
        return "광고 응답 totalRows 없음/오류; 전체 행 수 확인 불가"
    return ""
