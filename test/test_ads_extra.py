# test-only: all identifiers and amounts below are synthetic, never seller data.
import copy
import datetime as dt
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
import ads_extra
import report
from http_collect import collect, render


PAGES = [p for p in ads_extra.catalog()['pages'] if p.get('adsExtra')]


def args(**kw):
    return SimpleNamespace(start_d=dt.date(2026, 1, 1), end_d=dt.date(2026, 1, 7),
        tables='', extras='', seeds=[], kw_pages=3, kw_nd='7d', granularity='week',
        max_pages=kw.get('max_pages', 3), max_passes=1)


def ok(data, **kw):
    return {'status': 1000, 'ok': True, 'data': data, **kw}


class Client:
    transport = 'api'

    def __init__(self, result=None):
        self.calls = []
        self.result = result
        self.metrics = dict(httpRequests=0, asideCalls=0, collectionTabOpens=0)

    def request(self, method, url, query=None, params=None, *rest):
        self.calls.append((method, url, copy.deepcopy(params)))
        self.metrics['httpRequests'] += 1
        data = (params or {}).get('data', {})
        if url.endswith('/campaign/type/allList'):
            typ = data['typeList'][0]
            row = {'id': 'fixture-campaign-' + str(typ), 'type': typ,
                   'productLineId': params['ads']['productLineId'], 'subType': 'ggs_ai_whole_site',
                   'properties': {'marketingPackageType': 'ggs_ai_whole_site_l3plus_90d_35ab'}}
            j = ok({str(typ): [row]}, totalCount=1)
        elif url.endswith('/recommend/product'):
            j = ok([{'id': 'fixture-product', 'cateLv2Id': 'fixture-category'}])
        else:
            j = self.result if self.result is not None else ok([])
        return {'status': 200, 'text': json.dumps(j)}


