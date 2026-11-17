"""Pure Python read transport; cookie_store stdout is consumed only through a RAM pipe."""
import http.cookiejar
import http.client
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from http_session import SessionError, compact


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ApiSession:
    transport = 'api'

    def __init__(self, acc, config, cookie_reader=None, opener=None):
        self.acc, self.config = acc, config
        self.cookie_reader = cookie_reader or self._pipe
        self.jar = http.cookiejar.CookieJar()
        self.opener = opener or urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect())
        self.metrics = dict(collectionTabOpens=0, refreshTabOpens=0, httpRequests=0, asideCalls=0, cookieRefreshes=0, replBatches=0)
        self.initialized = self.refreshed = False
        self.csrf = {}

    def _pipe(self, refresh):
        command = [sys.executable, str(Path(__file__).with_name('cookie_store.py')), 'refresh' if refresh else 'load', self.acc['name'], '--config', self.config]
        if refresh:
            command.append('--read-only')
        # No shell, file, diagnostic output, or cookie values in exception messages.
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=200)
            if result.returncode:
                raise SessionError('saved_cookie_unavailable')
            cookies = json.loads(result.stdout)
        except (ValueError, subprocess.TimeoutExpired):
            raise SessionError('saved_cookie_unavailable') from None
        return cookies

    def refresh(self, source=False):
        if source:
            if self.refreshed:
                raise SessionError('session_expired_or_captcha')
            self.refreshed = True
            self.metrics['cookieRefreshes'] += 1
        cookies = self.cookie_reader(source)
        from cookie_store import identity
        state = identity(cookies, self.acc['loginId'])
        if state == 'wrong_account':
            raise SessionError('wrong_account')
        if state != 'ok':
            raise SessionError('session_expired_or_captcha')
        self.jar.clear()
        self.csrf.clear()
        for c in cookies:
            domain = c['domain']
            self.jar.set_cookie(http.cookiejar.Cookie(0, c['name'], c['value'], None, False,
                domain, domain.startswith('.'), domain.startswith('.'), c.get('path') or '/', True,
                c.get('secure', False), int(c['expires']) if c.get('expires', -1) > 0 else None,
                False, None, None, {}, False))
        self.initialized = True

    def cookie(self, name, url):
        req = urllib.request.Request(url)
        self.jar.add_cookie_header(req)
        return dict(x.strip().split('=', 1) for x in (req.get_header('Cookie') or '').split(';') if '=' in x).get(name)

    def send(self, method, url, headers=None, body=None):
        u = urllib.parse.urlsplit(url)
        if u.scheme != 'https' or not (u.hostname or '').endswith('.alibaba.com'):
            raise SessionError('untrusted_endpoint')
        self.metrics['httpRequests'] += 1
        try:
            response = self.opener.open(urllib.request.Request(url, data=body, headers=headers or {}, method=method), timeout=30)
        except urllib.error.HTTPError as e:
            response = e
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            raise SessionError('http_transport_failed') from None
        with response:
            status, text = response.code, response.read().decode('utf-8', 'replace')
            location = response.headers.get('Location', '')
            ct = response.headers.get('Content-Type', '')
        if status in (401, 403) or re.search(r'login|passport|punish', location, re.I) or re.search(
                r'User not login|FAIL_SYS_(?:SESSION_EXPIRED|TOKEN_EXPIRED|TOKEN_EMPTY|ILLEGAL_ACCESS)|RGV587_ERROR|"(?:code|errorCode)"\s*:\s*"?(?:NOT_LOGIN|LOGIN_REQUIRED)', text, re.I):
            raise SessionError('session_expired_or_captcha')
        return dict(status=status, text=text, ct=ct, redirectHost=urllib.parse.urlsplit(location).hostname)

    def request(self, method, url, query=None, params=None, encoding='form', referer=None):
        try:
            if not self.initialized:
                self.refresh()
            return self._request(method, url, query, params, encoding, referer)
        except SessionError as e:
            if str(e) not in ('session_expired_or_captcha', 'saved_cookie_unavailable') or self.refreshed:
                raise
            self.refresh(source=True)
            return self._request(method, url, query, params, encoding, referer)

    def _request(self, method, url, query, params, encoding, referer):
        u = urllib.parse.urlsplit(url)
        if u.scheme != 'https' or not (u.hostname or '').endswith('.alibaba.com'):
            raise SessionError('untrusted_endpoint')
        if u.path.startswith('/h5/'):
            raise SessionError('mtop_bootstrap_contract_missing')
        origin = 'https://' + u.netloc
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
                   'Accept': 'application/json, text/plain, */*', 'Referer': origin + '/' if u.hostname == 'www2.alibaba.com' else referer or origin + '/',
                   'Origin': origin, 'X-Requested-With': 'XMLHttpRequest'}
        form = lambda data: urllib.parse.urlencode({k: compact(v) if isinstance(v, (dict, list)) else str(v).lower() if isinstance(v, bool) else v for k,v in data.items()})
        for attempt in range(4):
            q, p = dict(query or {}), dict(params or {})
            target = q if method == 'GET' else p
            for k in ('ctoken', '_tb_token_', 'p_csrf'):
                value = self.cookie(k, url)
                if value:
                    target[k] = urllib.parse.unquote(value)
            if u.hostname == 'www2.alibaba.com':
                if u.hostname not in self.csrf:
                    r = self.send('GET', origin + '/api/csrf?_=' + str(time.time_ns()), headers)
                    try:
                        c = json.loads(r['text'])
                        assert c['headerName'] and c['parameterName'] and c['token']
                    except (ValueError, KeyError, TypeError, AssertionError):
                        raise SessionError('csrf_bootstrap_failed') from None
                    self.csrf[u.hostname] = c
                c = self.csrf[u.hostname]
                headers[c['headerName']] = c['token']
                target[c['parameterName']] = c['token']
            if u.hostname == 'message.alibaba.com' and method == 'POST':
                if u.hostname not in self.csrf:
                    r = self.send('GET', origin + '/message/default.htm', headers)
                    token = re.search(r'''["']?csrfTokenVal["']?\s*[:=]\s*["']([^"']+)''', r['text'])
                    if not token:
                        raise SessionError('csrf_bootstrap_failed')
                    self.csrf[u.hostname] = token[1]
                p['_csrf_token_'] = self.csrf[u.hostname]
            body = None
            if method != 'GET':
                headers['Content-Type'] = 'application/json' if encoding == 'json' else 'application/x-www-form-urlencoded; charset=UTF-8'
                body = (compact(p) if encoding == 'json' else form(p)).encode()
            response = self.send(method, url + (('&' if '?' in url else '?') + form(q) if q else ''), headers, body)
            if response['status'] == 302 and response['redirectHost'] == 'err.taobao.com' and u.hostname == 'www2.alibaba.com':
                self.csrf.pop(u.hostname, None)
                continue
            return response
        raise SessionError('csrf_redirect_failed')

    def request_many(self, requests):
        # Bootstrap each host serially; independent read requests then share the cookie jar.
        from concurrent.futures import ThreadPoolExecutor
        responses = []
        try:
            while requests and (not self.initialized or urllib.parse.urlsplit(requests[0][1]).hostname not in self.csrf):
                responses.append(self.request(*requests[0]))
                requests = requests[1:]
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(self.request, *r) for r in requests]
                for future in futures:
                    responses.append(future.result())
        except SessionError as e:
            raise SessionError(str(e), responses) from None
        return responses

    def buyer_profiles(self, items, config):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            groups = list(pool.map(lambda item: self._buyer_profiles_serial([item], config), items))
        return [profile for group in groups for profile in group]

    def _buyer_profiles_serial(self, items, config):
        from http_collect import parse, sanitize_buyer_profile
        profiles = []
        for item in items:
            summary = parse(self.request('POST', config['summaryUrl'], params={'params': {'secTradeId': item.get('secTradeId')}})['text']) or {}
            contact = (summary.get('data') or {}).get('contact') or {}
            if not contact.get('accountIdEncrypt'):
                profiles.append(dict(tradeId=item.get('tradeId'), available=False, reason='querySummary contact 없음'))
                continue
            # HTML is only fetched. No DOM, script execution, or read-status endpoint.
            detail = self.request('GET', config['detailUrl'], {'imInquiryId': item.get('tradeId')})
            token = re.search(r'''window\.KHTAccessToken\s*=\s*['"]([^'"]+)['"]''', detail['text'])
            if not token:
                profiles.append(dict(tradeId=item.get('tradeId'), available=False, reason='상세 HTML KHTAccessToken 없음'))
                continue
            query = dict(buyerAccountId=contact['accountIdEncrypt'], secTradeId=summary['data'].get('secTradeId'),
                         buyerLoginId='', secReqToken=token[1], clientType='', lang='en_US', callback='profileRead')
            response = self.request('GET', config['url'], query)
            profiles.append(dict(tradeId=item.get('tradeId'), **sanitize_buyer_profile(parse(response['text']))))
        return profiles
