"""Execute only the fixed read contracts in pages.json; no browser page collection."""
import copy
import datetime as dt
import html
import json
import re
import time
from urllib.parse import urlencode
from http_session import SessionError, compact, scrub


def get_path(obj, path):
    if not path:
        return None
    for k in path.split('.'):
        if isinstance(obj, dict):
            obj = obj.get(k)
        elif isinstance(obj, list) and k.isdigit() and int(k) < len(obj):
            obj = obj[int(k)]
        else:
            return None
    return obj


def set_path(obj, path, value):
    parts = path.split('.')
    for k in parts[:-1]:
        obj = obj.setdefault(k, {})
    obj[parts[-1]] = value


def parse(text):
    text = html.unescape(text)
    try:
        return json.loads(text)
    except ValueError:
        m = re.fullmatch(r'[\w$.]+\(([\s\S]*)\)\s*;?', text.strip())
        if m:
            try:
                return json.loads(m[1])
            except ValueError:
                pass
        return None


def sanitize_inquiry(text, lookup=None):
    value = html.unescape(text)
    parsed = None
    for _ in range(5000):
        try:
            parsed = json.loads(value)
            break
        except json.JSONDecodeError as e:
            quote = value.rfind('"', 0, e.pos)
            if quote < 0 or not e.pos:
                break
            value = value[:quote] + '\\' + value[quote:]
    keep = ('id', 'tradeId', 'feedbackId', 'secTradeId', 'createTime', 'lastestReplyTime', 'modifiedTime', 'source', 'feedbackType', 'targetIds', 'appFrom', 'inquiryCount', 'replyStatus', 'hasUnread', 'unreadCount', 'process', 'imInquiry', 'historyInquiry', 'spam')
    data = (parsed or {}).get('data') or {}
    items, fields = [], set()
    for group in data.get('list') or []:
        for item in group if isinstance(group, list) else [group]:
            if not isinstance(item, dict) or not str(item.get('id', '')).isdigit():
                continue
            row = {k: item[k] for k in keep if k in item}
            for k, v in row.items():
                if isinstance(v, int) and abs(v) > 2**53 - 1:
                    row[k] = str(v)
            row['buyerCountry'] = (item.get('sender') or {}).get('countryCode')
            attrs = {}
            for prefix, obj in [('', item), ('sender.', item.get('sender')), ('buyer.', item.get('buyer'))]:
                for k, v in (obj or {}).items():
                    fields.add(prefix + k)
                    if re.search(r'level|lvl|grade', k, re.I) and re.fullmatch(r'[A-Za-z0-9_.+-]{1,12}', str(v)):
                        attrs[prefix + k] = v
            if attrs:
                row['buyerAttrs'] = attrs
            items.append(row)
    return {'code': (parsed or {}).get('code'), 'pagination': data.get('pagination'), 'list': items,
            'parsed': parsed is not None, 'fieldNames': sorted(fields)}


def sanitize_allowed(j, allow):
    q = get_path(j, 'data.queryResult') or {}
    rows = q.get('detailList') or []
    return {'sanitized': True, 'data': {'queryResult': {'totalRows': q.get('totalRows'),
        'detailList': [{k: r[k] for k in allow if k in r} for r in rows],
        'fieldNames': sorted({k for r in rows for k in r})}}}


def render(value, start, end):
    if isinstance(value, dict):
        return {k: render(v, start, end) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, start, end) for v in value]
    if isinstance(value, str):
        return value.replace('{{start}}', start).replace('{{end}}', end)
    return value