class AdsExtraTests(unittest.TestCase):
    def test_every_contract_is_read_only_and_binds_ids_from_lists(self):
        self.assertEqual(len(PAGES), 13)
        for pg in PAGES:
            with self.subTest(page=pg['key']):
                client = Client()
                result = collect(pg, args(), client, {})
                self.assertEqual(result['status'], 'ok', result['notes'])
                self.assertEqual(result['metrics']['asideCalls'], 0)
                self.assertEqual(result['metrics']['collectionTabOpens'], 0)
                for method, url, params in client.calls:
                    self.assertEqual(method, 'POST')
                    self.assertNotRegex(url.lower(), r'save|update|delete|create|modify|submit|set|edit|bid|budget|apply')
                    self.assertNotIn('{{', json.dumps(params))
                    if url.endswith('/industry/compare') and '/campaign/' in url:
                        self.assertEqual(params['data']['beginDate'], '20260101')
                        self.assertTrue(params['data']['campaignId'].startswith('fixture-campaign-'))
                    if url.endswith('/productReport/industry/compare'):
                        self.assertEqual(params['data']['queryEffect']['endDate'], '20260107')
                        self.assertEqual(params['ads']['productLineId'], params['data']['productLineId'])
                    if url.endswith('/guaranteedperformance/data'):
                        self.assertEqual(params['data']['businessType'], 'ggs_l3_plus_guaranteed')
                    if url.endswith('/premium/industry/aggregation/data'):
                        self.assertEqual(params['data']['cateLv2Id'], 'fixture-category')

    def test_application_errors_are_persisted_verbatim_not_empty_success(self):
        pg = next(p for p in PAGES if p['key'] == 'ads-competition-keywords')
        for value in [{'code': 1000, 'message': 'fixture code error'},
                      {'code': 1003, 'message': 'fixture permission'},
                      {'status': 1001, 'ok': False, 'error': 'fixture internal error'},
                      {'status': 3001, 'errorVO': {'code': 3001, 'mcmsValue': 'fixture missing field'}},
                      {'code': 302, 'message': 'fixture redirect'}]:
            with self.subTest(value=value):
                result = collect(pg, args(), Client(value), {})
                self.assertEqual(result['status'], 'api_failed')
                self.assertEqual(json.loads(result['records'][0]['text']), value)
                self.assertIn(str(value.get('code', value.get('status'))), result['notes'][0])
                self.assertFalse(result['coverage'][0]['complete'])
        self.assertIsNone(ads_extra.failure({'status': 200}, ok(None)))
        self.assertIsNone(ads_extra.failure({'status': 200}, {'code': 200, 'data': []}))
        self.assertIsNone(ads_extra.failure({'status': 200}, {'status': 1000, 'code': 200, 'success': True, 'data': []}))
        self.assertIsNotNone(ads_extra.failure({'status': 200}, {'status': 1000, 'data': []}))
        self.assertIn('302', ads_extra.failure({'status': 302}, None))

    def test_binding_paginates_and_fails_closed_on_incomplete_or_error(self):
        pg = copy.deepcopy(next(p for p in PAGES if p['key'] == 'ads-industry-compare'))
        pg['requests'] = pg['requests'][:1]
        class Paged(Client):
            def request(self, method, url, query=None, params=None, *rest):
                r = super().request(method, url, query, params, *rest)
                if url.endswith('/allList'):
                    j = json.loads(r['text']);n = params['data']['page']
                    j['data']['1'][0]['id'] += '-page-' + str(n)
                    j['totalCount'] = 2
                    r['text'] = json.dumps(j)
                return r
        client = Paged()
        result = collect(pg, args(), client, {})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['records']), 2)
        self.assertEqual([p['data']['page'] for _,u,p in client.calls if u.endswith('/allList')], [1,2])
        result = collect(pg, args(max_pages=1), Paged(), {})
        self.assertNotEqual(result['status'], 'ok')
        self.assertEqual(result['records'], [])
        class Broken(Client):
            def request(self, *a):
                return {'status': 200, 'text': json.dumps({'code': 1003, 'message': 'fixture binding denied'})}
        result = collect(pg, args(), Broken(), {})
        self.assertIn('1003', result['status'])
        self.assertIn('fixture binding denied', result['status'])

    def test_empty_binding_does_not_send_placeholder_requests(self):
        pg = next(p for p in PAGES if p['key'] == 'ads-industry-compare')
        class Empty(Client):
            def request(self, *a):
                self.calls.append(a)
                return {'status': 200, 'text': json.dumps(ok({}, totalCount=0))}
        client = Empty();result = collect(pg, args(), client, {})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['records'], [])
        self.assertTrue(all('0행' in n for n in result['notes']))
        self.assertTrue(all(a[1].endswith('/allList') for a in client.calls))

    def test_missing_binding_fields_and_duplicate_pages_fail(self):
        pg = copy.deepcopy(next(p for p in PAGES if p['key'] == 'ads-guaranteed-performance'))
        class Missing(Client):
            def request(self, *a):
                r = super().request(*a);j = json.loads(r['text'])
                if a[1].endswith('/allList'):
                    del j['data']['51'][0]['productLineId']
                    r['text'] = json.dumps(j)
                return r
        result = collect(pg, args(), Missing(), {})
        self.assertIn('필수 필드 없음', result['status'])
        class Duplicate(Client):
            def request(self, *a):
                r = super().request(*a);j = json.loads(r['text']);j['totalCount'] = 2
                r['text'] = json.dumps(j);return r
        result = collect(pg, args(), Duplicate(), {})
        self.assertIn('중복', result['status'])

    def dump(self, root, key, value, status='ok'):
        (root/'raw').mkdir(exist_ok=True)
        (root/'manifest.json').write_text(json.dumps({'start':'2026-01-01','end':'2026-01-07','pages':[{'key':key,'status':status}]}))
        record = {'status':200,'json':value,'replay':{'tag':'fixture','start':'2026-01-01','end':'2026-01-07'}}
        (root/'raw'/(key+'.jsonl')).write_text(json.dumps(record)+'\n')

    def test_raw_report_keeps_zero_false_nested_values_and_missing_reasons(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);key='ads-recommended-products'
            self.dump(root,key,ok([{'id':'fixture-a','cost':'12.345','ctr':0,'flags':[False],'nullable':None},
                                   {'id':'fixture-b','cost':'0','flag':False}]))
            a=SimpleNamespace(command=key)
            result=ads_extra.report(report.Dump([d]),a)
            self.assertNotIn('_exit',result)
            first,second=result['data']
            self.assertEqual(first['cost'],'12.345')
            self.assertEqual(first['ctr'],0)
            self.assertEqual(first['flags'],[False])
            self.assertEqual(first['_missing']['nullable'],'응답 null')
            self.assertEqual(first['_missing']['flag'],'응답에 키 없음')
            self.assertEqual(second['flag'],False)
            for fmt in ['json','csv','table']:
                stdout=io.StringIO()
                with redirect_stdout(stdout),redirect_stderr(io.StringIO()):
                    code=report.main([key,'--from',d,'--format',fmt])
                self.assertEqual(code,0)
                self.assertIn('12.345',stdout.getvalue())
                self.assertNotIn('_numerator',stdout.getvalue())

    def test_reused_errors_missing_dump_and_successful_zero_rows_are_distinct(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);key='ads-competition-keywords'
            (root/'raw').mkdir()
            self.assertEqual(ads_extra.report(report.Dump([d]),SimpleNamespace(command=key))['_exit'],5)
            self.dump(root,key,{'code':1003,'message':'fixture error'},'api_failed')
            stdout=io.StringIO()
            with redirect_stdout(stdout),redirect_stderr(io.StringIO()):
                code=report.main([key,'--from',d,'--format','json'])
            self.assertEqual(code,5)
            self.assertIn('1003',stdout.getvalue())
            self.assertIn('fixture error',stdout.getvalue())
            self.dump(root,key,ok(None))
            err=io.StringIO()
            with redirect_stdout(io.StringIO()),redirect_stderr(err):
                code=report.main([key,'--from',d,'--format','json'])
            self.assertEqual(code,0)
            self.assertIn('0행',err.getvalue())

    def test_map_rows_preserve_keys_and_no_fake_aggregate(self):
        with tempfile.TemporaryDirectory() as d:
            key='ads-industry-compare'
            self.dump(Path(d),key,ok({'fixture-campaign':{'cost':1.25,'impr':0}}))
            result=ads_extra.report(report.Dump([d]),SimpleNamespace(command=key))
            self.assertEqual(result['data'][0]['_key'],'fixture-campaign')
            self.assertEqual(result['data'][0]['cost'],1.25)
            self.assertEqual(len(result['data']),1)

    def test_manual_campaign_without_subtype_uses_no_subtype_filter(self):
        class Manual(Client):
            def request(self, *a):
                response = super().request(*a)
                if a[1].endswith('/allList'):
                    value = json.loads(response['text'])
                    for rows in value['data'].values():
                        for row in rows:
                            row['subType'] = None
                    response['text'] = json.dumps(value)
                return response
        for key in ('ads-industry-compare', 'ads-campaign-countries', 'ads-product-benchmark'):
            pg = next(p for p in PAGES if p['key'] == key)
            client = Manual()
            result = collect(pg, args(), client, {})
            self.assertEqual(result['status'], 'ok')
            if key == 'ads-product-benchmark':
                for _, url, params in client.calls:
                    if url.endswith('/productReport/industry/compare'):
                        self.assertEqual(params['data']['inCampaignSubTypeList'], [])

    def test_date_formats(self):
        self.assertEqual(render(['{{start}}','{{endCompact}}'],'2026-01-01','2026-01-07'),['2026-01-01','20260107'])

