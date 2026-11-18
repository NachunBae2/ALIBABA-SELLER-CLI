# test-only: all identifiers, names and amounts below are synthetic, never seller data.
"""Manage write engine (광고 밖 셀러센트럴 조작): fake HTTP responses in the screens' real response shapes
(2026-11-18 read contracts), no network, no account, no token."""
import argparse
import contextlib
import html
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
import manage_write
from api_session import ApiSession
from http_session import SessionError

PID_A, PID_B, PID_NEW = 1000000001, 1000000002, 1000000009
GROUP_A, GROUP_B = 2000001, 2000002
CAMPAIGN = 300000001
FT_SMART, FT_ORDINARY, FT_PLAIN = 500000001, 500000002, 500000003
FIXTURE_CSRF, FIXTURE_IDENTITY = 'fixture-page-csrf', 'fixture-identity-1'
PACKAGE = 700000001
PAGE_LIVE, PAGE_DRAFT, PAGE_DUP1, PAGE_DUP2 = 600000001, 600000002, 600000003, 600000004
MEMBER_A, MEMBER_B = 800000001, 800000002


def product(pid, subject, display, group=0, group_name=''):
    return {'id': pid, 'subject': subject, 'displayStatus': display, 'status': 'approved',
            'groupId': group, 'groupId2': 0, 'groupId3': 0, 'groupName1': group_name, 'groupName2': '',
            'groupName3': ''}


SMART_OVERVIEW = {
    'ftId': FT_SMART, 'templateType': 'smartEntrust', 'discount': 120, 'title': 'Template S',
    'dispatchZipCode': '00001', 'insertCarriers': False, 'dispatchLocationList': [{'countryCode': 'KR'}],
    'countries': ['CN', 'US'], 'carriers': ['CARRIER-A'],
    'carrierList': [
        {'channelCode': 'x', 'tradeTerm': 'DAP', 'dropShipping': None,
         'carrierInfoMap': {'wtd': {'carrierCode': 'CARRIER-A', 'isAliCarrier': True}},
         'freightTemplateDetailList': [{'id': None, 'carrierCode': 'CARRIER-A', 'freightType': 'standard',
                                        'countries': ['CN'], 'minFreightTime': 3, 'maxFreightTime': 6,
                                        'shippingType': None, 'hasSurcharge': None, 'destinations': None,
                                        'enableDeliveryGuarantee': None,
                                        'warehouseDTO': {'name': 'Warehouse A', 'warehouseCode': 'WH_A', 'x': 1},
                                        'freightDetailsDTO': {'currency': None, 'discount': 120,
                                                              'byWeightFreightDetailsDTO': None}}]},
        {'channelCode': 'y', 'tradeTerm': 'DAP', 'dropShipping': None,
         'carrierInfoMap': {'wtd': {'carrierCode': 'CARRIER-UNUSED', 'isAliCarrier': True}},
         'freightTemplateDetailList': None},
    ]}
# 캡처(specs 34, 가격 조정 비율 저장)와 같은 칸·순서로 화면이 조립한 본문.
SMART_BODY = {
    'title': 'Template S', 'ftId': str(FT_SMART), 'dispatchLocation': 'KR', 'dispatchZipCode': '00001',
    'templateType': 'smartEntrust',
    'carrierList': [{'selectedWarehouse': {'name': 'Warehouse A', 'warehouseCode': 'WH_A'},
                     'carrierCode': 'CARRIER-A', 'dispatchZipCode': '00001', 'dispatchLocation': 'KR',
                     'hasSurcharge': None,
                     'detailList': [{'freightType': 'standard', 'countries': ['CN'], 'minFreightTime': 3,
                                     'maxFreightTime': 6, 'shippingType': None, 'isAliCarrier': True,
                                     'destinations': None, 'enableDeliveryGuarantee': None, 'id': None,
                                     'freightDetailsDTO': {'currency': 'USD', 'discount': 120}}],
                     'dropShipping': None, 'tradeTerm': 'DAP'}],
    'insertCarriers': False, 'lastCarrierMap': {'KR': ['CARRIER-A']}, 'discount': 120,
    'countriesForTemplate': ['CN', 'US']}
ORDINARY_OVERVIEW = {
    'ftId': FT_ORDINARY, 'templateType': 'ordinary', 'discount': 110, 'title': 'Template O',
    'dispatchZipCode': '00001', 'insertCarriers': True, 'dispatchLocationList': [{'countryCode': 'KR'}],
    'countries': ['AU'], 'carriers': ['GGS_SELLER_LOGISTICS_OTHER'],
    'carrierList': [{'tradeTerm': 'DAP', 'dropShipping': None,
                     'carrierInfoMap': {'wtd': {'carrierCode': 'GGS_SELLER_LOGISTICS_OTHER', 'isAliCarrier': False}},
                     'freightTemplateDetailList': [{
                         'id': 1, 'carrierCode': 'GGS_SELLER_LOGISTICS_OTHER', 'freightType': 'byWeight',
                         'countries': ['AU'], 'minFreightTime': 5, 'maxFreightTime': 10, 'shippingType': 'EXPRESS',
                         'hasSurcharge': False, 'destinations': None, 'enableDeliveryGuarantee': None,
                         'freightDetailsDTO': {'byWeightFreightDetailsDTO': {'rangeFreightsList': []}}}]}]}


PLAIN_OVERVIEW = json.loads(json.dumps(SMART_OVERVIEW))
PLAIN_OVERVIEW.update(ftId=FT_PLAIN, templateType='ordinary', title='Template P')


def reply(value, status=200, text=None):
    return {'status': status, 'text': text if text is not None else json.dumps(value),
            'ct': 'application/json', 'redirectHost': None}


def message_page(state):
    members = json.dumps([{'memberSeq': m, 'defaultConnect': m == state['defaultContact'], 'vname': 'Staff'}
                          for m in (MEMBER_A, MEMBER_B)])
    js = lambda v: json.dumps(v)
    return ('<html><script>window.GDATA = {runData: {csrfTokenKey: \'_csrf_token_\', csrfTokenVal: \'%s\'}};</script>'
            '<script>\nwindow.ASSIGN_DATA = {\n' % FIXTURE_CSRF +
            '  ccFlag: %s,\n  tmCcFlag: \'%s\' == \'true\',\n  isCustomerFirst: false,\n  type: \'product\',\n'
            '  adminInquiryAutoDistribute: %s,\n  inquiryAutoDistributeMemberList: %s,\n'
            '  syncRuleToIm: %s,\n  syncAutoTurnToIm: %s,\n  subTransferRec: false,\n  highLevelBuyerTurn: false,\n'
            '  pageTitle: someJsCall(),\n};\n</script></html>'
            % (js(state['cc']), str(state['tmCc']).lower(), js(state['autoDistribute']), members,
               js(state['syncRuleToIm']), js(state['syncAutoTurnToIm'])))


