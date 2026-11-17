"""Store read contracts: UI day selectors and lossless raw metric reports."""
import copy
import datetime as dt
import json
import re
import sys
from urllib.parse import parse_qs, urlsplit

from http_session import SessionError


def response_failure(status, value):
    """These store endpoints accept only 0/200, unlike legacy code-1000 contracts."""
    if not isinstance(value, dict):
        return dict(http_status=status, code=None, message='JSON 객체 응답 없음')
    codes = [value[k] for k in ('code', 'errorCode') if k in value]
    bad = [c for c in codes if c not in (None, '', 0, 200, '0', '200')]
    failed = not 200 <= status < 300 or bad or any(value.get(k) is False for k in ('success', 'successed', 'ok')) or value.get('error') not in (None, False, '', 0)
    if failed:
        return dict(http_status=status, code=bad[0] if bad else value.get('code', value.get('errorCode')),
                    message=next((value[k] for k in ('message', 'errorMsg', 'msg', 'error') if value.get(k) is not None), None))
    return None


def payload_failure(name, value, query_day=None):
    root = 'data' if name in ('summary', 'trends', 'visitors') else 'result' if name == 'today' else 'values'
    payload = value.get(root)
    expected = list if name in ('summary', 'trends', 'visitors', 'today', 'indicators') else dict
    if not isinstance(payload, expected):
        return '응답 데이터 경로/형식 없음: ' + root
    if isinstance(payload, list) and any(not isinstance(row, dict) for row in payload):
        return '응답 행이 객체가 아님: ' + root
    if name == 'summary' and query_day:
        for row in payload:
            if not isinstance(row.get('statDate'), dict) or row['statDate'].get('value') != query_day:
                return '요청 일자와 응답 statDate 불일치'
    if name in ('thresholds', 'benefits'):
        outer, inner = ('thresholds', 'thresholdList') if name == 'thresholds' else ('starBenefitCategoryConfigs', 'rewards')
        groups = payload.get(outer)
        if not isinstance(groups, list) or any(not isinstance(group, dict) or not isinstance(group.get(inner), list)
                or any(not isinstance(row, dict) for row in group[inner]) for group in groups):
            return '응답 목록 경로/형식 없음: ' + outer + '.' + inner
        if name == 'benefits' and not isinstance(payload.get('benefitMap'), dict):
            return '응답 데이터 경로/형식 없음: benefitMap'
    return None


def day_jobs(pg, jobs, args, session, out):
    """UI DateCal.cal(day) = STATISTICS.date.diff(day, 'days'); never use local today."""
    response = session.request('GET', pg['url'])
    match = re.search(r'window\.STATISTICS\s*=\s*\{([^}]+)\}', response['text'])
    fields = dict(re.findall(r"(\w+)\s*:\s*['\"]([^'\"]+)['\"]", match[1])) if match else {}
    if response['status'] != 200 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', fields.get('date', '')) or not fields.get('day', '').isdigit():
        raise SessionError('store_period_bootstrap_unverified')
    try:
        latest = dt.date.fromisoformat(fields['date'])
        if not 1 <= int(fields['day']) <= 3660:
            raise ValueError
    except ValueError:
        raise SessionError('store_period_bootstrap_unverified') from None
    # DateCal.isRange is exclusive at latest - day and latest + 1.
    earliest = latest - dt.timedelta(days=int(fields['day']) - 1)
    start, end = max(args.start_d, earliest), min(args.end_d, latest)
    if start > end:
        raise SessionError('store_period_outside_available_range')
    period = dict(period_mode='day-selector', available_start=earliest.isoformat(), available_end=latest.isoformat(),
                  effective_start=start.isoformat(), effective_end=end.isoformat())
    if start != args.start_d or end != args.end_d:
        out['notes'].append('요청 기간 중 조회 가능 범위만 수집: %s~%s (화면 STATISTICS 기준)' % (start, end))
    expanded = []
    for offset in range((end-start).days + 1):
        day = start + dt.timedelta(days=offset)
        for template in jobs:
            job = copy.deepcopy(template)
            job['query'].update(statisticsType='day', selected=(latest-day).days)
            job['replay'] = {**period, 'query_day': day.isoformat()}
            expanded.append(job)
    return expanded


def flatten(value, prefix=''):
    """Flatten objects only; keep arrays, booleans, numbers and strings as returned."""
    out = {}
    for key, item in value.items():
        key = prefix + key
        if isinstance(item, dict) and item:
            out.update(flatten(item, key + '.'))
        else:
            out[key] = item
    return out


