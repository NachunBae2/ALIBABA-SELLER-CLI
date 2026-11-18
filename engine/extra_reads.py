"""Collect the multi-endpoint reads without persisting cookies, IDs, or raw responses."""
import argparse
import itertools
import json
import re
import sys
from urllib.parse import unquote

from api_session import ApiSession
from http_collect import get_path, parse
from http_session import SECRET, SessionError, compact
from report import emit
from seller_central_raw import load_account


PRIVATE = re.compile(
    r'aliid$|loginid$|(?:email|phone|address|street)|^(?:mobile|telephone|fax|name|firstName|lastName|fullName|'
    r'givenName|familyName|buyerInfo|buyerDetail|contactInfo|receiverInfo|shippingInfo|panMask|'
    r'walletAccount(?:Id|Name)|realName|nickName|postalCode|zipCode)$|'
    r'^(?:buyer|contact|receiver|recipient|consignee|purchaser|customer|account|member).*'
    r'(?:name|mobile|tel|addr|mail)(?:number|num|[0-9])?$',
    re.I)


def private_key(key):
    return PRIVATE.search(re.sub(r'[^a-zA-Z0-9]', '', key))


def redact_params(value):
    def redact(match):
        key = unquote(match[1])
        return match[1] + '=***' if private_key(key) or SECRET.search(key) else match[0]
    return re.sub(r'\b([\w.%-]+)=([^&\s;"\\]+)', redact, value)


def sanitize(value, *, public_name=False):
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()
                if not SECRET.search(k)
                and (not private_key(k) or k == 'name' and public_name)
                and not (k.lower() in ('buyer', 'contact', 'receiver', 'recipient', 'consignee', 'purchaser')
                         and not isinstance(v, (int, float)))}
    if isinstance(value, list):
        return [sanitize(v, public_name=public_name) for v in value]
    if isinstance(value, str):
        try:
            nested = json.loads(value) if value.lstrip().startswith(('{', '[')) else None
        except ValueError:
            nested = None
        if isinstance(nested, (dict, list)):
            clean = sanitize(nested, public_name=public_name)
            if clean != nested:
                return compact(clean)
        return redact_params(value)
    return value


def successful(value):
    return ('code' in value and value['code'] in (0, 200, '0', '200')
            or value.get('result') in (True, 'success')
            or any(value.get(k) is True for k in ('success', 'ok')))


