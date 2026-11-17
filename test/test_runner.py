"""Synthetic offline fixtures for cookie-pipe HTTP and transport fallback."""
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'engine'))
from api_session import ApiSession
from http_session import TransportSession, SessionError

ACC = {'name':'fixture', 'loginId':'fixture-seller', 'asideAccount':'u0'}
COOKIES = [{'name':'cookie2','value':'fixture-seller','domain':'.alibaba.com','path':'/'},
           {'name':'ctoken','value':'synthetic-token','domain':'.alibaba.com','path':'/'}]

class Response(io.BytesIO):
    def __init__(self, text='{}', status=200, **headers):
        super().__init__(text.encode()); self.code=status; self.headers=headers

class Runner(unittest.TestCase):
    def session(self, responses):
        reader=Mock(return_value=COOKIES)
        opener=Mock(); opener.open.side_effect=responses
        return ApiSession(ACC, '/fixture/config', reader, opener), reader, opener

    def test_api_csrf_form_json_cookie_scope_and_no_browser(self):
        csrf=json.dumps(dict(headerName='X-Csrf', parameterName='_csrf', token='synthetic-csrf'))
        s, reader, opener=self.session([Response(csrf),Response('{"ok":true}'),Response('{}')])
        with patch('subprocess.run', side_effect=AssertionError('no browser')):
            s.request('POST','https://www2.alibaba.com/api/read', params={'data':{'x':1}})
            s.request('POST','https://www2.alibaba.com/api/read', params={'data':{'x':2}}, encoding='json')
        requests=[call.args[0] for call in opener.open.call_args_list]
        self.assertIn(b'data=%7B%22x%22%3A1%7D',requests[1].data)
        self.assertEqual(requests[1].get_header('X-csrf'),'synthetic-csrf')
        self.assertEqual(json.loads(requests[2].data)['data'],{'x':2})
        self.assertEqual(s.cookie('ctoken','https://www2.alibaba.com/'),'synthetic-token')
        self.assertIsNone(s.cookie('ctoken','https://example.test/'))
        reader.assert_called_once_with(False)
        self.assertEqual(s.metrics['asideCalls'],0)

    def test_user_not_login_refreshes_source_once_then_retries(self):
        s, reader, _=self.session([Response('User not login'),Response('{}')])
        s.request('GET','https://i.alibaba.com/query')
        self.assertEqual([c.args[0] for c in reader.call_args_list],[False,True])
        self.assertEqual(s.metrics['cookieRefreshes'],1)

    def test_expired_after_refresh_fails_without_login(self):
        s, reader, _=self.session([Response('User not login'),Response('User not login')])
        with self.assertRaisesRegex(SessionError,'session_expired'):
            s.request('GET','https://i.alibaba.com/query')
        self.assertEqual(reader.call_count,2)

    def test_redirect_login_is_not_followed(self):
        s, _, _=self.session([Response('',302,Location='https://login.alibaba.com/'),Response('',302,Location='https://login.alibaba.com/')])
        with self.assertRaisesRegex(SessionError,'session_expired'):
            s.request('GET','https://i.alibaba.com/query')
        from api_session import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,None,None,'https://login.alibaba.com/'))

    def test_wrong_account_never_falls_back(self):
        t=TransportSession(ACC,'aside',Mock(),'auto','/fixture')
        t.api.request_many=Mock(side_effect=SessionError('wrong_account'))
        t.repl.request_many=Mock()
        with self.assertRaisesRegex(SessionError,'wrong_account'):t.request('GET','https://i.alibaba.com/')
        t.repl.request_many.assert_not_called()

    def test_auto_preserves_partial_api_results_and_falls_back_to_repl(self):
        t=TransportSession(ACC,'aside',Mock(),'auto','/fixture')
        t.api.request_many=Mock(side_effect=SessionError('session_expired_or_captcha',[{'text':'first'}]))
        t.repl.request_many=Mock(return_value=[{'text':'second'}])
        requests=[('GET','https://i.alibaba.com/one'),('GET','https://i.alibaba.com/two')]
        self.assertEqual(t.request_many(requests),[{'text':'first'},{'text':'second'}])
        t.repl.request_many.assert_called_once_with(requests[1:])
        self.assertEqual(t.transport,'repl')

    def test_explicit_api_never_calls_aside_or_login(self):
        t=TransportSession(ACC,'aside',Mock(),'api','/fixture')
        t.api.request_many=Mock(side_effect=SessionError('saved_cookie_unavailable'))
        t.repl.request_many=Mock()
        with self.assertRaises(SessionError):t.request('GET','https://i.alibaba.com/')
        t.repl.request_many.assert_not_called()

    def test_pipe_credentials_never_enter_argv_or_files(self):
        s=ApiSession(ACC,'/fixture/config')
        with patch('subprocess.run',return_value=Mock(returncode=0,stdout=json.dumps(COOKIES).encode())) as run:
            self.assertEqual(s._pipe(False),COOKIES)
            s._pipe(True)
        argv=run.call_args.args[0]
        self.assertIn('--read-only',argv)
        self.assertNotIn('synthetic-token',str(run.call_args))
        import subprocess
        self.assertEqual(run.call_args.kwargs['stdout'],subprocess.PIPE)
        self.assertEqual(run.call_args.kwargs['stderr'],subprocess.DEVNULL)

    def test_customer_panel_html_get_only_and_allowlist(self):
        s=ApiSession(ACC,'/fixture')
        raw={'data':{'data':{'buyerInfo':{'country':'US','growthLevelInfo':{'growthLevel':'L2'},'spamInquiryMarkedBySupplierCount':0,'addedToBlacklistCount':1,'name':'private-fixture'}}}}
        s.request=Mock(side_effect=[{'text':json.dumps({'data':{'contact':{'accountIdEncrypt':'fixture-buyer'},'secTradeId':'fixture-sec'}})},
          {'text':'window.KHTAccessToken = "fixture-secret";'}, {'text':'/**/profileRead('+json.dumps(raw)+');'}])
        config={'summaryUrl':'https://message.alibaba.com/querySummary.htm','detailUrl':'https://message.alibaba.com/maDetail.htm','url':'https://alicrm.alibaba.com/queryCustomerInfo.json'}
        out=s.buyer_profiles([{'tradeId':'11','secTradeId':'fixture-sec'}],config)
        self.assertEqual(out[0]['growthLevel'],'L2')
        self.assertEqual(out[0]['spamInquiryMarkedBySupplierCount'],0)
        self.assertNotIn('private-fixture',json.dumps(out));self.assertNotIn('fixture-secret',json.dumps(out))
        self.assertEqual([c.args[0] for c in s.request.call_args_list],['POST','GET','GET'])

if __name__=='__main__':unittest.main()

class RefreshReadOnly(unittest.TestCase):
    def test_refresh_of_signin_source_never_reopens_login(self):
        import cookie_store
        rec={'source':{'browser':'chrome','profile':'Default','userDataDir':'/fixture/profile','signIn':True}}
        with patch.object(cookie_store,'load_record',return_value=rec), patch.object(cookie_store.bc,'read_profile',return_value=COOKIES) as read, patch.object(cookie_store,'sign_in',side_effect=AssertionError('no login')), patch.object(cookie_store,'save',side_effect=AssertionError('RAM only')):
            self.assertEqual(cookie_store.refresh_read_only(ACC),COOKIES)
        read.assert_called_once_with('chrome','Default','/fixture/profile')
