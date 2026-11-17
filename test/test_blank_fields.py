# 합성 데이터만 사용하는 오프라인 회귀 테스트.
import contextlib
import io
import json
import os
import unittest
from test_report import report, run, write_dump, range_record, product_row, m, T_0701
from urllib.parse import urlencode
from ad_capture_status import response_error
import seller_central_raw as raw


class CaptureFailures(unittest.TestCase):
    def test_failed_and_malformed_responses_are_not_empty_success(self):
        valid = range_record('component_ggs_offline_product', [])
        for patch in ({'status': 503}, {'error': 'fixture'}, {'json': {'ok': False}},
                      {'json': {'data': {'queryResult': {'detailList': []}}}}, {'json': [{}]}):
            with self.subTest(patch=patch):
                rec = {**valid, **patch}
                self.assertTrue(response_error(rec))
                self.assertEqual(raw.range_resume([rec]), {})
                d = write_dump({'ads-report': [rec]})
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(report.main(['ads-products', '--from', d, '--format', 'json']), 3)
        self.assertFalse(response_error(valid))
        self.assertTrue(next(iter(raw.range_resume([valid]).values()))['complete'])

    def test_successful_retry_clears_previous_page_failure(self):
        valid = range_record('component_ggs_offline_product', [])
        d = write_dump({'ads-report': [{**valid, 'status': 503}, valid]})
        rows = report.range_completeness(report.Dump([d]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['complete'], 'yes')

    def test_missing_range_response_is_visible(self):
        d = write_dump({'ads-report': []})
        self.assertEqual(report.range_completeness(report.Dump([d]))[0]['complete'], 'NO (no range response)')

    def test_day_and_period_queries_have_separate_resume_and_reporting(self):
        table = 'component_ggs_offline_product'
        records = []
        for granularity, clicks in [('DAY', 2), ('ALL', 99)]:
            rec = range_record(table, [product_row('fixture-product', '2026-07-01', clicks, 1)])
            rec['body'] = urlencode({'data': json.dumps({'tableName': table, 'time': {'granularity': granularity}})})
            rec['replay']['tag'] = 'manual.product' + ('.period' if granularity == 'ALL' else '')
            records.append(rec)
        self.assertEqual(len(raw.range_resume(records)), 2)
        d = write_dump({'ads-report': records})
        self.assertEqual(len(report.range_completeness(report.Dump([d]))), 2)
        self.assertEqual(run('ads-products', [d])['product_ad_performance'][0]['clicks'], 2)

    def test_manifest_missing_manual_table_is_visible(self):
        d = write_dump({'ads-report': []})
        with open(os.path.join(d, 'manifest.json'), 'w') as f:
            json.dump({'pages': [{'key': 'ads-report', 'status': 'ok', 'rangeTables': ['manual.product']}]}, f)
        result = report.range_completeness(report.Dump([d]))
        self.assertEqual(result[0]['range_table_key'], 'manual.product')
        self.assertNotEqual(result[0]['complete'], 'yes')


class BlankReasons(unittest.TestCase):
    def test_conversion_missing_keys_and_empty_values_are_distinct(self):
        rows = [{'chatTime': m('2026-07-01 12:00:00'), 'productId': m('fixture-ad'), **extra} for extra in
                ({}, {'fbProdId': m(''), 'channelDisplay': m('')}, {'fbProdId': m('fixture-product'), 'channelDisplay': m('fixture-channel')})]
        d = write_dump({'ads-report': [range_record(report.CONV_TABLE, rows)]})
        converted = run('ads-conversions', [d])['conversions']
        self.assertEqual([c['converted_scope'] for c in converted], ['', '', 'product'])
        self.assertIn('키 없음', converted[0]['converted_product_id_reason'])
        self.assertIn('값 없음', converted[1]['converted_product_id_reason'])
        self.assertEqual(converted[2]['channel'], 'fixture-channel')
        self.assertEqual(converted[2]['channel_reason'], '')
        self.assertEqual(converted[2]['converted_product_id_reason'], '')

    def test_conversion_does_not_fill_missing_clicks_or_include_manual_spend(self):
        manual = {'campaignType': m(2), 'campaignId': m('fixture-manual'), 'timeUs': m('2026-07-01'), 'cost': m(30), 'cvr3': m(3)}
        product = {'productId': m('fixture-product'), 'timeUs': m('2026-07-01')}
        conversion = {'chatTime': m('2026-07-01 12:00:00'), 'productId': m('fixture-product')}
        d = write_dump({'ads-report': [range_record(report.CAMPAIGN_TABLES[0], [manual]),
                                     range_record(report.PRODUCT_TABLES[0], [product]),
                                     range_record(report.CONV_TABLE, [conversion])]})
        result = run('ads-conversions', [d])
        self.assertIsNone(result['conversion_summary'][0]['campaign_spend_usd'])
        self.assertIsNone(result['conversions'][0]['ad_product_clicks_on_date'])
        self.assertIn('없음', result['conversions'][0]['ad_product_clicks_reason'])

    def test_inquiry_blank_product_reason_uses_recorded_type(self):
        items = [{'id': i, 'createTime': T_0701, **extra} for i, extra in enumerate([
            {'feedbackType': 1, 'targetIds': ['fixture-product']}, {'feedbackType': 1},
            {'feedbackType': 1, 'targetIds': []}, {'feedbackType': 2, 'targetIds': ['fixture-company']},
            {'feedbackType': 6, 'targetIds': ['fixture-chat']}, {'feedbackType': 9, 'targetIds': ['fixture-other']}])]
        d = write_dump({'inquiries': [{'replay': {'kind': 'inquiry'}, 'json': {'list': items}}]})
        rows = {int(r['inquiry_id']): r for r in run('inquiry-list', [d])['inquiries']}
        self.assertEqual(rows[0]['product_id_reason'], '')
        self.assertIn('키 없음', rows[1]['product_id_reason'])
        self.assertIn('값 없음', rows[2]['product_id_reason'])
        self.assertIn('회사', rows[3]['product_id_reason'])
        self.assertIn('TM', rows[4]['product_id_reason'])
        self.assertIn('확인 불가', rows[4]['product_id_reason'])
        self.assertTrue(all(r['ad'] == '광고 연결 기록 없음' for r in rows.values()))
        self.assertTrue(all(not rows[i]['product_id'] for i in range(1, 6)))
