"""Allowlisted seller-menu reads; filter both saved responses and replayed dumps."""
import json
import sys
from http_session import SessionError, compact, scrub

SUCCESS = (0, 200, '0', '200')


def failure(status, value, page=None):
    if not isinstance(value, dict):
        return {'http_status': status, 'code': None, 'message': 'JSON 객체 응답 없음'}
    codes = {k: value[k] for k in ('code', 'errorCode', 'responseCode') if k in value}
    bad = any(v not in (*SUCCESS, None, '') for v in codes.values())
    auth_center = (page or {}).get('menuRead', {}).get('successContract') == 'auth-center'
    acknowledged = value.get('hasError') is False if auth_center else any(v in SUCCESS for v in codes.values())
    if status != 200 or bad or value.get('hasError') is True or any(value.get(k) is False for k in ('success', 'ok', 'successed')) or not acknowledged:
        return {'http_status': status, **codes,
                **({'hasError': value['hasError']} if 'hasError' in value else {}),
                'message': next((value[k] for k in ('message', 'errorMsg', 'errorMessage', 'msg') if value.get(k) is not None), None)}
    return None


def parse_sse(text, module):
    """Only accept a complete stream with an acknowledged, successful requested module."""
    events, ack, complete, payload = [], False, False, None
    for block in text.replace('\r\n', '\n').split('\n\n'):
        event, data = '', []
        for line in block.splitlines():
            if line.startswith('event:'):
                event = line[6:].strip()
            elif line.startswith('data:'):
                data.append(line[5:].lstrip())
        if not data:
            continue
        try:
            value = json.loads('\n'.join(data))
        except ValueError:
            return {'code': None, 'message': 'SSE JSON 형식 오류'}
        if not isinstance(value, dict):
            return {'code': None, 'message': 'SSE 이벤트 객체 없음'}
        if event == 'complete':
            complete = True
        else:
            error = failure(200, value)
            if error:
                return {'code': value.get('code'), 'message': error['message']}
            if event == 'ack':
                ack = True
            if event == 'message' and value.get('name') == module:
                events.append(value)
                payload = value.get('payload')
    if not ack or not complete or len(events) != 1:
        return {'code': None, 'message': 'SSE ack/complete/요청 모듈 누락 또는 중복'}
    return {'code': events[0]['code'], 'message': events[0].get('message'), 'data': payload}


def project(value, page):
    """Keep only scalar leaves; an unexpected nested object never bypasses the allowlist."""
    from http_collect import get_path, set_path
    contract, job = page['menuRead'], page['requests'][0]
    rows = get_path(value, contract['path'])
    total = get_path(value, job.get('totalPath'))
    if rows is None and contract.get('nullRowsWhenTotalZero') and type(total) is int and total == 0:
        rows = []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('응답 목록 경로/형식 없음: ' + contract['path'])
    safe = {k: value[k] for k in ('code', 'errorCode', 'responseCode', 'success', 'hasError', 'message', 'errorMsg') if k in value}
    clean = []
    for row in rows:
        item = {}
        for field in contract['fields']:
            val = get_path(row, field)
            if val is not None and not isinstance(val, (str, int, float, bool)):
                continue
            # Preserve presence/null separately from missing fields.
            parent, _, leaf = field.rpartition('.')
            obj = get_path(row, parent) if parent else row
            if isinstance(obj, dict) and leaf in obj:
                set_path(item, field, val)
        clean.append(item)
    set_path(safe, contract['path'], clean)
    if job.get('totalPath'):
        total = get_path(value, job['totalPath'])
        if not (type(total) is int and total >= 0 or isinstance(total, str) and total.isdigit()):
            raise ValueError('응답 total 경로/형식 없음: ' + job['totalPath'])
        set_path(safe, job['totalPath'], total)
    return safe


def prepare(page, jobs, session):
    if getattr(session, 'transport', None) != 'api':
        raise SessionError('menu_reads_require_api_transport')
    if not page['menuRead'].get('contextBinding'):
        return jobs
    from http_collect import parse
    response = session.request('GET', 'https://biz.alibaba.com/ggs/page/service/context.json')
    value = parse(response['text'])
    error = failure(response['status'], value)
    if error:
        raise SessionError(compact(scrub(error)))
    user = (value.get('data') or {}).get('userInfo') or {}
    tenant = user.get('tenantInfo') or {}
    if not all((tenant.get('site'), tenant.get('tenant'), user.get('memberType'), user.get('locale'), user.get('aliId'))):
        raise SessionError('seller_context_fields_missing')
    for job in jobs:
        if page['key'] == 'funds-settlements':
            job['query'].update(siteCode=tenant['site'], memberType=user['memberType'], locale=user['locale'])
        elif page['key'] == 'logistics-orders':
            job['query']['json'].update(tenantCountryCode=tenant['tenant'], sellerAliId=user['aliId'], locale=user['locale'])
    return jobs


def report_menu(dump, args, page):
    from http_collect import get_path
    rows, errors, coverage, seen = [], [], [], set()
    for directory in dump.dirs:
        meta = next((p for p in dump._manifest(directory).get('pages', []) if p.get('key') == page['key']), {})
        coverage.extend(meta.get('coverage', []))
        if meta and meta.get('status') != 'ok':
            errors.append({'status': meta.get('status'), 'message': '; '.join(meta.get('notes', []))})
    found = False
    for record in dump.records(page['key']):
        found = True
        value = record.get('json')
        error = record.get('error') or failure(record.get('status', 200), value, page)
        if not error:
            try:
                value = project(value, page)
            except ValueError as exc:
                error = {'message': str(exc)}
        if error:
            errors.append(error)
            continue
        signature = compact([record.get('url'), record.get('body'), value])
        if signature in seen:
            continue
        seen.add(signature)
        for item in get_path(value, page['menuRead']['path']):
            row, reasons = {}, {}
            for field in page['menuRead']['fields']:
                val = get_path(item, field)
                row[field] = '' if val is None else val
                if val is None or val == '':
                    reasons[field] = '응답 필드 없음/null' if val is None else '응답 빈 문자열'
            row['_blank_reasons'] = compact(reasons) if reasons else ''
            rows.append(row)
    if not found:
        errors.append({'message': '해당 명령 응답 미수집'})
    print('안내: 현재 목록/상태 조회; --start/--end/--days는 서버 조회 필터에 적용되지 않음', file=sys.stderr)
    if not rows:
        print('안내: %s 0행 — %s' % (page['key'], '응답 미수집 또는 오류' if errors else '서버 정상 응답이 빈 배열'), file=sys.stderr)
    tables = {page['key'].replace('-', '_'): rows}
    if coverage:
        tables['completeness'] = coverage
    if errors:
        tables['errors'], tables['_exit'] = errors, 5
        for error in errors:
            print('API 오류: ' + compact(scrub(error)), file=sys.stderr)
    return tables
