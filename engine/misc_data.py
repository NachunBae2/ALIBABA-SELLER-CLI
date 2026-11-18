"""Read contracts for folders, product health, saved searches, orders and AI usage.

Only explicit scalar columns cross the persistence/report boundary. Values retain
server types and units; missing fields are never interpreted as zero.
"""
import json

FOLDER_GROUPS = ('contactFolders', 'customFolders', 'feedbackTypeList', 'systemFolders')
POWER_FIELDS = ('id', 'subject', 'status', 'tradeType',
                'productPowerScoreDetails.alreadyCalculated',
                'productPowerScoreDetails.prodPowerScore',
                'productPowerScoreDetails.productQualityScore',
                'productPowerScoreDetails.gmtModified')
ORDER_FIELDS = ('baseInfo.id', 'baseInfo.createTime', 'baseInfo.createTimestamp',
                'baseInfo.bizCode', 'statusAction.status.name', 'statusAction.status.displayName',
                'productInfo.productCount', 'productInfo.productTotalNum',
                'paymentInfo.currency', 'paymentInfo.totalAmount.number',
                'paymentInfo.totalAmount.numberStr')
FIELDS = {
    'inquiry-folders': ('group', 'name', 'count'),
    'product-negative': ('governanceType', 'count'),
    'product-identifiers': ('id',),
    'product-power': POWER_FIELDS,
    'product-risk-tasks': ('productId', 'count'),
    'orders-list': ORDER_FIELDS,
    'knowledge-metrics': ('buyerSearchCallCount', 'fileCount', 'smartReceptionCallCount', 'totalCallCount'),
}
COMMANDS = {
    'inquiry-folders': 'inquiry-folders', 'products-risk-counts': 'product-negative',
    'products-power-score': 'product-power', 'products-risk-tasks': 'product-risk-tasks',
    'orders-list': 'orders-list', 'knowledge-metrics': 'knowledge-metrics',
}


def path(obj, key):
    for part in key.split('.'):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def scalar(value):
    return value if value is None or isinstance(value, (str, int, float, bool)) else None


def project(row, fields, flat=False):
    if not isinstance(row, dict):
        raise ValueError('row_object_required')
    return {key: scalar(row.get(key) if flat else path(row, key)) for key in fields}


def identity(kind, row):
    key = {'product-identifiers': 'id', 'product-power': 'id', 'orders-list': 'baseInfo.id',
           'rfq-searches': 'id', 'product-risk-tasks': 'productId'}.get(kind)
    value = row.get(key) if key else None
    if key and kind != 'rfq-searches' and (value is None or value == '' or isinstance(value, bool)):
        raise ValueError('row_identifier_missing')
    return json.dumps(value if value is not None else row, sort_keys=True, ensure_ascii=False)


def project_rows(kind, rows, flat=False):
    result = [project(r, FIELDS[kind], flat=flat) for r in rows]
    for row in result:
        identity(kind, row)
    return result


def error(response, value):
    """Preserve actual error codes/messages, never response data or redirect tokens."""
    failed = not 200 <= response['status'] < 300 or value is None
    if isinstance(value, dict):
        failed |= (value.get('code') not in (None, 0, 200, '0', '200') or
                   any(value.get(k) is False for k in ('success', 'successed', 'ok', 'result')) or
                   bool(value.get('captureError')) or value.get('error') not in (None, False, '', 0))
    if not failed:
        return None
    result = {'httpStatus': response['status']}
    if isinstance(value, dict):
        result.update({k: scalar(value[k]) for k in ('code', 'message', 'msg', 'errorCode', 'errorMessage', 'error', 'captureError') if k in value})
    if response.get('redirectHost'):
        result['redirectHost'] = response['redirectHost']
    if value is None and 200 <= response['status'] < 300:
        result['reason'] = 'JSON 응답 없음'
    return result


def normalize(kind, value):
    """Validate envelopes before projecting, including --from unsanitized dumps."""
    if isinstance(value, dict) and value.get('readContract') == kind:
        rows = value.get('rows')
        if not isinstance(rows, list):
            raise ValueError('rows_missing')
        if kind in ('product-identifiers', 'product-power', 'orders-list'):
            total = value.get('total')
            if isinstance(total, bool) or not (isinstance(total, int) and total >= 0 or isinstance(total, str) and total.isdigit()):
                raise ValueError('server_total_missing')
        return {'readContract': kind, 'rows': project_rows(kind, rows, flat=True),
                'total': value.get('total')}
    total = None
    if kind == 'inquiry-folders':
        data = path(value, 'data')
        if not isinstance(data, dict) or any(not isinstance(data.get(k), list) for k in FOLDER_GROUPS):
            raise ValueError('folder_groups_missing')
        rows = []
        for group in FOLDER_GROUPS:
            for item in data[group]:
                if not isinstance(item, dict):
                    raise ValueError('folder_object_required')
                rows.append({'group': group, 'name': item.get('groupName') if group == 'contactFolders' else item.get('description'),
                             'count': item.get('count')})
    elif kind in ('product-identifiers', 'product-power'):
        rows, total = path(value, 'products'), path(value, 'count')
    elif kind == 'orders-list':
        rows, total = path(value, 'data.data'), path(value, 'data.pagination.totalRecord')
    elif kind == 'product-negative':
        rows = path(value, 'content')
    elif kind == 'product-risk-tasks':
        data = path(value, 'data')
        if not isinstance(data, dict) or any(isinstance(v, (dict, list)) for v in data.values()):
            raise ValueError('risk_count_map_required')
        rows = [{'productId': key, 'count': count} for key, count in data.items()]
    elif kind == 'rfq-searches':
        rows = value
    else:
        data = path(value, 'data')
        if not isinstance(data, dict) or not any(k in data for k in FIELDS[kind]):
            raise ValueError('metrics_object_required')
        rows = [data]
    if not isinstance(rows, list):
        raise ValueError('rows_missing')
    if kind in ('product-identifiers', 'product-power', 'orders-list'):
        if isinstance(total, bool) or not (isinstance(total, int) and total >= 0 or isinstance(total, str) and total.isdigit()):
            raise ValueError('server_total_missing')
    return {'readContract': kind, 'rows': project_rows(kind, rows), 'total': total}


