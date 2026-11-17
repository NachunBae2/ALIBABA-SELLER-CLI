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
    if path == '$':
        return obj
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
        obj = obj[int(k)] if isinstance(obj, list) else obj.setdefault(k, {})
    obj[int(parts[-1]) if isinstance(obj, list) else parts[-1]] = value


def parse_review_stream(text):
    """Decode the finite reviewManager SSE response, requiring its completion event."""
    events, rows, pagination = [], [], None
    complete = False
    for block in re.split(r'\r?\n\r?\n', text):
        kind, data = '', []
        for line in block.splitlines():
            if line.startswith('event:'):
                kind = line[6:].strip()
            elif line.startswith('data:'):
                data.append(line[5:].lstrip())
        if not data:
            continue
        try:
            value = json.loads('\n'.join(data))
        except ValueError:
            return {'captureError': 'invalid_review_event'}
        if not isinstance(value, dict):
            return {'captureError': 'invalid_review_event'}
        events.append({'event': kind, 'data': value})
        if kind in ('error', 'servererror') or value.get('code') not in (None, 200, '200'):
            return {'captureError': 'review_stream_error', **{k: value[k] for k in ('code', 'message') if k in value}}
        if kind == 'complete':
            complete = True
        if kind == 'message':
            name, payload = value.get('name'), value.get('payload')
            if name == 'interplay.reviewmananger.review.list':
                if not isinstance(payload, dict) or value.get('mergeType') != 'incremental':
                    return {'captureError': 'invalid_review_list'}
                rows.append(payload)
            elif name == 'interplay.reviewmananger.review.pagination':
                pagination = payload
    if not complete or not isinstance(pagination, dict):
        return {'captureError': 'incomplete_review_stream'}
    # The service emits this placeholder even when totalCount is zero.
    if pagination.get('totalCount') == 0 and rows == [{'orderInfo': {'orderId': '-1'}}]:
        rows = []
    return {'code': 200, 'data': rows, 'pagination': pagination, 'events': events}


def parse(text):
    # JSONP may have Alibaba's anti-hijacking comment before its callback.
    # Parse raw JSON before HTML entities to preserve quotes inside JSON strings.
    for value in (text, html.unescape(text)):
        try:
            return json.loads(value)
        except ValueError:
            match = re.fullmatch(r'[^()]*\(([\s\S]*)\)\s*;?', value.strip())
            if match:
                try:
                    return json.loads(match[1])
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


def sanitize_buyer_profile(value):
    b = get_path(value, 'data.data.buyerInfo')
    if not isinstance(b, dict):
        return {'available': False, 'reason': 'queryCustomerInfo buyerInfo 없음'}
    keep = ('country', 'productViewCount', 'validInquiryCount', 'validRfqCount', 'loginDays', 'spamInquiryMarkedBySupplierCount', 'addedToBlacklistCount')
    visits = get_path(b, 'buyerShopBehaviorInfo.productVisitHistory')
    return {'available': True, **{k: b[k] for k in keep if k in b},
            'growthLevel': get_path(b, 'growthLevelInfo.growthLevel'),
            'productVisitHistory': [{k: v[k] for k in ('productId', 'productName') if k in v} for v in visits] if isinstance(visits, list) else None,
            'viewedProductsReason': ('' if visits else 'productVisitHistory 0행') if isinstance(visits, list) else 'queryCustomerInfo buyerShopBehaviorInfo/productVisitHistory 없음'}


def sanitize_inquiry_extra(text, allow):
    value = html.unescape(text)
    for _ in range(5000):
        try:
            j = json.loads(value)
            rows = get_path(j, 'data.list') or {}
            return {'parsed': True, 'list': [dict(tradeId=str(k), **{field: row['sender'][field] for field in allow if field in (row.get('sender') or {})}) for k, row in rows.items()]}
        except json.JSONDecodeError as e:
            quote = value.rfind('"', 0, e.pos)
            if quote < 0: break
            value = value[:quote] + '\\' + value[quote:]
    return {'parsed': False, 'list': []}


def sanitize_allowed(j, allow):
    q = get_path(j, 'data.queryResult') or {}
    if j.get('captureError') or j.get('ok') is False or j.get('success') is False or not isinstance(q.get('detailList'), list) or not isinstance(q.get('totalRows'), (int, float)):
        return {'sanitized': True, 'captureError': 'invalid_query_result'}
    rows = q['detailList']
    return {'sanitized': True, 'data': {'queryResult': {'totalRows': q.get('totalRows'),
        'detailList': [{k: r[k] for k in allow if k in r} for r in rows],
        'fieldNames': sorted({k for r in rows for k in r})}}}


