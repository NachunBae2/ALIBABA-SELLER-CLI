"""Additional read-only ads contracts; values and query scopes stay as returned."""
import copy
import json
from pathlib import Path
from http_session import SessionError, compact, scrub


def catalog():
    return json.loads(Path(__file__).with_name('pages.json').read_text())


def failure(response, value):
    """Ads status=1000/ok=true is success; code=1000 is a business error."""
    if response.get('status') != 200 or not isinstance(value, dict):
        return compact({'httpStatus': response.get('status'), 'message': 'HTTP/JSON 응답 실패'})
    bad = (value.get('code') not in (None, 0, 200, '0', '200') or
           value.get('status') not in (None, 0, 200, 1000, '0', '200', '1000') or
           any(value.get(k) is False for k in ('ok', 'success', 'successed')) or
           value.get('error') not in (None, False, '', 0) or bool(value.get('errorVO')) or
           value.get('status') in (1000, '1000') and not any(value.get(k) is True for k in ('ok', 'success', 'successed')) or bool(value.get('captureError')))
    if bad:
        return compact(scrub({k: value[k] for k in
            ('status', 'code', 'ok', 'success', 'successed', 'error', 'errorVO', 'message', 'msg', 'captureError') if k in value}))
    return None


def bind(jobs, session, out, a):
    """Fetch every page of each runtime list and bind the returned row fields."""
    from http_collect import get_path, set_path, parse, render
    sources, loaded = catalog()['runtimeBindings'], {}
    result = []
    for job in jobs:
        binding = job.get('rowBinding')
        if not binding:
            result.append(job)
            continue
        name = binding['source']
        if name not in loaded:
            source = sources[name]
            params = copy.deepcopy(source['params'])
            rows, seen, total, complete = [], set(), None, False
            for page in range(1, a.max_pages * a.max_passes + 1):
                if source.get('pageParam'):
                    set_path(params, source['pageParam'], page)
                response = session.request(source['method'], source['url'], {}, params)
                value = parse(response['text'])
                error = failure(response, value)
                if error:
                    raise SessionError('binding ' + name + ': ' + error)
                batch = get_path(value, source['rowsPath'])
                if batch is None and get_path(value, source.get('emptyRowsPath')) == {}:
                    batch = []
                if not isinstance(batch, list):
                    raise SessionError('binding ' + name + ': 응답 행 경로 없음')
                for row in batch:
                    sig = compact(row)
                    if sig in seen:
                        raise SessionError('binding ' + name + ': 중복 페이지/행')
                    seen.add(sig)
                    rows.append(row)
                total = get_path(value, source.get('totalPath'))
                if isinstance(total, str) and total.isdigit():
                    total = int(total)
                if total is not None and (not isinstance(total, int) or len(rows) > total):
                    raise SessionError('binding ' + name + ': totalCount 불일치')
                if not source.get('pageParam') or total == len(rows) or not batch and total is None:
                    complete = True
                    break
                if not batch:
                    break
            if not complete:
                raise SessionError('binding ' + name + ': 목록 페이지 미완료')
            loaded[name] = rows
        candidates, signatures = 0, set()
        for row in loaded[name]:
            if any(get_path(row, k) not in v for k, v in binding.get('where', {}).items()):
                continue
            values = {k: get_path(row, path) for k, path in binding['fields'].items()}
            if any(v is None and k not in binding.get('optional', []) for k, v in values.items()):
                raise SessionError('binding ' + name + ': 필수 필드 없음 (' + ','.join(k for k,v in values.items() if v is None) + ')')
            for key in binding.get('lists', []):
                values[key] = [] if values[key] is None else [values[key]]
            if binding.get('guaranteeBusinessType'):
                package = get_path(row, 'properties.marketingPackageType')
                values['guaranteeBusinessType'] = ('ggs_l3_plus_guaranteed' if package in
                    ('ggs_ai_whole_site_l3plus_90d_35ab', 'ggs_ai_whole_site_l3plus_90d_20ab') else 'ggs_ai_whole_site_guaranteed')
            signature = compact(values)
            if signature in signatures:
                continue
            signatures.add(signature)
            def substitute(v):
                if isinstance(v, dict):
                    return {k: substitute(x) for k,x in v.items()}
                if isinstance(v, list):
                    return [substitute(x) for x in v]
                return values.get(v[2:-2], v) if isinstance(v, str) and v.startswith('{{') and v.endswith('}}') else v
            bound = substitute(job)
            bound['replay'] = {**bound.get('replay', {}), 'binding': values}
            # Resolve dates before the legacy binder's unresolved-placeholder check.
            bound = render(bound, a.start_d.isoformat(), a.end_d.isoformat())
            result.append(bound)
            candidates += 1
        if not candidates:
            out['notes'].append(job['tag'] + ': 목록 API에 조건에 맞는 대상 없음 (0행)')
    return result


