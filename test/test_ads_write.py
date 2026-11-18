# test-only: all identifiers and amounts below are synthetic, never seller data.
"""Ads write engine: fake HTTP responses, no network, no account, no token."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
import ads_write


CAMPAIGN = {'title': 'Old Name', 'onlineStatus': 9, 'settleStatus': 9,
            'properties': {'budget': 30.0, 'operationScope': {'campaignScope': {'regions': 'CA,US'}}}}
GROUPS = [{'id': 9001, 'productId': 11000040100479}]


def reply(value, status=200):
    return {'status': status, 'text': json.dumps(value), 'ct': 'application/json', 'redirectHost': None}


class FakeSession:
    """Answers each POST by path; every call is recorded so tests can assert what was sent."""
    transport = 'api'
    metrics = {}

    def __init__(self, campaign=None, groups=None, write=None, apply=True):
        self.campaign = json.loads(json.dumps(campaign or CAMPAIGN))
        self.groups = json.loads(json.dumps(GROUPS if groups is None else groups))
        self.write = write or (lambda path, data: {'status': 1000, 'ok': True})
        # apply=False: the server answers 1000 but keeps the old values (read-back must catch it).
        self.apply = apply
        self.calls = []

    def request(self, method, url, query=None, params=None, encoding='form', referer=None):
        data = json.loads(params['data']) if isinstance(params.get('data'), str) else (params or {}).get('data')
        self.calls.append({'method': method, 'url': url, 'data': data, 'params': params, 'referer': referer,
                           'query': query, 'encoding': encoding})
        path = url.split('www2.alibaba.com', 1)[1]
        if path == '/api/campaign/{id}'.replace('{id}', ''):  # unreachable, kept for clarity
            pass
        if path.startswith('/api/campaign/adgroup/count'):
            return reply({'status': 1000, 'data': {'3791': len(self.groups)}})
        if path.endswith('/adgroup/batch'):
            return reply({'status': 1000, 'data': self.groups})
        if path.endswith('/can/create'):
            return reply({'status': 1000, 'data': {'status': 1000}})
        if path.endswith('/adgroup/create'):
            self.groups.append({'id': 9002 + len(self.groups), 'productId': data['adGroupOperationList'][0]['productId']})
            return reply(self.write(path, data))
        if path.endswith('/adgroup/delete'):
            drop = {i['id'] for i in data['adGroupOperationList']}
            self.groups = [g for g in self.groups if g['id'] not in drop]
            return reply(self.write(path, data))
        if path.endswith('/adgroup/update'):
            return reply(self.write(path, data))
        if path.endswith('/campaign/update/batch'):
            for operation in data['campaignOperationList']:
                scope = self.campaign['properties'].setdefault('operationScope', {})
                scope['campaignScope'] = {'regions': operation.get('syncPromotionRegionConf')}
            return reply(self.write(path, data))
        if path.endswith('/campaign/update'):
            if self.apply:
                if 'title' in data:
                    self.campaign['title'] = data['title']
                elif 'name' in data:
                    self.campaign['title'] = data['name']
                if 'onlineStatus' in data:
                    self.campaign['onlineStatus'] = data['onlineStatus']
                if 'budget' in data:
                    self.campaign['properties']['budget'] = data['budget']
            return reply(self.write(path, data))
        if path.count('/') == 3 and path.startswith('/api/campaign/'):
            return reply({'status': 1000, 'data': json.loads(json.dumps(self.campaign))})
        raise AssertionError('unexpected url ' + url)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.config = Path(self.dir.name) / 'config.json'
        self.config.write_text(json.dumps({'thedozers': {'loginId': 'seller@example.com', 'outDir': self.dir.name}}),
                               encoding='utf-8')
        self.account = {'name': 'thedozers', 'loginId': 'seller@example.com', 'outDir': self.dir.name}
        self.backups = Path(self.dir.name) / 'ads-backups'

    def tearDown(self):
        self.dir.cleanup()

    def run_cli(self, argv, session, dry_run=True):
        argv = list(argv) + ['--account', 'thedozers', '--config', str(self.config)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = ads_write.run(argv, session, dry_run=dry_run)
            except ads_write.Usage as error:
                code = 64
                err.write(str(error))
            except ads_write.Unverified as error:
                code = 64
                err.write(str(error))
        return code, out.getvalue(), err.getvalue()

    def base(self, *extra):
        return list(extra)

    # --- dry-run never sends a write ---------------------------------------------------------------
    def test_campaign_rename_dry_run_sends_reads_only_and_shows_request(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['campaign', 'set', '3791', '--name', 'New Name'], session)
        self.assertEqual(code, 0)
        self.assertTrue(all(call['data'] == {} or 'campaignIdList' in str(call['data']) for call in session.calls),
                        'dry-run must not send a write: ' + json.dumps(session.calls))
        self.assertIn('Old Name', out)
        self.assertIn('New Name', out)
        self.assertIn('/api/campaign/update', out)
        self.assertIn('dry-run', out)
        self.assertEqual(session.campaign['title'], 'Old Name')
        self.assertFalse(self.backups.exists())
        # printed requests carry no token and no cookie
        self.assertNotIn('_csrf', out)
        self.assertEqual(session.calls[0]['params']['ads'], {'productLineId': 110106})
        self.assertEqual(session.calls[0]['params']['type'], 'normal')

    def test_campaign_countries_dry_run_reports_regions_and_cost(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['campaign', 'set', '3791', '--countries', 'CA,US,MX'], session)
        self.assertEqual(code, 0)
        self.assertIn('CA,US', out)
        self.assertIn('CA,US,MX', out)
        self.assertIn('비용 증가 가능', out)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.campaign['properties']['operationScope']['campaignScope']['regions'], 'CA,US')

    def test_products_add_dry_run_shows_plan_and_cost(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['products', 'add', '3791', '11000040100500'], session)
        self.assertEqual(code, 0)
        self.assertIn('11000040100500', out)
        self.assertIn('비용 증가 가능', out)
        self.assertIn('can/create', out)
        self.assertEqual([c['url'].split('www2.alibaba.com')[1] for c in session.calls], ['/api/campaign/3791/adgroup/batch'])
        self.assertEqual(len(session.groups), 1)

    def test_products_remove_dry_run_resolves_adgroup_ids(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['products', 'remove', '3791', '11000040100479'], session)
        self.assertEqual(code, 0)
        self.assertIn('9001', out)
        self.assertIn('adgroup/delete', out)
        self.assertEqual(len(session.groups), 1)

    # --- --yes order: backup -> precondition -> write -> read back ---------------------------------
    def test_yes_backs_up_first_then_writes_then_reads_back(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['campaign', 'set', '3791', '--name', 'New Name'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('백업: ', out)
        self.assertIn('리드백 일치', out)
        files = list(self.backups.glob('*.json'))
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text(encoding='utf-8'))
        self.assertEqual(record['account'], 'thedozers')
        self.assertEqual(record['campaignId'], 3791)
        self.assertEqual(record['action'], 'campaign.rename')
        self.assertEqual(record['before']['title'], 'Old Name')
        self.assertEqual(len(record['request']), 2)
        self.assertNotIn('_csrf', json.dumps(record))
        self.assertIn(str(files[0]), out)
        writes = [c for c in session.calls if c['method'] == 'POST' and 'adgroup' not in c['url'] and c['data']]
        self.assertEqual(session.campaign['title'], 'New Name')
        self.assertTrue(writes)

    def test_yes_product_add_runs_can_create_before_create(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['products', 'add', '3791', '11000040100500'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        paths = [c['url'].split('www2.alibaba.com')[1] for c in session.calls]
        self.assertLess(paths.index('/api/campaign/3791/adgroup/can/create'),
                        paths.index('/api/campaign/3791/adgroup/create'))
        self.assertEqual([g['productId'] for g in session.groups], [11000040100479, 11000040100500])
        self.assertIn('리드백 일치', out)

    def test_precondition_refusal_stops_before_write(self):
        session = FakeSession()
        session.request = (lambda inner: lambda method, url, query=None, params=None, encoding='form', referer=None:
                           reply({'status': 1000, 'data': {'status': 1001}}) if url.endswith('/can/create')
                           else inner(method, url, query, params, encoding, referer))(session.request)
        code, out, _ = self.run_cli(['products', 'add', '3791', '11000040100500'], session, dry_run=False)
        self.assertEqual(code, 1)
        self.assertIn('사전확인 거부', out)
        self.assertIn('1001', out)
        self.assertEqual(len(session.groups), 1)

    def test_readback_mismatch_exits_three_and_keeps_backup(self):
        # the server answers 1000 but keeps the old title: the read-back must catch it
        session = FakeSession(apply=False)
        code, out, _ = self.run_cli(['campaign', 'set', '3791', '--name', 'New Name'], session, dry_run=False)
        self.assertEqual(code, 3, out)
        self.assertIn('리드백 불일치', out)
        self.assertEqual(len(list(self.backups.glob('*.json'))), 1)

    def test_write_refusal_exits_one(self):
        session = FakeSession(write=lambda path, data: {'status': 1001, 'message': 'nope'})
        code, out, _ = self.run_cli(['campaign', 'set', '3791', '--budget', '50'], session, dry_run=False)
        self.assertEqual(code, 1, out)
        self.assertIn('실패: 서버가 요청을 거부함', out)

    def test_budget_rise_warns_cost(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['campaign', 'set', '3791', '--budget', '50'], session)
        self.assertEqual(code, 0)
        self.assertIn('비용 증가 가능', out)

    def test_status_on_warns_cost_status_off_does_not(self):
        session = FakeSession()
        _, on_out, _ = self.run_cli(['campaign', 'set', '3791', '--status', 'on'], session)
        self.assertIn('비용 증가 가능', on_out)
        _, off_out, _ = self.run_cli(['campaign', 'set', '3791', '--status', 'off'], session)
        self.assertNotIn('비용 증가 가능', off_out)

    # --- refusals ----------------------------------------------------------------------------------
    def test_unverified_keywords_exit_64_and_send_nothing(self):
        for sub in ('add', 'remove', 'bid'):
            session = FakeSession()
            extra = ['--price', '1'] if sub == 'bid' else []
            code, out, err = self.run_cli(['keywords', sub, '3791', 'fence'] + extra, session, dry_run=False)
            self.assertEqual(code, 64, sub)
            self.assertIn('요청 형식 미검증', err)
            self.assertEqual(session.calls, [])
            self.assertFalse(self.backups.exists())

    def test_blocked_account_exit_77(self):
        for name in ('wonnischoice', 'linea'):
            config = Path(self.dir.name) / ('blocked-%s.json' % name)
            config.write_text(json.dumps({name: {'loginId': 'seller@example.com'}}), encoding='utf-8')
            argv = ['campaign', 'set', '3791', '--name', 'x', '--account', name, '--config', str(config)]
            with self.assertRaises(ads_write.Blocked) as caught:
                ads_write.run(list(argv), FakeSession())
            self.assertIn(name, str(caught.exception))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(ads_write.cli(list(argv)), 77, name)

    def test_usage_errors_exit_64(self):
        for argv in (['campaign', 'set', '3791'], ['campaign', 'set', '3791', '--name', 'a', '--budget', '5'],
                     ['campaign', 'set', '0', '--name', 'a'],
                     ['campaign', 'set', '3791', '--countries', 'CA,ca']):
            session = FakeSession()
            code, _, _ = self.run_cli(argv, session)
            self.assertEqual(code, 64, argv)
            self.assertEqual(session.calls, [], argv)
        # a product id missing from the campaign can only be rejected after reading the groups:
        # the read is expected, but nothing may be written.
        session = FakeSession()
        code, _, err = self.run_cli(['products', 'remove', '3791', '999'], session)
        self.assertEqual(code, 64)
        self.assertIn('999', err)
        self.assertEqual([call for call in session.calls if call['data']], [])

    # --- restore / backups -------------------------------------------------------------------------
    def test_restore_is_dry_run_then_yes_puts_the_backup_value_back(self):
        session = FakeSession()
        self.run_cli(['campaign', 'set', '3791', '--name', 'New Name'], session, dry_run=False)
        backup = next(self.backups.glob('*.json'))
        self.assertEqual(session.campaign['title'], 'New Name')
        code, out, _ = self.run_cli(['restore', str(backup)], session)
        self.assertEqual(code, 0, out)
        self.assertIn('Old Name', out)
        self.assertEqual(session.campaign['title'], 'New Name')
        code, out, _ = self.run_cli(['restore', str(backup)], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(session.campaign['title'], 'Old Name')
        self.assertEqual(len(list(self.backups.glob('*.json'))), 2)

    def test_backups_lists_written_files(self):
        session = FakeSession()
        self.run_cli(['campaign', 'set', '3791', '--name', 'New Name'], session, dry_run=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            listed = ads_write.list_backups(self.account)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['action'], 'campaign.rename')
        self.assertEqual(listed[0]['campaignId'], 3791)

    # --- plan helpers ------------------------------------------------------------------------------
    def test_country_count_and_budget_compare(self):
        self.assertEqual(ads_write.country_count(None), 1)
        self.assertEqual(ads_write.country_count('CA,US'), 2)
        self.assertTrue(ads_write.equal('budget', 30, 30.0))
        self.assertFalse(ads_write.equal('budget', 30, 31))

    def test_every_request_uses_the_captured_envelope(self):
        session = FakeSession()
        self.run_cli(['campaign', 'set', '3791', '--countries', 'CA,US'], session, dry_run=False)
        for call in session.calls:
            self.assertEqual(call['method'], 'POST')
            self.assertTrue(call['url'].startswith('https://www2.alibaba.com/api/'))
            self.assertEqual(call['encoding'], 'form')
            self.assertEqual(call['params']['ads'], {'productLineId': 110106})
            self.assertEqual(call['params']['type'], 'normal')
            self.assertFalse(call['query'])
            self.assertEqual(call['referer'], 'https://www2.alibaba.com/supereme/index.htm')


if __name__ == '__main__':
    unittest.main()
