"""Read-only requests through Aside's cookie-bearing fetch; credentials stay in REPL."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

SECRET = re.compile(r'csrf|xsrf|token|^sign$|^_m_h5_tk|^cookie$|^authorization$|^ua$|^bx-|^x5sec$', re.I)


class SessionError(Exception):
    def __init__(self, message, responses=None):
        super().__init__(message)
        self.responses = responses or []


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
        return re.sub(r'([\w-]*(?:csrf|xsrf|token|sign)[\w-]*=)[^&\s;"\\]+', r'\1***', value, flags=re.I)
    return value


def aside_command(exe, account):
    wrapper = os.environ.get('ALIBABA_SELLER_ASIDE_WRAPPER')
    prefix = [] if os.environ.get('ALIBABA_SELLER_ASIDE_LOCK_HELD') == '1' else ([wrapper] if wrapper else [])
    if prefix:
        bridge = str(Path(__file__).resolve().parent.parent / 'scripts' / 'aside-fetch.mjs')
        return prefix + [shutil.which('node') or 'node', bridge, exe, account]
    return [exe, '--host', 'local', '--account', account, 'repl']


class Session:
    def __init__(self, acc, exe, login):
        self.acc, self.exe, self.login = acc, exe, login
        self.metrics = {'collectionTabOpens': 0, 'refreshTabOpens': 0, 'httpRequests': 0, 'replBatches': 0}
        self.started = time.monotonic()
        self.verified = False

    def request(self, method, url, query=None, params=None, encoding='form', referer=None):
        return self.request_many([(method, url, query, params, encoding, referer)])[0]

    def request_many(self, requests):
        if not os.environ.get('ALIBABA_SELLER_ASIDE_WRAPPER') and os.environ.get('ALIBABA_SELLER_ASIDE_LOCK_HELD') != '1':
            raise SessionError('aside_lock_wrapper_required')
        jobs = []
        for method, url, query, params, encoding, referer in requests:
            u = urlsplit(url)
            if u.scheme != 'https' or not (u.hostname or '').endswith('.alibaba.com'):
                raise SessionError('untrusted_endpoint')
            jobs.append(dict(method=method, url=url, query=query or {}, params=params or {}, encoding=encoding, referer=referer))
        results, refreshed, transport_failures = [], False, 0
        while len(results) < len(jobs):
            # The script stops after 65 s; each fetch has a 20 s bound, below REPL's 120 s limit.
            batch = self._batch(jobs[len(results):len(results) + 20])
            self.metrics['httpRequests'] += batch.get('httpRequests', 0)
            self.metrics['replBatches'] += 1
            results.extend(batch.get('responses', []))
            error = batch.get('error')
            if error == 'session_expired_or_captcha' and not refreshed:
                self.metrics['refreshTabOpens'] += 1
                status = self.login(self.acc)
                if status not in ('already_signed_in', 'signed_in'):
                    raise SessionError('session_expired_or_captcha')
                refreshed = True
            elif error == 'http_transport_failed' and transport_failures < 2:
                transport_failures += 1
                continue
            elif error:
                raise SessionError(error, results)
            elif not batch.get('responses'):
                raise SessionError('aside_empty_batch')
        return results

    def _batch(self, jobs):
        source = Path(__file__).with_name('http_fetch.js').read_text(encoding='utf-8')
        # Only request definitions enter this private file. No credentials/cookies/token handoff.
        source += '\nawait sellerFetchBatch(' + compact({'jobs': jobs, 'expectedIdentity': None if self.verified else self.acc.get('loginId')}) + ');\n'
        with tempfile.TemporaryDirectory(prefix='seller-fetch-') as work:
            script = Path(work) / 'batch.js'
            script.write_text(source, encoding='utf-8')
            script.chmod(0o600)
            try:
                # The lock wrapper's heartbeat can inherit pipes after its child exits.
                # Private files avoid waiting for that unrelated sleep process to close stdout.
                output = Path(work) / 'result.jsonl'
                with output.open('w+', encoding='utf-8') as stream:
                    output.chmod(0o600)
                    proc = subprocess.run(aside_command(self.exe, self.acc['asideAccount']) + ['--file', str(script)],
                                          stdout=stream, stderr=subprocess.DEVNULL, text=True, timeout=2000)
                    stream.seek(0)
                    stdout = stream.read()
            except subprocess.TimeoutExpired:
                raise SessionError('aside_transport_timeout') from None
        # Never echo REPL errors/source, which could contain request values.
        lines = [line[len('SELLER_FETCH '):] for line in stdout.splitlines() if line.startswith('SELLER_FETCH ')]
        if proc.returncode or not lines:
            raise SessionError('aside_transport_failed')
        try:
            result = json.loads(lines[-1])
            self.verified = self.verified or result.get('accountVerified', False)
            return result
        except ValueError:
            raise SessionError('aside_invalid_response') from None
