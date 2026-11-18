# Test-only: all identifiers, names, amounts and tokens below are synthetic fixtures.
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'engine'))
from http_collect import collect, parse
import misc_data as md
import report
from test_http import FakeSession, args

CATALOG = json.loads((Path(__file__).resolve().parent.parent / 'engine/pages.json').read_text())

def page(kind):
    return copy.deepcopy(next(p for p in CATALOG['pages'] if p['key'] == 'misc-' + kind))


def fixtures():
    return {
        'inquiry-folders': {'code': 200, 'data': {k: [{'groupName': 'fixture group', 'description': 'fixture folder', 'count': 0,
                                                    'groupIdsEncrypt': ['private-fixture'], 'buyerName': 'private-fixture'}] for k in md.FOLDER_GROUPS}},
        'product-negative': {'code': 200, 'success': True, 'content': [{'governanceType': 9, 'count': 0}]},
        'product-power': {'count': 1, 'products': [{'id': 101, 'subject': 'fixture product', 'ownerMemberName': 'private-fixture',
                           'productPowerScoreDetails': {'prodPowerScore': '4.50', 'alreadyCalculated': False}}]},
        'product-risk-tasks': {'code': 200, 'data': {'101': 2}},
        'orders-list': {'code': 0, 'data': {'pagination': {'totalRecord': 1}, 'data': [
            {'baseInfo': {'id': 'fixture-order', 'createTime': '2026-01-02', 'merchandiser': 'private-fixture'},
             'buyer': {'fullName': 'private-fixture', 'email': 'private-fixture', 'aliTalkToken': 'fixture-secret'},
             'seller': {'fullName': 'private-fixture'},
             'paymentInfo': {'totalAmount': {'number': '12.34', 'numberStr': 'USD 12.34'}, 'currency': 'USD'}}]}},
        'knowledge-metrics': {'code': 200, 'success': True, 'data': {'buyerSearchCallCount': 0, 'fileCount': 2, 'smartReceptionCallCount': 0, 'totalCallCount': 0}},
    }


class MiscContracts(unittest.TestCase):
    def test_all_six_contracts_and_persistence_allowlist(self):
        for kind, value in fixtures().items():
            with self.subTest(kind=kind):
                responses = [value]
                if kind == 'product-risk-tasks':
                    responses.insert(0, {'count': 1, 'products': [{'id': 101, 'ownerMemberName': 'private-fixture'}]})
                result = collect(page(kind), args(), FakeSession(responses), {})
                self.assertEqual(result['status'], 'ok')
                self.assertTrue(all(c['complete'] for c in result['coverage']))
                self.assertNotIn('private-fixture', json.dumps(result))
                self.assertNotIn('fixture-secret', json.dumps(result))
                self.assertTrue(result['records'])

    def test_fields_are_raw_and_missing_stays_blank(self):
        row = md.normalize('product-power', fixtures()['product-power'])['rows'][0]
        self.assertEqual(row['productPowerScoreDetails.prodPowerScore'], '4.50')
        self.assertIs(row['productPowerScoreDetails.alreadyCalculated'], False)
        self.assertIsNone(row['productPowerScoreDetails.productQualityScore'])
        f = fixtures()['inquiry-folders']
        for group in f['data'].values():
            group[0].pop('count')
        self.assertTrue(all(r['count'] is None for r in md.normalize('inquiry-folders', f)['rows']))

    def test_root_array_and_empty_maps_are_valid_but_missing_envelopes_fail(self):
        for kind, value in [('rfq-searches', []), ('product-risk-tasks', {'data': {}})]:
            self.assertEqual(md.normalize(kind, value)['rows'], [])
        for kind in md.FIELDS:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                md.normalize(kind, {})

    def test_server_totals_and_repeated_pages(self):
        one = {'count': 2, 'products': [{'id': 101}]}
        two = {'count': 2, 'products': [{'id': 202}]}
        session = FakeSession([one, two])
        result = collect(page('product-power'), args(), session, {})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([c[3]['page'] for c in session.calls], [1, 2])
        for second, expected in [(one, 'duplicate_rows'), ({'count': 2, 'products': []}, 'incomplete'),
                                 ({'count': 2, 'products': [{'id': 101, 'status': 'changed'}]}, 'duplicate_rows'),
                                 ({'count': 3, 'products': [{'id': 202}]}, 'incomplete')]:
            result = collect(page('product-power'), args(), FakeSession([one, second]), {})
            self.assertEqual(result['status'], expected)
            self.assertFalse(result['coverage'][0]['complete'])

    def test_orders_nested_page_binding_and_missing_total(self):
        one = fixtures()['orders-list']; one['data']['pagination']['totalRecord'] = 2
        two = copy.deepcopy(one); two['data']['data'][0]['baseInfo']['id'] = 'fixture-order-two'
        session = FakeSession([one, two])
        result = collect(page('orders-list'), args(), session, {})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([c[2]['otherParams']['currentPage'] for c in session.calls], [1, 2])
        self.assertEqual(session.calls[0][2]['otherParams']['filter']['orderStatus'], 'orderStatus@all')
        one['data']['pagination'].clear()
        result = collect(page('orders-list'), args(), FakeSession([one]), {})
        self.assertEqual(result['status'], 'api_failed')
        self.assertIn('server_total_missing', result['detail'])

    def test_risk_dependencies_page_fully_and_empty_map_is_not_zero(self):
        a = args(); a.requests = 'product-risk-tasks'
        session = FakeSession([{'count': 2, 'products': [{'id': 101}]}, {'count': 2, 'products': [{'id': 202}]}, {'code': 200, 'data': {}}])
        result = collect(page('product-risk-tasks'), a, session, {})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(session.calls[-1][2]['productIds'], '101,202')
        self.assertEqual(result['coverage'][0]['rowsGot'], 2)
        self.assertEqual(parse(result['records'][-1]['text'])['rows'], [])
        self.assertEqual(result['records'][0]['replay']['kind'], 'dependency')
        failed = FakeSession([{'count': 2, 'products': [{'id': 101}]}, {'count': 2, 'products': [{'id': 101}]}])
        self.assertEqual(collect(page('product-risk-tasks'), a, failed, {})['status'], 'duplicate_rows')
        self.assertEqual(len(failed.calls), 2)

    def test_empty_product_source_does_not_query_risk_with_empty_ids(self):
        session = FakeSession([{'count': 0, 'products': []}])
        result = collect(page('product-risk-tasks'), args(), session, {})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(session.calls), 1)

    def test_application_errors_keep_original_code_message_and_fail(self):
        for code in [-1, 302, 500, 1000, 1003]:
            result = collect(page('knowledge-storage'), args(), FakeSession([{'code': code, 'message': 'fixture original error', 'data': {'secret': 'private-fixture'}}]), {})
            self.assertEqual(result['status'], 'api_failed')
            self.assertIn('fixture original error', result['detail'])
            self.assertEqual(parse(result['records'][0]['text'])['readError']['code'], code)
            self.assertNotIn('private-fixture', json.dumps(result))
        s = FakeSession([]); s.request = Mock(return_value={'status': 302, 'text': 'html', 'redirectHost': 'passport.alibaba.com'})
        result = collect(page('knowledge-storage'), args(), s, {})
        self.assertEqual(parse(result['records'][0]['text'])['readError']['httpStatus'], 302)
        self.assertFalse(result['coverage'][0]['complete'])

    def test_explicit_error_flag_and_missing_identifiers_fail_closed(self):
        for value in [{'error': 'fixture failure', 'data': {'fileCount': 2}},
                      {'code': 200, 'success': False, 'data': {'fileCount': 2}}]:
            result = collect(page('knowledge-metrics'), args(), FakeSession([value]), {})
            self.assertEqual(result['status'], 'api_failed')
        for value in [{'count': 1, 'products': [{'subject': 'fixture'}]},
                      {'readContract': 'product-power', 'total': 1, 'rows': [{}]}]:
            with self.assertRaisesRegex(ValueError, 'row_identifier_missing'):
                md.normalize('product-power', value)