def render(value, start, end):
    if isinstance(value, dict):
        return {k: render(v, start, end) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, start, end) for v in value]
    if isinstance(value, str):
        return (value.replace('{{start}}', start).replace('{{end}}', end)
                .replace('{{startCompact}}', start.replace('-', '')).replace('{{endCompact}}', end.replace('-', '')))
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
    if '{{' in compact(bound).replace('{{start}}', '').replace('{{end}}', '').replace('{{startCompact}}', '').replace('{{endCompact}}', ''):
        raise SessionError('unresolved_runtime_binding')
    return bound


def history_jobs(config, a, session, out):
    response = session.request('POST', config['intervalUrl'], {}, {'ads': {'productLineId': 110106}, 'type': 'normal', 'data': {}})
    max_date = get_path(parse(response['text']), 'data.maxDate')
    if not isinstance(max_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', max_date):
        raise SessionError('history_retention_unverified')
    import calendar
    end_date = dt.date.fromisoformat(max_date)
    months = end_date.year * 12 + end_date.month - 3
    year, month0 = divmod(months, 12)
    retention = dt.date(year, month0+1, min(end_date.day, calendar.monthrange(year, month0+1)[1])).isoformat()
    start, end = max(a.start_d.isoformat(), retention), min(a.end_d.isoformat(), max_date)
    out['notes'].append('Operation Log 보존 2개월; 이전 일별 설정 복원 불가')
    if start > end:
        out['notes'].append('요청 구간이 운영 이력 보존 범위 밖')
        return []
    return [dict(tag='history.'+str(q['productLineId'])+'.'+q['logMajor'], kind='ads-history', url=config['url'], method='POST',
        params={'ads': {'productLineId':q['productLineId']}, 'type':'normal', 'data':{**q,'pageIndex':1,'pageSize':100,'dateBegin':start,'dateEnd':end}},
        pageParam='data.pageIndex', rowsPath='data', totalPath='totalCount', windowStart=start, windowEnd=end,
        replay={**q,'retentionStart':retention,'maxDate':max_date}) for q in config['queries']]


def collect_campaign_targets(pg, session, out):
    seen = set()
    for r in list(out['records']):
        if '/campaign/type/allList' not in r['url']: continue
        data = (parse(r.get('text','')) or {}).get('data') or {}
        for rows in data.values():
            for c in rows if isinstance(rows, list) else []:
                cid = str(c.get('id', ''))
                if not cid.isdigit() or cid in seen or c.get('productLineId') != 110106: continue
                seen.add(cid)
                for path in pg['campaignTargetReads']:
                    url = 'https://www2.alibaba.com' + path.replace('{campaignId}', cid)
                    p = {'ads': {'productLineId': c['productLineId']}, 'type':'normal','data':{}}
                    response = session.request('POST', url, {}, p)
                    value = parse(response['text'])
                    if response['status'] != 200 or not isinstance(value, dict) or value.get('ok') is False:
                        raise SessionError('campaign_targets_failed')
                    out['records'].append({'via':'http','method':'POST','url':url,'body':urlencode({k:compact(v) for k,v in p.items()}),
                        'status':response['status'],'text':compact(scrub(value)),
                        'replay':{'kind':'campaign-targets','campaignId':cid,'path':path}})


def collect(pg, a, session, resume):
    start, end = a.start_d.isoformat(), a.end_d.isoformat()
    began = time.monotonic()
    metrics_before = dict(session.metrics)
    out = {'key': pg['key'], 'url': pg['url'], 'status': 'ok', 'records': [], 'notes': [], 'coverage': []}
    jobs = copy.deepcopy([] if getattr(a, 'skip_requests', False) else [r for r in pg.get('requests', []) if not getattr(a, 'requests', '') or r['tag'] in a.requests.split(',')])
    from seller_central_raw import range_tables
    for t in range_tables(pg, a):
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
            sig = t['table'] + compact(t.get('filters', [])) + ('|' + t['granularity'] if t.get('granularity', 'DAY') != 'DAY' else '')
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
    if pg.get('operationLogs'):
        try:
            jobs.extend(history_jobs(pg['operationLogs'], a, session, out))
        except SessionError as exc:
            out['status'] = str(exc)
    if not jobs and not resume and not pg.get('operationLogs'):
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
        if pg.get('discovery'):
            limit = a.max_pages
        cov = {'table': job['tag'], 'start': job_start, 'end': job_end, 'rowsGot': count, 'totalRows': None, 'pages': [], 'complete': False}
        def fail(status, reason):
            out['status'] = status
            cov['reason'] = reason
            if not cov['pages']:
                cov['rowsGot'] = None
            out['notes'].append(job['tag'] + ': ' + reason)
        for n in range(first, first + (limit if paged else 1)):
            if paged:
                for page_path in job.get('pageParams', [paged]):
                    set_path(q if job.get('pageLocation') == 'query' or method == 'GET' else p, page_path, n)
            response = yield (method, url, copy.deepcopy(q), copy.deepcopy(p), job.get('encoding', 'form'), pg['url'])
            j = (parse_review_stream(response['text']) if job.get('responseFormat') == 'review-sse' else
                 sanitize_inquiry(response['text']) if job.get('kind') == 'inquiry' else parse(response['text']))
            if pg.get('storeContract'):
                from store_metrics import response_failure, payload_failure
                failure = response_failure(response['status'], j)
                invalid = payload_failure(pg['key'].removeprefix('store-'), j, job.get('replay', {}).get('query_day')) if not failure else None
                if invalid:
                    failure = {'http_status': response['status'], 'code': None, 'message': invalid}
                if failure:
                    out['status'] = 'api_failed'
                    out['detail'] = compact(scrub(failure))
                    out['notes'].append(job['tag'] + ': ' + out['detail'])
                    out['records'].append({'url': url, 'status': response['status'], 'text': compact(scrub(j)),
                        'replay': {'kind': 'page', 'tag': job['tag'], **job.get('replay', {})}, 'error': scrub(failure)})
                    break
            if job.get('allow') and j is not None:
                j = sanitize_allowed(j, job['allow'])
            if job.get('strictBusiness'):
                from discovery import business_error
                error = business_error(j)
                if response['status'] != 200 or error:
                    out['status'] = 'api_failed'
                    out['notes'].append(job['tag'] + ': HTTP %s %s' % (response['status'], error or ''))
                    out['records'].append({'via': 'http', 'method': method, 'url': url,
                        'status': response['status'], 'text': compact(scrub(j)),
                        'replay': {'kind': 'error', 'tag': job['tag']}})
                    break
            if not pg.get('adsExtra') and (not 200 <= response['status'] < 300 or j is None):
                fail('http_failed', 'HTTP %s; %s' % (response['status'],
                     ('redirectHost=' + response['redirectHost']) if response.get('redirectHost') else 'JSON 응답 없음'))
                break
            # Fail closed on explicit application errors, even HTTP 200.
            if not pg.get('adsExtra') and isinstance(j, dict) and (j.get('captureError') or j.get('hasError') is True or j.get('result') is False or j.get('ok') is False or j.get('success') is False or j.get('successed') is False or j.get('error') not in (None, False, '', 0) or j.get('code') not in (None, 0, 200, 1000, '0', '200', '1000', 'SUCCESS')):
                detail = {k: j[k] for k in ('code', 'errorCode', 'message', 'msg', 'errorMsg', 'error', 'captureError', 'hasError', 'result', 'success') if k in j}
                fail('api_failed', 'API 오류 ' + compact(scrub(detail)))
                break
            safe_q, safe_p = scrub(q), scrub(p)
            encode = lambda obj: urlencode({k: compact(v) if isinstance(v, (dict, list)) else v for k,v in obj.items()})
            rp = {'kind': job.get('kind', 'page'), 'tag': job['tag'], 'page': n, 'start': job_start, 'end': job_end}
            rp.update(job.get('replay', {}))
            if job.get('table'):
                rp['table'] = job['table']
            if job.get('subwindow'):
                rp['subwindow'] = True
            # Historical inquiry format is the inner params, not the form wrapper.
            body = compact(safe_p['params']) if job.get('kind') == 'inquiry' else (compact(safe_p) if job.get('encoding') == 'json' else encode(safe_p)) if method != 'GET' else None
            record = {'seq': len(out['records']), 'via': 'http', 'method': method,
                'url': url + ('?' + encode(safe_q) if safe_q else ''), 'body': body,
                'status': response['status'], 'ct': 'application/json', 'text': compact(scrub(j)), 'replay': rp}
            if pg.get('adsExtra'):
                from ads_extra import failure
                error = failure(response, j)
                if error or 'data' not in j:
                    out['records'].append(record)
                    out['status'] = 'api_failed'
                    out['notes'].append(job['tag'] + ': ' + (error or '응답 data 키 없음'))
                    break
            rows = get_path(j, job.get('rowsPath'))
            if rows is None and job.get('emptyRowsPath') and get_path(j, job['emptyRowsPath']) == {}:
                rows = []
            total = get_path(j, job.get('totalPath'))
            if isinstance(total, str) and total.isdigit():
                total = int(total)
            if total is not None and not isinstance(total, int):
                total = None
            if paged and not isinstance(rows, list):
                detail = {k: j[k] for k in ('code', 'message', 'msg') if isinstance(j, dict) and k in j}
                fail('schema_mismatch', '응답 행 경로 %s 없음; %s' % (job.get('rowsPath'), compact(scrub(detail))))
                break
            size = len(rows) if isinstance(rows, list) else None
            if isinstance(rows, list):
                keys = [compact(row) for row in rows]
                duplicates = len(keys) - len(set(keys)) + sum(key in seen for key in set(keys))
                cov['duplicateRows'] = cov.get('duplicateRows', 0) + duplicates
                seen.update(keys)
                if duplicates:
                    fail('duplicate_rows', 'page=%s; 중복 행 %s; 받은 행 %s / API total %s' % (n, duplicates, count, total))
                    break
            out['records'].append(record)
            if job.get('kind') == 'inquiry' and rows:
                if pg.get('inquiryExtra'):
                    config = pg['inquiryExtra']
                    response_extra = session.request('POST', config['url'], params={'params': {
                        'tradeIds': [row.get('tradeId') for row in rows], 'secTradeIds': [row.get('secTradeId') for row in rows]}, 'postId': str(time.time_ns())})
                    clean = sanitize_inquiry_extra(response_extra['text'], config['allow'])
                    out['records'].append({'via': 'http', 'method': 'POST', 'url': config['url'], 'status': response_extra['status'],
                        'text': compact(clean), 'replay': {'kind': 'inquiry-extra', 'page': n}})
                if pg.get('inquiryProfile'):
                    profiles = session.buyer_profiles(rows, pg['inquiryProfile'])
                    out['records'].append({'via': 'http', 'method': 'GET', 'url': pg['inquiryProfile']['url'], 'status': 200,
                        'text': compact({'list': profiles, 'activityWindow': '90d', 'capturedAt': dt.datetime.now(dt.timezone.utc).isoformat()}),
                        'replay': {'kind': 'inquiry-profile', 'page': n}})
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
        elif paged and total is None and 'reason' not in cov:
            if size != 0 and out['status'] == 'ok':
                out['status'] = 'incomplete'
            if size == 0:
                cov['complete'] = True
                cov['completionBasis'] = 'empty_terminal_page'
            out['notes'].append(job['tag'] + ': totalRows 미반환; 빈 마지막 페이지로 종료')
        if not cov['complete'] and 'reason' not in cov:
            cov['reason'] = '수집 미완료: 받은 행 %s / API total %s' % (count, total)
        out['coverage'].append(cov)
    try:
        if pg.get('discovery'):
            from discovery import prepare_jobs
            jobs = prepare_jobs(pg, jobs, a, session, out)
        if pg.get('storeDaySelector'):
            from store_metrics import day_jobs
            jobs = day_jobs(pg, jobs, a, session, out)
        if pg.get('adsExtra'):
            from ads_extra import bind
            jobs = bind(jobs, session, out, a)
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
        if pg.get('campaignTargetReads'):
            collect_campaign_targets(pg, session, out)
    except SessionError as e:
        out['status'] = str(e)
        if pg.get('discovery') or pg.get('adsExtra'):
            out['notes'].append(str(e))
    out['metrics'] = {**{k: v - metrics_before.get(k, 0) for k,v in session.metrics.items()}, 'elapsedSeconds': round(time.monotonic()-began, 3), 'transport': getattr(session, 'transport', 'repl')}
    return out
