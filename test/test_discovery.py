# test-only — all identifiers, metrics and response values below are fictional fixtures.
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'engine'))
from discovery import business_error, validate_options, raw_tables, with_missing_reasons
from http_collect import collect
from report import Dump, COMMANDS
from test_http import FakeSession, args

CATALOG = json.loads((Path(__file__).resolve().parent.parent / 'engine/pages.json').read_text())
PAGES = {p['key']: p for p in CATALOG['pages'] if p.get('discovery')}


def capture(key, responses, options=None, **kwargs):
    a = args(**kwargs)
    a.discovery_options = json.dumps(options or {})
    s = FakeSession(responses)
    result = collect(copy.deepcopy(PAGES[key]), a, s, {})
    return result, s


class Discovery(unittest.TestCase):
    def test_all_commands_have_read_only_strict_contracts(self):
        self.assertEqual(len(PAGES), 16)
        for page in PAGES.values():
            self.assertIn(page['report']['command'], COMMANDS)
            for request in page['requests']:
                self.assertEqual(request['method'], 'GET')
                self.assertTrue(request['strictBusiness'])
                self.assertNotRegex(request['url']+request['query'].get('iName', ''), r'(?i)save|update|delete|create|modify|submit|set|edit|bid|budget|apply')

    def test_each_contract_collects_and_reports_without_converting_raw(self):
        for key, page in PAGES.items():
            with self.subTest(key=key):
                options = {'keyword': 'test widget'} if page['discovery']['keyword'] else {}
                responses = []
                if page['discovery'].get('category'):
                    responses.append({'code': 0, 'data': [{'idDisplay': '90001 &gt; 90002 > 90003'}]})
                payload = [{'fixtureIndex': '1.25', 'fixtureZero': 0, 'fixtureFalse': False}]
                path = page['report']['path']
                response = {'returnCode': 0, 'successed': True, 'returnValue': payload} if path == 'returnValue' else {'code': 0, 'data': {'data': payload, 'recordCount': 1} if path == 'data.data' else payload}
                responses.append(response)
                if key == 'keyword-breakout': responses.append({'code': 0, 'data': []})
                result, session = capture(key, responses, options)
                self.assertEqual(result['status'], 'ok')
                with tempfile.TemporaryDirectory() as folder:
                    raw = Path(folder)/'raw';raw.mkdir()
                    records = [{**r, 'json': json.loads(r['text'])} for r in result['records']]
                    (raw/(key+'.jsonl')).write_text('\n'.join(json.dumps(r) for r in records))
                    tables = COMMANDS[page['report']['command']](Dump([folder]), SimpleNamespace())
                    row = tables[key.replace('-', '_')][0]
                    self.assertEqual(row['fixtureIndex'], '1.25')
                    self.assertEqual(row['fixtureZero'], 0)
                    self.assertIs(row['fixtureFalse'], False)
                    self.assertNotIn('_exit', tables)

    def test_node_binding_uses_last_keyword_node_and_first_market_node(self):
        for key,field,expected in [('keyword-category-top','cateLv3Id','90003'),('market-summary','cateId','90001')]:
            r,s = capture(key, [{'code':0,'data':[{'idDisplay':'90001 &gt; 90002 > 90003'}]}, {'code':0,'data':[]}])
            self.assertEqual(r['status'],'ok')
            self.assertEqual(s.calls[1][2][field],expected)
            self.assertEqual(r['records'][0]['replay']['kind'],'binding')

    def test_explicit_category_skips_binding_and_days_reach_nd(self):
        r,s=capture('keyword-breakout',[{'code':0,'data':[]}],{'categoryId':'90003','days':180})
        self.assertEqual(len(s.calls),1)
        self.assertEqual(s.calls[0][2]['nd'],'180d')
        self.assertEqual(s.calls[0][2]['cateLv3Id'],'90003')

    def test_visitor_pagination_uses_record_count(self):
        r,s=capture('visitor-keywords',[{'code':0,'data':{'data':[{'word':'a'}],'recordCount':2}}, {'code':0,'data':{'data':[{'word':'b'}],'recordCount':2}}],{'days':90})
        self.assertEqual(r['status'],'ok')
        self.assertEqual([x[2]['pageNO'] for x in s.calls],[1,2])
        self.assertEqual(s.calls[0][2]['nd'],'90d')
        self.assertTrue(r['coverage'][0]['complete'])

    def test_breakout_requires_empty_terminal_page_and_honors_cap(self):
        r,s=capture('keyword-breakout',[{'code':0,'data':[{'word':'a'}]}],{'categoryId':'90003'},max_pages=1)
        self.assertEqual(r['status'],'incomplete')
        self.assertFalse(r['coverage'][0]['complete'])
        self.assertEqual(len(s.calls),1)

    def test_errors_preserve_code_message_and_never_succeed(self):
        for code in [1000,1003,302,'1000']:
            r,_=capture('visitor-products',[{'code':code,'message':'fixture business failure'}])
            self.assertEqual(r['status'],'api_failed')
            self.assertIn('code='+str(code),r['notes'][0])
            self.assertIn('fixture business failure',r['notes'][0])
            self.assertEqual(json.loads(r['records'][0]['text'])['code'],code)
        self.assertIsNotNone(business_error({'returnCode':1003,'returnMessage':'fixture','successed':True}))
        self.assertIsNotNone(business_error({'code':0,'data':{'isExceed':True}}))
        self.assertIsNotNone(business_error({}))

    def test_binding_error_stops_before_target_request(self):
        r,s=capture('market-trend',[{'code':1000,'message':'fixture binding failure'}])
        self.assertEqual(len(s.calls),1)
        self.assertIn('code=1000 message=fixture binding failure',r['status'])
        self.assertEqual(len(r['records']),1)
        r,s=capture('market-trend',[{'code':0,'data':['invalid fixture node']}])
        self.assertEqual(len(s.calls),1)
        self.assertIn('category list empty/missing',r['status'])

    def test_http_redirect_is_failure_even_with_success_json(self):
        class Redirect(FakeSession):
            def request(self,*a):return {'status':302,'text':'{"code":0,"data":[]}'}
        r=collect(PAGES['visitor-groups'],args(),Redirect([]),{})
        self.assertEqual(r['status'],'api_failed')
        self.assertIn('HTTP 302',r['notes'][0])

    def test_options_fail_before_http(self):
        cases=[('keyword-index-detail',{}),('keyword-breakout',{'days':7}),('visitor-products',{'days':180}),('market-summary',{'days':30}),('visitor-groups',{'keyword':'x'}),('market-trend',{'categoryId':'90001 > 90002'}),('market-trend',{'endpoint':'fake'})]
        for key,options in cases:
            with self.subTest(key=key),self.assertRaises(ValueError):validate_options(PAGES[key]['discovery'],options)

    def test_modules_nested_values_and_missing_reasons(self):
        self.assertEqual(raw_tables({},'fixture',True),{'fixture':[]})
        v={'preSearchWords':[{'pv':0,'nested':[{'index':'1.25'}]},{'query':'test-only'}],'keywordTrend':[],'isExceed':False}
        tables=raw_tables(v,'fixture',True)
        self.assertEqual(tables['fixture_preSearchWords'][0]['nested'],v['preSearchWords'][0]['nested'])
        rows=with_missing_reasons(tables['fixture_preSearchWords'])
        self.assertEqual(rows[1]['pv'],'')
        self.assertIn('pv: response field missing/null',rows[1]['_missing_reason'])
        self.assertEqual(tables['fixture_metadata'],[{'isExceed':False}])

    def test_replay_empty_missing_and_error_dumps(self):
        key='visitor-products';page=PAGES[key]
        with tempfile.TemporaryDirectory() as folder:
            raw=Path(folder)/'raw';raw.mkdir()
            self.assertEqual(COMMANDS[key](Dump([folder]),None)['_exit'],5)
            (raw/(key+'.jsonl')).write_text(json.dumps({'status':200,'json':{'code':0,'data':{'data':[],'recordCount':0}},'replay':{'kind':'page'}}))
            output=io.StringIO()
            with redirect_stderr(output):tables=COMMANDS[key](Dump([folder]),None)
            self.assertEqual(tables['visitor_products'],[])
            self.assertIn('0행',output.getvalue())
            self.assertNotIn('_exit',tables)
            (Path(folder)/'manifest.json').write_text(json.dumps({'pages':[{'key':key,'status':'incomplete','notes':['fixture cap']}]}))
            with redirect_stderr(io.StringIO()):tables=COMMANDS[key](Dump([folder]),None)
            self.assertEqual(tables['_exit'],5)
            (raw/(key+'.jsonl')).write_text(json.dumps({'status':200,'json':{'code':1000,'message':'fixture failure'},'replay':{'kind':'error'}}))
            tables=COMMANDS[key](Dump([folder]),None)
            self.assertEqual(tables['_exit'],5)
            self.assertTrue(any('code=1000' in x['message'] for x in tables['errors']))


if __name__=='__main__':unittest.main()
