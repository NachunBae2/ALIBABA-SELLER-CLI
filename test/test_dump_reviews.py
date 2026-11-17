# test-only: synthetic reviews responses.
import json
import unittest
from test_http import FakeSession, args
from test_dump_pages import page
from http_collect import collect


class ReviewsDump(unittest.TestCase):
    def test_reviews_decode_stream_and_page_both_modules(self):
        def stream(rows, total, complete=True):
            events = [('ack', {'code': 200})]
            events += [('message', {'code': 200, 'mergeType': 'incremental', 'name': 'interplay.reviewmananger.review.list', 'payload': row}) for row in rows]
            events += [('message', {'code': 200, 'mergeType': 'slice', 'name': 'interplay.reviewmananger.review.pagination', 'payload': {'totalCount': total}})]
            if complete:
                events += [('complete', {})]
            return ''.join('event:' + kind + '\ndata:' + json.dumps(data) + '\n\n' for kind, data in events)
        from unittest.mock import Mock
        from http_collect import parse_review_stream
        pg = page('orders-reviews')
        pg['requests'] = pg['requests'][:1]
        client = FakeSession([])
        client.request = Mock(side_effect=[{'status': 200, 'text': stream([{'orderInfo': {'orderId': 'fixture-a'}}], 2)},
                                         {'status': 200, 'text': stream([{'orderInfo': {'orderId': 'fixture-b'}}], 2)}])
        result = collect(pg, args(), client, {})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['coverage'][0]['rowsGot'], 2)
        params = client.request.call_args.args[2]['protocol']['modules']
        self.assertEqual([module['param']['currentPage'] for module in params], [2, 2])
        self.assertEqual(parse_review_stream(stream([{'orderInfo': {'orderId': '-1'}}], 0))['data'], [])
        self.assertEqual(parse_review_stream(stream([], 0, complete=False))['captureError'], 'incomplete_review_stream')
        self.assertEqual(parse_review_stream('event:error\ndata:{"code":500,"message":"fixture denied"}\n\n')['message'], 'fixture denied')

