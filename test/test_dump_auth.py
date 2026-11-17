# test-only: synthetic authentication fixtures.
import json
import unittest


class DumpAuth(unittest.TestCase):
    def test_product_center_does_not_replay_legacy_cookie_token(self):
        from api_session import ApiSession
        from test_runner import ACC, COOKIES, Response
        from unittest.mock import Mock
        opener = Mock()
        opener.open.return_value = Response('{"code":200,"content":[],"success":true}')
        cookies = COOKIES + [{'name': '_tb_token_', 'value': 'fixture-stale', 'domain': '.alibaba.com', 'path': '/'}]
        session = ApiSession(ACC, '/fixture', lambda _: cookies, opener)
        session.request('GET', 'https://productcenter.alibaba.com/product/governance/positivity/count')
        self.assertNotIn('token', opener.open.call_args.args[0].full_url)
        self.assertEqual(opener.open.call_args.args[0].get_header('Origin'), 'https://i.alibaba.com')
        self.assertEqual(session.metrics['httpRequests'], 1)

    def test_certificate_bootstrap_uses_its_own_token_once(self):
        from api_session import ApiSession
        from test_runner import ACC, COOKIES, Response
        from unittest.mock import Mock
        opener = Mock()
        opener.open.side_effect = [Response(json.dumps({'hasError': False, 'content': {
            'parameterName': '_csrf', 'token': 'fixture-auth-token'}})), Response('{}'), Response('{}')]
        session = ApiSession(ACC, '/fixture', lambda _: COOKIES, opener)
        session.request('GET', 'https://crmweb.alibaba.com/authcenter/cert/newCertificates.json')
        session.request('POST', 'https://crmweb.alibaba.com/authcenter/cert/filter.json', params={'conditions': {}})
        calls = [call.args[0] for call in opener.open.call_args_list]
        self.assertTrue(calls[0].full_url.endswith('/generateToken.json'))
        self.assertIn('_csrf_token=fixture-auth-token', calls[1].full_url)
        self.assertIn(b'_csrf_token=fixture-auth-token', calls[2].data)
        self.assertNotIn('ctoken=', calls[1].full_url)
