import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'engine'))
from http_session import Session, SessionError, mtop_sign, scrub, browser_cookies
from http_collect import collect, sanitize_inquiry, sanitize_allowed, expanded_extras


def args(**kw):
    return SimpleNamespace(start_d=dt.date(2026, 1, 1), end_d=dt.date(2026, 1, 3), tables='', extras='',
        seeds=[], kw_pages=3, kw_nd='30d', granularity='week', max_pages=kw.get('max_pages', 3), max_passes=1)


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.metrics = {'collectionTabOpens': 0}

    def request(self, *a):
        self.calls.append(json.loads(json.dumps(a)))
        return {'status': 200, 'text': json.dumps(next(self.responses))}


def ads_page():
    return {'key': 'ads-report', 'url': 'https://www2.alibaba.com/ads/report.htm', 'rangeTables': [
        {'key': 'fixture', 'table': 'fixture_table', 'ctx': {'productLineId': 1}, 'filters': [], 'paged': True,
         'url': 'https://www2.alibaba.com/api/data/dynamic/component/data', 'method': 'POST',
         'rowsPath': 'data.queryResult.detailList', 'totalPath': 'data.queryResult.totalRows'}]}


def result(rows, total):
    return {'data': {'queryResult': {'detailList': rows, 'totalRows': total}}}