class CookieJarRegression(unittest.TestCase):
    def test_exhausted_csrf_redirect_reports_actual_http_code(self):
        from unittest.mock import Mock
        from api_session import ApiSession
        from http_session import SessionError
        cookies = [{'name':'cookie2','value':'fixture-seller','domain':'.alibaba.com','path':'/'}]
        client = ApiSession({'name':'fixture','loginId':'fixture-seller'}, '/fixture', lambda _: cookies)
        bootstrap = {'status':200,'text':json.dumps({'headerName':'X-Csrf','parameterName':'_csrf','token':'fixture-secret'})}
        redirect = {'status':302,'text':'','redirectHost':'err.taobao.com'}
        client.send = Mock(side_effect=[bootstrap, redirect] * 4)
        with self.assertRaisesRegex(SessionError, 'HTTP 302 err.taobao.com'):
            client.request('POST','https://www2.alibaba.com/api/campaign/advice',params={'data':{}})
        self.assertEqual(client.send.call_count, 8)
        self.assertEqual(client.metrics['asideCalls'], 0)

    def test_bootstrap_set_cookie_is_sent_on_both_read_requests(self):
        import urllib.request
        import urllib.response
        from email.message import Message
        from api_session import ApiSession
        observed = []
        class FixtureHTTPS(urllib.request.HTTPSHandler):
            def https_open(self, req):
                observed.append(req)
                headers = Message()
                if '/api/csrf' in req.full_url:
                    headers['Set-Cookie'] = 'fixture_session=fixture-value; Path=/; Secure'
                    value = {'headerName':'X-Fixture-Csrf','parameterName':'fixture_csrf','token':'fixture-secret'}
                else:
                    value = ok([])
                response = urllib.response.addinfourl(io.BytesIO(json.dumps(value).encode()), headers, req.full_url, 200)
                response.msg = 'OK'
                return response
        cookies=[{'name':'cookie2','value':'fixture-seller','domain':'.alibaba.com','path':'/'}]
        client=ApiSession({'name':'fixture','loginId':'fixture-seller'},'/fixture/config',lambda _:cookies)
        client.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(client.jar),FixtureHTTPS())
        for _ in range(2):
            client.request('POST','https://www2.alibaba.com/api/campaign/advice',params={'data':{}})
        self.assertEqual(len(observed),3)
        for request in observed[1:]:
            self.assertIn('fixture_session=fixture-value',request.get_header('Cookie'))
            self.assertEqual(request.get_header('X-fixture-csrf'),'fixture-secret')
        self.assertEqual(client.metrics['asideCalls'],0)


class ReportWindowTests(unittest.TestCase):
    dump = AdsExtraTests.dump
    def test_partial_aggregate_window_fails_and_disjoint_window_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            key = 'ads-industry-compare'
            self.dump(Path(d), key, ok({'fixture-campaign': {'cost': 1.25}}))
            partial = ads_extra.report(report.Dump([d], '2026-01-03', '2026-01-05'), SimpleNamespace(command=key))
            self.assertEqual(partial['_exit'], 5)
            self.assertEqual(partial['data'], [])
            outside = ads_extra.report(report.Dump([d], '2026-02-01', '2026-02-07'), SimpleNamespace(command=key))
            self.assertEqual(outside['data'], [])


if __name__ == '__main__':
    unittest.main()
