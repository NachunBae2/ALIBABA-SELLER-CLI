# test-only: all identifiers and amounts below are synthetic, never seller data.
"""Ads write engine: fake HTTP responses, no network, no account, no token."""
import contextlib
import argparse
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
import ads_write
from ads_write import with_scope


CAMPAIGN = {'id': 3791, 'type': 2, 'title': 'Old Name', 'onlineStatus': 9, 'settleStatus': 9,
            'properties': {'budget': 30.0, 'maxPrice': '0.5', 'bidType': 2, 'weekBudgetStatus': 'on',
                           'operationScope': {'campaignScope': {'regions': 'CA,US',
                                                                'syncPromotionHourConf': '0,1,2,3'}}}}
GROUPS = [{'id': 9001, 'productId': 11000040100479, 'onlineStatus': 1}]
KEYWORDS = [{'id': 7001, 'word': 'fence', 'price': '1.30', 'onlineStatus': 1},
            {'id': 7002, 'word': 'gate', 'price': '1.10', 'onlineStatus': 1}]


def reply(value, status=200):
    return {'status': status, 'text': json.dumps(value), 'ct': 'application/json', 'redirectHost': None}


class FakeSession:
    """Answers each POST by path; every call is recorded so tests can assert what was sent.

    The fake mimics the server's side effects only for fields it knows; read-back then sees them.
    """
    transport = 'api'
    metrics = {}

    def __init__(self, campaign=None, groups=None, keywords=None, write=None, apply=True):
        self.campaign = json.loads(json.dumps(campaign or CAMPAIGN))
        self.groups = json.loads(json.dumps(GROUPS if groups is None else groups))
        self.keywords = json.loads(json.dumps(KEYWORDS if keywords is None else keywords))
        self.write = write or (lambda path, data: {'status': 1000, 'ok': True})
        # apply=False: the server answers 1000 but keeps the old values (read-back must catch it).
        self.apply = apply
        self.calls = []

    def request(self, method, url, query=None, params=None, encoding='form', referer=None):
        data = json.loads(params['data']) if isinstance(params.get('data'), str) else (params or {}).get('data')
        self.calls.append({'method': method, 'url': url, 'data': data, 'params': params, 'referer': referer,
                           'query': query, 'encoding': encoding})
        path = url.split('www2.alibaba.com', 1)[1]
        scope = self.campaign['properties'].setdefault('operationScope', {})
        if path.startswith('/api/campaign/adgroup/count'):
            return reply({'status': 1000, 'data': {'3791': len(self.groups)}})
        if path.endswith('/adgroup/batch'):
            return reply({'status': 1000, 'data': self.groups})
        if path.endswith('/can/create') or path.endswith('/can/startup'):
            return reply({'status': 1000, 'data': {'status': 1000}})
        if path.endswith('/adgroup/create'):
            for item in data['adGroupOperationList']:
                self.groups.append({'id': 9001 + len(self.groups), 'productId': item['productId'],
                                    'onlineStatus': 1})
            return reply(self.write(path, data))
        if path.endswith('/adgroup/delete'):
            drop = {i['id'] for i in data['adGroupOperationList']}
            self.groups = [g for g in self.groups if g['id'] not in drop]
            return reply(self.write(path, data))
        if path.endswith('/adgroup/update'):
            if self.apply:
                for item in data['adGroupOperationList']:
                    for group in self.groups:
                        if group['id'] == item['id']:
                            group['onlineStatus'] = item['onlineStatus']
            return reply(self.write(path, data))
        if path.endswith('/bidword/query'):
            return reply({'status': 1000, 'data': self.keywords})
        if path.endswith('/bidword/create'):
            if self.apply:
                for item in data['keywordList']:
                    self.keywords.append({'id': 7100 + len(self.keywords), 'word': item['word'],
                                          'price': str(item['price']), 'onlineStatus': 1})
            return reply(self.write(path, data))
        if path.endswith('/bidword/delete'):
            if self.apply:
                drop = {i['id'] for i in data['keywordList']}
                self.keywords = [k for k in self.keywords if k['id'] not in drop]
            return reply(self.write(path, data))
        if path.endswith('/bidword/update'):
            if self.apply:
                for item in data['keywordList']:
                    for keyword in self.keywords:
                        if keyword['id'] != item['id']:
                            continue
                        if data['updateType'] == 'add_price':
                            keyword['price'] = str(item['price'])
                        else:
                            keyword['onlineStatus'] = data['updateInfo']['onlineStatus']
            return reply(self.write(path, data))
        if path.endswith('/campaign/update/batch'):
            if self.apply:
                # 배치 응답에는 갱신된 캠페인이 없다 — 리드백은 단건 읽기 응답에 반영된다.
                for operation in data['campaignOperationList']:
                    fields = {}
                    if 'syncPromotionRegionConf' in operation:
                        fields['regions'] = operation['syncPromotionRegionConf']
                    if 'syncPromotionHourConf' in operation:
                        fields['syncPromotionHourConf'] = operation['syncPromotionHourConf']
                    self.campaign = with_scope(self.campaign, **fields)
            return reply(self.write(path, data))
        if path.endswith('/campaign/update/precheck'):
            return reply({'status': 1000, 'data': {'modifyNum': 1}})
        if path.endswith('/campaign/create') or path.endswith('/campaign/can/create'):
            if path.endswith('/campaign/can/create'):
                return reply({'status': 1000, 'data': {'status': 1000}})
            created = {'id': 3792, 'type': 2, 'title': data['title'], 'onlineStatus': 1, 'settleStatus': 1,
                       'properties': {'budget': data['budget'], 'maxPrice': data['maxPrice'], 'bidType': 2,
                                      'weekBudgetStatus': data['weekBudgetStatus'],
                                      'operationScope': {'campaignScope': {}}}}
            self.created = created
            return reply(self.write(path, data) | {'data': {'id': created['id']}})
        if path.endswith('/campaign/type/allList'):
            rows = [] if not hasattr(self, 'created') else [self.created]
            return reply({'status': 1000, 'data': {'1': rows + [self.campaign]}})
        if path.endswith('/campaign/update'):
            if self.apply:
                target = self.created if getattr(self, 'created', None) and data.get('id') == self.created['id'] \
                    else self.campaign
                if 'title' in data:
                    target['title'] = data['title']
                if 'name' in data:
                    target['title'] = data['name']
                if 'onlineStatus' in data:
                    target['onlineStatus'] = data['onlineStatus']
                if 'budget' in data:
                    target['properties']['budget'] = data['budget']
                if 'maxPrice' in data:
                    target['properties']['maxPrice'] = str(data['maxPrice'])
                if 'weekBudgetStatus' in data:
                    target['properties']['weekBudgetStatus'] = data['weekBudgetStatus']
            return reply(self.write(path, data))
        if path.count('/') == 3 and path.startswith('/api/campaign/'):
            if getattr(self, 'created', None) and path == '/api/campaign/%s' % self.created['id']:
                return reply({'status': 1000, 'data': json.loads(json.dumps(self.created))})
            return reply({'status': 1000, 'data': json.loads(json.dumps(self.campaign))})
        raise AssertionError('unexpected url ' + url)


