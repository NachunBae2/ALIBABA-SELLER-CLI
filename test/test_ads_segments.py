# test-only: rows come from test/fixtures/ads-segments.json (synthetic after the capture's shape).
import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
import ads_segments  # noqa: E402

FIXTURE = json.loads((ROOT / 'test' / 'fixtures' / 'ads-segments.json').read_text())


def args(command='ads-segments', start='2026-07-01', end='2026-07-31', category_id=None):
    return SimpleNamespace(command=command, start=start, end=end, category_id=category_id)


class FakeDump:
    def __init__(self, records, start='2026-07-01', end='2026-07-31'):
        self._records = records
        self.start, self.end = start, end

    def records(self, _command):
        return list(self._records)


def record(tag, table, group, campaign_type, rows, total, start='2026-07-01', end='2026-07-31',
           tag_type=None, view='', status=200, value=None, diagnosis_type=None,
           rows_path='data.queryResult.detailList', total_path='data.queryResult.totalRows'):
    meta = {'table': table, 'group': group, 'campaign_type': campaign_type, 'view': view,
            'tag_type': tag_type, 'diagnosis_type': diagnosis_type, 'category_id': None}
    body = value if value is not None else {
        'status': 1000, 'ok': True,
        'data': {'queryResult': {'detailList': rows, 'totalRows': total}}}
    return {'status': status, 'json': body, 'replay': {
        'kind': 'range', 'tag': tag, 'page': 1, 'start': start, 'end': end, 'meta': meta,
        'rowsPath': rows_path, 'totalPath': total_path}}


class ContractTests(unittest.TestCase):
    def test_segments_cover_every_screen_sub_tab(self):
        jobs = ads_segments.command('segments', dt.date(2026, 7, 1), dt.date(2026, 7, 31))['rangeReplay']['jobs']
        keys = {(j['meta']['table'], j['meta']['group'], j['meta']['campaign_type'], j['meta']['tag_type'])
                for j in jobs}
        for table, group, campaign_type in [
                ('region', 'ai', '51'), ('region', 'ai', '47'),
                ('region', 'manual', '1,2'), ('region', 'manual', '4,10'),
                ('hour', 'ai', '4,10'), ('hour', 'manual', '1,2')]:
            self.assertIn((table, group, campaign_type, None), keys)
        for group, campaign_type in [('ai', '4,10'), ('manual', '1,2')]:
            for tag_type in (13, 14, 15, 16, 17):
                self.assertIn(('target', group, campaign_type, tag_type), keys)
        self.assertEqual(len(jobs), 16)

    def test_every_body_carries_the_requested_period(self):
        jobs = ads_segments.command('segments', dt.date(2026, 7, 1), dt.date(2026, 7, 31))['rangeReplay']['jobs']
        for job in jobs:
            data = job['params']['data']
            with self.subTest(tag=job['tag']):
                self.assertEqual(data['time']['beginDateTime'], '2026-07-01 00:00:00')
                self.assertEqual(data['time']['endDateTime'], '2026-07-31 23:59:59')
                self.assertEqual(data['time']['granularity'], 'ALL')
                self.assertEqual(data['page']['size'], ads_segments.PAGE_SIZE)
                expected = 110106 if job['meta']['group'] == 'ai' and job['meta']['table'] == 'region' else 110101
                self.assertEqual(job['params']['ads']['productLineId'], expected)

    def test_long_range_splits_into_window_caps(self):
        jobs = ads_segments.command('segments', dt.date(2026, 1, 1), dt.date(2026, 4, 30))['rangeReplay']['jobs']
        region = [j for j in jobs if j['meta']['table'] == 'region' and j['meta']['group'] == 'ai'
                  and j['meta']['campaign_type'] == '51']
        windows = [(j['windowStart'], j['windowEnd']) for j in region]
        self.assertEqual(len(windows), 4)  # 120 days at the 31-day cap
        self.assertEqual(windows[0][0], '2026-01-01')
        self.assertEqual(windows[-1][1], '2026-04-30')
        for (_, last), (first, _) in zip(windows, windows[1:]):
            self.assertEqual((dt.date.fromisoformat(last) + dt.timedelta(days=1)).isoformat(), first)

    def test_industry_contract_keeps_its_own_limits(self):
        jobs = ads_segments.command('industry', dt.date(2026, 1, 1), dt.date(2026, 11, 16),
                                    3306)['rangeReplay']['jobs']
        compare = next(j for j in jobs if j['meta']['table'] == 'industry_compare')
        self.assertEqual(compare['params']['data']['dateRange'], 30)
        self.assertEqual(compare['params']['data']['statDate'], '2026-11-16')
        self.assertEqual(compare['params']['data']['categoryId'], 3306)
        high = [j for j in jobs if j['meta']['diagnosis_type'] == 'high']
        self.assertTrue(all(j['windowEnd'] == '2026-11-16' for j in high))
        cate = next(j for j in jobs if j['meta']['diagnosis_type'] == 'cate')
        self.assertEqual(cate['params']['data']['filters'][0], {'field': 'cateLv2Id', 'mode': 'EQ', 'value': 3306})


