# test-only: synthetic credit responses.
import json
import unittest
from test_http import FakeSession, args
from test_dump_pages import page
from http_collect import collect


class CreditDump(unittest.TestCase):
    def test_credit_missing_data_is_blank_with_original_status(self):
        pg = page('orders-credit')
        pg['requests'] = [r for r in pg['requests'] if 'getAmountUsedList' in r['url']]
        result = collect(pg, args(), FakeSession([{'code': 200, 'message': 'Request success!'}]), {})
        self.assertEqual(result['status'], 'schema_mismatch')
        self.assertIsNone(result['coverage'][0]['rowsGot'])
        self.assertIn('data.data', result['coverage'][0]['reason'])
        self.assertIn('Request success!', result['coverage'][0]['reason'])
        result = collect(pg, args(), FakeSession([{'code': 200, 'data': {'data': [], 'totalSize': 0}}]), {})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['coverage'][0]['rowsGot'], 0)