class EngineTest(unittest.TestCase):
    """쓰기 로직 검증. 잠금은 코드 상수라 테스트에서만 patch 로 내린다(환경변수·플래그 경로 없음).

    잠금을 내리는 것은 아래 setUp 하나뿐 — AdsLockTest 는 패치 없이 실제 잠금 상태를 본다.
    """
    def setUp(self):
        self.lock_off = mock.patch.object(ads_write, 'ADS_WRITE_LOCKED', False)
        self.lock_off.start()
        self.dir = tempfile.TemporaryDirectory()
        self.config = Path(self.dir.name) / 'config.json'
        self.config.write_text(json.dumps({'thedozers': {'loginId': 'seller@example.com', 'outDir': self.dir.name}}),
                               encoding='utf-8')
        self.account = {'name': 'thedozers', 'loginId': 'seller@example.com', 'outDir': self.dir.name}
        self.backups = Path(self.dir.name) / 'ads-backups'

    def tearDown(self):
        self.dir.cleanup()
        self.lock_off.stop()

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
            except ads_write.AdsLocked as error:   # 잠금(패치로 내려도 create 는 하드 차단)
                code = 69
                err.write(str(error))
        return code, out.getvalue(), err.getvalue()

    def writes(self, session):
        """Requests that change state: reads (campaign, adgroup/batch|count, bidword/query, precheck) are not writes."""
        reads = ('/adgroup/batch', '/adgroup/count', '/bidword/query', '/precheck', '/type/allList', '/campaign/can/')
        return [c for c in session.calls if c['data'] and not c['url'].endswith(reads)]

    # --- dry-run never sends a write ---------------------------------------------------------------
    def test_campaign_rename_dry_run_sends_reads_only_and_shows_request(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['campaign', 'set', '3791', '--name', 'New Name'], session)
        self.assertEqual(code, 0)
        self.assertEqual(self.writes(session), [], 'dry-run must not send a write: ' + json.dumps(session.calls))
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
        # reads only: product list + the campaign (its type decides whether --yes may send)
        self.assertEqual([c['url'].split('www2.alibaba.com')[1] for c in session.calls],
                         ['/api/campaign/3791/adgroup/batch', '/api/campaign/3791'])
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
        self.assertEqual([r['data'] for r in record['request']],
                         [{'id': 3791, 'title': 'New Name'}])  # captured shape, one request
        self.assertNotIn('_csrf', json.dumps(record))
        self.assertIn(str(files[0]), out)
        self.assertEqual(session.campaign['title'], 'New Name')

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
        code, out, _ = self.run_cli(['campaign', 'set', '3791', '--name', 'Other'], session, dry_run=False)
        self.assertEqual(code, 1, out)
        self.assertIn('실패: 요청 거부', out)

    def test_all_countries_to_a_list_still_refuses_yes(self):
        # from 'all countries' a narrowed list has no captured way back: refused, nothing sent
        session = FakeSession(campaign=dict(CAMPAIGN, type=51))
        session.campaign['properties'].pop('operationScope', None)
        code, out, err = self.run_cli(['campaign', 'set', '3791', '--countries', 'CA,US'], session, dry_run=False)
        self.assertEqual(code, 64, out)
        self.assertIn('restore 불가', out + err)
        self.assertFalse([c for c in session.calls if '/update' in c['url']])

    def test_action_outside_its_captured_campaign_type_refuses_yes(self):
        # budget/keywords were captured on a manual (type 2) campaign only; 51 is a paid package
        for ctype, argv in ((51, ['campaign', 'set', '3791', '--budget', '50']),
                            (51, ['keywords', 'add', '3791', 'mesh', '--price', '1.5']),
                            (47, ['campaign', 'set', '3791', '--status', 'off']),
                            (2, ['campaign', 'set', '3791', '--countries', 'CA,US'])):
            session = FakeSession(campaign=dict(CAMPAIGN, type=ctype))
            code, out, err = self.run_cli(argv, session, dry_run=False)
            self.assertEqual(code, 64, (ctype, argv, out))
            self.assertIn('캡처되지 않음', out + err)
            self.assertFalse([c for c in session.calls if re.search(r'/(update|create|delete)', c['url'])], argv)

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

    def test_blocked_account_exit_77(self):
        for name in ('wonnischoice', 'linea'):
            config = Path(self.dir.name) / ('blocked-%s.json' % name)
            config.write_text(json.dumps({name: {'loginId': 'seller@example.com'}}), encoding='utf-8')
            argv = ['campaign', 'set', '3791', '--name', 'x', '--account', name, '--config', str(config)]
            with self.assertRaises(ads_write.Blocked) as caught:
                ads_write.run(list(argv), FakeSession(), dry_run=False)
            self.assertIn(name, str(caught.exception))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                # 이 클래스는 setUp 에서 잠금을 내리므로(로직 검증) 차단 계정 77 이 그대로 보인다.
                # 잠금이 켜진 실제 상태에서 69 가 77 보다 먼저인지는 AdsLockTest 가 검증한다.
                self.assertEqual(ads_write.cli(list(argv)), 77, name)

    def test_usage_errors_exit_64(self):
        for argv in (['campaign', 'set', '3791'], ['campaign', 'set', '3791', '--name', 'a', '--budget', '5'],
                     ['campaign', 'set', '0', '--name', 'a'],
                     ['campaign', 'set', '3791', '--countries', 'CA,ca'],
                     ['campaign', 'set', '3791', '--hours', '24'],
                     ['keywords', 'bid', '3791', 'fence']):
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
        self.assertEqual(self.writes(session), [])

    # --- campaign set: captured update shapes ------------------------------------------------------
    def test_status_off_sends_online_status_with_product_line(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['campaign', 'set', '3791', '--status', 'off'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual([c['data'] for c in self.writes(session)],
                         [{'id': 3791, 'onlineStatus': 0, 'productLineId': 110106}])
        self.assertEqual(session.campaign['onlineStatus'], 0)

    def test_status_on_calls_startup_first(self):
        session = FakeSession(campaign=dict(CAMPAIGN, onlineStatus=0))
        code, out, _ = self.run_cli(['campaign', 'set', '3791', '--status', 'on'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        paths = [c['url'].split('www2.alibaba.com')[1] for c in session.calls]
        self.assertLess(paths.index('/api/campaign/can/startup'), paths.index('/api/campaign/update'))
        self.assertEqual(session.campaign['onlineStatus'], 1)

    def test_budget_update_carries_every_price_field_and_modify_num(self):
        # 예산을 내리면 precheck(module price) 를 먼저 보내고 update 에 modifyNum 을 붙인다(m36).
        session = FakeSession()
        code, out, _ = self.run_cli(['campaign', 'set', '3791', '--budget', '20'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        paths = [c['url'].split('www2.alibaba.com')[1] for c in session.calls]
        self.assertIn('/api/campaign/update/precheck', paths)
        self.assertLess(paths.index('/api/campaign/update/precheck'), paths.index('/api/campaign/update'))
        precheck = next(c for c in session.calls if c['url'].endswith('/precheck'))
        self.assertEqual(precheck['data'], {'id': 3791, 'module': 'price',
                                           'params': {'bidType': '2', 'maxPrice': 0.5}})
        update = next(c for c in session.calls if c['url'].endswith('/campaign/update'))
        self.assertEqual(update['data'], {'id': 3791, 'maxPrice': 0.5, 'bidType': 2, 'budget': 20.0,
                                          'weekBudgetStatus': 'on', 'needUpdateCampaignIds': [],
                                          'modifyNum': 1})
        self.assertEqual(session.campaign['properties']['budget'], 20.0)

    def test_bid_rise_skips_precheck_and_sets_max_price(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['campaign', 'set', '3791', '--bid', '0.8'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual([c['url'].split('www2.alibaba.com')[1] for c in self.calls_with_data(session)],
                         ['/api/campaign/update'])
        update = self.writes(session)[0]['data']
        self.assertEqual(update['maxPrice'], 0.8)
        self.assertEqual(update['budget'], 30.0)
        self.assertNotIn('modifyNum', update)

    def calls_with_data(self, session):
        return [c for c in session.calls if c['data']]

    def test_week_budget_and_hours_shapes(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['campaign', 'set', '3791', '--week-budget', 'off'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.writes(session)[0]['data'], {'id': 3791, 'weekBudgetStatus': 'off'})
        session = FakeSession()
        code, out, _ = self.run_cli(['campaign', 'set', '3791', '--hours', '0,1,2'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        operation = self.writes(session)[0]['data']['campaignOperationList'][0]
        self.assertEqual(operation['id'], 3791)
        self.assertEqual(operation['syncPromotionHourConf'], '0,1,2')
        self.assertEqual(session.campaign['properties']['operationScope']['campaignScope']['syncPromotionHourConf'],
                         '0,1,2')

    # --- products ----------------------------------------------------------------------------------
    def test_product_remove_pauses_resumes_shapes(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['products', 'remove', '3791', '11000040100479'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.writes(session)[0]['data'],
                         {'adGroupOperationList': [{'id': 9001, 'productId': 11000040100479}]})
        self.assertEqual(session.groups, [])

        session = FakeSession()
        code, out, _ = self.run_cli(['products', 'pause', '3791', '11000040100479'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.writes(session)[0]['data'], {'adGroupOperationList': [{'id': 9001, 'onlineStatus': 0}]})
        self.assertEqual(session.groups[0]['onlineStatus'], 0)

        session = FakeSession(groups=[{'id': 9001, 'productId': 11000040100479, 'onlineStatus': 0}])
        code, out, _ = self.run_cli(['products', 'resume', '3791', '11000040100479'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        paths = [c['url'].split('www2.alibaba.com')[1] for c in session.calls]
        self.assertLess(paths.index('/api/campaign/3791/adgroup/can/startup'),
                        paths.index('/api/campaign/3791/adgroup/update'))
        self.assertEqual(session.groups[0]['onlineStatus'], 1)

    # --- keywords ----------------------------------------------------------------------------------
    def test_keyword_add_body_and_readback(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['keywords', 'add', '3791', 'mesh', '--price', '1.5'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.writes(session)[0]['data'],
                         {'keywordList': [{'word': 'mesh', 'price': 1.5, 'source': 'customer'}]})
        self.assertIn('리드백 일치', out)
        self.assertEqual([k['word'] for k in session.keywords], ['fence', 'gate', 'mesh'])

    def test_keyword_remove_pause_bid_bodies(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['keywords', 'remove', '3791', 'gate'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.writes(session)[0]['data'], {'keywordList': [{'id': 7002}]})
        self.assertEqual([k['word'] for k in session.keywords], ['fence'])

        session = FakeSession()
        code, out, _ = self.run_cli(['keywords', 'pause', '3791', 'fence'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.writes(session)[0]['data'],
                         {'updateType': 'update_status', 'keywordList': [{'id': 7001}],
                          'updateInfo': {'onlineStatus': 0}})
        self.assertEqual(session.keywords[0]['onlineStatus'], 0)

        session = FakeSession()
        code, out, _ = self.run_cli(['keywords', 'bid', '3791', 'fence', '--price', '2'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.writes(session)[0]['data'],
                         {'updateType': 'add_price', 'keywordList': [{'id': 7001, 'price': 2.0}]})
        self.assertEqual(session.keywords[0]['price'], '2.0')

    def test_keyword_unknown_word_refuses_before_write(self):
        session = FakeSession()
        code, _, err = self.run_cli(['keywords', 'pause', '3791', 'unknown'], session, dry_run=False)
        self.assertEqual(code, 64)
        self.assertIn('unknown', err)
        self.assertEqual(self.writes(session), [])

    # --- campaign create: 하드 차단(잠금을 내려도 거절) ---------------------------------------------
    def test_create_is_hard_blocked_even_with_the_lock_patched_off(self):
        # ADS_WRITE_LOCKED 는 setUp 에서 False 로 패치돼 있다 — 그래도 생성은 하드 차단.
        session = FakeSession()
        code, out, err = self.run_cli(['campaign', 'create', '--name', 'New', '--products', '11000040100500',
                                       '--keywords', 'mesh', '--start-billing'], session)
        self.assertEqual(code, 69, out + err)
        self.assertIn('하드 차단', err)
        self.assertEqual(session.calls, [])
        with self.assertRaises(ads_write.AdsLocked) as caught:
            ads_write.run(['campaign', 'create', '--name', 'New', '--start-billing', '--account', 'thedozers',
                           '--config', str(self.config)], FakeSession(), dry_run=False)
        self.assertIn('캠페인 생성은 하드 차단', str(caught.exception))

    # --- restore / backups -------------------------------------------------------------------------
    def test_restore_is_dry_run_then_yes_puts_the_backup_value_back(self):
        session = FakeSession()
        self.run_cli(['campaign', 'set', '3791', '--name', 'New Name'], session, dry_run=False)
        backup = next(self.backups.glob('*.json'))
        self.assertEqual(session.campaign['title'], 'New Name')
        code, out, err = self.run_cli(['restore', str(backup)], session)
        self.assertEqual(code, 0, out + err)
        self.assertIn('Old Name', out)
        self.assertEqual(session.campaign['title'], 'New Name')
        code, out, _ = self.run_cli(['restore', str(backup)], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(session.campaign['title'], 'Old Name')
        self.assertEqual(len(list(self.backups.glob('*.json'))), 2)

    def test_restore_reverses_keyword_and_product_changes(self):
        session = FakeSession()
        self.run_cli(['keywords', 'add', '3791', 'mesh', '--price', '1.5'], session, dry_run=False)
        self.assertIn('mesh', [k['word'] for k in session.keywords])
        backup = sorted(self.backups.glob('*.json'))[-1]
        code, out, err = self.run_cli(['restore', str(backup)], session, dry_run=False)
        self.assertEqual(code, 0, 'restore=' + str(code) + ' | ' + (out or '') + ' | ' + (err or ''))
        self.assertEqual(code, 0, out + err)
        self.assertEqual([k['word'] for k in session.keywords], ['fence', 'gate'])

        session = FakeSession()
        self.run_cli(['products', 'remove', '3791', '11000040100479'], session, dry_run=False)
        self.assertEqual(session.groups, [])
        backup = sorted(self.backups.glob('*.json'))[-1]
        code, out, err = self.run_cli(['restore', str(backup)], session, dry_run=False)
        self.assertEqual(code, 0, 'restore=' + str(code) + ' | ' + (out or '') + ' | ' + (err or ''))
        self.assertEqual([g['productId'] for g in session.groups], [11000040100479])

    def test_restore_of_keyword_remove_adds_the_original_price(self):
        session = FakeSession()
        self.run_cli(['keywords', 'remove', '3791', 'gate'], session, dry_run=False)
        backup = sorted(self.backups.glob('*.json'))[-1]
        code, out, _ = self.run_cli(['restore', str(backup)], session, dry_run=False)
        self.assertEqual(code, 0, out)
        restored = [k for k in session.keywords if k['word'] == 'gate']
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]['price'], '1.1')

    def test_backups_lists_written_files(self):
        session = FakeSession()
        self.run_cli(['campaign', 'set', '3791', '--name', 'New Name'], session, dry_run=False)
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
        self.assertTrue(ads_write.equal('hours', '0,1,2', '0,1,2'))
        self.assertFalse(ads_write.equal('hours', '0,1,2', '0,1'))

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


LOCK_ARGS = ['--account', 'thedozers', '--config', '/nonexistent/x.json']

# 잠금 대상 = 쓰기 명령 12개(campaign set 는 7변형 모두).
# config 는 일부러 없는 경로 — 잠금이 계정 파일 읽기보다 먼저면 실패 대신 69 가 나온다.
LOCKED_COMMANDS = [
    ['campaign', 'set', '3791', '--name', 'x'],
    ['campaign', 'set', '3791', '--status', 'off'],
    ['campaign', 'set', '3791', '--budget', '30'],
    ['campaign', 'set', '3791', '--bid', '0.5'],
    ['campaign', 'set', '3791', '--week-budget', 'off'],
    ['campaign', 'set', '3791', '--hours', '0,1'],
    ['campaign', 'set', '3791', '--countries', 'CA,US'],
    ['campaign', 'create', '--name', 'x', '--budget', '30', '--bid', '0.5', '--products', '1',
     '--keywords', 'a', '--start-billing', '--keep-on'],
    ['products', 'add', '3791', '11000040100500'],
    ['products', 'remove', '3791', '11000040100479'],
    ['products', 'pause', '3791', '11000040100479'],
    ['products', 'resume', '3791', '11000040100479'],
    ['keywords', 'add', '3791', 'mesh', '--price', '1.5'],
    ['keywords', 'remove', '3791', 'gate'],
    ['keywords', 'pause', '3791', 'fence'],
    ['keywords', 'resume', '3791', 'fence'],
    ['keywords', 'bid', '3791', 'fence', '--price', '2'],
    ['restore', '/tmp/backup.json'],
]
LOCKED_GROUPS_IN_COMMANDS = {'campaign', 'products', 'keywords', 'restore'}

# 반려 1회차 재현 목록: 전역 옵션을 명령 앞/사이에 둔 변형. 잠금이 파싱 결과로 판단되는지 본다.
OPTIONS_FIRST_COMMANDS = [
    ['--acc', 'thedozers', 'campaign', 'set', '1', '--name', 'x', '--yes'],
    ['--format', 'json', 'products', 'add', '1', '2', '--account', 'thedozers', '--yes'],
    ['--config', '/nonexistent/x.json', '--account', 'thedozers', 'keywords', 'bid', '1', 'w', '--price', '1.5', '--yes'],
    ['--account=thedozers', 'restore', '/nonexistent/b.json', '--yes'],
    ['--account', 'linea', 'campaign', 'create', '--name', 'x', '--yes'],
]


def leaves(parser, path=None):
    """(명령 경로, 이름, 하위 파서) — argparse 내부 구조로 leaf 명령을 순회한다."""
    path = path or []
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, child in action.choices.items():
            sub = [a for a in child._actions if isinstance(a, argparse._SubParsersAction)]
            if sub and sub[0].choices:
                yield from leaves(child, path + [name])
            else:
                yield path + [name], name, child


class AdsLockTest(unittest.TestCase):
    """잠금 자체(ADS_WRITE_LOCKED=True)를 검증한다. a 만 잠금을 내리고(setUp), b~f 는 실제 잠금 상태."""

    def lock_off(self):
        # 잠금은 코드 상수라 테스트에서만 내린다(환경변수·플래그로는 못 푼다).
        return mock.patch.object(ads_write, 'ADS_WRITE_LOCKED', False)

    def assertLocked(self, session, message=ads_write.LOCK_MESSAGE):
        self.assertEqual(session.calls, [], '잠금인데 요청이 나감: ' + json.dumps(session.calls))
        self.assertIn(message, ads_write.LOCK_MESSAGE)

    # a. cli: 12개 쓰기 명령(--yes / dry-run) → 69, stderr LOCK_MESSAGE, 요청 0
    def test_cli_refuses_every_write_command_with_yes(self):
        # 패치 없음 = 실제 잠금. ADS_WRITE_LOCKED 를 False 로 바꾸면 여기서 69 가 안 나와 실패한다.
        for command in LOCKED_COMMANDS:
            session = FakeSession()
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = ads_write.cli(list(command) + ['--yes'] + LOCK_ARGS, session=session)
            self.assertEqual(code, 69, command)
            self.assertIn(ads_write.LOCK_MESSAGE, err.getvalue(), command)
            self.assertLocked(session)

    def test_cli_refuses_every_write_command_in_dry_run(self):
        for command in LOCKED_COMMANDS:
            session = FakeSession()
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = ads_write.cli(list(command) + LOCK_ARGS, session=session)
            self.assertEqual(code, 69, command)
            self.assertIn(ads_write.LOCK_MESSAGE, err.getvalue(), command)
            self.assertLocked(session)

    # a2. 단일 관문 send() 가 먼저 막는다: run()·create_plan() 없이 cli() 만 거쳐도 요청 0.
    #     ADS_WRITE_LOCKED=False 로 잠금을 내리면 여기서 실패한다.
    def test_cli_sends_nothing_with_the_lock_read_only_at_send(self):
        for command in LOCKED_COMMANDS:
            session = FakeSession()
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                code = ads_write.cli(list(command) + ['--yes'] + LOCK_ARGS, session=session)
            self.assertEqual(session.calls, [], '잠금인데 요청이 나감: %s → %s' % (command, session.calls))
            self.assertEqual(code, 69, command)

    # b. run(): 잠금 그룹은 parse 직후 AdsLocked, 요청 0
    def test_run_raises_ads_locked_and_sends_nothing(self):
        for command in LOCKED_COMMANDS:
            session = FakeSession()
            with self.assertRaises(ads_write.AdsLocked, msg=str(command)) as caught:
                ads_write.run(list(command) + LOCK_ARGS, session, dry_run=False)
            self.assertIn(ads_write.LOCK_MESSAGE, str(caught.exception))
            self.assertLocked(session)

    # c. send(): 네트워크 쓰기 단일 관문
    def test_send_raises_ads_locked_and_sends_nothing(self):
        session = FakeSession()
        with self.assertRaises(ads_write.AdsLocked) as caught:
            ads_write.send(session, ads_write.CAMPAIGN_UPDATE, {'id': 3791, 'title': 'x'})
        self.assertEqual(str(caught.exception), ads_write.LOCK_MESSAGE)
        self.assertEqual(session.calls, [])

    # d. 캠페인 생성 하드 차단: ADS_WRITE_LOCKED=False + 환경변수를 넣어도 거절
    def test_campaign_create_is_hard_blocked_with_lock_patched_off_and_env(self):
        create = ['campaign', 'create', '--name', 'x', '--budget', '30', '--bid', '0.5', '--products', '1',
                  '--keywords', 'a', '--start-billing', '--keep-on', '--yes'] + LOCK_ARGS
        with mock.patch.object(ads_write, 'ADS_WRITE_LOCKED', False), \
                mock.patch.dict(os.environ, {'ALIBABA_ADS_UNLOCK': '1', 'ADS_WRITE_LOCKED': '0'}):
            self.assertFalse(ads_write.ADS_WRITE_LOCKED)
            session = FakeSession()
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = ads_write.cli(list(create), session=session)
            self.assertEqual(code, 69)
            self.assertIn('캠페인 생성은 하드 차단', err.getvalue())
            self.assertLocked(session)
            args = ads_write.build_parser().parse_args([a for a in create if a != '--yes'])
            with self.assertRaises(ads_write.AdsLocked) as caught:
                ads_write.create_plan(args)
            self.assertIn('하드 차단', str(caught.exception))
            with self.assertRaises(ads_write.AdsLocked) as caught:
                ads_write.execute_create(FakeSession(), {'action': 'campaign.create'})
            self.assertIn('하드 차단', str(caught.exception))
            with self.assertRaises(ads_write.AdsLocked) as caught:
                ads_write.run([a for a in create if a != '--yes'], FakeSession(), dry_run=False)
            self.assertIn('하드 차단', str(caught.exception))

    # a3. 반려 1회차 회귀: 옵션을 명령 앞에 두면 cli() 의 토큰 선검사가 잠금을 놓쳐 뒷단(--account 검사)까지
    #     진행하던 경로. 이제 잠금 판단은 파싱 결과만 보므로 load_account·세션 생성이 0 이어야 한다.
    def test_global_options_before_command_still_lock(self):
        no_load = mock.patch.object(ads_write, 'load_account', side_effect=AssertionError('load_account 호출됨'))
        for argv in OPTIONS_FIRST_COMMANDS:
            with self.subTest(argv=argv):
                session = FakeSession()
                err = io.StringIO()
                with no_load, contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    code = ads_write.cli(list(argv), session=session)
                # 잠금이면 69(LOCK_MESSAGE). --acc 축약은 argparse 가 --account 로 못 읽어 64 가 될 수도 있지만
                # (그 경우도 계정 접근·요청 0), 잠금이 파싱 뒤에 있으니 정상 경로는 69 다.
                self.assertIn(code, (69, 64), (argv, err.getvalue()))
                if code == 69:
                    self.assertIn(ads_write.LOCK_MESSAGE, err.getvalue(), argv)
                else:
                    self.assertIn('사용법', err.getvalue(), argv)
                self.assertEqual(session.calls, [], '잠금인데 요청이 나감: %s → %s' % (argv, session.calls))

    # a4. 같은 목록을 run() 직접 호출로: AdsLocked(또는 parse Usage) + load_account 0
    def test_run_global_options_before_command_still_lock(self):
        no_load = mock.patch.object(ads_write, 'load_account', side_effect=AssertionError('load_account 호출됨'))
        for argv in OPTIONS_FIRST_COMMANDS:
            with self.subTest(argv=argv):
                session = FakeSession()
                with no_load, contextlib.redirect_stderr(io.StringIO()):
                    try:
                        ads_write.run([a for a in argv if a != '--yes'], session, dry_run=False)
                    except ads_write.AdsLocked as error:
                        self.assertIn(ads_write.LOCK_MESSAGE, str(error), argv)
                    except ads_write.Usage:
                        pass    # --acc 축약은 파싱 단계에서 64 — 잠금 이전에 끝나도 계정 접근은 없어야 한다
                    else:
                        self.fail('%s: 잠금/사용법 오류 없이 진행됨' % (argv,))
                self.assertEqual(session.calls, [], '잠금인데 요청이 나감: %s → %s' % (argv, session.calls))

    def test_lock_precedes_blocked_account_and_config_read(self):
        # 차단 계정 + 없는 config: 잠금이 먼저면 계정 파일을 열지도 않고 69(77/예외 아님).
        for name in ('wonnischoice', 'linea'):
            argv = ['campaign', 'set', '3791', '--name', 'x', '--account', name,
                    '--config', '/nonexistent/x.json']
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ads_write.cli(argv), 69, name)
            self.assertEqual(err.getvalue().strip(), ads_write.LOCK_MESSAGE)

    # e. backups 는 잠금 대상 아님: 없으면 0, 있으면 파일 목록
    def test_backups_is_not_locked(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.write_text(json.dumps({'thedozers': {'loginId': 'seller@example.com', 'outDir': directory}}),
                              encoding='utf-8')
            argv = ['backups', '--account', 'thedozers', '--config', str(config)]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(ads_write.cli(argv), 0)
            self.assertIn('백업 없음', out.getvalue())
            self.assertEqual(ads_write.run(list(argv), FakeSession()), 0)
            backups = Path(directory) / 'ads-backups'
            backups.mkdir()
            record = {'account': 'thedozers', 'campaignId': 3791, 'action': 'campaign.rename',
                      'createdAt': '2026-11-19T00:00:00', 'before': {'title': 'Old'}, 'request': []}
            (backups / 'thedozers-3791-20261119T000000-000001.json').write_text(json.dumps(record), encoding='utf-8')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(ads_write.cli(argv), 0)
            self.assertIn('campaign.rename', out.getvalue())

    def test_help_exits_zero_and_shows_lock_first(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                ads_write.build_parser().parse_args(['--help'])
        self.assertEqual(caught.exception.code, 0)
        text = out.getvalue()
        self.assertIn('광고 조작: 이용 불가', text.split('usage:')[1])
        for group in ('campaign', 'products', 'keywords', 'restore'):
            self.assertIn('[잠금·이용 불가]', text, group)
        self.assertNotIn('[잠금', [line for line in text.splitlines() if line.strip().startswith('backups')][0])
        # 하위 help: campaign create 첫 줄, backups 그대로
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                ads_write.build_parser().parse_args(['campaign', 'create', '--help'])
        self.assertEqual(caught.exception.code, 0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit):
                ads_write.build_parser().parse_args(['campaign', '--help'])
        self.assertIn('[하드 차단·이용 불가] 수동 캠페인 생성', out.getvalue())
        # cli 경유 --help 도 0 이고 잠금 거절이 아니다
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ads_write.cli(['campaign', 'set', '--help']), 0)
            self.assertEqual(ads_write.cli(['campaign', 'create', '--help']), 0)

    # f. 잠금 커버리지 자기검증: 새 쓰기 명령이 생기면 여기서 잡힌다
    def test_every_leaf_command_is_covered_by_the_lock_list(self):
        covered = set(['backups'])   # 잠금 아님을 확인하는 쪽(아래에서 별도로 검사)
        for command in LOCKED_COMMANDS:
            group = next(token for token in command if not token.startswith('-'))
            rest = command[command.index(group) + 1:]
            sub = next((token for token in rest if not token.startswith('-') and token.isalpha()), group)
            covered.add(group if group in ('restore', 'backups') else '%s %s' % (group, sub))
        self.assertEqual(covered, {'campaign set', 'campaign create', 'products add', 'products remove',
                                   'products pause', 'products resume', 'keywords add', 'keywords remove',
                                   'keywords pause', 'keywords resume', 'keywords bid', 'restore', 'backups'})
        seen = set()
        for path, name, parser in leaves(ads_write.build_parser()):
            key = ' '.join(path)
            seen.add(key)
            if key == 'backups':
                self.assertNotIn('[잠금', parser.format_help())
                continue
            self.assertIn(key, covered, '%s 가 잠금 목록에 없다 — LOCKED_GROUPS/LOCKED_COMMANDS 확인' % key)
            # 렌더된 도움말로 확인: 그 leaf 줄 / 그룹 help 에 잠금 표시가 실제로 찍히는지
            # (leaf 단독 --help 는 부모 목록을 안 보여주므로 부모 help 에서 그 줄을 본다)
            parent = ' '.join(path[:-1])
            argv = ([parent] if parent else []) + ['--help']
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    ads_write.build_parser().parse_args(argv)
            line = [l for l in out.getvalue().splitlines()
                    if l.strip().startswith(name) and ('[잠금' in l or '[하드 차단' in l)]
            self.assertTrue(line, '%s 도움말에 잠금 표시 없음: %s' % (key, out.getvalue()))
        self.assertEqual(seen, covered)


if __name__ == '__main__':
    unittest.main()