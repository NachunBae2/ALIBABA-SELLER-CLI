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
from http_session import Session, SessionError, scrub
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
        self.assertEqual(s.calls[0][3]['data']['time']['endDateTime'], '2026-01-03 23:59:59')
        self.assertTrue(r['coverage'][0]['complete'])
        self.assertEqual(r['coverage'][0]['rowsGot'], 3)
        self.assertEqual(r['metrics']['collectionTabOpens'], 0)

    def test_empty_page_before_total_is_incomplete(self):
        r = collect(ads_page(), args(), FakeSession([result([{'v': 1}], 3), result([], 3)]), {})
        self.assertEqual(r['status'], 'incomplete')
        self.assertFalse(r['coverage'][0]['complete'])

    def test_account_queries_page_published_rows_without_date_padding(self):
        catalog = json.loads((Path(__file__).resolve().parent.parent/'engine/pages.json').read_text())
        account = next(t for p in catalog['pages'] for t in p.get('rangeTables', []) if t['key']=='ai.account_daily')
        s = FakeSession([result([{'day':'2026-01-01'}, {'day':'2026-01-02'}], 2)])
        r = collect({**ads_page(), 'rangeTables':[account]}, args(), s, {})
        self.assertEqual(s.calls[0][3]['data']['page'], {'index':1,'size':500})
        self.assertEqual(r['coverage'][0]['rowsGot'],2)
        self.assertTrue(r['coverage'][0]['complete'])

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

    def test_repl_batches_continue_without_tabs_or_cookie_transfer(self):
        session = Session({'asideAccount':'u0'}, 'aside', Mock())
        responses = [{'status':200,'text':'{}'}]
        with patch.dict(os.environ, {'ALIBABA_SELLER_ASIDE_WRAPPER':'fixture-lock'}), patch.object(session, '_batch', side_effect=[
            {'responses':responses,'httpRequests':2}, {'responses':responses,'httpRequests':2}]) as batch:
            self.assertEqual(len(session.request_many([('GET','https://seller.alibaba.com/query',{}, {},'form',None)]*2)),2)
        self.assertEqual(batch.call_count, 2)
        self.assertEqual(session.metrics['collectionTabOpens'],0)
        self.assertEqual(session.metrics['httpRequests'],4)
        session.login.assert_not_called()

    def test_login_only_on_expired_session_and_once(self):
        login = Mock(return_value='signed_in')
        session = Session({'asideAccount':'u0'}, 'aside', login)
        with patch.dict(os.environ, {'ALIBABA_SELLER_ASIDE_WRAPPER':'fixture-lock'}), patch.object(session, '_batch', side_effect=[
            {'responses':[], 'httpRequests':1, 'error':'session_expired_or_captcha'},
            {'responses':[{'status':200,'text':'{}'}], 'httpRequests':1}]):
            session.request('GET','https://seller.alibaba.com/query')
        login.assert_called_once()
        self.assertEqual(session.metrics['refreshTabOpens'],1)

    def test_requires_wrapper_and_trusted_host(self):
        s = Session({'asideAccount':'u0'}, 'aside', Mock())
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(SessionError,'wrapper_required'):
            s.request('GET','https://seller.alibaba.com/query')
        with patch.dict(os.environ, {'ALIBABA_SELLER_ASIDE_WRAPPER':'fixture-lock'}), self.assertRaisesRegex(SessionError,'untrusted_endpoint'):
            s.request('GET','https://example.com/query')

    def test_duplicate_pages_fail_even_when_row_count_matches(self):
        r = collect(ads_page(), args(), FakeSession([result([{'v':1}],2), result([{'v':1}],2)]), {})
        self.assertEqual(r['status'],'duplicate_rows')
        self.assertFalse(r['coverage'][0]['complete'])
        self.assertEqual(len(r['records']),1)

    def test_runtime_ids_are_discovered_and_typed(self):
        page = {'key':'fixture','url':'https://www2.alibaba.com/', 'requests':[{
            'tag':'counts', 'method':'POST', 'url':'https://www2.alibaba.com/api/campaign/keyword/count',
            'params':{'data':{'campaignIdList':'{{campaignIds}}'}},
            'binding':{'source':'campaigns','key':'campaignId','many':True}}]}
        session = FakeSession([{'data':{'51':[{'id':123},{'id':456}]}}, {'data':{}}])
        r = collect(page,args(),session,{})
        self.assertEqual(r['status'],'ok')
        self.assertEqual(session.calls[1][3]['data']['campaignIdList'],[123,456])

    def test_campaign_empty_data_envelope_ends_pagination(self):
        page = {'key':'fixture','url':'https://www2.alibaba.com/', 'requests':[{
            'tag':'campaigns', 'method':'POST', 'url':'https://www2.alibaba.com/api/campaign/type/allList',
            'params':{'data':{'page':1}}, 'pageParam':'data.page', 'rowsPath':'data.51', 'emptyRowsPath':'data'}]}
        s=FakeSession([{'data':{'51':[{'id':123}]}}, {'data':{}}])
        r=collect(page,args(),s,{})
        self.assertEqual(r['status'],'ok')
        self.assertEqual(r['coverage'][0]['rowsGot'],1)
        self.assertEqual(r['coverage'][0]['completionBasis'],'empty_terminal_page')

    def test_daily_product_jobs_do_not_share_pagination_or_resume(self):
        pg=ads_page();pg['rangeTables'][0]['requestDays']=1
        client=FakeSession([result([{'v':1}],1),result([{'v':3}],1)])
        out=collect(pg,args(),client,{'fixture_table[]|2026-01-02|2026-01-02':{'complete':True}})
        self.assertEqual(out['status'],'ok')
        self.assertEqual([r['replay']['start'] for r in out['records']],['2026-01-01','2026-01-03'])
        self.assertTrue(all(r['replay']['subwindow'] for r in out['records']))
        self.assertTrue(all(c['complete'] for c in out['coverage']))

    def test_transport_retry_keeps_completed_responses_without_repeating_them(self):
        s=Session({'asideAccount':'u0'},'aside',Mock())
        first={'status':200,'text':'{"row":1}'};last={'status':200,'text':'{"row":2}'}
        with patch.dict(os.environ,{'ALIBABA_SELLER_ASIDE_WRAPPER':'fixture-lock'}), patch.object(s,'_batch',side_effect=[
            {'responses':[first],'httpRequests':2,'error':'http_transport_failed'},
            {'responses':[last],'httpRequests':1,'error':None}]) as batch:
            out=s.request_many([('GET','https://seller.alibaba.com/query',{}, {},'form',None)]*2)
        self.assertEqual(out,[first,last])
        self.assertEqual(len(batch.call_args_list[1].args[0]),1)
        s.login.assert_not_called()

    def test_catalog_identity_fields_only_templates(self):
        catalog = json.loads((Path(__file__).resolve().parent.parent/'engine/pages.json').read_text())
        def check(value):
            if isinstance(value,dict):
                for k,v in value.items():
                    if k in ('campaignId','campaignIdList','productId','productIdList','memberId','loginId','accountId') and v:
                        self.assertTrue(isinstance(v,str) and v.startswith('{{'),k)
                    check(v)
            elif isinstance(value,list):
                for v in value: check(v)
        for page in catalog['pages']:
            for req in page.get('requests',[]):
                check(req.get('params',{})); check(req.get('query',{}))


