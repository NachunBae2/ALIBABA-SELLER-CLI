"""Pure Python read transport; cookie_store stdout is consumed only through a RAM pipe."""
import http.cookiejar
import http.client
import hashlib
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


PUNISH_URL = 'https://acs.h.alibaba.com/h5/mtop.alibaba.camel.antioffline.ggs.punish.get/1.0/'
MTOP_TOKEN_ERROR = re.compile(r'^FAIL_SYS_TOKEN_(?:EXPIRED|EXOIRED|EMPTY)(?:::|$)')
GROWTH_ENDPOINT = ('productcenter.alibaba.com', '/product/management/query-incubation-product.json')
ADS_CSRF_HOSTS = ('www2.alibaba.com', 'creative.alibaba.com')
# GET reads whose screen sends ctoken/_tb_token_ in the query (captured 2026-11-18). Exact
# paths only: other GETs on the same hosts (e.g. biz settlements context.json) must not get it.
SELLER_QUERY_TOKEN_PATHS = frozenset([
    ('biz.alibaba.com', '/ggs/web/fee/getFeeStatusView.json'),
    ('biz.alibaba.com', '/ggs/web/funds/queryWalletList.json'),
    ('biz.alibaba.com', '/ggs/web/withdraw/queryBalance.json'),
    ('fund.alibaba.com', '/seller/apollo/forex/queryWaitRelateAmountEveryCcy.json'),
    ('i.alibaba.com', '/ajax/ggsTaskProgress.do'),
    ('marketing.alibaba.com', '/enroll/GGSAcitities.do'),
    ('marketing.alibaba.com', '/enroll/myEnrollActivityList.do'),
    ('merchant-rating.alibaba.com', '/task/ggsFetchStarTaskV2.jsonp'),
])


