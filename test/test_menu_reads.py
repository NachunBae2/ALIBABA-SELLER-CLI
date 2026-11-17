# test-only: synthetic fixtures; no real account, buyer, product, money, or network.
import contextlib
import copy
import datetime as dt
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
import report
from http_collect import collect, get_path, set_path
from menu_reads import failure, parse_sse, project

PAGES = [p for p in json.loads((Path(__file__).resolve().parents[1] / 'engine/pages.json').read_text())['pages'] if p.get('menuRead')]
CONTEXT = {'code': 200, 'data': {'userInfo': {'aliId': 'fixture-seller', 'memberType': 'fixture-member', 'locale': 'en_US', 'tenantInfo': {'site': 'ZZ', 'tenant': 'ZZ'}}}}


def args(max_pages=2):
    return SimpleNamespace(start_d=dt.date(2026, 1, 1), end_d=dt.date(2026, 1, 2),
                           tables='', extras='', max_pages=max_pages, max_passes=1, kw_pages=1)


def payload(page, rows, total=None):
    value = {'code': 200, 'responseCode': 200, 'success': True, 'buyerName': 'PRIVATE_SENTINEL'}
    set_path(value, page['menuRead']['path'], rows)
    job = page['requests'][0]
    if job.get('totalPath'):
        set_path(value, job['totalPath'], len(rows) if total is None else total)
    return value


def sse(module, rows):
    return '\n\n'.join('event:'+event+'\ndata:'+json.dumps(value) for event, value in [
        ('ack', {'code': '200', 'message': 'success'}),
        ('message', {'code': '200', 'message': 'success', 'name': module, 'payload': rows}),
        ('complete', {})]) + '\n\n'


class Session:
    transport = 'api'
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []
        self.metrics = {'httpRequests': 0, 'asideCalls': 0}
    def request(self, *call):
        self.calls.append(call)
        self.metrics['httpRequests'] += 1
        value = next(self.responses)
        return value if 'text' in value else {'status': 200, 'text': json.dumps(value)}


