# test-only: synthetic keywords responses.
import json
import unittest
from test_http import FakeSession, args
from test_dump_pages import page
from http_collect import collect


class KeywordsDump(unittest.TestCase):
    def test_keyword_default_completes_beyond_three_pages(self):
        from seller_central_raw import parse_args
        a = parse_args(['--seeds', 'fixture', '--start', '2026-01-01', '--end', '2026-01-03'])
        client = FakeSession([{'code': 0, 'data': [{'query': 'fixture-' + str(n), '_TOTAL': 4}]} for n in range(4)])
        result = collect(page('keyword-index'), a, client, {})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['coverage'][0]['rowsGot'], 4)
        self.assertTrue(result['coverage'][0]['complete'])
        a.kw_pages = 1
        result = collect(page('keyword-index'), a, FakeSession([{'code': 0, 'data': [{'query': 'fixture', '_TOTAL': 4}]}]), {})
        self.assertEqual(result['status'], 'incomplete')
        self.assertFalse(result['coverage'][0]['complete'])
