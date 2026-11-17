"""Read-only Keyword Index, Visitor Analyzer and Market Analyzer contracts."""
import html
import json
import re
from urllib.parse import urlencode
from http_session import SessionError, compact, scrub


def business_error(value):
    if not isinstance(value, dict):
        return 'JSON object required'
    code = value.get('code', value.get('returnCode'))
    if code not in (0, 200, '0', '200') or any(value.get(k) is False for k in ('success', 'successed', 'ok')):
        return 'code=%s message=%s' % (code, value.get('message', value.get('returnMessage', value.get('msg', ''))))
    if isinstance(value.get('data'), dict) and value['data'].get('isExceed'):
        return 'code=%s isExceed=true (keyword query limit)' % code
    return None


def validate_options(config, options):
    if not isinstance(options, dict) or set(options) - {'days', 'keyword', 'categoryId'}:
        raise ValueError('unsupported discovery options')
    days = options.get('days', config.get('defaultDays'))
    if days is not None and (type(days) is not int or days not in config['days']):
        raise ValueError('unsupported --days; allowed: %s' % config['days'])
    keyword = options.get('keyword')
    if config.get('keyword'):
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError('--keyword required')
    elif keyword is not None:
        raise ValueError('--keyword unsupported')
    category = options.get('categoryId')
    if category is not None and (not config.get('category') or not re.fullmatch(r'[1-9][0-9]*', str(category))):
        raise ValueError('--category-id must be a numeric category node')
    return {k: v for k, v in dict(days=days, keyword=keyword, categoryId=category).items() if v is not None}


def prepare_jobs(pg, jobs, a, session, out):
    """Bind the same numeric node used by the bundle's category selector."""
    from http_collect import parse
    config = pg['discovery']
    options = validate_options(config, json.loads(getattr(a, 'discovery_options', '{}')))
    query = {}
    if 'days' in options:
        query['nd'] = str(options['days']) + 'd'
    if 'keyword' in options:
        query['queryRaw'] = options['keyword']
    if config.get('category'):
        source = config['category']
        category = options.get('categoryId')
        if category is None:
            url = 'https://mydata.alibaba.com/self/.json'
            params = {'action': 'OneAction', 'iName': source['iName']}
            response = session.request('GET', url, params)
            value = parse(response['text'])
            out['records'].append({'via': 'http', 'method': 'GET', 'url': url+'?'+urlencode(params),
                'status': response['status'], 'text': compact(scrub(value)), 'replay': {'kind': 'binding'}})
            error = business_error(value)
            if response['status'] != 200 or error:
                raise SessionError('category binding: HTTP %s %s' % (response['status'], error or ''))
            rows = value.get('data')
            if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
                raise SessionError('category binding: category list empty/missing; use --category-id')
            nodes = re.sub(r'\s+', '', html.unescape(str(rows[0].get('idDisplay', '')))).split('>')
            category = nodes[source['node']]
            if not re.fullmatch(r'[1-9][0-9]*', category):
                raise SessionError('category binding: invalid idDisplay node')
        query[source['field']] = category
    for job in jobs:
        job['query'].update(query)
    return jobs


def raw_tables(value, prefix, modules=False):
    """Keep raw field names, values, nested objects and indices without conversions."""
    if modules and isinstance(value, dict):
        tables = {}
        scalars = {}
        for key, child in value.items():
            if isinstance(child, list):
                tables[prefix+'_'+key] = [r if isinstance(r, dict) else {'value': r} for r in child]
            else:
                scalars[key] = child
        if scalars:
            tables[prefix+'_metadata'] = [scalars]
        return tables or {prefix: []}
    return {prefix: [r if isinstance(r, dict) else {'value': r} for r in value] if isinstance(value, list)
            else [value] if isinstance(value, dict) and value else []}


def with_missing_reasons(rows):
    keys = list(dict.fromkeys(k for row in rows for k in row))
    result = []
    for row in rows:
        missing = [k for k in keys if k not in row or row[k] is None]
        clean = {k: '' if row.get(k) is None else row[k] for k in keys}
        if missing:
            clean['_missing_reason'] = '; '.join(k+': response field missing/null' for k in missing)
        result.append(clean)
    return result