PRIVACY_FORM = {'0': ('3', ['0', '2', '3']), '9': ('2', ['0', '2', '3'])}


def privacy_page(selected):
    parts = ['<html><body><form id="J-form">']
    for key, (_, values) in PRIVACY_FORM.items():
        for value in values:
            parts.append('<input type="radio" name="keyof%s" data-key="%s" value="%s"%s>'
                         % (key, key, value, ' checked' if selected[key] == value else ''))
        parts.append('<div data-parent="keyof%s-2" data-key="1" data-value="900000001"></div>' % key)
        parts.append('<div data-parent="keyof%s-2" data-key="2" data-value="10001"></div>' % key)
    parts.append('</form><form><input type="radio" name="other" data-key="99" value="1" checked></form>')
    return html.unescape(''.join(parts)) + '</body></html>'


class FakeSession:
    """Answers each request by path in the real response shapes; every call is recorded."""
    transport = 'api'
    metrics = {}

    def __init__(self, apply=True, write=None):
        self.products = [product(PID_A, 'Product A', 'y', GROUP_A, 'Group A'),
                         product(PID_B, 'Product B', 'n', 0, '')]
        self.groups = [{'id': GROUP_A, 'name': 'Group A', 'productCount': 1, 'childrenCount': 0, 'hasChildren': False},
                       {'id': GROUP_B, 'name': 'Group B', 'productCount': 0, 'childrenCount': 0, 'hasChildren': False}]
        self.showcase = [{'adGroupId': 400000001, 'productId': PID_A, 'adGroupSort': 2, 'adGroupStatus': 1},
                         {'adGroupId': 400000002, 'productId': PID_B, 'adGroupSort': 1, 'adGroupStatus': 1}]
        self.searches = [{'id': 3000000001, 'keyword': 'serum', 'url': '', 'categoryId': ''}]
        self.atm = 'Y'
        self.coupons = []
        self.activities = [{'activityId': 900000011, 'activityName': 'Sale', 'status': 'ONGOING'}]
        self.templates = {FT_SMART: json.loads(json.dumps(SMART_OVERVIEW)),
                          FT_ORDINARY: json.loads(json.dumps(ORDINARY_OVERVIEW)),
                          FT_PLAIN: json.loads(json.dumps(PLAIN_OVERVIEW))}
        self.default_ft = FT_ORDINARY
        self.versions = [{'pageId': PAGE_LIVE, 'pageName': 'Live Home'},
                         {'pageId': PAGE_DRAFT, 'pageName': 'Draft'},
                         {'pageId': PAGE_DUP1, 'pageName': 'Copy'},
                         {'pageId': PAGE_DUP2, 'pageName': 'Copy'}]
        self.current_page = PAGE_LIVE
        self.message = {'defaultContact': MEMBER_A, 'cc': True, 'tmCc': True, 'autoDistribute': False,
                        'syncRuleToIm': True, 'syncAutoTurnToIm': True}
        self.privacy = {'0': '3', '9': '2'}
        self.apply = apply
        self.write = write or (lambda path, data: {'success': True})
        self.calls = []

    def cookie(self, name, url):
        return None

    @staticmethod
    def _data(params):
        raw = (params or {}).get('data')
        return json.loads(raw) if isinstance(raw, str) else raw

    def request(self, method, url, query=None, params=None, encoding='form', referer=None):
        query, params = dict(query or {}), dict(params or {})
        self.calls.append({'method': method, 'url': url, 'query': query, 'params': params,
                           'encoding': encoding, 'referer': referer})
        path = url.split('alibaba.com', 1)[1]
        ok = lambda data: reply(self.write(path, data))

        # products
        if path == '/product/managementproducts/asyQueryProductsList.do':
            page, size = int(query['page']), int(query['size'])
            rows = self.products[(page - 1) * size:page * size]
            return reply({'result': True, 'count': len(self.products), 'currentPage': page, 'products': rows})
        if path == '/product/managementproducts/asyChangeDisplayStatus.do':
            if self.apply:
                for row in self.products:
                    if str(row['id']) in str(params['productIds']).split(','):
                        row['displayStatus'] = 'y' if params['newStatus'] == 'on' else 'n'
            return ok(params)
        if path == '/product/group_ajax.do':
            if params.get('_csrf_token_') != FIXTURE_CSRF:
                return reply({'success': False, 'message': 'csrf'})
            if self.apply:
                name = next(g['name'] for g in self.groups if str(g['id']) == str(params['groupId']))
                for row in self.products:
                    if str(row['id']) in str(params['productIdList']).split(','):
                        row.update(groupId=int(params['groupId']), groupName1=name)
            return ok(params)
        if path == '/product/managementproducts/asyDelete.do':
            if self.apply:
                drop = str(params['productIds']).split(',')
                self.products = [r for r in self.products if str(r['id']) not in drop]
            return ok(params)
        if path == '/product/asyQueryParameter.do':
            return reply({'uniqueId': 'x', 'csrfToken': {'headerName': 'X-XSRF-TOKEN', 'parameterName': '_csrf_token_',
                                                        'token': FIXTURE_CSRF}})
        if path == '/product/group_query_ajax.do':
            return reply({'result': True, 'data': self.groups})

        # showcase
        if path == '/api/campaign/find/showcase':
            return reply({'status': 1000, 'ok': True, 'data': CAMPAIGN})
        if path == '/api/campaign/%d/adgroup' % CAMPAIGN:
            return reply({'status': 1000, 'ok': True, 'data': self.showcase, 'totalCount': len(self.showcase)})
        if path.startswith('/api/campaign/%d/adgroup/' % CAMPAIGN):
            data, kind = self._data(params), path.rsplit('/', 1)[1]
            if self.apply and kind == 'create':
                for item in data['adGroupOperationList']:
                    self.showcase.append({'adGroupId': 400000100 + len(self.showcase), 'productId': item['productId'],
                                          'adGroupSort': 0, 'adGroupStatus': 1})
            if self.apply and kind == 'delete':
                drop = {i['id'] for i in data['adGroupOperationList']}
                self.showcase = [g for g in self.showcase if g['adGroupId'] not in drop]
            if self.apply and kind == 'replace':
                for group in self.showcase:
                    if group['adGroupId'] == data['id']:
                        group['productId'] = data['newEntityId']
            if self.apply and kind == 'sort':
                item = next(g for g in self.showcase if g['adGroupId'] == data['id'])
                self.showcase.remove(item)
                self.showcase.insert(data['targetIndex'] - 1, item)
            return reply(dict(self.write(path, data), status=1000, ok=True))

        # rfq
        if path == '/rfq/rfq_search_list.htm':
            return reply(None, text="<html><input name='_csrf_token_' type='hidden' value='%s'>"
                                    "<input type=\"hidden\" value=\"%s\" id=\"my-search-identity-code\" ></html>"
                         % (FIXTURE_CSRF, FIXTURE_IDENTITY))
        if path == '/rfq/ajax/my_search_list_ajax.do':
            return reply(self.searches)
        if path == '/rfq/mySearch/rfq_my_search_ajax.do':
            criteria = json.loads(query['params'])
            if self.apply and query['type'] == 'new':
                self.searches.append({'id': 3000000002, 'keyword': criteria['keyword'], 'url': '', 'categoryId': ''})
            if self.apply and query['type'] == 'delete':
                self.searches = [s for s in self.searches if str(s['id']) != criteria['id']]
            if query.get('_csrf_token_') != FIXTURE_CSRF or not query.get('identityCode', '').startswith('fixture-identity'):
                return reply(None, text='/**/jQuery1({"code":"403"})')
            return reply(None, text='/**/jQuery1({"code":200,"identityCode":"fixture-identity-2"})')
        if path == '/rfq/mySearch/RfqMySearchNotifyAjax.do':
            if query['op'] == 'view':
                return reply({'notify': {'sendAtm': self.atm}, 'status': True})
            if self.apply:
                self.atm = query['atm']
            return reply({'status': True})
        if path.startswith('/quotation/ajax/'):
            return ok(params)

        # promo
        if path == '/coupon/v2/createSellerCouponAjax.do':
            if self.apply:
                self.coupons.append({'couponTemplateId': 900000002, 'name': params['name'], 'status': 'ISSUING'})
            return ok(params)
        if path == '/coupon/v2/terminateApplyAjax.do':
            if self.apply:
                for coupon in self.coupons:
                    if str(coupon['couponTemplateId']) == str(query['couponTemplateId']):
                        coupon['status'] = 'TERMINATED'
            return ok(query)
        if path == '/coupon/v2/querySellerCouponListAjax.do':
            return reply({'code': 200, 'message': 'success',
                          'data': {'page': {'totalNum': len(self.coupons)}, 'sellerCouponTemplateList': self.coupons}})
        if path == '/promotion/earlyTerminateActivityAjax.do':
            if self.apply:
                for activity in self.activities:
                    if str(activity['activityId']) == str(params['activityId']):
                        activity['status'] = 'ENDED'
            return ok(params)
        if path == '/promotion/querySellerActivityListAjax.do':
            return reply({'code': 200, 'data': {'sellerActivityList': self.activities}})
        if path == '/enroll/sign.do':
            return ok(query)

        # shipping
        if path == '/luyou/ggs/getTemplateList.json':
            return reply({'code': 200, 'data': {'templateSimplifyInfoList': [
                {'id': ft, 'title': t['title'], 'templateType': t['templateType'], 'isDefault': ft == self.default_ft,
                 'dispatchLocationList': [{'id': 'KR', 'status': 1}]} for ft, t in self.templates.items()]}})
        if path == '/luyou/ggs/shippingTemplates/getTemplateInfoOverview.json':
            assert query['dispatchLocation'] == 'KR'
            return reply({'code': 200, 'data': self.templates[int(query['ftId'])]})
        if path == '/luyou/ggs/updateTemplate.json':
            body = json.loads(params['json'])
            if self.apply:
                template = self.templates[int(body['ftId'])]
                template.update(title=body['title'], discount=body['discount'], insertCarriers=body['insertCarriers'])
            return ok(body)
        if path == '/luyou/ggs/addTemplate.json':
            body = json.loads(params['json'])
            if self.apply:
                self.templates[500000009] = {'ftId': 500000009, 'title': body['title'], 'templateType': 'ordinary'}
            return ok(body)
        if path == '/luyou/settemplateDefault.json':
            body = json.loads(params['json'])
            if self.apply:
                self.default_ft = body['id']
            return ok(body)

        # store (siteadmin)
        if path == '/page/getPageTypeNew':
            return reply({'success': True, 'result': [
                {'pageType': 1, 'commonPageType': 1, 'pageTemplateDetailId': 1},
                {'pageType': 6, 'commonPageType': 100, 'pageTemplateDetailId': 59386, 'pagePackageId': PACKAGE}]})
        if path == '/package/getPagePackageList':
            assert query['pageTemplateId'] == 59386 and query['pageType'] == 6
            return reply({'success': True, 'result': {'data': [
                {'pagePackageId': PACKAGE, 'pagePackageName': 'Home', 'currentPageId': self.current_page}]}})
        if path == '/page/list/keyword/packageId':
            assert query == {'pagePackageId': PACKAGE}
            return reply({'success': True, 'result': [
                dict(v, status=2 if v['pageId'] == self.current_page else 1, publishTime=None, deletable=True,
                     pageDisplayStatus='published' if v['pageId'] == self.current_page else 'editing')
                for v in self.versions]})
        if path in ('/page/editPage', '/page/deletePage', '/publish'):
            if self.apply and path == '/page/editPage':
                for version in self.versions:
                    if version['pageId'] == params['pageId']:
                        version['pageName'] = params['pageName']
            if self.apply and path == '/page/deletePage':
                self.versions = [v for v in self.versions if v['pageId'] != params['pageId']]
            if self.apply and path == '/publish':
                self.current_page = query['pageId']
            return reply(dict({'success': True, 'totalCount': None, 'result': None, 'error': None},
                              **self.write(path, params or query)))

        # settings
        if path == '/feedbackset/basic.htm':
            return reply(None, text=message_page(self.message))
        if path == '/feedbackset/index.htm':
            body = json.loads(params['json'])
            if body['params'].get('_csrf_token_') != FIXTURE_CSRF:
                return reply({'success': False, 'message': 'csrf'})
            if self.apply:
                p = body['params']
                self.message.update(defaultContact=p['defaultContact'], cc=p['cc'], tmCc=p['tmCc'],
                                    autoDistribute=p['adminFeedback']['autoDistribute'],
                                    syncRuleToIm=p['syncRuleToIm'], syncAutoTurnToIm=p['syncAutoTurnToIm'])
            return ok(body)
        if path == '/privacy/privacySettingShow.htm':
            return reply(None, text=privacy_page(self.privacy))
        if path == '/ajax/privacy/privacy_setting_update.do':
            if self.apply:
                self.privacy = {k: next(iter(v)) for k, v in json.loads(params['privaceSetting']).items()}
            return ok(params)

        # reviews
        if path == '/review/action/inviteReviewAction.do':
            return ok(params)
        raise AssertionError('unexpected url ' + url)


