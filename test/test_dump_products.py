# test-only: synthetic products responses.
import json
import unittest
from test_http import FakeSession, args
from test_dump_pages import page
from http_collect import collect


class ProductsDump(unittest.TestCase):
    def test_product_list_total_and_single_tab_count_response(self):
        pg = page('products')
        pg['requests'] = [r for r in pg['requests'] if any(x in r['url'] for x in ('asyQueryProductsList.do', 'asyCountProductsForAllTabs'))]
        client = FakeSession([{'result': True, 'count': 1, 'products': [{'id': 'fixture-product'}]},
                              [{'status': 'all', 'count': 1}, {'status': 'pending', 'count': 0}]])
        result = collect(pg, args(), client, {})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([c['rowsGot'] for c in result['coverage']], [1, 2])
        self.assertEqual(len(client.calls), 2)