def expanded_extras(pg, a):
    from seller_central_raw import today_pst, week_selections
    extra = []
    for x in pg.get("extra") or []:
        if a.extras and x["tag"] not in a.extras.split(","):
            continue
        if x.get("perSeed"):
            # keyword index: one paged query per seed (1-2 word head term), nd window from --kw-nd
            for seed in a.seeds:
                extra.append({**x, "tag": "%s.%s" % (x["tag"], seed), "maxPages": a.kw_pages,
                              "params": {**x["params"], "queryRaw": seed, "nd": a.kw_nd}})
            continue
        if x.get("perWeek"):
            if a.granularity in ("week", "both"):
                for week_start, back in week_selections(a.start_d, a.end_d, today_pst()):
                    extra.append({**x, "tag": "%s.%s" % (x["tag"], week_start.isoformat()), "params": {**x["params"], "selected": str(back + int(x.get("selectedBase", 0)))}})
        elif x.get("perMonth") and a.granularity in ("month", "both"):
            # one query per month in --start..--end. Data advisor picks the period with
            # selected=N months back from the latest complete month; statDate is ignored.
            # Both endpoints use selectedBase = 1. The returned statDate and visit-product
            # counts are checked by analytics_periods on every report run.
            latest = dt.date.today().replace(day=1) - dt.timedelta(days=1)
            m = a.start_d.replace(day=1)
            while m <= a.end_d.replace(day=1):
                base = int(x.get("selectedBase", 0))
                back = (latest.year * 12 + latest.month) - (m.year * 12 + m.month) + base
                if back >= base:
                    extra.append({**x, "tag": "%s.%s" % (x["tag"], m.strftime("%Y-%m")), "params": {**x["params"], "selected": str(back)}})
                m = (m + dt.timedelta(days=32)).replace(day=1)
        if not x.get("perWeek") and not x.get("perMonth"):
            extra.append(x)
    return extra


def bind_jobs(jobs, session, out):
    """Resolve identifiers from read contracts, never captured account-specific constants."""
    from pathlib import Path
    sources = json.loads(Path(__file__).with_name('pages.json').read_text())['runtimeBindings']
    names = list(dict.fromkeys(job['binding']['source'] for job in jobs if job.get('binding')))
    values = {}
    for name in names:
        source = sources[name]
        response = session.request(source['method'], source['url'], {}, source['params'])
        data = parse(response['text'])
        rows = get_path(data, source['rowsPath'])
        if response['status'] != 200 or not isinstance(rows, list):
            raise SessionError('binding_list_failed')
        ids = [get_path(row, source['idPath']) for row in rows]
        if any(value is None for value in ids):
            raise SessionError('binding_id_missing')
        values[name] = list(dict.fromkeys(ids))
    def substitute(value, replacements):
        if isinstance(value, dict):
            return {k: substitute(v, replacements) for k, v in value.items()}
        if isinstance(value, list):
            return [substitute(v, replacements) for v in value]
        return replacements.get(value, value) if isinstance(value, str) else value
    bound = []
    for job in jobs:
        binding = job.get('binding')
        if not binding:
            bound.append(job)
            continue
        ids = values[binding['source']]
        if not ids:
            out['notes'].append(job['tag'] + ': 목록 API에 대상 ID 없음')
            continue
        key = binding['key']
        for value in ([ids] if binding.get('many') else ids):
            bound.append(substitute(job, {'{{'+key+('s' if binding.get('many') else '')+'}}': value}))
    if '{{' in compact(bound).replace('{{start}}', '').replace('{{end}}', ''):
        raise SessionError('unresolved_runtime_binding')
    return bound