READ_PATHS = ('/api/campaign/find/showcase', 'asyQueryProductsList.do', 'group_query_ajax.do',
              'my_search_list_ajax.do', 'querySellerCouponListAjax.do', 'querySellerActivityListAjax.do',
              'getTemplateList.json', 'getTemplateInfoOverview.json', '/page/getPageTypeNew',
              '/package/getPagePackageList', '/page/list/keyword/packageId', '/feedbackset/basic.htm',
              'privacySettingShow.htm', '/rfq/rfq_search_list.htm', 'asyQueryParameter.do')


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
            except (manage_write.Usage, manage_write.Unverified) as error:
                code, _ = 64, err.write(str(error))
        return code, out.getvalue(), err.getvalue()

    def writes(self, session):
        """상태를 바꾸는 요청만(읽기 경로·쇼케이스 목록·알림 op=view 는 뺀다)."""
        return [c for c in session.calls
                if not c['url'].endswith('/adgroup') and c['query'].get('op') != 'view'
                and not any(s in c['url'] for s in READ_PATHS)]

    def latest_backup(self):
        return json.loads(max(self.backups.glob('*.json'), key=lambda p: p.stat().st_mtime_ns)
                          .read_text(encoding='utf-8'))

    # --- CLI parser --------------------------------------------------------------------------------
    def test_every_subcommand_help_parses(self):
        """help 문자열의 '%' 같은 것이 argparse 를 깨면 여기서 잡힌다(테스트는 통과하는데 CLI 만 죽는 일 방지)."""
        parser = manage_write.build_parser()
        paths = []

        def walk(p, prefix):
            paths.append(prefix)
            for action in p._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for name, child in action.choices.items():
                        walk(child, prefix + [name])
        walk(parser, [])
        self.assertGreater(len(paths), 35)
        for prefix in paths:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as exit_:
                manage_write.build_parser().parse_args(prefix + ['--help'])
            self.assertEqual(exit_.exception.code, 0, prefix)
            self.assertIn('usage:', out.getvalue(), prefix)
        for name in ('search-delete',):
            self.assertIn(['rfq', name], paths)
        self.assertIn(['store', 'version', 'delete'], paths)

    # --- reads: real response shapes, empty lists --------------------------------------------------
    def test_reads_parse_real_shapes(self):
        session = FakeSession()
        products = manage_write.read_products(session)
        self.assertEqual([(p['id'], p['status'], p['groupId']) for p in products],
                         [(PID_A, 'on', GROUP_A), (PID_B, 'off', 0)])
        self.assertEqual(session.calls[0]['query']['showPowerScore'], 'true')
        self.assertEqual(len(manage_write.read_groups(session)), 2)
        cid = manage_write.showcase_campaign(session)
        groups = manage_write.read_showcase_groups(session, cid)
        self.assertEqual([(g['id'], g['index']) for g in groups], [(400000001, 1), (400000002, 2)])
        self.assertEqual(session.calls[-1]['referer'], 'https://www2.alibaba.com/showcase/index.htm')
        self.assertEqual(manage_write.read_saved_searches(session), [{'id': 3000000001, 'keyword': 'serum'}])
        self.assertEqual(manage_write.read_search_notify(session), 'Y')
        self.assertEqual(manage_write.read_activities(session)[0]['id'], 900000011)
        templates = manage_write.read_templates(session)
        self.assertEqual([(t['ftId'], t['isDefault'], t['dispatchLocation']) for t in templates],
                         [(FT_SMART, False, 'KR'), (FT_ORDINARY, True, 'KR'), (FT_PLAIN, False, 'KR')])
        versions = manage_write.read_versions(session)
        self.assertEqual([(v['pageId'], v['live']) for v in versions][:2], [(PAGE_LIVE, True), (PAGE_DRAFT, False)])
        message = manage_write.read_message_settings(session)
        self.assertEqual(message['members'], [MEMBER_A, MEMBER_B])
        self.assertEqual(message['params']['defaultContact'], MEMBER_A)
        self.assertIs(message['params']['tmCc'], True)
        self.assertNotIn('Staff', json.dumps(message))
        privacy = manage_write.read_privacy(session)
        self.assertEqual(privacy['setting'], {'0': {'3': {}}, '9': {'2': {'1': '900000001', '2': '10001'}}})
        self.assertNotIn('99', privacy['options'])

    def test_empty_lists_in_success_responses_are_empty_not_errors(self):
        session = FakeSession()
        session.products, session.groups, session.showcase, session.searches = [], [], [], []
        session.coupons, session.activities, session.templates, session.versions = [], [], {}, []
        self.assertEqual(manage_write.read_products(session), [])
        self.assertEqual(manage_write.read_groups(session), [])
        self.assertEqual(manage_write.read_showcase_groups(session, CAMPAIGN), [])
        self.assertEqual(manage_write.read_saved_searches(session), [])
        self.assertEqual(manage_write.read_coupons(session), [])
        self.assertEqual(manage_write.read_activities(session), [])
        self.assertEqual(manage_write.read_templates(session), [])
        self.assertEqual(manage_write.read_versions(session), [])
        session.request = lambda *a, **k: reply({'code': 200, 'data': {'sellerCouponTemplateList': None}})
        self.assertEqual(manage_write.read_coupons(session), [])

    def test_missing_list_field_is_a_read_failure(self):
        session = FakeSession()
        session.request = lambda *a, **k: reply({'result': True, 'count': 0})
        with self.assertRaises(SessionError):
            manage_write.read_products(session)
        session.request = lambda *a, **k: reply({'code': 200, 'data': {}})
        with self.assertRaises(SessionError):
            manage_write.read_coupons(session)
        session.request = lambda *a, **k: reply({'status': 1000, 'ok': True, 'data': {'rows': []}})
        with self.assertRaises(SessionError):
            manage_write.read_showcase_groups(session, CAMPAIGN)

    def test_products_read_pages_until_count(self):
        session = FakeSession()
        session.products = [product(PID_A + i, 'P%d' % i, 'y') for i in range(manage_write.PRODUCT_PAGE + 3)]
        rows = manage_write.read_products(session)
        self.assertEqual(len(rows), manage_write.PRODUCT_PAGE + 3)
        self.assertEqual([c['query']['page'] for c in session.calls], [1, 2])
        session.calls.clear()
        manage_write.read_products(session, [PID_A])
        self.assertEqual([c['query']['page'] for c in session.calls], [1])

    def test_template_update_body_matches_captured_screen_body(self):
        self.assertEqual(manage_write.template_update_body(SMART_OVERVIEW), SMART_BODY)
        self.assertEqual(list(manage_write.template_update_body(SMART_OVERVIEW)), list(SMART_BODY))
        ordinary = manage_write.template_update_body(ORDINARY_OVERVIEW)
        self.assertEqual(list(ordinary['carrierList'][0]['detailList'][0]['freightDetailsDTO'])[0],
                         'byWeightFreightDetailsDTO')
        self.assertEqual(manage_write.unverified_fares(ordinary), ['GGS_SELLER_LOGISTICS_OTHER'])
        self.assertEqual(manage_write.unverified_fares(SMART_BODY), [])

    # --- products ----------------------------------------------------------------------------------
    def test_products_offline_dry_run_shows_grade_and_request_without_writing(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['products', 'offline', '--ids', str(PID_A)], session)
        self.assertEqual(code, 0, out)
        self.assertIn('되돌릴 수 있음', out.splitlines()[0])
        self.assertIn('"newStatus": "off"', out)
        self.assertIn('dry-run', out)
        row = next(line for line in out.splitlines() if line.startswith('상품 표시상태'))
        self.assertIn("'on'", row)
        self.assertIn("{'newStatus': 'off'}", row)     # 현재·변경후가 한 줄
        self.assertFalse(any(line.lstrip().startswith('→') for line in out.splitlines()))
        self.assertEqual(self.writes(session), [])
        self.assertFalse(self.backups.exists())

    def test_products_online_of_online_product_is_refused(self):
        code, _, err = self.run_cli(['products', 'online', '--ids', str(PID_A)], FakeSession())
        self.assertEqual(code, 64)
        self.assertIn('이미 온라인', err)

    def test_products_delete_grade_is_irreversible_and_restore_is_refused(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['products', 'delete', '--ids', str(PID_A)], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('되돌릴 수 없음', out.splitlines()[0])
        self.assertIn('불가', self.latest_backup()['restore']['note'])
        self.assertNotIn(PID_A, [p['id'] for p in session.products])

    def test_yes_writes_after_backup_and_reads_back(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['products', 'offline', '--ids', str(PID_A)], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('리드백 일치', out)
        record = self.latest_backup()
        self.assertEqual(record['restore']['argv'], ['products', 'online', '--ids', str(PID_A)])
        self.assertEqual(record['request'][0]['params']['newStatus'], 'off')
        self.assertNotIn('token', json.dumps(record).lower())

    def test_readback_mismatch_exits_three(self):
        code, out, _ = self.run_cli(['products', 'offline', '--ids', str(PID_A)], FakeSession(apply=False),
                                    dry_run=False)
        self.assertEqual(code, 3, out)
        self.assertIn('리드백 불일치', out)

    def test_write_refusal_exits_one(self):
        session = FakeSession(write=lambda path, data: {'success': False, 'message': 'nope'})
        code, out, _ = self.run_cli(['products', 'offline', '--ids', str(PID_A)], session, dry_run=False)
        self.assertEqual(code, 1, out)

    def test_group_moves_and_restores_original_group(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['products', 'group', '--ids', str(PID_A), '--group', 'Group B'], session,
                                    dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('리드백 일치', out)
        self.assertEqual(session.products[0]['groupId'], GROUP_B)
        self.assertEqual(self.latest_backup()['restore']['requests'][0]['params'],
                         {'productIdList': str(PID_A), 'groupId': str(GROUP_A)})
        self.assertEqual(self.writes(session)[0]['params'],
                         {'productIdList': str(PID_A), 'groupId': str(GROUP_B), '_csrf_token_': FIXTURE_CSRF})
        self.assertNotIn(FIXTURE_CSRF, out + json.dumps(self.latest_backup()))
        backup = max(self.backups.glob('*.json'), key=lambda p: p.stat().st_mtime_ns)
        code, out, err = self.run_cli(['restore', str(backup)], session, dry_run=False)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(session.products[0]['groupId'], GROUP_A)

    def test_group_of_ungrouped_product_has_no_restore(self):
        code, out, _ = self.run_cli(['products', 'group', '--ids', str(PID_B), '--group', 'Group A'], FakeSession())
        self.assertEqual(code, 0, out)
        self.assertIn('되돌리기: 불가: 원래 미분류', out)

    # --- showcase ----------------------------------------------------------------------------------
    def test_showcase_add_body_matches_capture(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['showcase', 'add', '--ids', str(PID_NEW)], session, dry_run=False)
        self.assertEqual(code, 0, out)
        create = [c for c in self.writes(session) if c['url'].endswith('/adgroup/create')][0]
        self.assertEqual(json.loads(create['params']['data']),
                         {'adgroupStatus': 1, 'campaignType': 9,
                          'adGroupOperationList': [{'productId': PID_NEW, 'onlineStatus': 1}]})
        self.assertEqual(create['referer'], 'https://www2.alibaba.com/showcase/index.htm')
        self.assertIn('리드백 일치', out)

    def test_showcase_slot_limit_is_refused(self):
        session = FakeSession()
        session.showcase = [{'adGroupId': 400000000 + i, 'productId': PID_A + 10 + i, 'adGroupStatus': 1}
                            for i in range(20)]
        code, _, err = self.run_cli(['showcase', 'add', '--ids', str(PID_NEW)], session)
        self.assertEqual(code, 64)
        self.assertIn('한도 20', err)

    def test_showcase_remove_replace_sort_bodies(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['showcase', 'remove', '--ids', str(PID_B)], session, dry_run=False)
        self.assertEqual(code, 0, out)
        data = json.loads(self.writes(session)[0]['params']['data'])
        self.assertEqual(data, {'onlineStatus': 1, 'adGroupOperationList': [{'id': 400000002}]})
        session = FakeSession()
        code, out, _ = self.run_cli(['showcase', 'replace', '--ids', '400000001', '--to', str(PID_NEW)], session,
                                    dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(self.writes(session)[0]['params']['data']),
                         {'adgroupStatus': 1, 'id': 400000001, 'oldEntityId': PID_A, 'newEntityId': PID_NEW})
        session = FakeSession()
        code, out, _ = self.run_cli(['showcase', 'sort', '--ids', '400000001', '--to-index', '2'], session,
                                    dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(self.writes(session)[0]['params']['data']),
                         {'onlineStatus': 1, 'id': 400000001, 'srcIndex': 1, 'targetIndex': 2})
        self.assertEqual(self.latest_backup()['restore']['argv'][-1], '1')

    # --- rfq ---------------------------------------------------------------------------------------
    def test_search_add_is_reversible_and_restore_deletes_it(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['rfq', 'search-add', '--keyword', 'vitamin c'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('되돌릴 수 있음', out.splitlines()[0])
        call = self.writes(session)[0]
        self.assertEqual(list(call['query']), ['params', 'type', '_csrf_token_', 'identityCode'])
        self.assertEqual((call['query']['_csrf_token_'], call['query']['identityCode']), (FIXTURE_CSRF, FIXTURE_IDENTITY))
        self.assertEqual(call['referer'], 'https://sourcing.alibaba.com/rfq/rfq_search_list.htm')
        self.assertNotIn(FIXTURE_CSRF, out + json.dumps(self.latest_backup()))
        self.assertNotIn(FIXTURE_IDENTITY, out + json.dumps(self.latest_backup()))
        self.assertEqual(json.loads(call['query']['params']),
                         {'keyword': 'vitamin c', 'categoryId': '', 'dateApproved': '', 'region': '', 'country': '',
                          'quantityMin': '', 'quantityMax': ''})
        self.assertEqual(self.latest_backup()['restore']['argv'], ['rfq', 'search-delete', '--search', 'vitamin c'])
        backup = max(self.backups.glob('*.json'), key=lambda p: p.stat().st_mtime_ns)
        code, out, err = self.run_cli(['restore', str(backup)], session, dry_run=False)
        self.assertEqual(code, 0, out + err)
        self.assertEqual([s['keyword'] for s in session.searches], ['serum'])

    def test_search_delete_dry_run_body_matches_capture(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['rfq', 'search-delete', '--search', 'serum'], session)
        self.assertEqual(code, 0, out)
        self.assertIn('되돌릴 수 없음', out.splitlines()[0])
        self.assertIn('되돌리기: 불가', out)
        self.assertEqual(self.writes(session), [])
        plan = manage_write.plan_for(['rfq', 'search-delete', '--search', '3000000001'], FakeSession())
        self.assertEqual(manage_write.printable(plan['requests']), [{
            'method': 'GET', 'url': 'https://mysourcing.alibaba.com/rfq/mySearch/rfq_my_search_ajax.do',
            'query': {'params': '{"id":"3000000001"}', 'type': 'delete', 'tracelog': 'list_del_my_search'},
            'params': {}, 'encoding': 'form'}])
        code, out, _ = self.run_cli(['rfq', 'search-delete', '--search', 'serum'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(session.searches, [])
        code, _, err = self.run_cli(['rfq', 'search-delete', '--search', 'serum'], session)
        self.assertEqual(code, 64)
        self.assertIn('없다', err)

    def test_search_notify_is_account_wide_and_restores(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['rfq', 'search-notify', 'off'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(session.atm, 'N')
        self.assertEqual(self.writes(session)[0]['query'], {'op': 'update', 'atm': 'N'})
        self.assertEqual(self.latest_backup()['restore']['argv'], ['rfq', 'search-notify', 'on'])
        code, _, err = self.run_cli(['rfq', 'search-notify', 'off'], session)
        self.assertEqual(code, 64)

    def test_quote_body_has_captured_keys(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['rfq', 'quote', '--rfq', '1000000123', '--price', '2.5', '--quantity', '10',
                                     '--port', 'BUSAN', '--payment', 'T/T'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        form = json.loads(self.writes(session)[0]['params']['formData'])
        self.assertEqual(form['rfqId'], 1000000123)
        self.assertEqual(form['product'][0]['price'], [{'quantity': 10, 'fobPrice': 2.5}])

    # --- promo -------------------------------------------------------------------------------------
    def test_coupon_create_uses_cents_and_restore_is_stop(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['promo', 'coupon', 'create', '--name', 'Spring', '--shop', '1000000500',
                                     '--start-money', '100', '--discount-money', '5'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('비용/외부 영향', out.splitlines()[0])
        params = self.writes(session)[0]['params']
        self.assertEqual((params['startMoney'], params['discountMoney']), (10000, 500))
        self.assertEqual(self.latest_backup()['restore']['argv'], ['promo', 'coupon', 'stop', '--id', '900000002'])

    def test_coupon_stop_and_discount_end(self):
        session = FakeSession()
        session.coupons = [{'couponTemplateId': 900000002, 'name': 'x', 'status': 'ISSUING'}]
        code, out, _ = self.run_cli(['promo', 'coupon', 'stop', '--id', '900000002'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(session.coupons[0]['status'], 'TERMINATED')
        session = FakeSession()
        code, out, _ = self.run_cli(['promo', 'discount', 'end', '--id', '900000011'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(session.activities[0]['status'], 'ENDED')

    def test_event_enroll_signs_with_topic_and_protocol(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['promo', 'event', 'enroll', '--topic', '123', '--protocol', '456'],
                                    session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(session.calls[-1]['query'], {'topicId': 123, 'protocolId': 456})

    # --- shipping ----------------------------------------------------------------------------------
    def test_shipping_rename_sends_screen_body(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['shipping', 'rename', '--template', 'Template P', '--to', 'New Name'],
                                    session, dry_run=False)
        self.assertEqual(code, 0, out)
        sent = json.loads(self.writes(session)[0]['params']['json'])
        self.assertEqual(sent, dict(SMART_BODY, title='New Name', ftId=str(FT_PLAIN), templateType='ordinary'))
        self.assertIn('리드백 일치', out)

    def test_smart_template_rename_refuses_yes_and_other_updates_say_unconfirmed(self):
        session = FakeSession()
        code, out, err = self.run_cli(['shipping', 'rename', '--template', 'Template S', '--to', 'X'], session,
                                      dry_run=False)
        self.assertEqual(code, 64, out + err)
        self.assertIn('이름이 안 바뀜', out + err)
        self.assertEqual(self.writes(session), [])
        code, out, _ = self.run_cli(['shipping', 'price-ratio', '--template', 'Template S', '--ratio', '1.3'], session)
        self.assertIn('실계정 미확인', out)
        code, out, _ = self.run_cli(['shipping', 'price-ratio', '--template', 'Template P', '--ratio', '1.3'], session)
        self.assertNotIn('실계정 미확인', out)

    def test_shipping_fare_table_template_refuses_yes(self):
        session = FakeSession()
        code, out, err = self.run_cli(['shipping', 'rename', '--template', 'Template O', '--to', 'X'], session,
                                      dry_run=False)
        self.assertEqual(code, 64, out + err)
        self.assertIn('요금표', out + err)
        self.assertEqual(self.writes(session), [])

    def test_shipping_price_ratio_changes_top_level_discount_only(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['shipping', 'price-ratio', '--template', str(FT_SMART), '--ratio', '1.3'],
                                    session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(self.writes(session)[0]['params']['json']), dict(SMART_BODY, discount=130))
        self.assertIn('리드백 일치', out)

    def test_shipping_auto_route_shows_current_and_refuses_yes(self):
        session = FakeSession()
        code, out, err = self.run_cli(['shipping', 'auto-route', 'on', '--template', 'Template S'], session,
                                      dry_run=False)
        self.assertEqual(code, 64, out + err)
        self.assertIn('insertCarriers', out)
        self.assertEqual(self.writes(session), [])

    def test_shipping_default_body_and_restore(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['shipping', 'default', '--template', 'Template S'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        call = self.writes(session)[0]
        self.assertEqual((call['encoding'], call['params']), ('json', {'json': '{"id":%d}' % FT_SMART}))
        self.assertEqual(self.latest_backup()['restore']['requests'][0]['params'],
                         {'json': '{"id":%d}' % FT_ORDINARY})
        session = FakeSession()
        session.default_ft = None
        code, out, _ = self.run_cli(['shipping', 'default', '--template', 'Template S'], session)
        self.assertIn('되돌리기: 불가', out)

    def test_carrier_add_is_blocked_and_add_template_is_irreversible(self):
        session = FakeSession()
        code, out, err = self.run_cli(['shipping', 'carrier', 'add', '--template', 'Template S',
                                       '--countries', 'US,CA'], session, dry_run=False)
        self.assertEqual(code, 64, out + err)
        self.assertIn('basicRule', out + err)
        session = FakeSession()
        code, out, _ = self.run_cli(['shipping', 'add-template', '--title', 'New Template', '--countries', 'US'],
                                    session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('되돌릴 수 없음', out.splitlines()[0])

    # --- store -------------------------------------------------------------------------------------
    def test_store_version_rename_and_publish(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['store', 'version', 'rename', '--version', 'Draft', '--to', 'v2'], session,
                                    dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.writes(session)[0]['params'], {'pageType': 6, 'pageId': PAGE_DRAFT, 'pageName': 'v2'})
        self.assertIn('리드백 일치', out)
        session = FakeSession()
        code, out, _ = self.run_cli(['store', 'version', 'publish', '--version', 'Draft'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        publish = self.writes(session)[0]
        self.assertEqual((publish['query'], publish['params']),
                         ({'pageType': 6, 'pageId': PAGE_DRAFT, 'commonPageType': 100}, {}))
        self.assertEqual(self.latest_backup()['restore']['argv'],
                         ['store', 'version', 'publish', '--version', str(PAGE_LIVE)])

    def test_store_version_delete_dry_run_body_matches_capture(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['store', 'version', 'delete', '--version', 'Draft'], session)
        self.assertEqual(code, 0, out)
        self.assertIn('되돌릴 수 없음', out.splitlines()[0])
        self.assertIn('삭제 후 복원 불가', out)
        self.assertEqual(self.writes(session), [])
        plan = manage_write.plan_for(['store', 'version', 'delete', '--version', str(PAGE_DRAFT)], FakeSession())
        self.assertEqual(manage_write.printable(plan['requests']), [{
            'method': 'POST', 'url': 'https://siteadmin.alibaba.com/page/deletePage', 'query': {},
            'params': {'pageType': 6, 'pageId': PAGE_DRAFT}, 'encoding': 'form'}])
        code, out, _ = self.run_cli(['store', 'version', 'delete', '--version', 'Draft'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertNotIn(PAGE_DRAFT, [v['pageId'] for v in session.versions])

    def test_store_version_delete_refuses_live_and_ambiguous_names(self):
        code, _, err = self.run_cli(['store', 'version', 'delete', '--version', 'Live Home'], FakeSession())
        self.assertEqual(code, 64)
        self.assertIn('라이브', err)
        code, _, err = self.run_cli(['store', 'version', 'rename', '--version', 'Copy', '--to', 'x'], FakeSession())
        self.assertEqual(code, 64)
        self.assertIn(str(PAGE_DUP2), err)
        code, out, _ = self.run_cli(['store', 'version', 'rename', '--version', str(PAGE_DUP2), '--to', 'x'],
                                    FakeSession())
        self.assertEqual(code, 0, out)

    # --- settings / reviews ------------------------------------------------------------------------
    def test_settings_message_starts_from_current_values(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['settings', 'message', '--contact', str(MEMBER_B), '--cc', 'off'], session,
                                    dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('리드백 일치', out)
        self.assertNotIn('Staff', out)
        body = json.loads(self.writes(session)[0]['params']['json'])
        self.assertEqual(body, {'cmd': 'updateForm', 'params': {
            'defaultContact': MEMBER_B, 'cc': False, 'tmCc': False, 'isCustomerFirst': False, 'rule': 'product',
            'adminFeedback': {'autoDistribute': False, 'accountStatus': [], 'hlbAccountStatus': []},
            '_csrf_token_': FIXTURE_CSRF, 'syncRuleToIm': True, 'syncAutoTurnToIm': True, 'subTransferRec': False, 'highLevelBuyerTurn': False}})
        self.assertEqual(list(body['params'])[5:7], ['adminFeedback', '_csrf_token_'])
        self.assertNotIn(FIXTURE_CSRF, out + json.dumps(self.latest_backup()))
        restore = json.loads(self.latest_backup()['restore']['requests'][0]['params']['json'])
        self.assertEqual((restore['params']['defaultContact'], restore['params']['cc']), (MEMBER_A, True))
        code, _, err = self.run_cli(['settings', 'message', '--contact', '1234'], FakeSession())
        self.assertEqual(code, 64)

    def test_settings_privacy_sets_key_value_with_children(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['settings', 'privacy', '--set', '0=2'], session, dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('리드백 일치', out)
        sent = json.loads(self.writes(session)[0]['params']['privaceSetting'])
        self.assertEqual(sent, {'0': {'2': {'1': '900000001', '2': '10001'}},
                                '9': {'2': {'1': '900000001', '2': '10001'}}})
        self.assertEqual(json.loads(self.latest_backup()['restore']['requests'][0]['params']['privaceSetting'])['0'],
                         {'3': {}})
        for bad in ('0=7', '5=2', '9.2.2=20002'):
            code, _, err = self.run_cli(['settings', 'privacy', '--set', bad], FakeSession())
            self.assertEqual(code, 64, bad)

    def test_reviews_invite_is_irreversible(self):
        session = FakeSession()
        code, out, _ = self.run_cli(['reviews', 'invite', '--order', '1000000777', '--comment', 'please'], session,
                                    dry_run=False)
        self.assertEqual(code, 0, out)
        self.assertIn('바이어', out.splitlines()[0])
        self.assertEqual(self.writes(session)[0]['params'], {'inviteComment': 'please'})

    # --- restore -----------------------------------------------------------------------------------
    def test_restore_replays_the_recorded_command(self):
        session = FakeSession()
        self.run_cli(['products', 'offline', '--ids', str(PID_A)], session, dry_run=False)
        backup = next(self.backups.glob('*.json'))
        code, out, err = self.run_cli(['restore', str(backup)], session, dry_run=False)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(session.products[0]['displayStatus'], 'y')

    def test_restore_of_irreversible_action_exits_64_with_reason(self):
        session = FakeSession()
        self.run_cli(['store', 'version', 'delete', '--version', 'Draft'], session, dry_run=False)
        backup = next(self.backups.glob('*.json'))
        code, _, err = self.run_cli(['restore', str(backup)], session, dry_run=False)
        self.assertEqual(code, 64)
        self.assertIn('복원 불가', err)

    def test_restore_by_request_body_reverts_template(self):
        session = FakeSession()
        self.run_cli(['shipping', 'rename', '--template', 'Template P', '--to', 'Renamed'], session, dry_run=False)
        self.assertEqual(session.templates[FT_PLAIN]['title'], 'Renamed')
        backup = next(self.backups.glob('*.json'))
        code, out, err = self.run_cli(['restore', str(backup)], session, dry_run=False)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(session.templates[FT_PLAIN]['title'], 'Template P')

    def test_backups_lists_written_files(self):
        session = FakeSession()
        self.run_cli(['products', 'offline', '--ids', str(PID_A)], session, dry_run=False)
        rows = manage_write.list_backups({'name': 'thedozers', 'outDir': self.dir.name})
        self.assertEqual([r['action'] for r in rows], ['products.offline'])

    # --- guards ------------------------------------------------------------------------------------
    def test_blocked_account_exit_77(self):
        for name in ('wonnischoice', 'linea'):
            config = Path(self.dir.name) / ('blocked-%s.json' % name)
            config.write_text(json.dumps({name: {'loginId': 'seller@example.com'}}), encoding='utf-8')
            argv = ['products', 'offline', '--ids', '1', '--account', name, '--config', str(config)]
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(manage_write.cli(list(argv)), 77, name)

    def test_usage_errors_exit_64_and_send_nothing(self):
        for argv in (['products', 'offline'],
                     ['products', 'offline', '--ids', '0'],
                     ['products', 'offline', '--ids', '999'],
                     ['shipping', 'rename', '--template', 'Template S'],
                     ['settings', 'message'],
                     ['rfq', 'search-delete', '--search', 'nope'],
                     ['store', 'version', 'publish', '--version', 'nope'],
                     ['store', 'version', 'publish', '--version', 'Live Home']):
            session = FakeSession()
            code, out, err = self.run_cli(argv, session)
            self.assertEqual(code, 64, (argv, out, err))
            self.assertEqual(self.writes(session), [], argv)

    def test_no_command_is_usage_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(manage_write.cli(['--account', 'thedozers', '--config', str(self.config)]), 64)
        self.assertIn('명령 필요', err.getvalue())

    def test_write_failure_reads_jsonp_codes_and_non_dict_bodies(self):
        self.assertIsNone(manage_write.write_failure({'status': 200}, {'code': 200}))
        self.assertIsNotNone(manage_write.write_failure({'status': 200}, {'code': 402}))
        self.assertIsNone(manage_write.write_failure({'status': 200}, {'status': True}))
        self.assertIsNone(manage_write.write_failure({'status': 200}, [1]))
        self.assertIsNotNone(manage_write.write_failure({'status': 500}, None))
        self.assertEqual(manage_write.jsonp('/**/cb({"code":200})'), {'code': 200})


class SiteadminSessionTest(unittest.TestCase):
    def test_store_admin_gets_ctoken_and_csrf_pair_without_cookie_tokens(self):
        session = ApiSession({}, 'fixture-config')
        session.initialized = True
        session.cookie = lambda name, url: {'xman_us_t': 'x=1&ctoken=fixture-ctoken',
                                            '_tb_token_': 'fixture-tb', 'ctoken': 'fixture-legacy'}.get(name)
        session.send = Mock(side_effect=[
            {'status': 200, 'text': json.dumps({'parameterName': 'csrf_token', 'token': 'fixture-csrf',
                                                'headerName': 'X-XSRF-TOKEN'})},
            {'status': 200, 'text': '{"success":true}'},
            {'status': 200, 'text': '{"success":true}'}])
        session.request('POST', 'https://siteadmin.alibaba.com/page/deletePage', {}, {'pageType': 6, 'pageId': 1})
        bootstrap, post = [c.args for c in session.send.call_args_list]
        self.assertEqual(bootstrap[1], 'https://siteadmin.alibaba.com/csrf/ajax?cToken=fixture-ctoken')
        self.assertEqual(post[1], 'https://siteadmin.alibaba.com/page/deletePage?cToken=fixture-ctoken&csrf_token=fixture-csrf')
        self.assertEqual(post[3], b'pageType=6&pageId=1')
        session.request('GET', 'https://siteadmin.alibaba.com/page/getPageTypeNew')
        self.assertIn('cToken=fixture-ctoken', session.send.call_args.args[1])
        self.assertNotIn('csrf_token', session.send.call_args.args[1])

    def test_store_admin_csrf_failure_is_reported(self):
        session = ApiSession({}, 'fixture-config')
        session.initialized = True
        session.cookie = lambda name, url: None
        session.send = Mock(return_value={'status': 200, 'text': '<html>'})
        with self.assertRaises(SessionError):
            session.request('POST', 'https://siteadmin.alibaba.com/publish', {'pageId': 1}, None)


if __name__ == '__main__':
    unittest.main()
