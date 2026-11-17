"""Seller HTTP session. Credentials travel over a kernel RAM pipe, never stdout/plaintext disk."""
import base64
import zlib
import hashlib
import http.cookiejar
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SECRET = re.compile(r'csrf|xsrf|token|^sign$|^_m_h5_tk|^cookie$|^authorization$|^ua$|^bx-|^x5sec$', re.I)


class SessionError(Exception):
    pass


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def scrub(value):
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if not SECRET.search(k)}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        try:
            nested = json.loads(value)
            if isinstance(nested, (dict, list)):
                return compact(scrub(nested))
        except ValueError:
            pass
        return re.sub(r'([\w-]*(?:csrf|xsrf|token)[\w-]*=)[^&\s;"\\]+', r'\1***', value, flags=re.I)
    return value


def mtop_sign(token, timestamp, app_key, data):
    return hashlib.md5(f'{token.split("_")[0]}&{timestamp}&{app_key}&{data}'.encode()).hexdigest()


def aside_command(exe, account):
    wrapper = os.environ.get('ALIBABA_SELLER_ASIDE_WRAPPER')
    # A caller already holding the shared wrapper must set this explicitly.
    prefix = [] if os.environ.get('ALIBABA_SELLER_ASIDE_LOCK_HELD') == '1' else ([wrapper] if wrapper else [])
    return prefix + [exe, '--host', 'local', '--account', account, 'repl']


def browser_cookies(acc, exe):
    if not os.environ.get('ALIBABA_SELLER_ASIDE_WRAPPER') and os.environ.get('ALIBABA_SELLER_ASIDE_LOCK_HELD') != '1':
        raise SessionError('aside_lock_wrapper_required')
    # A FIFO is a kernel RAM pipe, not a plaintext file. Aside's fs is sandboxed
    # under its profile directory, so create the private pipe there.
    import tempfile
    import shutil
    import select
    if not hasattr(os, 'mkfifo'):
        raise SessionError('cookie_pipe_unsupported_platform')
    root = os.path.join(os.environ.get('ALIBABA_SELLER_ASIDE_HOME', os.path.expanduser('~/.aside')), 'u', acc['asideAccount'][1:])
    os.makedirs(root, exist_ok=True)
    work = tempfile.mkdtemp(prefix='cookie-pipe-', dir=root)
    pipe = os.path.join(work, 'session.pipe')
    os.mkfifo(pipe, 0o600)
    fd = os.open(pipe, os.O_RDWR | os.O_NONBLOCK)
    data, done, received = bytearray(), threading.Event(), threading.Event()
    def read_pipe():
        while not done.is_set():
            if select.select([fd], [], [], 0.1)[0]:
                data.extend(os.read(fd, 65536))
                if data.endswith(b'\n'):
                    received.set()
                if len(data) > 2_000_000:
                    done.set()
    reader = threading.Thread(target=read_pipe, daemon=True)
    reader.start()
    script = '''await (async () => {
      const ts = await listBrowserTabs();
      const t = ts.find(t => /(^|\\.)alibaba\\.com$/.test(new URL(t.url).hostname));
      const p = t ? await attachBrowserTab(t.targetId) : await openTab('https://i.alibaba.com/spa-micro/www2/ads/report.htm');
      // Full context cookies preserve HttpOnly flags and domain/path scoping.
      // Never substitute document.cookie: that silently loses login cookies.
      const ctx = typeof context !== 'undefined' ? context : typeof p.context === 'function' ? p.context() : null;
      if (!ctx || typeof ctx.cookies !== 'function') {
        await fs.writeFile(PIPE, JSON.stringify({error:'cookie_context_unavailable'})+'\\n');
        return;
      }
      const cs = await ctx.cookies();
      await fs.writeFile(PIPE, JSON.stringify({cookies:cs.filter(c => /(^|\\.)alibaba\\.com$/.test(c.domain)),cookieTabOpens:t?0:1})+'\\n');
      console.log('STATUS cookies_transferred');
    })();'''.replace('PIPE', json.dumps(pipe))
    try:
        result = subprocess.run(aside_command(exe, acc['asideAccount']), input=script,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, timeout=2000)
        received.wait(1)
        if result.returncode or not data:
            raise SessionError('cookie_transfer_failed')
        payload = json.loads(data)
        if isinstance(payload, dict) and payload.get('error') == 'cookie_context_unavailable':
            raise SessionError('cookie_context_unavailable')
        if isinstance(payload, dict) and isinstance(payload.get('cookies'), list):
            acc['_cookieTabOpens'] = payload.get('cookieTabOpens', 0)
            payload = payload['cookies']
        if not isinstance(payload, list):
            raise SessionError('cookie_transfer_invalid')
        identity = urllib.parse.unquote(';'.join(c.get('value', '') for c in payload))
        if acc['loginId'] not in identity:
            raise SessionError('wrong_account')
        return payload
    except subprocess.TimeoutExpired:
        raise SessionError('cookie_transfer_timeout') from None
    except ValueError:
        raise SessionError('cookie_transfer_invalid') from None
    finally:
        done.set()
        reader.join()
        os.close(fd)
        data.clear()
        shutil.rmtree(work)



