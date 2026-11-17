// Request planning shares splitRange with the legacy w-<start>_<end> layout.
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import path from 'node:path';
import { splitRange, resolveRange, latestReportDay, iso } from './dates.mjs';
export const CATALOG = JSON.parse(readFileSync(new URL('../engine/pages.json', import.meta.url), 'utf8'));

export function periodGroups(pages, tables, catalog = CATALOG) {
  const groups = new Map();
  for (const key of pages || []) if (!catalog.pages.some(p => p.key === key)) throw new Error(`알 수 없는 페이지: ${key}`);
  for (const page of catalog.pages.filter(p => !pages || pages.includes(p.key))) {
    const requests = page.rangeTables?.filter(t => !tables || tables.includes(t.key)) || [...(page.extra || []), ...(page.requests || [])];
    if (!requests.length) requests.push(page);
    for (const request of requests) {
      const period = { ...catalog.periodDefaults, ...page.period, ...request.period };
      // Period-selector APIs already iterate complete weeks/months in the engine.
      if (!page.rangeTables) period.mode ||= 'snapshot';
      const key = JSON.stringify(period);
      if (!groups.has(key)) groups.set(key, { period, pages: [], tables: [], extras: [], requests: [] });
      const group = groups.get(key);
      if (!group.pages.includes(page.key)) group.pages.push(page.key);
      if (page.rangeTables) group.tables.push(request.key);
      if (page.extra?.includes(request)) group.extras.push(request.tag);
      if (page.requests?.includes(request)) group.requests.push(request.tag);
    }
  }
  return [...groups.values()];
}

export function resolveQuery(opt, pages, tables, today = latestReportDay(), catalog = CATALOG) {
  if (!opt.max && !opt.all) return resolveRange(opt, today);
  const groups = periodGroups(pages, tables, catalog).filter(g => !['snapshot'].includes(g.period.mode));
  const dates = groups.map(g => {
    if (g.period.status !== 'verified') throw new Error(`--max: ${g.tables.join(',') || g.pages.join(',')} 최초 날짜 미확인; --start/--end 지정 필요`);
    if (g.period.lookbackDays) return iso(new Date(today.getTime() - (g.period.lookbackDays - 1) * 864e5));
    if (!g.period.earliestDate) throw new Error('--max: 최초 조회 가능 날짜 미확인');
    return g.period.earliestDate;
  });
  return resolveRange({ ...opt, earliestDate: dates.sort()[0] }, today);
}

export function cacheDirs(root) {
  const dirs = new Set([root]);
  if (existsSync(root)) {
    for (const entry of readdirSync(root, { withFileTypes: true })) if (entry.isDirectory() && entry.name.startsWith("w-")) dirs.add(path.join(root, entry.name));
  }
  const parent = path.dirname(root);
  if (!existsSync(parent)) return [...dirs];
  for (const entry of readdirSync(parent, { withFileTypes: true })) {
    if (!entry.isDirectory() || !entry.name.startsWith('sellercentral-raw-')) continue;
    const dir = path.join(parent, entry.name);
    dirs.add(dir);
    for (const child of readdirSync(dir, { withFileTypes: true })) {
      if (child.isDirectory() && child.name.startsWith('w-')) dirs.add(path.join(dir, child.name));
    }
  }
  return [...dirs];
}

export function planWindows(range, period, cached = []) {
  const start = period.earliestDate && period.earliestDate > range.start ? period.earliestDate : range.start;
  const end = range.end;
  let cursor = start;
  const out = [];
  for (const c of cached.filter(c => c.start >= start && c.end <= end).sort((a,b) => a.start.localeCompare(b.start) || b.end.localeCompare(a.end))) {
    if (c.start < cursor) continue;
    if (c.start > cursor) out.push(...splitRange(cursor, iso(new Date(Date.parse(c.start) - 864e5)), period.maxDays));
    out.push(c);
    cursor = iso(new Date(Date.parse(c.end) + 864e5));
  }
  out.push(...splitRange(cursor, end, ["snapshot", "all-history", "week", "month", "period-selector"].includes(period.mode) ? Math.max(1, Math.round((Date.parse(end)-Date.parse(cursor))/864e5)+1) : period.maxDays));
  return out;
}

// Read the seller-center's own filter response. No rolling-day guess from old captures.
export function readAdsBounds(dir) {
  const file = path.join(dir, 'raw', 'ads-report.jsonl');
  if (!existsSync(file)) return null;
  for (const line of readFileSync(file, 'utf8').split('\n').filter(Boolean).reverse()) {
    const record = JSON.parse(line);
    if (!record.url?.includes('/dynamic/filter/data')) continue;
    const rows = record.json?.data?.queryResult;
    for (const row of Array.isArray(rows) ? rows : []) {
      if (row.sceneType !== 'offline_effect_report') continue;
      const start = row.beginDateTime?.slice(0, 10), end = row.endDateTime?.slice(0, 10);
      if (/^\d{4}-\d{2}-\d{2}$/.test(start) && /^\d{4}-\d{2}-\d{2}$/.test(end)) return { start, end };
    }
  }
  return null;
}

export function completeAdsCache(dir, tables, start, end) {
  try {
    const groups = new Map();
    const records = readFileSync(path.join(dir, 'raw', 'ads-report.jsonl'), 'utf8').split('\n').filter(Boolean).map(JSON.parse);
    for (const record of records) {
      const rp=record.replay || {}, q=record.json?.data?.queryResult;
      if(rp.kind !== 'range' || rp.start < start || rp.end > end || !Array.isArray(q?.detailList)) continue;
      const data=JSON.parse(new URLSearchParams(record.body).get('data'));
      const sig=data.tableName + JSON.stringify(data.filters || []);
      if(!groups.has(sig)) groups.set(sig,new Map());
      const windows=groups.get(sig), key=rp.start+'|'+rp.end;
      if(!windows.has(key)) windows.set(key,{start:rp.start,end:rp.end,pages:new Map(),total:null});
      const state=windows.get(key);
      state.pages.set(rp.page || 1,q.detailList);
      if(typeof q.totalRows === 'number') state.total=q.totalRows;
    }
    return CATALOG.pages.find(p=>p.key==='ads-report').rangeTables.filter(t=>!tables || tables.includes(t.key)).every(t=>{
      const windows=groups.get(t.table+JSON.stringify(t.filters || []));
      if(!windows) return false;
      let cursor=start;
      for(const state of [...windows.values()].sort((a,b)=>a.start.localeCompare(b.start))) {
        if(state.start !== cursor || state.total === null) return false;
        const rows=[];
        for(let n=1;n<=state.pages.size;n++) {
          if(!state.pages.has(n)) return false;
          rows.push(...state.pages.get(n));
        }
        if(rows.length !== state.total || new Set(rows.map(r=>JSON.stringify(r))).size !== rows.length) return false;
        cursor=iso(new Date(Date.parse(state.end)+864e5));
      }
      return cursor===iso(new Date(Date.parse(end)+864e5));
    });
  } catch { return false; }
}