class MiscReports(unittest.TestCase):
    def run_report(self, kind, records, meta=None, fmt='json'):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'raw').mkdir()
            (root/'raw'/('misc-'+kind+'.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in records))
            (root/'manifest.json').write_text(json.dumps({'pages': [{'key': 'misc-'+kind, **(meta or {})}]}))
            stdout, stderr = io.StringIO(), io.StringIO()
            command = next(c for c, k in md.COMMANDS.items() if k == kind)
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = report.main([command, '--from', folder, '--format', fmt])
            return code, stdout.getvalue(), stderr.getvalue()

    def test_each_report_resanitizes_old_dumps_and_formats(self):
        for kind, value in fixtures().items():
            for fmt in ['json', 'csv', 'table']:
                with self.subTest(kind=kind, fmt=fmt):
                    code, text, notes = self.run_report(kind, [{'json': value}], fmt=fmt)
                    self.assertEqual(code, 0)
                    self.assertNotIn('private-fixture', text)
                    self.assertNotIn('fixture-secret', text)
                    self.assertIn('스냅샷', notes)
                    if fmt == 'json': self.assertTrue(json.loads(text)[kind])

    def test_missing_reason_zero_notice_and_failed_dump_exit(self):
        code, text, _ = self.run_report('product-power', [{'json': fixtures()['product-power']}])
        self.assertIn('productPowerScoreDetails.productQualityScore', json.loads(text)['product-power'][0]['missing_reason'])
        code, text, notes = self.run_report('product-negative', [{'json': {'code': 200, 'success': True, 'content': []}}])
        self.assertEqual(code, 0); self.assertIn('0행', notes)
        self.assertNotEqual(self.run_report('orders-list', [])[0], 0)
        self.assertNotEqual(self.run_report('orders-list', [{'json': fixtures()['orders-list']}], {'status': 'incomplete'})[0], 0)
        code, text, _ = self.run_report('orders-list', [{'json': {'readError': {'code': -1, 'message': 'fixture original error'}}}])
        self.assertNotEqual(code, 0); self.assertIn('fixture original error', text)

    def test_reused_dump_does_not_hide_duplicates_or_short_total(self):
        value = fixtures()['orders-list']
        self.assertNotEqual(self.run_report('orders-list', [{'json': value}, {'json': value}])[0], 0)
        value['data']['pagination']['totalRecord'] = 3
        self.assertNotEqual(self.run_report('orders-list', [{'json': value}])[0], 0)

if __name__ == '__main__':
    unittest.main()