def report(dump, args):
    from http_collect import get_path, parse
    page = next(p for p in catalog()['pages'] if p['key'] == args.command)
    config = page['adsExtra']
    rows, requests, errors = [], [], []
    found = False
    for directory in dump.dirs:
        if dump.selected(directory, page['key']) == []:
            continue
        meta = next((p for p in dump._manifest(directory).get('pages', []) if p['key'] == page['key']), None)
        if meta:
            found = True
            if meta.get('status') != 'ok':
                errors.append({'status': meta.get('status'), 'message': '\n'.join(meta.get('notes', []))})
            for note in meta.get('notes', []):
                requests.append({'reason': note})
    for record in dump.records(page['key']):
        found = True
        value = record.get('json') if 'json' in record else parse(record.get('text', ''))
        rp = record.get('replay') or {}
        error = failure(record, value)
        dated = page['requests'][0]['period']['mode'] != 'snapshot'
        scope = {'tag': rp.get('tag'), 'start': rp.get('start') if dated else None,
                 'end': rp.get('end') if dated else None, 'periodMode': 'range' if dated else 'snapshot',
                 'binding': rp.get('binding', {})}
        if dated and not error:
            start, end = rp.get('start'), rp.get('end')
            if not start or not end:
                errors.append({**scope, 'message': '저장 응답의 요청 기간 없음'})
                continue
            if dump.start and end < dump.start or dump.end and start > dump.end:
                continue
            if dump.start and start < dump.start or dump.end and end > dump.end:
                errors.append({**scope, 'message': '저장 응답 집계 구간을 잘라낼 수 없음; 요청 기간으로 재조회 필요'})
                continue
        if error:
            errors.append({**scope, 'message': error})
            continue
        data = get_path(value, config['path'])
        if 'data' not in value:
            errors.append({**scope, 'message': '응답 data 키 없음'})
            continue
        if config['mode'] == 'entries' and isinstance(data, dict):
            batch = [{'_key': k, **v} if isinstance(v, dict) else {'_key': k, 'value': v} for k,v in data.items()]
        else:
            batch = data if isinstance(data, list) else [] if data is None or data == {} else [data]
        requests.append({**scope, 'httpStatus': record.get('status'), 'code': value.get('code'), 'status': value.get('status'),
                         'rows': len(batch), 'reason': '' if batch else 'API data null/빈 응답 (0행)'})
        for raw in batch:
            row = dict(raw) if isinstance(raw, dict) else {'value': raw}
            rows.append({**row, '_query': scope})
    if not found:
        errors.append({'message': '요청 덤프 없음; 실조회 필요'})
    fields = list(dict.fromkeys(k for row in rows for k in row if not k.startswith('_')))
    for row in rows:
        missing = {k: '응답 null' if k in row else '응답에 키 없음' for k in fields if row.get(k) is None}
        for k in missing:
            row[k] = None
        row['_missing'] = missing
    result = {'data': rows, 'requests': requests}
    if errors:
        result.update(errors=errors, _exit=5)
    return result