if __name__ == '__main__':
    unittest.main()

class MainIntegration(unittest.TestCase):
    def test_history_clamps_retention_and_pages_actual_rows(self):
        from http_collect import history_jobs
        a=args(); a.start_d=dt.date(2026,6,1); a.end_d=dt.date(2026,11,30)
        cfg={'url':'https://www2.alibaba.com/api/log','intervalUrl':'https://www2.alibaba.com/api/log/interval','queries':[{'productLineId':1,'logMajor':'campaign'}]}
        out={'notes':[]}
        jobs=history_jobs(cfg,a,FakeSession([{'data':{'maxDate':'2026-11-16'}}]),out)
        self.assertEqual(jobs[0]['params']['data']['dateBegin'],'2026-09-16')
        self.assertEqual(jobs[0]['params']['data']['dateEnd'],'2026-11-16')
        session=FakeSession([{'data':{'maxDate':'2026-11-16'}},{'ok':True,'totalCount':2,'data':[{'id':1}]},{'ok':True,'totalCount':2,'data':[{'id':2}]}])
        result=collect({'key':'ads-history','url':'https://www2.alibaba.com/','operationLogs':cfg},a,session,{})
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['coverage'][0]['rowsGot'],2)
        self.assertEqual(result['records'][1]['replay']['kind'],'ads-history')
        a.end_d=dt.date(2026,8,1)
        self.assertEqual(history_jobs(cfg,a,FakeSession([{'data':{'maxDate':'2026-11-16'}}]),out),[])

    def test_campaign_targets_use_runtime_shop_ids_once(self):
        from http_collect import collect_campaign_targets
        pg={'campaignTargetReads':['/api/campaign/{campaignId}/adgroup/batch','/api/{campaignId}/select/product/query']}
        out={'records':[{'url':'https://www2.alibaba.com/api/campaign/type/allList','text':json.dumps({'data':{'shop':[{'id':9001,'productLineId':110106}]*2,'other':[{'id':8001,'productLineId':1}]}})}]}
        session=FakeSession([{'ok':True,'data':[]},{'ok':True,'data':[]}])
        collect_campaign_targets(pg,session,out)
        self.assertEqual(len(session.calls),2)
        self.assertEqual(session.calls[0][1],'https://www2.alibaba.com/api/campaign/9001/adgroup/batch')
        self.assertEqual(out['records'][1]['replay']['campaignId'],'9001')

    def test_profile_and_extra_allowlists(self):
        from http_collect import sanitize_buyer_profile, sanitize_inquiry_extra
        raw={'data':{'data':{'buyerInfo':{'growthLevelInfo':{'growthLevel':'L1'},'spamInquiryMarkedBySupplierCount':0,'addedToBlacklistCount':2,'firstName':'private-fixture','buyerShopBehaviorInfo':{'productVisitHistory':[{'productId':'100','productName':'Fixture','buyer':'private-fixture'}]}}}}}
        clean=sanitize_buyer_profile(raw)
        self.assertEqual(clean['growthLevel'],'L1'); self.assertEqual(clean['spamInquiryMarkedBySupplierCount'],0)
        self.assertNotIn('private-fixture',json.dumps(clean))
        self.assertEqual(sanitize_buyer_profile({})['available'],False)
        extra=sanitize_inquiry_extra(json.dumps({'data':{'list':{'11':{'sender':{'userNewLevel':'L2','name':'private-fixture'}}}}}),['userNewLevel'])
        self.assertEqual(extra['list'],[{'tradeId':'11','userNewLevel':'L2'}])

    def test_conversion_error_remains_error(self):
        for payload in ({'ok':False},{'data':{}},{'data':{'queryResult':{'detailList':[]}}}):
            self.assertEqual(sanitize_allowed(payload,['timeUs'])['captureError'],'invalid_query_result')