def mtop_sign(token, timestamp, app_key, data):
    return hashlib.md5(f'{token}&{timestamp}&{app_key}&{data}'.encode()).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ApiSession:
    transport = 'api'

    def __init__(self, acc, config, cookie_reader=None, opener=None, *, refresh_saved=True):
        self.acc, self.config = acc, config
        self.cookie_reader = cookie_reader or self._pipe
        self.jar = http.cookiejar.CookieJar()
        self.opener = opener or urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect())
        self.metrics = dict(collectionTabOpens=0, refreshTabOpens=0, httpRequests=0, asideCalls=0, cookieRefreshes=0, replBatches=0)
        self.initialized = self.refreshed = False
        self.csrf = {}
        self.refresh_saved = refresh_saved

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
        auth_error = r'User not login|FAIL_SYS_(?:SESSION_EXPIRED|ILLEGAL_ACCESS)|RGV587_ERROR|"(?:code|errorCode)"\s*:\s*"?(?:NOT_LOGIN|LOGIN_REQUIRED)'
        if url.split('?', 1)[0] != PUNISH_URL:
            auth_error += r'|FAIL_SYS_TOKEN_(?:EXPIRED|EMPTY)'
        login_status = status == 401 or (self.refresh_saved and status == 403)
        if login_status or re.search(r'login|passport|punish|captcha|verify', location, re.I) or re.search(auth_error, text, re.I):
            diagnostic = {'httpStatus': status}
            if location:
                diagnostic['redirectHost'] = urllib.parse.urlsplit(location).hostname
            try:
                value = json.loads(text)
                if isinstance(value, dict):
                    diagnostic.update({k: value[k] for k in ('code', 'message', 'msg', 'errorCode')
                                       if k in value and isinstance(value[k], (str, int, bool))})
            except ValueError:
                pass
            raise SessionError('session_expired_or_captcha', diagnostic=diagnostic)
        return dict(status=status, text=text, ct=ct, redirectHost=urllib.parse.urlsplit(location).hostname)

    def request(self, method, url, query=None, params=None, encoding='form', referer=None, *, send_origin=True):
        try:
            if not self.initialized:
                self.refresh()
            return self._request(method, url, query, params, encoding, referer, send_origin)
        except SessionError as e:
            if not self.refresh_saved or str(e) not in ('session_expired_or_captcha', 'saved_cookie_unavailable') or self.refreshed:
                raise
            try:
                self.refresh(source=True)
            except SessionError as refresh_error:
                raise SessionError(str(refresh_error), diagnostic=e.diagnostic) from None
            return self._request(method, url, query, params, encoding, referer, send_origin)

    def request_mtop(self, url, query, data, *, referer=None):
        if url != PUNISH_URL:
            raise SessionError('mtop_bootstrap_contract_missing')
        if not self.initialized:
            self.refresh()
        payload = compact(data)
        referer = referer or 'https://onetalk.alibaba.com/message/weblitePWA.htm'
        source = urllib.parse.urlsplit(referer)
        headers = {'Referer': referer,
                   'Origin': source.scheme + '://' + source.netloc}
        for attempt in range(2):
            cookie = self.cookie('_m_h5_tk', url)
            token = urllib.parse.unquote(cookie or '').split('_', 1)[0]
            timestamp = str(time.time_ns() // 1_000_000)
            params = {**query, 't': timestamp, 'sign': mtop_sign(token, timestamp, query['appKey'], payload), 'data': payload}
            response = self.send('GET', url + '?' + urllib.parse.urlencode(params), headers)
            try:
                value = json.loads(response['text'])
                ret = value.get('ret', []) if isinstance(value, dict) else []
            except ValueError:
                ret = []
            retryable = isinstance(ret, list) and any(isinstance(r, str) and MTOP_TOKEN_ERROR.match(r) for r in ret)
            success = isinstance(ret, list) and any(isinstance(r, str) and r.startswith('SUCCESS::') for r in ret)
            # HTTPCookieProcessor has already applied the response's Set-Cookie.
            updated = self.cookie('_m_h5_tk', url)
            if attempt or (not retryable and (token or success)) or not updated or updated == cookie:
                return response

    def _request(self, method, url, query, params, encoding, referer, send_origin):
        u = urllib.parse.urlsplit(url)
        if u.scheme != 'https' or not (u.hostname or '').endswith('.alibaba.com'):
            raise SessionError('untrusted_endpoint')
        if u.path.startswith('/h5/'):
            raise SessionError('mtop_bootstrap_contract_missing')
        origin = 'https://' + u.netloc
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
                   'Accept': 'application/json, text/plain, */*', 'Referer': referer or origin + '/',
                   'Origin': origin, 'X-Requested-With': 'XMLHttpRequest'}
        if u.hostname == 'crmweb.alibaba.com':
            # This bundle is embedded in i.alibaba.com.
            headers['Origin'] = 'https://i.alibaba.com'
        seller_command = u.hostname == 'biz.alibaba.com' and u.path == '/ggs/page/service/command.json'
        if seller_command:
            headers['Origin'] = 'https://i.alibaba.com'
            headers.pop('X-Requested-With', None)
        if u.hostname in ('productcenter.alibaba.com', 'us-productposting.alibaba.com'):
            # These APIs are called by the i.alibaba.com product management SPA.
            headers['Referer'] = referer or 'https://i.alibaba.com/'
        if referer or u.hostname in ('productcenter.alibaba.com', 'us-productposting.alibaba.com'):
            source = urllib.parse.urlsplit(headers['Referer'])
            headers['Origin'] = source.scheme + '://' + source.netloc
        if not send_origin:
            headers.pop('Origin', None)
        form = lambda data: urllib.parse.urlencode({k: compact(v) if isinstance(v, (dict, list)) else str(v).lower() if isinstance(v, bool) else v for k,v in data.items()})
        for attempt in range(4):
            q, p = dict(query or {}), dict(params or {})
            target = q if method == 'GET' else p
            # These applications reject the legacy cookie token in the query.
            # Product Center reads use the session cookie; Auth Center uses its
            # own generateToken bootstrap, not the global _tb_token_ cookie.
            cookie_tokens = () if u.hostname in ('productcenter.alibaba.com', 'crmweb.alibaba.com') else ('ctoken', '_tb_token_', 'p_csrf')
            for k in cookie_tokens:
                value = self.cookie(k, url)
                if value:
                    target[k] = urllib.parse.unquote(value)
            seller_token_query = (seller_command or u.hostname == 'us-productposting.alibaba.com'
                                  or method == 'GET' and (u.hostname, u.path) in SELLER_QUERY_TOKEN_PATHS
                                  or (u.hostname, u.path) == GROWTH_ENDPOINT)
            if seller_token_query:
                # The seller apps read the nested token, even when a legacy
                # ctoken cookie with the same name exists.
                nested = re.search(r'(?:^|&)ctoken=([^&]+)', self.cookie('xman_us_t', url) or '')
                if nested:
                    q['ctoken'] = urllib.parse.unquote(nested[1])
                elif (value := self.cookie('ctoken', url)):
                    q['ctoken'] = urllib.parse.unquote(value)
                if (value := self.cookie('_tb_token_', url)):
                    q['_tb_token_'] = urllib.parse.unquote(value)
            if u.hostname == 'crmweb.alibaba.com':
                if u.hostname not in self.csrf:
                    r = self.send('GET', origin + '/ggsgolden/ggsGoldenMa/generateToken.json', headers)
                    try:
                        c = json.loads(r['text'])
                        assert r['status'] == 200 and c.get('hasError') is False
                        c = c['content']
                        assert c['parameterName'] and c['token']
                    except (ValueError, KeyError, TypeError, AssertionError):
                        raise SessionError('csrf_bootstrap_failed') from None
                    self.csrf[u.hostname] = c
                c = self.csrf[u.hostname]
                # The bundle uses _csrf_token, despite parameterName=_csrf
                # in the bootstrap response (the latter returns an empty body).
                target['_csrf_token'] = c['token']
            if u.hostname in ADS_CSRF_HOSTS:
                if u.hostname not in self.csrf:
                    r = self.send('GET', origin + '/api/csrf?_=' + str(time.time_ns()), headers)
                    try:
                        c = json.loads(r['text'])
                        assert r['status'] == 200 and c['headerName'] and c['parameterName'] and c['token']
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
                if u.hostname == 'crmweb.alibaba.com' and u.path == '/authcenter/cert/filter.json' and encoding == 'form':
                    # Match the certification list's fetch form contract.
                    headers['Content-Type'] = 'application/x-www-form-urlencoded;multipart/form-data'
                    headers.pop('X-Requested-With', None)
                body = (compact(p) if encoding == 'json' else form(p)).encode()
            response = self.send(method, url + (('&' if '?' in url else '?') + form(q) if q else ''), headers, body)
            if response['status'] == 302 and response['redirectHost'] == 'err.taobao.com' and u.hostname in ADS_CSRF_HOSTS:
                self.csrf.pop(u.hostname, None)
                continue
            return response
        raise SessionError('csrf_redirect_failed: HTTP 302 err.taobao.com')

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
            raise SessionError(str(e), responses, diagnostic=e.diagnostic) from None
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
