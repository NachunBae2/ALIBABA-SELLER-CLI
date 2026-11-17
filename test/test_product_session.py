# Test-only: all tokens and response values below are synthetic fixtures.
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'engine'))
from api_session import ApiSession
from http_session import SessionError


class ProductSession(unittest.TestCase):
    def test_product_origin_and_nested_ctoken_on_post_url(self):
        session = ApiSession({}, 'fixture-config'); session.initialized = True
        session.cookie = lambda name, url: 'other=x&ctoken=fixture-csrf' if name == 'xman_us_t' else None
        session.send = Mock(return_value={'status': 200, 'text': '{}'})
        session.request('POST', 'https://us-productposting.alibaba.com/product/query.do', {}, {'page': 1}, referer='https://i.alibaba.com/products/list-manage')
        call = session.send.call_args.args
        self.assertIn('ctoken=fixture-csrf', call[1])
        self.assertEqual(call[2]['Origin'], 'https://i.alibaba.com')
        self.assertNotIn(b'fixture-csrf', call[3])
        session.request('GET', 'https://productcenter.alibaba.com/product/governance/negative/count')
        self.assertEqual(session.send.call_args.args[2]['Origin'], 'https://i.alibaba.com')

    def test_auth_redirect_diagnostic_survives_refresh_and_collection_failure(self):
        response = Mock()
        response.code = 302
        response.read.return_value = b'login html'
        response.headers = {'Location': 'https://passport.alibaba.com/login?token=fixture-secret'}
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        session = ApiSession({}, 'fixture-config', opener=Mock(open=Mock(return_value=response)))
        session.initialized = True
        session.cookie = lambda *args: None
        session.refresh = Mock(side_effect=SessionError('saved_cookie_unavailable'))
        with self.assertRaises(SessionError) as raised:
            session.request_many([('POST', 'https://us-productposting.alibaba.com/product/query.do', {}, {}, 'form', None)])
        self.assertEqual(str(raised.exception), 'saved_cookie_unavailable')
        diagnostic = raised.exception.diagnostic
        self.assertEqual(diagnostic['httpStatus'], 302)
        self.assertEqual(diagnostic['redirectHost'], 'passport.alibaba.com')
        self.assertNotIn('fixture-secret', json.dumps(diagnostic))
