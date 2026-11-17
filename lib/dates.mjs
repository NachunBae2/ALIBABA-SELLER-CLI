// Date ranges for every period command. --start/--end are the query; --days/--all are presets
// turned into start/end here. Dates are calendar dates (YYYY-MM-DD) as the seller center takes them.

export const MAX_WINDOW_DAYS = 91; // longest ads report window seen answered in one call (2026-06-28..2026-09-26)
export const ALL_DAYS = 181; // offline_effect_report filter_timer range seen: 2026-05-19..2026-11-15

const DAY = 864e5;

export function parseDate(text, flag = "date") {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(text ?? ""));
  const d = m && new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  if (!d || d.getUTCMonth() !== +m[2] - 1 || d.getUTCDate() !== +m[3]) throw new Error(`${flag}: YYYY-MM-DD 형식이 아님: ${text}`);
  return d;
}

export const iso = (d) => d.toISOString().slice(0, 10);
const addDays = (d, n) => new Date(d.getTime() + n * DAY);

export function todayLocal(now = new Date()) {
  return new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
}

// Latest complete data advisor week (PST Sunday..Saturday) ends on the last Saturday before today (PST).
export function latestCompleteWeek(now = new Date()) {
  const pst = new Date(now.getTime() - 8 * 3600e3);
  const today = new Date(Date.UTC(pst.getUTCFullYear(), pst.getUTCMonth(), pst.getUTCDate()));
  const back = ((today.getUTCDay() - 6 + 7) % 7) || 7;
  const end = addDays(today, -back);
  return { start: addDays(end, -6), end };
}

// -> {start, end, warnings[]}; throws on bad input.
export function resolveRange({ start, end, days, all, weeks } = {}, today = todayLocal(), now = new Date()) {
  const warnings = [];
  const presets = [start || end ? "start/end" : null, days != null ? "days" : null, all ? "all" : null, weeks != null ? "weeks" : null].filter(Boolean);
  if (presets.length > 1) throw new Error("--start/--end, --days, --weeks, --all 중 하나만 쓴다");
  let s, e;
  if (weeks != null) {
    const n = Number(weeks);
    if (!Number.isInteger(n) || n < 1) throw new Error(`--weeks: 1 이상 정수가 아님: ${weeks}`);
    const w = latestCompleteWeek(now);
    return { start: iso(addDays(w.start, -7 * (n - 1))), end: iso(w.end), warnings: [`--weeks ${n} = 완료된 최근 ${n}주(PST 일~토)`] };
  }
  if (days != null) {
    const n = Number(days);
    if (!Number.isInteger(n) || n < 1) throw new Error(`--days: 1 이상 정수가 아님: ${days}`);
    e = today;
    s = addDays(today, -(n - 1));
  } else if (all) {
    e = today;
    s = addDays(today, -(ALL_DAYS - 1));
    warnings.push(`--all = 최근 ${ALL_DAYS}일(셀러센터 효과 리포트 조회 가능 범위)`);
  } else {
    if (!start || !end) throw new Error("기간: --start YYYY-MM-DD --end YYYY-MM-DD (또는 --days N, --weeks N, --all)");
    s = parseDate(start, "--start");
    e = parseDate(end, "--end");
  }
  if (e > today) {
    warnings.push(`--end ${iso(e)} 은 미래라 오늘 ${iso(today)} 로 자름`);
    e = today;
  }
  if (s > e) throw new Error(`--start ${iso(s)} 가 --end ${iso(e)} 보다 늦다`);
  return { start: iso(s), end: iso(e), warnings };
}

// Split start..end (inclusive) into consecutive windows of at most maxDays.
export function splitRange(start, end, maxDays = MAX_WINDOW_DAYS) {
  const out = [];
  let s = parseDate(start, "start");
  const e = parseDate(end, "end");
  while (s <= e) {
    const w = addDays(s, maxDays - 1);
    const we = w < e ? w : e;
    out.push({ start: iso(s), end: iso(we) });
    s = addDays(we, 1);
  }
  return out;
}