class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Session:
    def __init__(self, acc, exe, login, cookie_reader=browser_cookies):
        self.acc, self.exe, self.login, self.cookie_reader = acc, exe, login, cookie_reader
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect())
        self.metrics = {'collectionTabOpens': 0, 'cookieReads': 0, 'cookieReadAttempts': 0, 'cookieAcquisitionTabOpens': 0, 'refreshTabOpens': 0, 'httpRequests': 0}
        self.tokens = {}
        self.csrf_headers = {}
        self.initialized = False
        self.started = time.monotonic()

    def refresh(self, login=False):
        if login:
            self.metrics['refreshTabOpens'] += 1
            status = self.login(self.acc)
            if status not in ('already_signed_in', 'signed_in'):
                raise SessionError('session_expired_or_captcha')
        import secret_store
        run_id = os.environ.get('ALIBABA_SELLER_HTTP_SESSION')
        cache_key = hashlib.sha256((self.acc['asideAccount'] + self.acc['loginId']).encode()).hexdigest()[:24]
        cookies = None
        if run_id and not login and not self.initialized:
            try:
                encoded = secret_store.get_secret('alibaba-seller-http-session', cache_key)
                cached = json.loads(zlib.decompress(base64.b64decode(encoded))) if encoded else {}
                if cached.get('run') == run_id and time.time() - cached.get('at', 0) < 3600:
                    cookies = cached.get('cookies')
            except (ValueError, zlib.error, secret_store.StoreError):
                pass
        if cookies is None:
            self.metrics['cookieReadAttempts'] += 1
            cookies = self.cookie_reader(self.acc, self.exe)
            self.metrics['cookieReads'] += 1
            self.metrics['cookieAcquisitionTabOpens'] += self.acc.get('_cookieTabOpens', 0)
            if run_id:
                try:
                    secret_store.set_secret('alibaba-seller-http-session', cache_key, base64.b64encode(zlib.compress(compact({'run': run_id, 'at': time.time(), 'cookies': cookies}).encode())).decode())
                except secret_store.StoreError:
                    self.metrics['secretStoreUnavailable'] = True
        self.jar.clear()
        self.tokens.clear()
        self.csrf_headers.clear()
        for c in cookies:
            domain = c['domain']
            self.jar.set_cookie(http.cookiejar.Cookie(0, c['name'], c['value'], None, False,
                domain, domain.startswith('.'), domain.startswith('.'), c.get('path', '/'), True,
                c.get('secure', False), int(c['expires']) if c.get('expires', -1) > 0 else None,
                False, None, None, {}, False))
        self.initialized = True

    def cookie(self, name, url):
        request = urllib.request.Request(url)
        self.jar.add_cookie_header(request)
        return dict(x.strip().split('=', 1) for x in (request.get_header('Cookie') or '').split(';') if '=' in x).get(name)

    def request(self, method, url, query=None, params=None, encoding='form', referer=None, auth=True):
        u = urllib.parse.urlsplit(url)
        if u.scheme != 'https' or not (u.hostname or '').endswith('.alibaba.com'):
            raise SessionError('untrusted_endpoint')
        if not self.initialized:
            self.refresh()
        if u.hostname == 'www2.alibaba.com' and auth and u.hostname not in self.csrf_headers:
            bootstrap = self.request('GET', 'https://www2.alibaba.com/api/csrf', auth=False)
            try:
                csrf = json.loads(bootstrap['text'])
                self.tokens[(u.hostname, csrf['parameterName'])] = csrf['token']
                self.csrf_headers[u.hostname] = {csrf['headerName']: csrf['token']}
            except (ValueError, KeyError, TypeError):
                raise SessionError('csrf_bootstrap_failed') from None
        for attempt in range(3):
            q = dict(query or {})
            p = dict(params or {})
            token_keys = ('ctoken', '_tb_token_', '_csrf_token_', 'p_csrf')
            for k in token_keys:
                value = self.tokens.get((u.hostname, k)) or self.cookie(k, url)
                if value and auth:
                    (q if method == 'GET' else p)[k] = value
            if auth:
                for (host, name), value in self.tokens.items():
                    if host == u.hostname:
                        (q if method == 'GET' else p)[name] = value
            if '/h5/' in u.path:
                data = p.get('data', q.get('data', '{}'))
                data = compact(data) if not isinstance(data, str) else data
                (p if 'data' in p else q)['data'] = data
                tk = self.cookie('_m_h5_tk', url)
                if tk:
                    q['t'] = str(int(time.time() * 1000))
                    q['sign'] = mtop_sign(tk, q['t'], str(q['appKey']), data)
            def form(o):
                return urllib.parse.urlencode({k: compact(v) if isinstance(v, (dict, list)) else str(v).lower() if isinstance(v, bool) else v for k, v in o.items()})
            target = url + ('&' if '?' in url else '?') + form(q) if q else url
            headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json, text/plain, */*', 'Referer': referer or url,
                       'Origin': urllib.parse.urlunsplit((u.scheme, u.netloc, '', '', '')), 'X-Requested-With': 'XMLHttpRequest'}
            if auth:
                headers.update(self.csrf_headers.get(u.hostname, {}))
            body = None
            if method != 'GET':
                headers['Content-Type'] = 'application/json' if encoding == 'json' else 'application/x-www-form-urlencoded; charset=UTF-8'
                body = (compact(p) if encoding == 'json' else form(p)).encode()
            self.metrics['httpRequests'] += 1
            try:
                response = self.opener.open(urllib.request.Request(target, data=body, headers=headers, method=method), timeout=45)
            except urllib.error.HTTPError as e:
                response = e
            except (urllib.error.URLError, TimeoutError):
                raise SessionError('http_transport_failed') from None
            with response:
                status, text = response.code, response.read().decode('utf-8', errors='replace')
                ct = response.headers.get('Content-Type', '')
                location = response.headers.get('Location', '')
            expired = status in (401, 403) or status in (301, 302, 303, 307, 308) and re.search(r'login|passport|punish', location, re.I)
            expired = expired or re.search(r'FAIL_SYS_(?:SESSION_EXPIRED|TOKEN_EXPIRED|TOKEN_EMPTY|ILLEGAL_ACCESS)|RGV587_ERROR|"(?:code|errorCode)"\s*:\s*"?(?:NOT_LOGIN|LOGIN_REQUIRED)', text)
            if expired:
                if attempt == 2:
                    raise SessionError('session_expired_or_captcha')
                self.refresh(login=attempt == 1)
                if u.hostname == 'www2.alibaba.com' and auth:
                    bootstrap = self.request('GET', 'https://www2.alibaba.com/api/csrf', auth=False)
                    try:
                        csrf = json.loads(bootstrap['text'])
                        self.tokens[(u.hostname, csrf['parameterName'])] = csrf['token']
                        self.csrf_headers[u.hostname] = {csrf['headerName']: csrf['token']}
                    except (ValueError, KeyError, TypeError):
                        raise SessionError('csrf_bootstrap_failed') from None
                continue
            return {'status': status, 'ct': ct, 'text': text}
        raise SessionError('session_expired')
