# test-only: synthetic responses; no account data.
import json
from pathlib import Path
import unittest
from test_http import FakeSession, args
from http_collect import collect


def page(key):
    return next(p for p in json.loads((Path(__file__).resolve().parent.parent / 'engine/pages.json').read_text())['pages'] if p['key'] == key)


class DumpPages(unittest.TestCase):
    def test_rfq_equity_list_collects_every_page(self):
        pg = page('rfq-my-quotes')
        pg['requests'] = [r for r in pg['requests'] if 'getEquityListAjax' in r['url']]
        client = FakeSession([
            {'code': '200', 'data': {'quotesList': [{'id': 'fixture-a'}], 'total': 2}},
            {'code': '200', 'data': {'quotesList': [{'id': 'fixture-b'}], 'total': 2}},
        ])
        result = collect(pg, args(), client, {})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([call[3]['pageNumber'] for call in client.calls], [1, 2])
        self.assertEqual(result['coverage'][0]['rowsGot'], 2)
        self.assertTrue(result['coverage'][0]['complete'])

