# test-only: synthetic values, no real seller/account/buyer/product data or network.
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
import report
from http_collect import collect
from store_metrics import day_jobs, response_failure
from http_session import SessionError

CATALOG = json.loads((Path(__file__).resolve().parents[1] / 'engine/pages.json').read_text())
PAGES = {p['key']: p for p in CATALOG['pages'] if p.get('storeContract')}


def args(start='2026-01-02', end='2026-01-03'):
    return SimpleNamespace(start_d=dt.date.fromisoformat(start), end_d=dt.date.fromisoformat(end),
                           tables='', extras='', max_pages=1, max_passes=1, kw_pages=1)


class Session:
    transport = 'api'

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.metrics = {'httpRequests': 0, 'asideCalls': 0}

    def request(self, *call):
        self.calls.append(call)
        self.metrics['httpRequests'] += 1
        value = next(self.responses)
        return value if 'text' in value else {'status': 200, 'text': json.dumps(value)}


BOOT = {'status': 200, 'text': "window.STATISTICS={date:'2026-01-03',day:'3',statisticsType:'day',selected:'0'};"}


class StoreCollection(unittest.TestCase):
    def test_day_selector_uses_server_date_and_clips_with_notice(self):
        page = PAGES['store-summary']
        session = Session([BOOT, {'code': 0, 'data': []}, {'code': 0, 'data': []}, {'code': 0, 'data': []}])
        result = collect(page, args('2025-12-31', '2026-01-05'), session, {})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([c[2]['selected'] for c in session.calls[1:]], [2, 1, 0])
        self.assertEqual([r['replay']['query_day'] for r in result['records']], ['2026-01-01', '2026-01-02', '2026-01-03'])
        self.assertIn('조회 가능 범위', result['notes'][0])
        self.assertEqual(result['metrics']['httpRequests'], 4)
        self.assertEqual(result['metrics']['asideCalls'], 0)

    def test_visitors_empty_is_success_not_missing_response(self):
        result = collect(PAGES['store-visitors'], args(), Session([BOOT, {'code': 0, 'data': []}, {'code': 0, 'data': []}]), {})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['records']), 2)

    def test_bootstrap_missing_or_outside_period_fails_without_guess(self):
        for boot in ({'status': 200, 'text': '<html></html>'}, BOOT):
            result = collect(PAGES['store-summary'], args('2025-01-01','2025-01-02'), Session([boot]), {})
            self.assertNotEqual(result['status'], 'ok')
            self.assertFalse(result['records'])

    def test_explicit_errors_preserve_code_message_and_http(self):
        for payload in ({'code': 1000, 'message': 'fixture invalid'}, {'code': 1003, 'message': 'fixture parameter'},
                        {'success': False, 'errorCode': 'FIXTURE_DENIED', 'errorMsg': 'fixture denied'},
                        {'success': True, 'errorCode': 302, 'values': {}}):
            result = collect(PAGES['store-trends'], args(), Session([payload]), {})
            self.assertEqual(result['status'], 'api_failed')
            detail = json.loads(result['detail'])
            self.assertEqual(detail['code'], payload.get('code', payload.get('errorCode')))
            self.assertEqual(json.loads(result['records'][0]['text']), payload)
        result = collect(PAGES['store-trends'], args(), Session([{'status': 302, 'text': ''}]), {})
        self.assertEqual(json.loads(result['detail'])['http_status'], 302)

    def test_summary_date_mismatch_and_malformed_payload_fail(self):
        result = collect(PAGES['store-summary'], args('2026-01-03', '2026-01-03'), Session([BOOT, {'code': 0, 'data': [{'statDate': {'value': '2026-01-02'}}]}]), {})
        self.assertEqual(result['status'], 'api_failed')
        self.assertIn('불일치', result['detail'])
        for name, value in [('trends', {}), ('rating', []), ('benefits', {}), ('thresholds', {'thresholds': [{}]})]:
            root = 'data' if name == 'trends' else 'values'
            result = collect(PAGES['store-'+name], args(), Session([{'code': 0, root: value}]), {})
            self.assertEqual(result['status'], 'api_failed')

    def test_all_contracts_are_get_without_mutation_and_snapshots_single_request(self):
        self.assertEqual(len(PAGES), 10)
        for key, page in PAGES.items():
            self.assertEqual(page['requests'][0]['method'], 'GET')
            self.assertNotRegex(page['requests'][0]['url'], r'(?i)save|update|delete|create|modify|submit|/set|edit|/bid|budget|apply')
            if page.get('storeDaySelector'):
                continue
            root = page['requests'][0]['rowsPath']
            value = [] if key in ('store-trends', 'store-today', 'store-indicators') else {'thresholds': []} if key == 'store-thresholds' else {'starBenefitCategoryConfigs': [], 'benefitMap': {}} if key == 'store-benefits' else {}
            payload = {'code': 0, root: value} if root == 'data' else {'success': True, root: value}
            session = Session([payload])
            result = collect(page, args(), session, {})
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(len(session.calls), 1)