class Reader:
    def __init__(self, session, names, start=None, end=None, max_pages=None):
        self.session = session
        self.start, self.end, self.max_pages = start, end, max_pages
        self.tables = {name: [] for name in names}
        self.rc = 0
        self.protected = set()

    def fail(self, message):
        raise SessionError(message)

    def partial(self, message):
        self.rc = 3
        print(message, file=sys.stderr)

    def server_error(self, response, value):
        original = response['text']
        is_html = original.lstrip().startswith('<')
        if value is None and is_html:
            # HTML can contain account globals or renewed tokens, unlike a short error body.
            if re.search(r'login|captcha|verification code|验证码', original, re.I):
                self.fail('session_expired_or_captcha')
            self.fail('HTTP %s: JSON response expected' % response['status'])
        clean = sanitize(value)
        message = redact_params(original) if clean == value else compact(clean)
        self.fail(message.rstrip('\n') or 'HTTP %s: empty response' % response['status'])

    def payload(self, response, check=None):
        value = parse(response['text'])
        if (not 200 <= response['status'] < 300 or not isinstance(value, dict)
                or any(value.get(k) is False for k in ('success', 'successed', 'ok', 'result'))
                or value.get('hasError') is True or bool(value.get('errorVO'))
                or value.get('error') not in (None, False, '', 0)
                or value.get('code') not in (None, 0, 200, '0', '200')
                or value.get('errorCode') not in (None, '', 0, '0')
                or not (check or successful)(value)):
            self.server_error(response, value)
        return value

    def field(self, value, path, kind):
        field = get_path(value, path)
        if not isinstance(field, kind):
            self.fail('response field missing or invalid: ' + path)
        return field

    def get(self, url, query=None, *, referer=None, check=None, send_origin=True):
        return self.payload(self.session.request('GET', url, query, referer=referer, send_origin=send_origin), check)

    def post(self, url, params=None, *, query=None, encoding='form', referer=None, check=None):
        return self.payload(self.session.request('POST', url, query, params, encoding, referer), check)

    def html(self, url, *, referer=None):
        response = self.session.request('GET', url, referer=referer, send_origin=False)
        if response['status'] != 200:
            self.server_error(response, parse(response['text']))
        return response['text']

    def mtop(self, url, query, data, *, referer=None, check=None):
        self.protected.update(str(v) for k, v in data.items() if private_key(k))
        return self.payload(self.session.request_mtop(url, query, data, referer=referer), check)

    def pages(self, fetch, *, rows, total, start=1):
        result, expected, seen = [], None, set()
        for page in itertools.count(start):
            value = fetch(page)
            count, batch = get_path(value, total), get_path(value, rows)
            if isinstance(count, str) and count.isascii() and count.isdigit():
                count = int(count)
            if type(count) is not int or count < 0:
                if isinstance(batch, list) and compact(batch) not in seen:
                    result.extend(batch)
                self.partial(f'{total}: missing or invalid total; received {len(result)} rows')
                return result
            if expected is None:
                expected = count
            if batch is None and count == 0:
                batch = []
            if not isinstance(batch, list):
                break
            signature = compact(batch)
            if signature in seen:
                break
            seen.add(signature)
            result.extend(batch)
            if count != expected:
                self.partial(f'{total}: changed from {expected} to {count}; received {len(result)} rows')
                return result
            if len(result) > expected:
                break
            if len(result) == expected:
                return result
            if not batch or self.max_pages is not None and page - start + 1 >= self.max_pages:
                break
        self.partial(f'{rows}: received {len(result)} rows; {total}={count}')
        return result

    def add(self, table, value, *, public_name=False):
        if value is None:
            return
        batch = value if isinstance(value, list) else [value]
        self.tables[table].extend(sanitize(row if isinstance(row, dict) else {'value': row},
                                           public_name=public_name) for row in batch)


def collect(command, args, session):
    from dashboard_reads import HANDLERS, TABLE_NAMES
    from ads_dashboard import ADS_HANDLERS, ADS_TABLES
    handlers, names = HANDLERS | ADS_HANDLERS, TABLE_NAMES | ADS_TABLES
    read = Reader(session, names[command], args.start, args.end, args.max_pages)
    period = {'period_mode': 'snapshot'}
    if command in ('ads-brand', 'ads-showcase'):
        period = {'period_mode': 'range', 'table': 'brand_keyword_effect' if command == 'ads-brand' else 'showcase_effect_total',
                  'start': args.start, 'end': args.end,
                  'other_tables': 'snapshot' if command == 'ads-brand' else 'showcase_effect_total only'}
    elif command == 'products-effect':
        period['statisticsType'] = 'month'
    try:
        handlers[command](read)
    except SessionError as error:
        message = str(error)
        for secret in read.protected:
            if secret:
                message = message.replace(secret, '***')
        print(message, file=sys.stderr)
        read.rc = 5
    return {**read.tables, 'period': [period], '_exit': read.rc}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command')
    parser.add_argument('--account', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--max-pages', type=int)
    parser.add_argument('--format', choices=('table', 'json', 'csv'), default='table')
    parser.add_argument('--out')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args(argv)
    session = ApiSession(load_account(args.config, args.account), args.config, refresh_saved=False)
    tables = collect(args.command, args, session)
    emit(tables, args, raw=True)
    return tables['_exit']


if __name__ == '__main__':
    sys.exit(main())
