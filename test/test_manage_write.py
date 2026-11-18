# test-only: all identifiers and amounts below are synthetic, never seller data.
"""Manage write engine (광고 밖 셀러센트럴 조작): fake HTTP responses, no network, no account, no token."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
import manage_write

PRODUCTS = [
    {'id': 11000040100479, 'subject': 'Product A', 'status': 'on', 'groupId': 917012724, 'groupName': 'Group A'},
    {'id': 11000040100480, 'subject': 'Product B', 'status': 'off', 'groupId': 917012725, 'groupName': 'Group B'},
]
GROUPS = [{'id': 917012724, 'name': 'Group A'}, {'id': 917012725, 'name': 'Group B'}]
SHOWCASE = [{'id': 13412900717, 'productId': 11000040100479, 'onlineStatus': 1, 'index': 1},
            {'id': 13412900718, 'productId': 11000040100480, 'onlineStatus': 1, 'index': 2}]
TEMPLATE = {'title': 'Smart Template', 'ftId': '10000013845001', 'dispatchLocation': 'KR',
            'dispatchZipCode': '01000', 'templateType': 'smartEntrust',
            'carrierList': [{'carrierCode': 'KRGS_ASP_KRYD_EXPRESS_BEAUTY-CN',
                             'dispatchZipCode': '01000', 'dispatchLocation': 'KR', 'hasSurcharge': None,
                             'detailList': [{'freightType': 'standard', 'countries': ['CN'],
                                             'minFreightTime': 3, 'maxFreightTime': 6,
                                             'freightDetailsDTO': {'currency': 'USD', 'discount': 120}}],
                             'dropShipping': None, 'tradeTerm': 'DAP'}]}
VERSIONS = [{'pageId': 200005267007, 'pageName': 'Ministore_260510', 'online': 1},
            {'pageId': 200005267008, 'pageName': 'capture-test', 'online': 0}]
PRIVACY = {'0': {'3': {}}, '9': {'2': {'1': '9000176342799', '2': '10001'}}}


def reply(value, status=200, text=None):
    return {'status': status, 'text': text if text is not None else json.dumps(value),
            'ct': 'application/json', 'redirectHost': None}


class FakeSession:
    """Answers each request by path; every call is recorded so tests can assert what was sent."""
    transport = 'api'
    metrics = {}

    def __init__(self, apply=True, write=None):
        self.products = json.loads(json.dumps(PRODUCTS))
        self.groups = json.loads(json.dumps(GROUPS))
        self.showcase = json.loads(json.dumps(SHOWCASE))
        self.searches = [{'id': 3106072044, 'keyword': 'serum', 'atm': 'N'}]
        self.coupons = []
        self.activities = [{'id': 900000011, 'status': 'ONGOING'}]
        self.template = json.loads(json.dumps(TEMPLATE))
        self.versions = json.loads(json.dumps(VERSIONS))
        self.privacy = json.loads(json.dumps(PRIVACY))
        self.message = None
        self.default_ft = '10000013845001'
        self.apply = apply
        self.write = write or (lambda path, data: {'success': True})
        self.calls = []

    def cookie(self, name, url):
        return {'ctoken': 'cookie-ctoken-value', '_tb_token_': 'cookie-tb-value'}.get(name)

    def _data(self, params):
        raw = (params or {}).get('data')
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except ValueError:
                return None
        return raw

    def request(self, method, url, query=None, params=None, encoding='form', referer=None):
        query, params = dict(query or {}), dict(params or {})
        self.calls.append({'method': method, 'url': url, 'query': query, 'params': params,
                           'encoding': encoding, 'referer': referer})
        path = url.split('alibaba.com', 1)[1] if 'alibaba.com' in url else url
        host = url.split('//', 1)[1].split('.alibaba.com')[0] + '.alibaba.com'

        # products
        if path.endswith('/product/managementproducts/asyQueryProductsList.do'):
            return reply({'rows': self.products, 'total': len(self.products)})
        if path.endswith('/product/managementproducts/asyChangeDisplayStatus.do'):
            if self.apply:
                want = params.get('newStatus')
                for pid in str(params.get('productIds', '')).split(','):
                    for row in self.products:
                        if str(row['id']) == pid:
                            row['status'] = want
            return reply(self.write(path, params))
        if path.endswith('/product/group_ajax.do'):
            if self.apply:
                gid = str(params.get('groupId'))
                name = next((g['name'] for g in self.groups if str(g['id']) == gid), None)
                for pid in str(params.get('productIdList', '')).split(','):
                    for row in self.products:
                        if str(row['id']) == pid:
                            row['groupId'], row['groupName'] = int(gid), name
            return reply(self.write(path, params))
        if path.endswith('/product/managementproducts/asyDelete.do'):
            if self.apply:
                drop = {str(p) for p in str(params.get('productIds', '')).split(',')}
                self.products = [r for r in self.products if str(r['id']) not in drop]
            return reply(self.write(path, params))
        if path.endswith('/product/group_query_ajax.do'):
            return reply({'data': self.groups})

        # showcase / campaign
        if path.endswith('/api/campaign/find/showcase'):
            return reply({'status': 1000, 'ok': True, 'data': 379784541})
        if path.endswith('/adgroup'):
            return reply({'status': 1000, 'ok': True, 'data': self.showcase})
        if path.endswith('/adgroup/create'):
            data = self._data(params) or {}
            if self.apply:
                for item in data.get('adGroupOperationList', []):
                    self.showcase.append({'id': 13412900900 + len(self.showcase),
                                          'productId': item['productId'], 'onlineStatus': 1,
                                          'index': len(self.showcase) + 1})
            return reply(self.write(path, data))
        if path.endswith('/adgroup/delete'):
            data = self._data(params) or {}
            if self.apply:
                drop = {i['id'] for i in data.get('adGroupOperationList', [])}
                self.showcase = [g for g in self.showcase if g['id'] not in drop]
            return reply(self.write(path, data))
        if path.endswith('/adgroup/replace'):
            data = self._data(params) or {}
            if self.apply:
                for group in self.showcase:
                    if group['id'] == data.get('id'):
                        group['productId'] = data.get('newEntityId')
            return reply(self.write(path, data))
        if path.endswith('/adgroup/sort'):
            data = self._data(params) or {}
            if self.apply:
                item = next((g for g in self.showcase if g['id'] == data.get('id')), None)
                if item is not None:
                    self.showcase.remove(item)
                    self.showcase.insert(int(data['targetIndex']) - 1, item)
                    for i, group in enumerate(self.showcase):
                        group['index'] = i + 1
            return reply(self.write(path, data))

        # rfq
        if path.endswith('/rfq/ajax/my_search_list_ajax.do'):
            return reply({'rows': self.searches})
        if path.endswith('/rfq/mySearch/rfq_my_search_ajax.do'):
            keyword = json.loads(query.get('params') or '{}').get('keyword')
            if self.apply:
                self.searches.append({'id': 3106072045, 'keyword': keyword, 'atm': 'N'})
            return reply({'success': True})
        if path.endswith('/rfq/mySearch/RfqMySearchNotifyAjax.do'):
            if self.apply:
                for item in self.searches:
                    if str(item['id']) == str(query.get('searchId')):
                        item['atm'] = query.get('atm')
            return reply({'success': True})
        if path.endswith('/rfq_quotation_post_ajax.do') or path.endswith('/rfq_quotation_draft_post_ajax.do'):
            return reply(self.write(path, params))

        # promo
        if path.endswith('/coupon/v2/createSellerCouponAjax.do'):
            if self.apply:
                self.coupons.append({'couponTemplateId': 900000002, 'name': params.get('name'),
                                     'status': 'ISSUING'})
            return reply(self.write(path, params))
        if path.endswith('/coupon/v2/terminateApplyAjax.do'):
            if self.apply:
                for coupon in self.coupons:
                    if str(coupon['couponTemplateId']) == str(query.get('couponTemplateId')):
                        coupon['status'] = 'TERMINATED'
            return reply(self.write(path, query))
        if path.endswith('/coupon/v2/querySellerCouponListAjax.do'):
            return reply({'data': self.coupons})
        if path.endswith('/promotion/earlyTerminateActivityAjax.do'):
            if self.apply:
                for activity in self.activities:
                    if str(activity['id']) == str(params.get('activityId')):
                        activity['status'] = 'ENDED'
            return reply(self.write(path, params))
        if path.endswith('/promotion/querySellerActivityListAjax.do'):
            return reply({'data': self.activities})
        if path.endswith('/enroll/sign.do'):
            return reply(self.write(path, query))

        # shipping templates
        if path.endswith('/luyou/ggs/getTemplateList.json'):
            return reply({'data': [{'ftId': self.template['ftId'], 'title': self.template['title'],
                                    'isDefault': '1' if str(self.template['ftId']) == str(self.default_ft) else '0'}]})
        if path.endswith('/luyou/ggs/shippingTemplates/getTemplateInfoOverview.json'):
            return reply({'data': self.template})
        if path.endswith('/luyou/ggs/updateTemplate.json'):
            body = json.loads(params.get('json') or '{}')
            if self.apply:
                self.template = body
            return reply(self.write(path, body))
        if path.endswith('/luyou/ggs/addTemplate.json'):
            body = json.loads(params.get('json') or '{}')
            return reply(self.write(path, body))
        if path.endswith('/luyou/settemplateDefault.json'):
            body = json.loads(params.get('json') or '{}')
            if self.apply:
                self.default_ft = body.get('id')
            return reply(self.write(path, body))
        if path.endswith('/shippingTemplates/verify.json'):
            return reply({'success': True})

        # store
        if path.endswith('/page/list/keyword/packageId'):
            return reply({'data': self.versions})
        if path.endswith('/csrf/ajax'):
            return reply({'parameterName': '_csrf_token_', 'token': 'csrf-value'})
        if path.endswith('/page/editPage'):
            if self.apply:
                for version in self.versions:
                    if str(version['pageId']) == str(params.get('pageId')):
                        version['pageName'] = params.get('pageName')
            return reply(self.write(path, params))
        if path.endswith('/publish'):
            if self.apply:
                for version in self.versions:
                    version['online'] = 1 if str(version['pageId']) == str(query.get('pageId')) else 0
            return reply(self.write(path, query))

        # settings
        if path.endswith('/feedbackset/index.htm'):
            body = json.loads(params.get('json') or '{}')
            self.message = body
            return reply(self.write(path, body))
        if path.endswith('/privacy/privacySettingShow.htm'):
            return reply(None, text='<script>var privaceSetting = %s;</script>' % json.dumps(self.privacy))
        if path.endswith('/ajax/privacy/privacy_setting_update.do'):
            if self.apply:
                self.privacy = json.loads(params.get('privaceSetting') or '{}')
            return reply(self.write(path, params))

        # reviews
        if path.endswith('/review/action/inviteReviewAction.do'):
            return reply(self.write(path, params))

        raise AssertionError('unexpected url ' + url)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.config = Path(self.dir.name) / 'config.json'
        self.config.write_text(json.dumps({'thedozers': {'loginId': 'seller@example.com', 'outDir': self.dir.name}}),
                               encoding='utf-8')
        self.backups = Path(self.dir.name) / 'manage-backups'

    def tearDown(self):
        self.dir.cleanup()

    def run_cli(self, argv, session, dry_run=True):
        argv = list(argv) + ['--account', 'thedozers', '--config', str(self.config)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = manage_write.run(argv, session, dry_run=dry_run)
            except manage_write.Usage as error:
                code, _ = 64, err.write(str(error))
            except manage_write.Unverified as error:
                code, _ = 64, err.write(str(error))
        return code, out.getvalue(), err.getvalue()

    READS = ('/api/campaign/find/showcase', 'asyQueryProductsList.do', 'group_query_ajax.do',
             'my_search_list_ajax.do', 'querySellerCouponListAjax.do', 'querySellerActivityListAjax.do',
             'getTemplateList.json', 'getTemplateInfoOverview.json', 'page/list/keyword/packageId',
             '/csrf/ajax', 'privacySettingShow.htm')

    def writes(self, session):
        """상태를 바꾸는 요청만(읽기 GET·명시한 읽기 경로는 뺀다)."""
        return [c for c in session.calls
                if c['method'] != 'GET' and not c['url'].endswith('/adgroup')
                and not any(s in c['url'] for s in self.READS)]

    # --- dry-run -----------------------------------------------------------------------------------
    def test_products_offline_dry_run_shows_grade_and_request_without_writing(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['products', 'offline', '--ids', '11000040100479'], session)
        self.assertEqual(code, 0, out)
        self.assertIn('되돌릴 수 있음', out.splitlines()[0])
        self.assertIn('asyChangeDisplayStatus', out)
        self.assertIn('"newStatus": "off"', out)
        self.assertIn('dry-run', out)
        self.assertEqual(self.writes(session), [])
        self.assertEqual(session.products[0]['status'], 'on')
        self.assertFalse(self.backups.exists())
        self.assertNotIn('_tb_token_', out)

    def test_products_delete_grade_is_irreversible_and_restore_is_refused(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['products', 'delete', '--ids', '11000040100479'], session)
        self.assertEqual(code, 0, out)
        self.assertIn('되돌릴 수 없음', out.splitlines()[0])
        self.assertIn('불가', out)
        self.assertEqual(self.writes(session), [])
        code, out, _ = self.run_cli(['products', 'delete', '--ids', '11000040100479'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        backup = next(self.backups.glob('*.json'))
        self.assertIn('불가', json.loads(backup.read_text(encoding='utf-8'))['restore']['note'])
        self.assertNotIn('11000040100479', [str(p['id']) for p in session.products])

    def test_yes_writes_after_backup_and_reads_back(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['products', 'offline', '--ids', '11000040100479'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('백업: ', out)
        self.assertIn('리드백 일치', out)
        record = json.loads(next(self.backups.glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(record['action'], 'products.offline')
        self.assertEqual(record['restore']['argv'], ['products', 'online', '--ids', '11000040100479'])
        self.assertEqual(record['request'][0]['params']['newStatus'], 'off')
        self.assertNotIn('token', json.dumps(record).lower())

    def test_readback_mismatch_exits_three(self):
        session = FakeSession(apply=False)
        code, out, _ = self.run_cli(['products', 'offline', '--ids', '11000040100479'], session, dry_run=False)
        self.assertEqual(code, 3, out)
        self.assertIn('리드백 불일치', out)

    def test_write_refusal_exits_one(self):
        session = FakeSession(write=lambda path, data: {'success': False, 'message': 'nope'})
        code, out, _ = self.run_cli(['products', 'offline', '--ids', '11000040100479'], session, dry_run=False)
        self.assertEqual(code, 1, out)
        self.assertIn('실패', out)

    def test_group_resolves_name_and_restores_original_group(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['products', 'group', '--ids', '11000040100479', '--group', 'Group B'], session,
                                    dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(session.products[0]['groupName'], 'Group B')
        record = json.loads(next(self.backups.glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(json.loads(record['restore']['requests'][0]['params']['groupId']), 917012724)
        self.assertEqual(self.writes(session)[0]['params']['productIdList'], '11000040100479')

    def test_unknown_group_is_usage_error_before_write(self):
        session = FakeSession()
        code, _, err = self.run_cli(['products', 'group', '--ids', '11000040100479', '--group', 'Nope'], session)
        self.assertEqual(code, 64)
        self.assertIn('Nope', err)
        self.assertEqual(self.writes(session), [])

    # --- showcase ----------------------------------------------------------------------------------
    def test_showcase_add_uses_campaign_from_read_and_slot_limit(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['showcase', 'add', '--ids', '11000040100500'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        create = [c for c in self.writes(session) if c['url'].endswith('/adgroup/create')]
        self.assertEqual(len(create), 1)
        self.assertIn('/api/campaign/379784541/adgroup/create', create[0]['url'])
        data = json.loads(create[0]['params']['data'])
        self.assertEqual(data['campaignType'], 9)
        self.assertEqual(data['adGroupOperationList'], [{'productId': 11000040100500, 'onlineStatus': 1}])
        self.assertEqual(create[0]['params']['type'], 'normal')
        self.assertEqual(create[0]['params']['ads'], {'productLineId': 110101, 'productId': 110101009})
        self.assertEqual([g['productId'] for g in session.showcase][-1], 11000040100500)

    def test_showcase_slot_limit_is_refused(self):
        session = FakeSession()
        session.showcase = [{'id': 13412900000 + i, 'productId': 11000041000000 + i, 'onlineStatus': 1,
                             'index': i + 1} for i in range(20)]
        code, _, err = self.run_cli(['showcase', 'add', '--ids', '11000040100500'], session, dry_run=False)
        self.assertEqual(code, 64)
        self.assertIn('한도 20', err)
        self.assertEqual(self.writes(session), [])

    def test_showcase_remove_resolves_adgroup_id(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['showcase', 'remove', '--ids', '11000040100480'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        data = json.loads([c for c in self.writes(session) if c['url'].endswith('/delete')][0]['params']['data'])
        self.assertEqual(data['adGroupOperationList'], [{'id': 13412900718}])
        self.assertEqual([g['id'] for g in session.showcase], [13412900717])

    def test_showcase_replace_and_sort_bodies(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['showcase', 'replace', '--ids', '13412900717', '--to', '11000040100500'],
                                    session, dry_run=False)
        self.assertEqual(code, 0, out)
        body = json.loads([c for c in self.writes(session) if c['url'].endswith('/replace')][0]['params']['data'])
        self.assertEqual(body, {'adgroupStatus': 1, 'id': 13412900717, 'oldEntityId': 11000040100479,
                                'newEntityId': 11000040100500})
        session = FakeSession()
        code, out, _ = self.run_cli(['showcase', 'sort', '--ids', '13412900717', '--to-index', '2'], session,
                                    dry_run=False)
        self.assertEqual(code, 0, out)
        body = json.loads([c for c in self.writes(session) if c['url'].endswith('/sort')][0]['params']['data'])
        self.assertEqual(body, {'onlineStatus': 1, 'id': 13412900717, 'srcIndex': 1, 'targetIndex': 2})
        backup = sorted(self.backups.glob('*.json'))[-1]
        self.assertEqual(json.loads(backup.read_text(encoding='utf-8'))['restore']['argv'][-1], '1')

    # --- rfq ---------------------------------------------------------------------------------------
    def test_search_add_is_irreversible_and_uses_jsonp_query(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['rfq', 'search-add', '--keyword', 'vitamin c'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('되돌릴 수 없음', out.splitlines()[0])
        call = next(c for c in session.calls if c['url'].endswith('rfq_my_search_ajax.do'))
        self.assertEqual(json.loads(call['query']['params'])['keyword'], 'vitamin c')
        self.assertEqual(call['query']['type'], 'new')
        self.assertEqual([s['keyword'] for s in session.searches], ['serum', 'vitamin c'])

    def test_search_notify_toggles_and_restores(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['rfq', 'search-notify', 'on', '--search', '3106072044'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(session.searches[0]['atm'], 'Y')
        backup = json.loads(next(self.backups.glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(backup['restore']['argv'], ['rfq', 'search-notify', 'on', '--search', '3106072044'])

    def test_quote_body_has_captured_keys(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['rfq', 'quote', '--rfq', '1685609002', '--price', '2.5', '--quantity', '10',
                                     '--port', 'BUSAN', '--payment', 'T/T'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('되돌릴 수 없음', out.splitlines()[0])
        form = json.loads(self.writes(session)[0]['params']['formData'])
        self.assertEqual(form['rfqId'], 1685609002)
        self.assertEqual(form['product'][0]['price'], [{'quantity': 10, 'fobPrice': 2.5}])
        self.assertEqual(form['product'][0]['port'], 'BUSAN')
        self.assertEqual(form['layout'] if 'layout' in form else form['quoOpen'], 'N')

    # --- promo -------------------------------------------------------------------------------------
    def test_coupon_create_uses_cents_and_restore_is_stop(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['promo', 'coupon', 'create', '--name', 'Spring', '--shop', '286234217',
                                     '--start-money', '100', '--discount-money', '5'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('비용/외부 영향', out.splitlines()[0])
        params = self.writes(session)[0]['params']
        self.assertEqual(params['startMoney'], 10000)
        self.assertEqual(params['discountMoney'], 500)
        self.assertEqual(json.loads(params['couponParticipateList']), [{'rangeType': 'shop', 'participateId': 286234217}])
        self.assertEqual(session.coupons[0]['name'], 'Spring')
        backup = json.loads(next(self.backups.glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(backup['restore']['argv'], ['promo', 'coupon', 'stop', '--id', '900000002'])

    def test_coupon_stop_and_discount_end_are_irreversible(self):
        session = FakeSession()
        session.coupons = [{'couponTemplateId': 900000002, 'name': 'x', 'status': 'ISSUING'}]
        code, out, _ = self.run_cli(['promo', 'coupon', 'stop', '--id', '900000002'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(session.coupons[0]['status'], 'TERMINATED')
        session = FakeSession()
        code, out, _ = self.run_cli(['promo', 'discount', 'end', '--id', '900000011'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(session.activities[0]['status'], 'ENDED')
        self.assertEqual(self.writes(session)[0]['params'], {'activityId': 900000011})

    def test_event_enroll_signs_with_topic_and_protocol(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['promo', 'event', 'enroll', '--topic', '123715', '--protocol', '2822401'],
                                    session, dry_run=False)
        self.assertEqual(code, 0, out)
        call = session.calls[-1]
        self.assertEqual(call['query'], {'topicId': 123715, 'protocolId': 2822401})

    # --- shipping ----------------------------------------------------------------------------------
    def test_shipping_rename_preserves_every_other_field(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['shipping', 'rename', '--template', 'Smart Template', '--to', 'New Name'],
                                    session, dry_run=False)
        self.assertEqual(code, 0, out)
        sent = json.loads(self.writes(session)[0]['params']['json'])
        self.assertEqual(sent['title'], 'New Name')
        self.assertEqual(sent['carrierList'], TEMPLATE['carrierList'])
        self.assertEqual(sent['ftId'], TEMPLATE['ftId'])
        self.assertEqual(sent['templateType'], TEMPLATE['templateType'])
        self.assertEqual(session.template['title'], 'New Name')

    def test_shipping_price_ratio_changes_only_discount(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['shipping', 'price-ratio', '--template', 'Smart Template', '--ratio', '1.3'],
                                    session, dry_run=False)
        self.assertEqual(code, 0, out)
        sent = json.loads(self.writes(session)[0]['params']['json'])
        self.assertEqual(sent['carrierList'][0]['detailList'][0]['freightDetailsDTO']['discount'], 130)
        self.assertEqual(sent['carrierList'][0]['detailList'][0]['countries'], ['CN'])
        self.assertEqual(sent['carrierList'][0]['carrierCode'], 'KRGS_ASP_KRYD_EXPRESS_BEAUTY-CN')

    def test_shipping_auto_route_refuses_yes(self):
        session = FakeSession()
        code, out, err = self.run_cli(['shipping', 'auto-route', 'on', '--template', 'Smart Template'], session,
                                      dry_run=False)
        self.assertEqual(code, 64, out + err)
        self.assertIn('형식 미검증', out + err)
        self.assertEqual(self.writes(session), [])

    def test_shipping_default_and_add_template(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['shipping', 'default', '--template', 'Smart Template'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.writes(session)[0]['encoding'], 'json')
        session = FakeSession()
        code, out, _ = self.run_cli(['shipping', 'add-template', '--title', 'New Template',
                                     '--countries', 'US,CA'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('되돌릴 수 없음', out.splitlines()[0])
        body = json.loads(self.writes(session)[0]['params']['json'])
        self.assertEqual(body['title'], 'New Template')
        self.assertEqual(body['ftId'], None)

    def test_carrier_add_appends_entry_and_keeps_old_carriers(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['shipping', 'carrier', 'add', '--template', 'Smart Template',
                                     '--countries', 'US,CA'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        update = [c for c in self.writes(session) if c['url'].endswith('updateTemplate.json')][0]
        body = json.loads(update['params']['json'])
        self.assertEqual([c['carrierCode'] for c in body['carrierList']],
                         ['KRGS_ASP_KRYD_EXPRESS_BEAUTY-CN', 'GGS_SELLER_LOGISTICS_OTHER'])
        self.assertEqual(body['carrierList'][1]['detailList'][0]['countries'], ['US', 'CA'])
        self.assertEqual(body['carrierList'][0], TEMPLATE['carrierList'][0])

    # --- store -------------------------------------------------------------------------------------
    def test_store_version_rename_and_publish(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['store', 'version', 'rename', '--version', 'capture-test', '--to', 'v2'],
                                    session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.writes(session)[0]['params'],
                         {'pageType': 6, 'pageId': 200005267008, 'pageName': 'v2'})
        session = FakeSession()
        code, out, _ = self.run_cli(['store', 'version', 'publish', '--version', 'capture-test'], session,
                                    dry_run=False)
        self.assertEqual(code, 0, out)
        publish = [c for c in self.writes(session) if c['url'].endswith('/publish')][0]
        self.assertEqual(publish['query']['pageId'], 200005267008)
        self.assertEqual(publish['query']['pageType'], 6)
        self.assertEqual(publish['query']['_csrf_token_'], 'csrf-value')
        self.assertNotIn('csrf-value', out)
        backup = json.loads(max(self.backups.glob('*.json'), key=lambda p: p.stat().st_mtime_ns)
                            .read_text(encoding='utf-8'))
        self.assertEqual(backup['restore']['argv'], ['store', 'version', 'publish', '--version', 'Ministore_260510'])

    # --- settings / reviews ------------------------------------------------------------------------
    def test_settings_message_keeps_captured_fields(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['settings', 'message', '--contact', '286234217', '--cc', 'off'], session,
                                    dry_run=False)
        self.assertEqual(code, 0, out)
        body = json.loads(self.writes(session)[0]['params']['json'])
        self.assertEqual(body['cmd'], 'updateForm')
        self.assertEqual(body['params']['defaultContact'], 286234217)
        self.assertFalse(body['params']['cc'])
        self.assertEqual(body['params']['rule'], 'product')
        self.assertEqual(body['params']['highLevelBuyerTurn'], False)

    def test_settings_privacy_only_changes_given_path(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['settings', 'privacy', '--set', '9.2.2=20002'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(session.privacy['9']['2']['2'], 20002)
        self.assertEqual(session.privacy['9']['2']['1'], '9000176342799')
        self.assertEqual(session.privacy['0'], PRIVACY['0'])

    def test_reviews_invite_is_irreversible_and_reads_compose(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['reviews', 'invite', '--order', '312629763501229853', '--comment', 'please'],
                                    session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('바이어', out.splitlines()[0])
        call = self.writes(session)[0]
        self.assertEqual(call['query'], {'reviewEntity': 'TAOrder', 'entityId': 312629763501229853})
        self.assertEqual(call['params'], {'inviteComment': 'please'})

    # --- restore -----------------------------------------------------------------------------------
    def test_restore_replays_the_recorded_command(self):
        session = FakeSession()
        self.run_cli(['products', 'offline', '--ids', '11000040100479'], session, dry_run=False)
        self.assertEqual(session.products[0]['status'], 'off')
        backup = next(self.backups.glob('*.json'))
        code, out, err = self.run_cli(['restore', str(backup)], session, dry_run=False)
        self.assertEqual(code, 0, out + err)
        self.assertIn('복원 대상', out)
        self.assertEqual(session.products[0]['status'], 'on')

    def test_restore_of_irreversible_action_exits_64_with_reason(self):
        session = FakeSession()
        self.run_cli(['products', 'delete', '--ids', '11000040100479'], session, dry_run=False)
        backup = next(self.backups.glob('*.json'))
        code, _, err = self.run_cli(['restore', str(backup)], session, dry_run=False)
        self.assertEqual(code, 64)
        self.assertIn('불가', err)

    def test_restore_by_request_body_reverts_template(self):
        session = FakeSession()
        self.run_cli(['shipping', 'rename', '--template', 'Smart Template', '--to', 'Renamed'], session, dry_run=False)
        self.assertEqual(session.template['title'], 'Renamed')
        backup = next(self.backups.glob('*.json'))
        code, out, err = self.run_cli(['restore', str(backup)], session, dry_run=False)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(session.template['title'], 'Smart Template')

    def test_backups_lists_written_files(self):
        session = FakeSession()
        self.run_cli(['products', 'offline', '--ids', '11000040100479'], session, dry_run=False)
        rows = manage_write.list_backups({'name': 'thedozers', 'outDir': self.dir.name})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['action'], 'products.offline')

    # --- guards ------------------------------------------------------------------------------------
    def test_blocked_account_exit_77(self):
        for name in ('wonnischoice', 'linea'):
            config = Path(self.dir.name) / ('blocked-%s.json' % name)
            config.write_text(json.dumps({name: {'loginId': 'seller@example.com'}}), encoding='utf-8')
            argv = ['products', 'offline', '--ids', '1', '--account', name, '--config', str(config)]
            with self.assertRaises(manage_write.Blocked):
                manage_write.run(list(argv), FakeSession())
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(manage_write.cli(list(argv)), 77, name)

    def test_usage_errors_exit_64_and_send_nothing(self):
        for argv in (['products', 'offline'],
                     ['products', 'offline', '--ids', '0'],
                     ['products', 'offline', '--ids', '999'],
                     ['shipping', 'rename', '--template', 'Smart Template'],
                     ['settings', 'message'],
                     ['rfq', 'search-notify', 'on', '--search', 'nope'],
                     ['store', 'version', 'publish', '--version', 'nope']):
            session = FakeSession()
            code, out, err = self.run_cli(argv, session)
            self.assertEqual(code, 64, (argv, out, err))
            self.assertEqual(self.writes(session), [], argv)

    def test_no_command_is_usage_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(manage_write.cli(['--account', 'thedozers', '--config', str(self.config)]), 64)
        self.assertIn('명령 필요', err.getvalue())

    def calls(self, session):
        return session.calls


if __name__ == '__main__':
    unittest.main()