class StoreReport(unittest.TestCase):
    def run_report(self, name, values, replay=None, status=200, **opts):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path/'raw').mkdir()
            records = [{'url': 'https://fixture.invalid/read?statisticsType=day&selected=0', 'status': status, 'json': value, 'replay': replay or {}} for value in values]
            (path/'raw'/('store-'+name+'.jsonl')).write_text('\n'.join(map(json.dumps, records)))
            output, errors = io.StringIO(), io.StringIO()
            argv = ['store-'+name, '--from', directory, '--format', opts.pop('format', 'json')]
            for key, value in opts.items():
                argv.extend(['--'+key, value])
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = report.main(argv)
            return code, output.getvalue(), errors.getvalue()

    def test_raw_types_null_zero_false_and_missing_reasons(self):
        payload = {'success': True, 'values': [{'value': 0, 'ratio': 0.0123, 'flag': False, 'optional': None}, {'value': '3.50', 'extra': ''}]}
        code, out, _ = self.run_report('indicators', [payload])
        rows = json.loads(out)['store_indicators']
        self.assertEqual(code, 0)
        self.assertEqual(rows[0]['value'], 0)
        self.assertIs(rows[0]['flag'], False)
        self.assertEqual(rows[0]['ratio'], 0.0123)
        self.assertEqual(rows[1]['value'], '3.50')
        self.assertIsNone(rows[1]['flag'])
        self.assertIn('응답 필드 없음', rows[1]['_blank_reasons'])
        self.assertIn('응답 null', rows[0]['_blank_reasons'])
        self.assertIn('응답 빈 문자열', rows[1]['_blank_reasons'])

    def test_summary_flattens_without_replacing_null_and_filters_query_day(self):
        payload = {'code': 0, 'data': [{'statDate': {'value': '2026-01-02'}, 'uv': {'value': None, 'cycleCrc': 0}}]}
        code, out, _ = self.run_report('summary', [payload, payload], {'query_day': '2026-01-02'}, start='2026-01-01', end='2026-01-03')
        rows = json.loads(out)['store_summary']
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['uv.value'])
        self.assertEqual(rows[0]['uv.cycleCrc'], 0)
        code, _, _ = self.run_report('summary', [payload], {'query_day': '2026-01-02'}, start='2026-02-01', end='2026-02-02')
        self.assertNotEqual(code, 0)

    def test_trends_default_keeps_returned_dates(self):
        code, out, _ = self.run_report('summary', [{'code': 0, 'data': [{'statDate': '2025-12-01', 'uv': 2}]}], start='2026-01-01', end='2026-01-03')
        self.assertEqual(code, 0)

    def test_empty_missing_and_errors_are_distinct(self):
        code, out, error = self.run_report('visitors', [{'code': 0, 'data': []}], {'query_day': '2026-01-02'})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['store_visitors'], [])
        self.assertIn('서버 정상 응답이 빈 배열', error)
        for values in ([], [{'code': 1003, 'message': 'fixture bad'}], [{'success': True}], [{'success': False, 'errorCode': 1000, 'errorMsg': 'fixture failure'}]):
            code, out, error = self.run_report('rating', values)
            self.assertEqual(code, 1)
            self.assertTrue(json.loads(out)['errors'])
            self.assertIn('API 오류', error)

    def test_thresholds_keep_parent_context_and_levels(self):
        code, out, _ = self.run_report('thresholds', [{'success': True, 'values': {'tenant': 'fixture', 'thresholds': [{'starCode': 'fixture_code', 'thresholdList': [{'star': 1, 'value': '5.00'}]}]}}])
        row = json.loads(out)['store_thresholds'][0]
        self.assertEqual((row['starCode'], row['value']), ('fixture_code', '5.00'))

    def test_other_object_and_list_reports_and_csv_table(self):
        for name, root, value in [('rating','values',{'star': 2}), ('health','values',{'value': 0}), ('today','result',[{'value': 0}])]:
            for fmt in ('json', 'csv', 'table'):
                code, out, _ = self.run_report(name, [{'success': True, root: value}], format=fmt)
                self.assertEqual(code, 0)
                self.assertIn('snapshot', out)


if __name__ == '__main__':
    unittest.main()