def collect(pg, a, session, resume):
    start, end = a.start_d.isoformat(), a.end_d.isoformat()
    began = time.monotonic()
    metrics_before = dict(session.metrics)
    out = {'key': pg['key'], 'url': pg['url'], 'status': 'ok', 'records': [], 'notes': [], 'coverage': []}
    jobs = copy.deepcopy([] if getattr(a, 'skip_requests', False) else [r for r in pg.get('requests', []) if not getattr(a, 'requests', '') or r['tag'] in a.requests.split(',')])
    for t in ([] if getattr(a, 'skip_tables', False) else pg.get('rangeTables', [])):
        if a.tables and t['key'] not in a.tables.split(','):
            continue
        span = t.get('requestDays', (a.end_d-a.start_d).days+1)
        for offset in range(0, (a.end_d-a.start_d).days+1, span):
            ws = a.start_d + dt.timedelta(days=offset)
            we = min(a.end_d, ws + dt.timedelta(days=span-1))
            window_start, window_end = ws.isoformat(), we.isoformat()
            data = {'dataMode': 'struct', 'tableName': t['table'], 'filters': t.get('filters', []),
                    'time': {'granularity': t.get('granularity', 'DAY'), 'beginDateTime': window_start + ' 00:00:00', 'endDateTime': window_end + ' 23:59:59'},
                    'derives': [{'mode': 'MAIN'}], 'extendParams': {'context': {**t.get('ctx', {}), 'dateRange': (we-ws).days+1}}}
            if t.get('paged'):
                data['page'] = {'index': 1, 'size': t.get('pageSize', 100)}
            sig = t['table'] + compact(t.get('filters', []))
            if t.get('requestDays'):
                sig += '|' + window_start + '|' + window_end
            prior = resume.get(sig, {})
            if prior.get('complete'):
                continue
            jobs.append({**t, 'tag': t['key'], 'kind': 'range', 'params': {'ads': {'productLineId': t.get('adsProductLineId') or t['ctx'].get('productLineId')},
                'type': 'normal', 'data': data}, 'pageParam': 'data.page.index' if t.get('paged') else None,
                'windowStart': window_start, 'windowEnd': window_end, 'subwindow': bool(t.get('requestDays')),
                'firstPage': prior.get('nextPage', 1), 'covered': prior.get('covered', 0)})
    for x in ([] if getattr(a, "skip_extras", False) else expanded_extras(pg, a)):
        jobs.append({**x, 'kind': 'extra', 'query': x['params'], 'params': {}})
    if pg.get('inquiryList'):
        jobs.append({'tag': 'inquiries', 'kind': 'inquiry', 'method': 'POST',
            'url': 'https://message.alibaba.com/message/ajax/feedback/subjectList.htm',
            'params': {'params': {'listType': 'all', 'pagination': {'nextPage': 1, 'pageSize': 20}, 'filter': {'isShowAtm': False},
                        'order': {'order': 'desc', 'orderBy': 'latest_contact_time'}, 'search': {}}},
            'pageParam': 'params.pagination.nextPage', 'rowsPath': 'list', 'totalPath': 'pagination.totalCount'})
    if not jobs and not resume:
        out['status'] = 'unavailable'
        out['notes'].append(pg.get('unavailableReason', '조회 API 계약 미확인'))
    def run_job(job):
        job_start, job_end = job.get('windowStart', start), job.get('windowEnd', end)
        method, url = job.get('method', 'GET'), job['url']
        q, p = render(job.get('query', {}), job_start, job_end), render(job.get('params', {}), job_start, job_end)
        first, count, total = job.get('firstPage', 1), job.get('covered', 0), None
        seen = set()
        size = None
        paged = job.get('pageParam')
        limit = job.get('maxPages', a.kw_pages) if job.get('perSeed') else max(a.max_pages, job.get('maxPages', 0)) * a.max_passes
        cov = {'table': job['tag'], 'start': job_start, 'end': job_end, 'rowsGot': count, 'totalRows': None, 'pages': [], 'complete': False}
        for n in range(first, first + (limit if paged else 1)):
            if paged:
                set_path(q if job.get('pageLocation') == 'query' or method == 'GET' else p, paged, n)
            response = yield (method, url, copy.deepcopy(q), copy.deepcopy(p), job.get('encoding', 'form'), pg['url'])
            j = sanitize_inquiry(response['text']) if job.get('kind') == 'inquiry' else parse(response['text'])
            if job.get('allow') and j is not None:
                j = sanitize_allowed(j, job['allow'])
            if not 200 <= response['status'] < 300 or j is None:
                out['status'] = 'http_failed'
                out['notes'].append(job['tag'] + ': HTTP/JSON 응답 실패')
                break
            # Fail closed on explicit application errors, even HTTP 200.
            if isinstance(j, dict) and (j.get('success') is False or j.get('successed') is False or j.get('error') not in (None, False, '', 0) or j.get('code') not in (None, 0, 200, 1000, '0', '200', '1000', 'SUCCESS')):
                out['status'] = 'api_failed'
                out['notes'].append(job['tag'] + ': API 오류(행 완결성 검증 불가)')
                break
            safe_q, safe_p = scrub(q), scrub(p)
            encode = lambda obj: urlencode({k: compact(v) if isinstance(v, (dict, list)) else v for k,v in obj.items()})
            rp = {'kind': job.get('kind', 'page'), 'tag': job['tag'], 'page': n, 'start': job_start, 'end': job_end}
            if job.get('table'):
                rp['table'] = job['table']
            if job.get('subwindow'):
                rp['subwindow'] = True
            # Historical inquiry format is the inner params, not the form wrapper.
            body = compact(safe_p['params']) if job.get('kind') == 'inquiry' else (compact(safe_p) if job.get('encoding') == 'json' else encode(safe_p)) if method != 'GET' else None
            record = {'seq': len(out['records']), 'via': 'http', 'method': method,
                'url': url + ('?' + encode(safe_q) if safe_q else ''), 'body': body,
                'status': response['status'], 'ct': 'application/json', 'text': compact(scrub(j)), 'replay': rp}
            rows = get_path(j, job.get('rowsPath'))
            if rows is None and job.get('emptyRowsPath') and get_path(j, job['emptyRowsPath']) == {}:
                rows = []
            total = get_path(j, job.get('totalPath'))
            if isinstance(total, str) and total.isdigit():
                total = int(total)
            if total is not None and not isinstance(total, int):
                total = None
            if paged and not isinstance(rows, list):
                out['status'] = 'schema_mismatch'
                out['notes'].append(job['tag'] + ': 응답 행 경로 미확인')
                break
            size = len(rows) if isinstance(rows, list) else None
            if isinstance(rows, list):
                keys = [compact(row) for row in rows]
                duplicates = len(keys) - len(set(keys)) + sum(key in seen for key in set(keys))
                cov['duplicateRows'] = cov.get('duplicateRows', 0) + duplicates
                seen.update(keys)
                if duplicates:
                    out['status'] = 'duplicate_rows'
                    break
            out['records'].append(record)
            if size is not None:
                count += size
            cov.update(rowsGot=count if size is not None else None, totalRows=total)
            cov['pages'].append(n)
            if total is not None and count == total:
                cov['complete'] = True
                break
            if total is not None and count > total:
                out['status'] = 'incomplete'
                out['notes'].append(job['tag'] + ': 받은 행이 API totalRows보다 많음')
                break
            if not paged or size == 0:
                if total is None and not paged:
                    cov['complete'] = True
                    cov['completionBasis'] = 'single_response'
                break
        if total is not None and count != total and out['status'] == 'ok':
            out['status'] = 'incomplete'
        elif paged and total is None:
            if size != 0 and out['status'] == 'ok':
                out['status'] = 'incomplete'
            if size == 0:
                cov['complete'] = True
                cov['completionBasis'] = 'empty_terminal_page'
            out['notes'].append(job['tag'] + ': totalRows 미반환; 빈 마지막 페이지로 종료')
        out['coverage'].append(cov)
    try:
        jobs = bind_jobs(jobs, session, out)
        pending = []
        for job in jobs:
            runner = run_job(job)
            try:
                pending.append((runner, next(runner)))
            except StopIteration:
                pass
        while pending:
            failure = None
            try:
                responses = session.request_many([request for _, request in pending]) if hasattr(session, 'request_many') else [session.request(*request) for _, request in pending]
            except SessionError as exc:
                responses, failure = exc.responses, exc
            following = []
            for (runner, _), response in zip(pending, responses):
                try:
                    following.append((runner, runner.send(response)))
                except StopIteration:
                    pass
            pending = following
            if failure:
                raise failure
    except SessionError as e:
        out['status'] = str(e)
    out['metrics'] = {**{k: v - metrics_before.get(k, 0) for k,v in session.metrics.items()}, 'elapsedSeconds': round(time.monotonic()-began, 3)}
    return out