class DirectHTTP(unittest.TestCase):
    def test_mtop_signature_uses_exact_payload_and_cookie_token_prefix(self):
        data = '{"q":"가짜"}'
        expected = hashlib.md5(('fixture&123&456&'+data).encode()).hexdigest()
        self.assertEqual(mtop_sign('fixture_999', '123', '456', data), expected)

    def test_nested_secret_scrubbing(self):
        j = {'ctoken': 'fixture-secret', 'data': json.dumps({'_csrf_token_': 'fixture-secret', 'ok': 1}),
             'list': [{'sign': 'fixture-secret', 'x': 2}], 'url': 'x?token=fixture-secret&x=2'}
        safe = scrub(j)
        self.assertNotIn('fixture-secret', json.dumps(safe))
        self.assertEqual(json.loads(safe['data']), {'ok': 1})

    def test_page_parameters_and_dates_and_exact_total(self):
        s = FakeSession([result([{'v': 1}, {'v': 2}], 3), result([{'v': 3}], 3)])
        r = collect(ads_page(), args(), s, {})
        self.assertEqual(r['status'], 'ok')
        self.assertEqual([x[3]['data']['page']['index'] for x in s.calls], [1, 2])
        self.assertEqual(s.calls[0][3]['data']['time']['endDateTime'], '2026-01-03 00:00:00')
        self.assertTrue(r['coverage'][0]['complete'])
        self.assertEqual(r['coverage'][0]['rowsGot'], 3)
        self.assertEqual(r['metrics']['collectionTabOpens'], 0)

    def test_empty_page_before_total_is_incomplete(self):
        r = collect(ads_page(), args(), FakeSession([result([{'v': 1}], 3), result([], 3)]), {})
        self.assertEqual(r['status'], 'incomplete')
        self.assertFalse(r['coverage'][0]['complete'])

    def test_resume_starts_first_missing_page_and_counts_prior_rows(self):
        s = FakeSession([result([{'v': 3}], 3)])
        r = collect(ads_page(), args(), s, {'fixture_table[]': {'covered': 2, 'nextPage': 2}})
        self.assertEqual(s.calls[0][3]['data']['page']['index'], 2)
        self.assertTrue(r['coverage'][0]['complete'])

    def test_missing_contract_fails_closed_without_browser(self):
        s = FakeSession([])
        r = collect({'key': 'fixture', 'url': 'https://i.alibaba.com/'}, args(), s, {})
        self.assertEqual(r['status'], 'unavailable')
        self.assertEqual(s.calls, [])

    def test_inquiry_allowlist_preserves_large_ids_and_drops_private_values(self):
        raw = json.dumps({'code': 200, 'data': {'pagination': {'totalCount': 1}, 'list': [[{
            'id': 1601234567890123456, 'sender': {'name': 'private-fixture', 'countryCode': 'US', 'buyerLevel': 'L3'},
            'subject': 'private-fixture', 'chatToken': 'fixture-secret'}]]}})
        j = sanitize_inquiry(raw)
        self.assertEqual(j['list'][0]['id'], '1601234567890123456')
        self.assertEqual(j['list'][0]['buyerAttrs'], {'sender.buyerLevel': 'L3'})
        self.assertNotIn('private-fixture', json.dumps(j))
        self.assertNotIn('fixture-secret', json.dumps(j))

    def test_conversion_allowlist_before_persistence(self):
        j = sanitize_allowed(result([{'timeUs': '2026-01-01', 'buyerName': 'private-fixture'}], 1), ['timeUs'])
        self.assertNotIn('private-fixture', json.dumps(j))
        self.assertEqual(j['data']['queryResult']['totalRows'], 1)

    def test_domain_scoped_cookies_and_no_redirect(self):
        cookies = [{'name': 'ctoken', 'value': 'fixture', 'domain': 'www2.alibaba.com', 'path': '/', 'secure': True}]
        s = Session({'asideAccount': 'u0', 'loginId': 'fixture'}, 'aside', lambda _: 'signed_in', lambda *a: cookies)
        with patch.dict(os.environ, {}, clear=True):
            s.refresh()
        self.assertEqual(s.cookie('ctoken', 'https://www2.alibaba.com/x'), 'fixture')
        self.assertIsNone(s.cookie('ctoken', 'https://mydata.alibaba.com/x'))
        with self.assertRaises(SessionError):
            s.request('GET', 'https://example.com/')

    def test_catalog_never_replays_mutating_endpoints(self):
        catalog = json.loads((Path(__file__).resolve().parent.parent/'engine/pages.json').read_text())
        import re
        for p in catalog['pages']:
            for r in p.get('requests', []):
                self.assertNotRegex(r['url'], re.compile(r'/[^/]*(save|delete|submit|create|upload|publish|modify|update)', re.I))
                self.assertNotIn('{{binding}}', json.dumps(r))

    def test_period_selector_does_not_duplicate_latest_period(self):
        a = args()
        a.start_d, a.end_d = dt.date(2026, 1, 4), dt.date(2026, 1, 10)
        page = {'extra': [{'tag': 'fixture.week', 'perWeek': True, 'params': {}}]}
        with patch('seller_central_raw.today_pst', return_value=dt.date(2026, 1, 13)):
            result = expanded_extras(page, a)
        self.assertEqual([r['tag'] for r in result], ['fixture.week.2026-01-04'])
        self.assertEqual(result[0]['params']['selected'], '0')

    def test_request_selection_does_not_repeat_other_periods(self):
        a = args()
        a.skip_extras = True
        a.requests = 'second'
        page = {'key': 'fixture', 'url': 'https://i.alibaba.com/', 'requests': [
            {'tag': tag, 'url': 'https://i.alibaba.com/query', 'rowsPath': 'rows'} for tag in ['first', 'second']],
            'extra': [{'tag': 'unused', 'url': 'https://i.alibaba.com/query', 'params': {}}]}
        client = FakeSession([{'rows': []}])
        result = collect(page, a, client, {})
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(result['records'][0]['replay']['tag'], 'second')

    def test_http_200_application_error_is_not_success(self):
        r = collect(ads_page(), args(), FakeSession([{'code': 500, 'message': 'fixture'}]), {})
        self.assertEqual(r['status'], 'api_failed')
        self.assertFalse(r['coverage'][0]['complete'])
        self.assertEqual(r['records'], [])

    def test_context_cookies_use_private_pipe_not_stdout(self):
        import tempfile, subprocess, re
        import http_session
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'ALIBABA_SELLER_ASIDE_HOME': root, 'ALIBABA_SELLER_ASIDE_WRAPPER': '/fixture/lock'}):
            def run(command, **kw):
                pipe = json.loads(re.search(r'fs.writeFile\(("[^"\n]+")', kw['input']).group(1))
                self.assertTrue(Path(pipe).is_fifo())
                self.assertEqual(Path(pipe).stat().st_mode & 0o777, 0o600)
                self.assertEqual(kw['stdout'], subprocess.DEVNULL)
                with open(pipe, 'w') as fh:
                    fh.write(json.dumps({'cookies':[{'name':'session','value':'fixture-user','domain':'.alibaba.com','path':'/'}], 'cookieTabOpens':1})+'\n')
                return subprocess.CompletedProcess(command, 0)
            with patch.object(http_session.subprocess, 'run', side_effect=run):
                cookies = browser_cookies({'asideAccount':'u0','loginId':'fixture-user'}, 'aside')
            self.assertEqual(cookies[0]['value'], 'fixture-user')
            self.assertEqual(list(Path(root).rglob('session.pipe')), [])

    def test_expired_session_reloads_cookies_and_csrf_without_following_login_redirect(self):
        class Response:
            def __init__(self, code, body, headers=None):
                self.code, self.body, self.headers = code, json.dumps(body).encode(), headers or {}
            def read(self): return self.body
            def __enter__(self): return self
            def __exit__(self, *a): pass
        csrf = {'headerName':'X-CSRF-Token','parameterName':'_csrf','token':'fixture-secret'}
        reader = Mock(return_value=[{'name':'session','value':'fixture-user','domain':'.alibaba.com','path':'/'}])
        login = Mock(return_value='signed_in')
        session = Session({'asideAccount':'u0','loginId':'fixture-user'}, 'aside', login, reader)
        session.opener = Mock()
        session.opener.open.side_effect = [Response(200, csrf), Response(302, {}, {'Location':'https://login.alibaba.com/'}),
                                            Response(200, csrf), Response(200, {'status':1000})]
        with patch.dict(os.environ, {}, clear=True):
            result = session.request('POST', 'https://www2.alibaba.com/api/data/dynamic/component/data', params={'type':'normal'})
        self.assertEqual(result['status'], 200)
        self.assertEqual(reader.call_count, 2)
        login.assert_not_called()
        calls = session.opener.open.call_args_list
        self.assertIn('_csrf=fixture-secret', calls[1].args[0].data.decode())
        self.assertEqual(calls[1].args[0].get_header('X-csrf-token'), 'fixture-secret')
        self.assertEqual(session.metrics['collectionTabOpens'], 0)


if __name__ == '__main__':
    unittest.main()