class MenuReads(unittest.TestCase):
    def run_page(self, page, rows):
        responses = [CONTEXT] if page['menuRead'].get('contextBinding') else []
        if page['menuRead'].get('sseModule'):
            responses.append({'status': 200, 'text': sse(page['menuRead']['sseModule'], rows)})
        else:
            responses.append(payload(page, rows))
        return collect(page, args(), Session(responses), {})

    def test_every_command_empty_and_nonempty_are_projected_before_disk_and_on_replay(self):
        for page in PAGES:
            for empty in (False, True):
                with self.subTest(page=page['key'], empty=empty), tempfile.TemporaryDirectory() as tmp:
                    row = {'buyerName': 'PRIVATE_SENTINEL', 'address': 'PRIVATE_SENTINEL', 'payment': 'PRIVATE_SENTINEL'}
                    field = page['menuRead']['fields'][0]
                    set_path(row, field, 'fixture-raw')
                    # Missing fields must become blanks, with reasons, without invented values.
                    source_rows = [] if empty else [row]
                    result = self.run_page(page, source_rows)
                    self.assertEqual(result['status'], 'ok')
                    self.assertNotIn('PRIVATE_SENTINEL', json.dumps(result))
                    self.assertEqual(result['metrics']['asideCalls'], 0)
                    raw = Path(tmp) / 'raw'; raw.mkdir()
                    # Deliberately replay an older, unsanitized dump.
                    value = payload(page, source_rows)
                    (raw / (page['key']+'.jsonl')).write_text(json.dumps({'status': 200, 'json': value})+'\n')
                    with contextlib.redirect_stderr(io.StringIO()) as notice:
                        tables = report.COMMANDS[page['menuRead']['command']](report.Dump([tmp]), args())
                    output = tables[page['key'].replace('-', '_')]
                    self.assertNotIn('_exit', tables)
                    self.assertNotIn('PRIVATE_SENTINEL', json.dumps(tables))
                    self.assertEqual(len(output), len(source_rows))
                    if empty:
                        self.assertIn('0행', notice.getvalue())
                    else:
                        self.assertEqual(output[0][field], 'fixture-raw')
                        self.assertEqual(bool(output[0]['_blank_reasons']), len(page['menuRead']['fields']) > 1)

    def test_errors_and_empty_http_fail_with_original_codes(self):
        for page in PAGES:
            if page['menuRead'].get('sseModule'):
                continue
            for value in ({'code': 1000, 'message': 'fixture invalid'}, {'code': 1003, 'message': 'fixture denied'},
                          {'responseCode': 10000002, 'success': True, 'errorMsg': 'fixture error'},
                          {'status': 302, 'text': ''}, {'status': 200, 'text': ''}):
                responses = ([CONTEXT] if page['menuRead'].get('contextBinding') else []) + [value]
                result = collect(page, args(), Session(responses), {})
                self.assertEqual(result['status'], 'api_failed')
                error = json.loads(result['detail'])
                for key in ('code', 'responseCode'):
                    if key in value:
                        self.assertEqual(error[key], value[key])
                self.assertTrue(result['records'][0]['error'])

    def test_sse_requires_ack_complete_module_and_success(self):
        text = sse('fixture.module', [{'count': 0, 'selected': False}])
        self.assertEqual(parse_sse(text, 'fixture.module')['data'][0]['count'], 0)
        for bad in (text.replace('event:complete','event:other'), text.replace('event:ack','event:other'),
                    text.replace('fixture.module','other.module'), text.replace('"200"','"1003"')):
            self.assertIsNotNone(failure(200, parse_sse(bad, 'fixture.module')))

    def test_pagination_completeness_and_bound_context(self):
        page = next(p for p in PAGES if p['key']=='logistics-orders')
        values = [CONTEXT, payload(page, [{'orderId': 'fixture-1'}], 2), payload(page, [{'orderId': 'fixture-2'}], 2)]
        session = Session(values)
        result = collect(page, args(), session, {})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([c[2]['json']['currentPage'] for c in session.calls[1:]], [1,2])
        self.assertEqual(session.calls[1][2]['json']['tenantCountryCode'], 'ZZ')
        self.assertEqual(result['coverage'][0]['rowsGot'], 2)
        result = collect(page, args(1), Session(values), {})
        self.assertEqual(result['status'], 'incomplete')

    def test_projection_rejects_bad_shapes_and_drops_nested_pii(self):
        page = next(p for p in PAGES if p['key']=='orders-refunds')
        value = payload(page, [{'id': {'buyerName': 'PRIVATE_SENTINEL'}, 'issueStatus': False, 'refundAmount': {'amount': '12.30', 'currencyCode': 'ZZZ', 'bank': 'PRIVATE_SENTINEL'}}])
        safe = project(value, page)
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(safe))
        self.assertEqual(get_path(safe, 'data.pageData.data')[0]['refundAmount']['amount'], '12.30')
        self.assertIs(get_path(safe, 'data.pageData.data')[0]['issueStatus'], False)
        for rows in (None, {}, ['not-an-object']):
            value = payload(page, [])
            set_path(value, page['menuRead']['path'], rows)
            with self.assertRaises(ValueError): project(value, page)

    def test_replay_error_is_nonzero_and_preserves_message(self):
        page = PAGES[1]
        with tempfile.TemporaryDirectory() as tmp:
            raw=Path(tmp)/'raw';raw.mkdir()
            (raw/(page['key']+'.jsonl')).write_text(json.dumps({'status':200,'json':{'code':1003,'message':'fixture error'}})+'\n')
            with contextlib.redirect_stderr(io.StringIO()):
                result=report.COMMANDS[page['key']](report.Dump([tmp]),args())
            self.assertEqual(result['_exit'],5)
            self.assertEqual(result['errors'][0]['message'],'fixture error')

    def test_invoice_null_rows_only_with_explicit_zero_total(self):
        page = next(p for p in PAGES if p['key'] == 'funds-invoice-history')
        value = payload(page, [], 0)
        set_path(value, page['menuRead']['path'], None)
        self.assertEqual(get_path(project(value, page), page['menuRead']['path']), [])
        set_path(value, 'data.total', 1)
        with self.assertRaises(ValueError): project(value, page)

    def test_non_api_never_collects(self):
        session=Session([]);session.transport='repl'
        result=collect(PAGES[0],args(),session,{})
        self.assertEqual(result['status'],'menu_reads_require_api_transport')
        self.assertFalse(session.calls)


if __name__ == '__main__': unittest.main()