def blank_reasons(rows):
    keys = list(dict.fromkeys(k for row in rows for k in row))
    for row in rows:
        missing = {k: '응답 필드 없음' if k not in row else '응답 null' if row[k] is None else '응답 빈 문자열'
                   for k in keys if k not in row or row[k] is None or row[k] == ''}
        for key in keys:
            row.setdefault(key, None)
        row['_blank_reasons'] = json.dumps(missing, ensure_ascii=False) if missing else ''
    return rows


def report_store(dump, args):
    name = args.command.removeprefix('store-')
    page = 'store-' + name
    rows, periods, errors, notes, seen = [], [], [], [], set()
    root = 'data' if name in ('summary', 'trends', 'visitors') else 'result' if name == 'today' else 'values'
    levels = []
    responses = 0
    for directory, observed, record in dump.located_records(page):
        value = record.get('json')
        failure = response_failure(record.get('status', 200), value)
        if failure:
            errors.append(failure)
            continue
        rp = record.get('replay') or {}
        query_day = rp.get('query_day')
        if query_day and not dump.in_range(query_day):
            continue
        payload = value.get(root)
        invalid = payload_failure(name, value, query_day)
        if invalid:
            errors.append(dict(http_status=record.get('status'), code=None, message=invalid))
            continue
        signature = json.dumps([record.get('url'), query_day, payload], sort_keys=True, ensure_ascii=False)
        if signature in seen:
            continue
        seen.add(signature)
        responses += 1
        query = parse_qs(urlsplit(record.get('url', '')).query)
        mode = 'day-selector' if query_day else 'server-default' if name == 'trends' else 'snapshot'
        reason = '' if query_day else '서버 기본 기간(statisticsType=day, selected=0); --start/--end/--days 미적용, 반환 statDate 그대로' if name == 'trends' else '현재 스냅샷; --start/--end/--days 미적용'
        period = dict(requested_start=dump.start, requested_end=dump.end, period_mode=mode,
                      query_day=query_day, statisticsType=query.get('statisticsType', [None])[0],
                      selected=query.get('selected', [None])[0], observed_at=observed, reason=reason)
        period.update({k: rp[k] for k in ('available_start', 'available_end', 'effective_start', 'effective_end') if k in rp})
        periods.append(period)
        meta = {'_query_day': query_day} if query_day else {}
        if name == 'thresholds':
            items = [{**{k: v for k, v in payload.items() if k != 'thresholds'},
                      **{k: v for k, v in group.items() if k != 'thresholdList'}, **threshold}
                     for group in payload.get('thresholds', []) for threshold in group.get('thresholdList', [])]
        elif name == 'benefits':
            items = [{**{k: v for k, v in category.items() if k != 'rewards'}, **{'reward.'+k: v for k, v in reward.items()}}
                     for category in payload.get('starBenefitCategoryConfigs', []) for reward in category.get('rewards', [])]
            levels.extend({'level': level, 'benefits': benefits} for level, benefits in payload.get('benefitMap', {}).items())
        else:
            items = payload if isinstance(payload, list) else [payload]
        for item in items:
            rows.append({**meta, **flatten(item if isinstance(item, dict) else {'value': item})})
        manifest = dump._manifest(directory)
        for entry in manifest.get('pages', []):
            if entry.get('key') == page:
                notes.extend(entry.get('notes', []))
                if entry.get('status') != 'ok':
                    errors.append(dict(http_status=None, code=None, message='덤프 수집 상태: ' + str(entry.get('status'))))
    if not responses and not errors:
        errors.append(dict(http_status=None, code=None, message='해당 명령 응답 미수집/요청 기간에 수집된 일자 없음'))
    if not rows:
        notes.append('%s 0행 — %s' % (page, '서버 정상 응답이 빈 배열' if responses and not errors else '응답 미수집 또는 오류'))
    for note in dict.fromkeys(notes):
        print('안내: ' + note, file=sys.stderr)
    for error in errors:
        print('API 오류: ' + json.dumps(error, ensure_ascii=False), file=sys.stderr)
    tables = {page.replace('-', '_'): blank_reasons(rows), 'period': periods}
    if levels:
        tables['benefit_levels'] = levels
    if errors:
        tables['errors'] = errors
        tables['_exit'] = 1
    return tables
