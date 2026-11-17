# test-only: fabricated API errors and temporary dump manifests.
import json
import tempfile
import unittest
from pathlib import Path
from test_http import FakeSession, args, ads_page
from http_collect import collect
from report import Dump, collection_coverage


class DumpDiagnostics(unittest.TestCase):
    def test_business_errors_preserve_code_message_and_blank_count(self):
        for payload in [{'code': 1000, 'message': 'fixture denied'},
                        {'code': '6003', 'success': False, 'msg': 'fixture denied'},
                        {'hasError': True, 'message': 'fixture denied'},
                        {'result': False, 'message': 'fixture denied'}]:
            result = collect(ads_page(), args(), FakeSession([payload]), {})
            self.assertEqual(result['status'], 'api_failed')
            self.assertIn('fixture denied', result['notes'][0])
            self.assertIsNone(result['coverage'][0]['rowsGot'])
            self.assertFalse(result['coverage'][0]['complete'])

    def test_failed_paging_does_not_claim_empty_terminal_page(self):
        client = FakeSession([])
        client.request = lambda *a: {'status': 302, 'text': '', 'redirectHost': 'err.taobao.com'}
        result = collect(ads_page(), args(), client, {})
        self.assertIn('HTTP 302', result['notes'][0])
        self.assertIn('err.taobao.com', result['notes'][0])
        self.assertFalse(any('빈 마지막' in n for n in result['notes']))

    def test_coverage_distinguishes_missing_and_explicit_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'manifest.json').write_text(json.dumps({'pages': [{
                'key': 'fixture', 'status': 'schema_mismatch', 'coverage': [
                    {'table': 'missing', 'rowsGot': None, 'complete': False, 'reason': 'fixture missing data'},
                    {'table': 'empty', 'rowsGot': 0, 'totalRows': 0, 'complete': True}]}]}))
            rows = collection_coverage(Dump([directory]))
        self.assertEqual(rows[0]['rows'], '')
        self.assertEqual(rows[0]['reason'], 'fixture missing data')
        self.assertEqual(rows[1]['rows'], 0)
        self.assertEqual(rows[1]['status'], 'ok')
    def test_keyword_report_reuse_does_not_hide_partial_dump(self):
        from report import cmd_keywords
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'raw').mkdir()
            Path(directory, 'manifest.json').write_text(json.dumps({'pages': [{
                'key': 'keyword-index', 'status': 'duplicate_rows', 'coverage': [
                    {'table': 'kwindex.fixture', 'rowsGot': 2, 'totalRows': 3,
                     'complete': False, 'reason': 'fixture repeated page'}]}]}))
            result = cmd_keywords(Dump([directory]), SimpleNamespace())
        self.assertEqual(result['_exit'], 5)
        self.assertEqual(result['keyword_index_coverage'][0]['reason'], 'fixture repeated page')