class ReportTests(unittest.TestCase):
    def test_rows_become_numbers_and_repeat_pages_are_read(self):
        dump = FakeDump([
            record('segment.region.ai.0.2026-07-01', 'region', 'ai', '51',
                   [FIXTURE['region_row']], 100),
            record('segment.region.ai.0.2026-07-01', 'region', 'ai', '51',
                   [FIXTURE['region_row2']], 100),
        ])
        out = ads_segments.report(dump, args())
        self.assertEqual(len(out['region']), 2)
        first = out['region'][0]
        self.assertEqual(first['clickCnt'], 14)
        self.assertEqual(first['impsCnt'], 772)
        self.assertEqual(first['region'], '테스트 지역')
        self.assertEqual(first['campaign_type'], '51')
        self.assertEqual(first['period_start'], '2026-07-01')
        self.assertEqual(first['period_end'], '2026-07-31')
        self.assertEqual([r['rows'] for r in out['requests']], [1, 1])
        self.assertNotIn('errors', out)

    def test_zero_rows_stay_explicit(self):
        dump = FakeDump([record('segment.target.ai.13.2026-07-01', 'target', 'ai', '4,10', [], 0,
                                tag_type=13)])
        out = ads_segments.report(dump, args())
        self.assertNotIn('target', out)
        self.assertEqual(out['requests'][0]['rows'], 0)
        self.assertNotIn('errors', out)

    def test_compare_keeps_the_three_scopes(self):
        dump = FakeDump([record('industry.compare.2026-11-16', 'industry_compare', 'industry', '',
                                None, None, value={'status': 1000, 'ok': True,
                                                   'data': FIXTURE['compare_data']},
                                rows_path='data')],
                        start='2026-10-17', end='2026-11-16')
        out = ads_segments.report(dump, args('ads-industry', '2026-10-17', '2026-11-16'))
        scopes = [r['scope'] for r in out['industry_compare']]
        self.assertEqual(scopes, ['mine', 'cateAvg', 'cateTop10'])
        self.assertEqual(out['industry_compare'][0]['click'], 1036)

    def test_market_rows_are_deduped_by_product(self):
        rows = [FIXTURE['cate_row']]
        dump = FakeDump([
            record('industry.category_products.cate.2026-10-17', 'category_products', 'ai', '',
                   rows, 100, diagnosis_type='cate'),
            record('industry.category_products.cate.2026-10-17', 'category_products', 'ai', '',
                   rows, 100, diagnosis_type='cate'),
        ], start='2026-10-17', end='2026-11-16')
        out = ads_segments.report(dump, args('ads-industry', '2026-10-17', '2026-11-16'))
        self.assertEqual(len(out['category_products']), 1)
        self.assertEqual(out['category_products'][0]['grade'], 'cate')
        self.assertEqual([r['rows'] for r in out['requests']], [1, 0])

    def test_refused_window_keeps_the_server_reason_and_exit_code(self):
        dump = FakeDump([record('industry.product_diagnosis.high.2026-10-17', 'product_diagnosis', 'ai', '',
                                None, None, diagnosis_type='high',
                                value={'status': 1001, 'ok': False, 'error': 'isp.internal-error'})],
                        start='2026-10-17', end='2026-11-16')
        out = ads_segments.report(dump, args('ads-industry', '2026-10-17', '2026-11-16'))
        self.assertEqual(out['_exit'], 3)
        self.assertIn('isp.internal-error', json.dumps(out['errors'], ensure_ascii=False))

    def test_response_outside_the_contract_fails_loudly(self):
        broken = record('unknown.tag', 'region', 'ai', '51', [], 0)
        broken['replay'].pop('rowsPath')
        out = ads_segments.report(FakeDump([broken]), args())
        self.assertEqual(out['_exit'], 5)
        self.assertEqual(out['errors'][0]['message'], '이 실행의 요청 계약에 없는 응답')


if __name__ == '__main__':
    unittest.main()