def bind_products(jobs, page, args, session, out):
    """Page the read-only product source completely before binding risk queries."""
    from http_collect import collect, parse
    bound = []
    for job in jobs:
        source = job.get('productSource')
        if not source:
            bound.append(job)
            continue
        import copy
        source_args = copy.copy(args)
        source_args.requests, source_args.skip_requests = '', False
        result = collect({'key': 'product-identifiers', 'url': page['url'], 'requests': [source]}, source_args, session, {})
        for record in result['records']:
            record['replay']['kind'] = 'dependency'
        out['records'].extend(result['records'])
        out['coverage'].extend(result['coverage'])
        out['notes'].extend(result['notes'])
        if result['status'] != 'ok':
            out['status'] = result['status']
            out['detail'] = result.get('detail', '')
            continue
        ids = [row['id'] for rec in result['records'] for row in parse(rec['text'])['rows']]
        if any(value is None for value in ids):
            out['status'] = 'schema_mismatch'
            out['detail'] = 'product identifier missing'
            continue
        for offset in range(0, len(ids), 100):
            bound.append({**job, 'tag': job['tag'] + '.' + str(offset // 100 + 1),
                          'query': {**job.get('query', {}), 'productIds': ','.join(map(str, ids[offset:offset+100]))}})
    return bound


def report(dump, args):
    from http_collect import parse
    kind = COMMANDS[args.command]
    page = 'misc-' + kind
    # These are current snapshots. Pick one capture, never add snapshots together.
    captures = []
    located = list(dump.located_records(page))
    for directory in dump.dirs:
        records = [r for d, observed, r in located if d == directory]
        meta = next((p for p in dump._manifest(directory).get('pages', []) if p.get('key') == page), {})
        if records or meta:
            captures.append((meta.get('fetchedAt') or dump._manifest(directory).get('generatedAt') or '', records, meta))
    if not captures:
        return {kind: [], 'errors': [{'reason': '요청 덤프 없음'}], '_exit': 5}
    _, records, meta = sorted(captures, key=lambda x: x[0])[-1]
    rows, errors, groups = [], [], {}
    for rec in records:
        tag = (rec.get('replay') or {}).get('tag', kind)
        contract = 'product-identifiers' if tag == 'product-identifiers' else kind
        value = rec.get('json') if 'json' in rec else parse(rec.get('text', ''))
        if isinstance(value, dict) and 'readError' in value:
            errors.append(value['readError'])
            continue
        problem = error({'status': rec.get('status', 200)}, value)
        if problem:
            errors.append(problem)
            continue
        try:
            clean = normalize(contract, value)
        except ValueError as exc:
            errors.append({'reason': str(exc)})
            continue
        g = groups.setdefault(tag, {'rows': [], 'total': None, 'contract': contract})
        g['rows'].extend(clean['rows'])
        if g['total'] is not None and g['total'] != clean['total']:
            errors.append({'reason': 'server_total_changed'})
        g['total'] = clean['total']
        if contract == kind:
            for row in clean['rows']:
                missing = [k for k, v in row.items() if v is None or v == '']
                rows.append({**row, 'missing_reason': '응답 값 없음: ' + ', '.join(missing) if missing else ''})
    completeness = []
    for tag, group in groups.items():
        unique = {identity(group['contract'], r) for r in group['rows']}
        total = group['total']
        complete = len(unique) == len(group['rows']) and (total is None or len(group['rows']) == int(total))
        completeness.append({'source': tag, 'rows_received': len(group['rows']), 'server_total': total,
                             'complete': complete, 'total_reason': '단일 응답; 서버 total 없음' if total is None else ''})
    if meta.get('status', 'ok') != 'ok' or any(not c.get('complete') for c in meta.get('coverage', [])):
        errors.append({'reason': meta.get('status'), 'message': meta.get('detail') or '; '.join(meta.get('notes', []))})
    # A risk map can omit zero counts. Never make missing product entries zero.
    if kind == 'product-risk-tasks' and not any(g['contract'] == kind for g in groups.values()):
        sources = [g for g in groups.values() if g['contract'] == 'product-identifiers']
        if not sources or any(g['rows'] for g in sources):
            errors.append({'reason': 'risk_response_missing'})
    if not completeness and not errors:
        errors.append({'reason': '응답 완결성 근거 없음'})
    result = {kind: rows, 'completeness': completeness}
    if errors or any(not c['complete'] for c in completeness):
        result.update(errors=errors or [{'reason': 'duplicate_rows_or_total_mismatch'}], _exit=5)
    return result
