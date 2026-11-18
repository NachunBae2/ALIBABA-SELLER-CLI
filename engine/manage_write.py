"""광고 밖 셀러센트럴 조작(상품 표시·그룹·삭제, 쇼케이스, RFQ, 쿠폰·할인·행사, 배송 템플릿, 스토어 버전, 설정, 리뷰 요청).

기본 dry-run, --yes 로만 전송, 보내기 전 자동 백업(manage-backups), 보낸 뒤 리드백 대조.
요청 형상은 2026-11-18 실계정 1개 화면 캡처(specs.json 69건 / captured 38·captured-xform 2)에서
나온 것만 쓴다. 캡처에 없는 경로·본문은 만들지 않는다(형식 미검증 = --yes 거부, 종료 64).

등급(모든 계획 첫 줄):
  되돌릴 수 있음  — restore <백업> 으로 원값을 되돌린다.
  되돌릴 수 없음  — 삭제·중지·외부 발송 같은 단방향. --yes 로 보낼 수 있지만 restore 는 '불가: 이유'.
  비용/외부 영향  — 쿠폰(매출 할인)·바이어 발송·행사 서명·스토어 게시. --yes 로 보낼 수 있다.

종료: 0 정상/dry-run, 1 요청 실패, 2 연결 불가·세션 만료, 3 리드백 불일치,
64 사용법 오류·형식 미검증, 77 차단 계정(wonnischoice·linea).
"""
import argparse
import json
import re
import sys
import time
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

from api_session import ApiSession
from http_session import SessionError, compact, scrub
from seller_central_raw import DEFAULT_CONFIG, load_account

PRODUCT = 'https://us-productposting.alibaba.com'
WWW2 = 'https://www2.alibaba.com'
MARKETING = 'https://marketing.alibaba.com'
TEMPLATE = 'https://template.alibaba.com'
SITEADMIN = 'https://siteadmin.alibaba.com'
MESSAGE = 'https://message.alibaba.com'
PROFILE = 'https://profile.alibaba.com'
FB = 'https://fb.alibaba.com'
SOURCING = 'https://sourcing.alibaba.com'
MYSOURCING = 'https://mysourcing.alibaba.com'
RFQPOST = 'https://rfqposting.alibaba.com'

SHOWCASE_ADS = {'productLineId': 110101, 'productId': 110101009}   # 쇼케이스 화면의 ads 봉투(engine/ads_dashboard.py)
SHOWCASE_SLOTS = 20            # 쇼케이스 노출 슬롯 한도(캡처: 17/20)
BLOCKED_ACCOUNTS = ('wonnischoice', 'linea')

REVERSIBLE = '되돌릴 수 있음'
IRREVERSIBLE = '되돌릴 수 없음'
COST = '비용/외부 영향'
SOURCE_LABEL = {
    'captured': '형식 출처: captured (화면 캡처)',
    'captured-xform': '형식 출처: captured-xform (읽기 응답 변형)',
}
# action → (등급, 형식 출처). 표에 없는 action 은 만들지 않는다.
ACTIONS = {
    'products.offline': (REVERSIBLE, 'captured'),
    'products.online': (REVERSIBLE, 'captured'),
    'products.group': (REVERSIBLE, 'captured'),
    'products.delete': (IRREVERSIBLE, 'captured'),
    'showcase.add': (REVERSIBLE, 'captured'),
    'showcase.remove': (REVERSIBLE, 'captured'),
    'showcase.replace': (REVERSIBLE, 'captured'),
    'showcase.sort': (REVERSIBLE, 'captured'),
    'rfq.search-add': (REVERSIBLE, 'captured'),
    'rfq.search-notify': (REVERSIBLE, 'captured'),
    'rfq.search-delete': (IRREVERSIBLE, 'captured'),
    'rfq.quote': (IRREVERSIBLE, 'captured'),
    'rfq.quote-template': (REVERSIBLE, 'captured'),
    'promo.coupon.create': (COST, 'captured'),
    'promo.coupon.stop': (IRREVERSIBLE, 'captured-xform'),
    'promo.discount.end': (IRREVERSIBLE, 'captured-xform'),
    'promo.event.enroll': (IRREVERSIBLE, 'captured'),
    'shipping.rename': (REVERSIBLE, 'captured'),
    'shipping.default': (REVERSIBLE, 'captured'),
    'shipping.price-ratio': (REVERSIBLE, 'captured'),
    'shipping.auto-route': (REVERSIBLE, 'captured'),
    'shipping.carrier-add': (REVERSIBLE, 'captured'),
    'shipping.add-template': (IRREVERSIBLE, 'captured'),
    'store.version.rename': (REVERSIBLE, 'captured'),
    'store.version.publish': (REVERSIBLE, 'captured'),
    'store.version.delete': (IRREVERSIBLE, 'captured'),
    'settings.message': (REVERSIBLE, 'captured'),
    'settings.privacy': (REVERSIBLE, 'captured'),
    'reviews.invite': (IRREVERSIBLE, 'captured'),
}
# 되돌릴 수 없는 action 의 restore 문구(그대로 출력한다).
IRREVERSIBLE_NOTE = {
    'products.delete': '불가: 휴지통 복원 요청 형식 미캡처 — 24시간이 지나야 화면에서만 복원된다',
    'rfq.search-delete': '불가: 지운 저장 검색은 되살릴 수 없다(rfq search-add 로 다시 추가해야 한다)',
    'rfq.quote': '불가: 바이어에게 이미 발송됨(견적 권한 1회 소모)',
    'promo.coupon.stop': '불가: 중지한 쿠폰은 다시 발급할 수 없다(새 쿠폰을 만들어야 한다)',
    'promo.discount.end': '불가: 일찍 종료한 할인은 되살릴 수 없다(새 할인을 만들어야 한다)',
    'promo.event.enroll': '불가: 행사 약관 서명은 되돌리는 형식이 캡처에 없다',
    'shipping.add-template': '불가: 템플릿 삭제 버튼 UI 없음(specs not-capturable)',
    'store.version.delete': '불가: 삭제 후 복원 불가(화면 문구: 삭제 후에는 복원할 수 없습니다)',
    'reviews.invite': '불가: 바이어에게 이미 발송됨',
}


class Usage(Exception):
    pass


class Unverified(Exception):
    pass


class Blocked(Exception):
    """Account refused for seller-center writes (exit 77)."""


# ---- 공통 --------------------------------------------------------------------------------------

def get_in(value, *keys):
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def short(value):
    return compact(scrub(value))[:240] if value is not None else ''


def jsonp(text):
    """JSON 또는 JSONP(callback({...})) 본문을 dict/list 로. 아니면 None(HTML 확인 페이지)."""
    text = (text or '').strip()
    if text.startswith('/**/'):          # mysourcing JSONP 앞머리
        text = text[4:].lstrip()
    if not text:
        return None
    if text[0] in '{[':
        try:
            return json.loads(text)
        except ValueError:
            return None
    match = re.match(r'^[\w$.]+\((.*)\)\s*;?$', text, re.S)
    if match:
        try:
            return json.loads(match.group(1))
        except ValueError:
            return None
    return None


def list_at(value, path, what):
    """응답의 정해진 칸(점 경로, '' = 최상위)에서 목록을 꺼낸다. 칸이 비어 있으면(빈 목록·null) [] — 오류 아님.
    칸 자체가 없거나 목록이 아니면 응답 모양이 바뀐 것이므로 실패."""
    node = value
    for key in [k for k in path.split('.') if k]:
        if not isinstance(node, dict) or key not in node:
            raise SessionError('%s 읽기 실패: 응답에 %s 없음' % (what, path))
        node = node[key]
    if node is None:
        return []
    if not isinstance(node, list):
        raise SessionError('%s 읽기 실패: %s 가 목록이 아님' % (what, path or '응답'))
    return node


def read_json(session, method, url, query, params, what, encoding='form', referer=None):
    response = session.request(method, url, query, params, encoding, referer)
    if not 200 <= response.get('status', 0) < 300:
        raise SessionError('%s 읽기 실패: HTTP %s' % (what, response.get('status')))
    value = jsonp(response['text'])
    if value is None:
        raise SessionError('%s 읽기 실패: JSON 응답 아님' % what)
    return value


def write_failure(response, value):
    """None 이면 서버가 받았다. 아니면 짧은 사유(토큰·원문 없이)."""
    status = response.get('status')
    if not isinstance(status, int) or not 200 <= status < 300:
        return 'HTTP %s: %s' % (status, short(value) or 'JSON 응답 아님')
    if not isinstance(value, dict):
        return None                      # HTML 확인 페이지·배열 — 2xx 면 접수로 본다
    keys = ('status', 'code', 'ok', 'success', 'result', 'error', 'errorVO', 'message', 'msg')
    rejected = (value.get('ok') is False or value.get('success') is False or value.get('result') is False
                or bool(value.get('errorVO')) or value.get('error') not in (None, False, '', 0))
    for key in ('status', 'code'):
        flag = value.get(key)
        if flag is True:
            continue                     # RfqMySearchNotifyAjax 는 {"status": true}
        if flag is not None and str(flag).lower() not in ('200', '1000', '0', 'ok', 'success'):
            rejected = True
    if not rejected:
        return None
    return short({k: value[k] for k in keys if k in value}) or 'HTTP %s 거부' % status


def flat_args(**kwargs):
    return argparse.Namespace(**kwargs)


def request(method, url, query=None, params=None, encoding='form', label=None, referer=None, fill=None):
    """fill: 보낼 때 화면에서 읽어 붙일 토큰 종류(product·rfq·message). 값은 계획·백업·출력에 남지 않는다."""
    req = {'method': method, 'url': url, 'query': query or {}, 'params': params or {},
           'encoding': encoding, 'label': label or url}
    if referer:
        req['referer'] = referer
    if fill:
        req['fill'] = fill
    return req


SECRET_KEY = re.compile(r'csrf|xsrf|token|^sign$|^_m_h5_tk|cookie|authorization', re.I)


def masked(value):
    """출력용: 토큰류 값은 *** 로 가린다(세션 쿠키 값이 계획에 섞여도 새지 않게)."""
    if isinstance(value, dict):
        return {k: ('***' if SECRET_KEY.search(str(k)) and value[k] not in (None, '') else masked(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [masked(v) for v in value]
    if isinstance(value, str) and '{{' in value:
        return '<세션 토큰>'
    return value


def printable(requests):
    """보낼 요청을 그대로 보여준다 — 토큰은 세션이 붙이므로 여기에 없다."""
    return [{k: masked(v) for k, v in r.items() if k in ('method', 'url', 'query', 'params', 'encoding')}
            for r in requests]


def send_one(session, req):
    query, params = dict(req['query']), dict(req['params'])
    if req.get('fill'):
        query, params = FILLERS[req['fill']](session, query, params)
    response = session.request(req['method'], req['url'], query, params, req['encoding'], req.get('referer'))
    if req.get('fill') == 'rfq':
        value = jsonp(response.get('text'))
        if isinstance(value, dict) and value.get('identityCode'):
            page_tokens(session)['rfq']['identityCode'] = value['identityCode']   # 화면도 응답 값으로 바꿔 둔다
    return response


# ---- 화면 토큰(보낼 때만 읽어 붙인다) -------------------------------------------------------------
PRODUCT_CONFIG = PRODUCT + '/product/asyQueryParameter.do'      # 상품 관리 화면 초기값(csrfToken.token)
RFQ_PAGE = SOURCING + '/rfq/rfq_search_list.htm'                # 내 검색 화면(_csrf_token_·my-search-identity-code)


def page_tokens(session):
    cache = getattr(session, '_manage_page_tokens', None)
    if cache is None:
        cache = {}
        setattr(session, '_manage_page_tokens', cache)
    return cache


def html_input(text, attr, name):
    """<input ... attr='name' ... value='...'> 의 value(속성 순서 무관)."""
    for tag in re.findall(r'<input\b[^>]*>', text or ''):
        if re.search(r'''\b%s=['"]%s['"]''' % (attr, re.escape(name)), tag):
            value = re.search(r'''\bvalue=['"]([^'"]*)['"]''', tag)
            return value.group(1) if value else None
    return None


def product_token(session):
    cache = page_tokens(session)
    if 'product' not in cache:
        value = read_json(session, 'GET', PRODUCT_CONFIG, {}, None, '상품 화면 토큰')
        token = get_in(value, 'csrfToken', 'token')
        if not token:
            raise SessionError('상품 화면 토큰 읽기 실패: csrfToken 없음')
        cache['product'] = token
    return cache['product']


def rfq_tokens(session):
    cache = page_tokens(session)
    if 'rfq' not in cache:
        response = session.request('GET', RFQ_PAGE, {}, None)
        text = response.get('text') or ''
        csrf, identity = html_input(text, 'name', '_csrf_token_'), html_input(text, 'id', 'my-search-identity-code')
        if response.get('status') != 200 or not csrf or not identity:
            raise SessionError('RFQ 화면 토큰 읽기 실패: _csrf_token_·identityCode 없음(로그인 화면이면 세션 만료)')
        cache['rfq'] = {'csrf': csrf, 'identityCode': identity}
    return cache['rfq']


def message_token(session):
    cache = page_tokens(session)
    if 'message' not in cache:
        response = session.request('GET', MESSAGE_SETTINGS_PAGE, {'source': 'portal'}, None)
        token = re.search(r'''["']?csrfTokenVal["']?\s*[:=]\s*["']([^"']+)''', response.get('text') or '')
        if response.get('status') != 200 or not token:
            raise SessionError('메시지 설정 화면 토큰 읽기 실패: csrfTokenVal 없음')
        cache['message'] = token.group(1)
    return cache['message']


def fill_product(session, query, params):
    """상품 관리 화면: group_ajax 본문 끝에 _csrf_token_ = asyQueryParameter.csrfToken.token."""
    return query, dict(params, _csrf_token_=product_token(session))


def fill_rfq(session, query, params):
    """내 검색 화면: params·type 뒤에 _csrf_token_·identityCode(화면 hidden input) — 캡처 순서 그대로."""
    tokens = rfq_tokens(session)
    head = {k: query[k] for k in ('params', 'type') if k in query}
    tail = {k: v for k, v in query.items() if k not in head and k not in ('_csrf_token_', 'identityCode')}
    return dict(head, _csrf_token_=tokens['csrf'], identityCode=tokens['identityCode'], **tail), params


def fill_message(session, query, params):
    """메시지 설정 화면: json.params 안 adminFeedback 뒤에 _csrf_token_(화면 csrfTokenVal). 바깥 값은 세션이 붙인다."""
    body = json.loads(params['json'])
    inner, token = {}, message_token(session)
    for key, value in body['params'].items():
        if key != '_csrf_token_':
            inner[key] = value
        if key == 'adminFeedback':
            inner['_csrf_token_'] = token
    body['params'] = inner
    return query, dict(params, json=compact(body))


FILLERS = {'product': fill_product, 'rfq': fill_rfq, 'message': fill_message}


def run_requests(session, requests):
    """보낸다. 하나라도 거부되면 (거부 request, 사유) 를 돌려준다."""
    for req in requests:
        response = send_one(session, req)
        value = jsonp(response['text'])
        error = write_failure(response, value)
        if error:
            return req, error
    return None, None


# ---- 읽기(대상 id 찾기·리드백) -------------------------------------------------------------------
# 경로·파라미터·응답 칸은 화면이 실제로 부르는 것(2026-11-18 실계정 GET/조회 POST 확인).
# 정상 응답의 빈 목록은 [] 이다(오류 아님). 칸 자체가 없으면 응답 모양이 바뀐 것 → 실패.

PRODUCT_LIST = PRODUCT + '/product/managementproducts/asyQueryProductsList.do'
PRODUCT_LIST_QUERY = {'statisticsType': 'month', 'repositoryType': 'all', 'imageType': 'all', 'status': 'all',
                      'displayStatus': '', 'showPowerScore': 'true', 'lang': 'en_US'}
PRODUCT_PAGE = 50
SHOWCASE_REFERER = WWW2 + '/showcase/index.htm'
SHOWCASE_LIST_DATA = {'orderBy': 'ad_group_sort', 'order': 'desc', 'adgroupOnlineStatus': 1, 'page': 1, 'size': 50}
SEARCH_LIST = SOURCING + '/rfq/ajax/my_search_list_ajax.do'
SEARCH_AJAX = MYSOURCING + '/rfq/mySearch/rfq_my_search_ajax.do'
SEARCH_NOTIFY = MYSOURCING + '/rfq/mySearch/RfqMySearchNotifyAjax.do'
COUPON_LIST = MARKETING + '/coupon/v2/querySellerCouponListAjax.do'
COUPON_QUERY = {'pageNo': 1, 'pageSize': 20}
ACTIVITY_LIST = MARKETING + '/promotion/querySellerActivityListAjax.do'
ACTIVITY_QUERY = {'pageNo': 1, 'pageSize': 10}
TEMPLATE_LIST = TEMPLATE + '/luyou/ggs/getTemplateList.json'
TEMPLATE_OVERVIEW = TEMPLATE + '/luyou/ggs/shippingTemplates/getTemplateInfoOverview.json'
VERSION_HOME_TYPE = 6          # 스토어 홈(버전 목록이 있는 페이지 종류)
VERSION_LIST = SITEADMIN + '/page/list/keyword/packageId'
MESSAGE_SETTINGS_PAGE = MESSAGE + '/feedbackset/basic.htm'
PRIVACY_PAGE = PROFILE + '/privacy/privacySettingShow.htm'


def read_products(session, want=None):
    """상품 관리 목록(화면: asyQueryProductsList, products[]). want 의 id 를 다 찾으면 페이지를 그만 넘긴다.
    status = 표시상태(displayStatus y/n → on/off), groupId = 가장 깊은 그룹(0 = 미분류)."""
    want = {str(p) for p in want or ()}
    products, page = [], 1
    while True:
        value = read_json(session, 'GET', PRODUCT_LIST, dict(PRODUCT_LIST_QUERY, page=page, size=PRODUCT_PAGE),
                          None, '상품 목록')
        rows = list_at(value, 'products', '상품 목록')
        for row in rows:
            if not isinstance(row, dict) or row.get('id') is None:
                continue
            level = next((n for n in ('3', '2', '') if row.get('groupId' + n) not in (None, 0, '0', '')), None)
            products.append({'id': row['id'], 'subject': row.get('subject'),
                             'status': norm_status(row.get('displayStatus')),
                             'approval': row.get('status'),
                             'groupId': 0 if level is None else row.get('groupId' + level),
                             'groupName': None if level is None else row.get('groupName' + (level or '1')) or None})
        total = value.get('count') if isinstance(value, dict) else None
        if not rows or len(rows) < PRODUCT_PAGE or (isinstance(total, int) and len(products) >= total):
            return products
        if want and want <= {str(p['id']) for p in products}:
            return products
        page += 1


def product_status(products, pid):
    for row in products:
        if str(row['id']) == str(pid):
            return row
    return None


def read_groups(session):
    value = read_json(session, 'GET', PRODUCT + '/product/group_query_ajax.do',
                      {'event': 'listGroupCombine', 'lang': 'en_US'}, None, '상품 그룹')
    groups = []
    for row in list_at(value, 'data', '상품 그룹'):
        if isinstance(row, dict) and row.get('id') is not None and row.get('name'):
            groups.append({'id': row['id'], 'name': row['name']})
    return groups


def showcase_post(session, path, data, what):
    value = read_json(session, 'POST', WWW2 + path, {}, {'ads': SHOWCASE_ADS, 'type': 'normal', 'data': data},
                      what, referer=SHOWCASE_REFERER)
    if not isinstance(value, dict) or value.get('ok') is not True or str(value.get('status')) != '1000':
        raise SessionError('%s 읽기 실패: %s' % (what, short({k: value.get(k) for k in ('status', 'ok', 'error')}
                                                         if isinstance(value, dict) else value)))
    return value


def showcase_campaign(session, explicit=None):
    """쇼케이스 캠페인 id — engine/ads_dashboard.py 와 같은 읽기 계약(ads 봉투 POST, data = 캠페인 id)."""
    if explicit is not None:
        return positive_int(explicit, '--campaign')
    data = showcase_post(session, '/api/campaign/find/showcase', {}, '쇼케이스 캠페인').get('data')
    if type(data) is int and data > 0 or isinstance(data, str) and data.isdigit() and int(data) > 0:
        return int(data)
    raise SessionError('쇼케이스 캠페인 id 를 읽지 못함 — --campaign 으로 지정')


def read_showcase_groups(session, cid):
    """노출 중 쇼케이스(화면: adgroup 목록 data[], 광고그룹 id = adGroupId). index 는 화면 순서(1부터)."""
    value = showcase_post(session, '/api/campaign/%d/adgroup' % cid, SHOWCASE_LIST_DATA, '쇼케이스 목록')
    groups = []
    for row in list_at(value, 'data', '쇼케이스 목록'):
        if isinstance(row, dict) and row.get('adGroupId') is not None:
            groups.append({'id': row['adGroupId'], 'productId': row.get('productId'),
                           'status': row.get('adGroupStatus'), 'index': len(groups) + 1})
    return groups


def read_saved_searches(session):
    """RFQ 내 검색(화면: sourcing my_search_list_ajax — 최상위 배열, 행 {id, keyword, ...})."""
    value = read_json(session, 'GET', SEARCH_LIST, {}, None, 'RFQ 저장 검색')
    return [{'id': row.get('id'), 'keyword': row.get('keyword')}
            for row in list_at(value, '', 'RFQ 저장 검색') if isinstance(row, dict) and row.get('id') is not None]


def read_search_notify(session):
    """내 검색 알림(TradeManager) 현재값 — 계정 하나에 스위치 하나(op=view → notify.sendAtm Y/N)."""
    value = read_json(session, 'GET', SEARCH_NOTIFY, {'op': 'view'}, None, 'RFQ 내 검색 알림')
    atm = get_in(value, 'notify', 'sendAtm')
    if not isinstance(value, dict) or value.get('status') is not True or atm not in ('Y', 'N'):
        raise SessionError('RFQ 내 검색 알림 읽기 실패: notify.sendAtm 없음')
    return atm


def marketing_rows(session, url, query, path, what):
    value = read_json(session, 'GET', url, query, None, what)
    if not isinstance(value, dict) or str(value.get('code')) != '200':
        raise SessionError('%s 읽기 실패: code %s' % (what, value.get('code') if isinstance(value, dict) else '?'))
    return list_at(value, path, what)


def read_coupons(session):
    """쇼핑 쿠폰(화면: data.sellerCouponTemplateList). 행 키는 화면 행 렌더링 기준(couponTemplateId·name·status)."""
    rows = marketing_rows(session, COUPON_LIST, COUPON_QUERY, 'data.sellerCouponTemplateList', '쿠폰 목록')
    return [{'id': r.get('couponTemplateId'), 'name': r.get('name'), 'status': r.get('status')}
            for r in rows if isinstance(r, dict)]


def read_activities(session):
    """제품 할인(화면: data.sellerActivityList)."""
    rows = marketing_rows(session, ACTIVITY_LIST, ACTIVITY_QUERY, 'data.sellerActivityList', '할인 목록')
    return [{'id': r.get('activityId'), 'name': r.get('activityName') or r.get('name'), 'status': r.get('status')}
            for r in rows if isinstance(r, dict)]


def read_templates(session):
    """배송 템플릿 탭(화면: data.templateSimplifyInfoList, ftId = id)."""
    value = read_json(session, 'GET', TEMPLATE_LIST, {}, None, '배송 템플릿 목록')
    templates = []
    for row in list_at(value, 'data.templateSimplifyInfoList', '배송 템플릿 목록'):
        if not isinstance(row, dict) or row.get('id') is None or not row.get('title'):
            continue
        location = next((d.get('id') for d in row.get('dispatchLocationList') or [] if isinstance(d, dict)), None)
        templates.append({'ftId': row['id'], 'title': row['title'], 'templateType': row.get('templateType'),
                          'isDefault': row.get('isDefault') is True, 'dispatchLocation': location})
    return templates


def read_template_body(session, template, what='배송 템플릿'):
    """템플릿 화면이 읽는 원본(getTemplateInfoOverview, ftId + 발송지)."""
    query = {'ftId': str(template['ftId'])}
    if template.get('dispatchLocation'):
        query['dispatchLocation'] = template['dispatchLocation']
    value = read_json(session, 'GET', TEMPLATE_OVERVIEW, query, None, what)
    body = value.get('data') if isinstance(value, dict) else None
    if not isinstance(body, dict) or not isinstance(body.get('carrierList'), list):
        raise SessionError('%s 본문 읽기 실패: data.carrierList 없음' % what)
    return body


FARE_TABLES = ('byWeightFreightDetailsDTO', 'byNumberFreightDetailsDTO', 'byOrderFreightDetailsDTO')


def template_update_body(overview):
    """읽은 원본 → 화면이 updateTemplate.json 에 보내는 본문(json=...).
    smartEntrust 템플릿 캡처 본문(specs 34)과 칸·값이 같다(2026-11-18 실계정 대조). 바꿀 칸만 호출한 쪽이 덮어쓴다."""
    location = next((d.get('countryCode') for d in overview.get('dispatchLocationList') or [] if isinstance(d, dict)),
                    None)
    zip_code = overview.get('dispatchZipCode')
    carriers = []
    for carrier in overview.get('carrierList') or []:
        details = carrier.get('freightTemplateDetailList') or []
        if not details:
            continue                       # 화면에 안 붙은 배송사(선택지)
        info = get_in(carrier, 'carrierInfoMap', 'wtd') or {}
        entry = {}
        warehouse = details[0].get('warehouseDTO')
        if warehouse:
            entry['selectedWarehouse'] = {'name': warehouse.get('name'), 'warehouseCode': warehouse.get('warehouseCode')}
        entry.update({'carrierCode': info.get('carrierCode') or details[0].get('carrierCode'),
                      'dispatchZipCode': zip_code, 'dispatchLocation': location,
                      'hasSurcharge': details[0].get('hasSurcharge'), 'detailList': []})
        for detail in details:
            source = detail.get('freightDetailsDTO') or {}
            fares = {k: source[k] for k in FARE_TABLES if source.get(k) is not None}
            fares['currency'] = source.get('currency') or 'USD'      # 캡처 순서: 요금표 → currency → discount
            fares.update({k: v for k, v in source.items() if k not in FARE_TABLES and v is not None})
            entry['detailList'].append({
                'freightType': detail.get('freightType'), 'countries': detail.get('countries'),
                'minFreightTime': detail.get('minFreightTime'), 'maxFreightTime': detail.get('maxFreightTime'),
                'shippingType': detail.get('shippingType'), 'isAliCarrier': info.get('isAliCarrier'),
                'destinations': detail.get('destinations'),
                'enableDeliveryGuarantee': detail.get('enableDeliveryGuarantee'), 'id': detail.get('id'),
                'freightDetailsDTO': fares})
        entry['dropShipping'] = carrier.get('dropShipping')
        entry['tradeTerm'] = carrier.get('tradeTerm')
        carriers.append(entry)
    return {'title': overview.get('title'), 'ftId': str(overview.get('ftId')), 'dispatchLocation': location,
            'dispatchZipCode': zip_code, 'templateType': overview.get('templateType'), 'carrierList': carriers,
            'insertCarriers': overview.get('insertCarriers'), 'lastCarrierMap': {location: overview.get('carriers') or []},
            'discount': overview.get('discount'), 'countriesForTemplate': overview.get('countries') or []}


def unverified_fares(body):
    """요금표(무게·수량·주문별) 칸이 있는 배송사 — 캡처 본문이 그 앞에서 잘려 형식 미검증."""
    return sorted({c['carrierCode'] for c in body['carrierList'] for d in c['detailList']
                   if any(k in d['freightDetailsDTO'] for k in FARE_TABLES)})


def siteadmin_read(session, path, query, what):
    value = read_json(session, 'GET', SITEADMIN + path, query, None, what)
    if not isinstance(value, dict) or value.get('success') is not True:
        raise SessionError('%s 읽기 실패: success 아님' % what)
    return value.get('result')


def read_versions(session):
    """스토어 홈 버전(화면: getPageTypeNew → getPagePackageList → page/list/keyword/packageId?pagePackageId).
    라이브 = 묶음의 currentPageId(게시 중인 버전)."""
    types = siteadmin_read(session, '/page/getPageTypeNew', {}, '스토어 페이지 종류')
    home = next((t for t in types or [] if isinstance(t, dict) and t.get('pageType') == VERSION_HOME_TYPE), None)
    if home is None:
        raise SessionError('스토어 페이지 종류 읽기 실패: 홈(pageType 6) 없음')
    packages = siteadmin_read(session, '/package/getPagePackageList',
                              {'currentPage': 1, 'pageSize': 10, 'keyword': '', 'pageType': VERSION_HOME_TYPE,
                               'pageTemplateId': home.get('pageTemplateDetailId'),
                               'commonPageType': home.get('commonPageType')}, '스토어 버전 묶음')
    versions = []
    for package in list_at(packages or {}, 'data', '스토어 버전 묶음'):
        if not isinstance(package, dict) or package.get('pagePackageId') is None:
            continue
        rows = siteadmin_read(session, '/page/list/keyword/packageId', {'pagePackageId': package['pagePackageId']},
                              '스토어 버전 목록')
        for row in list_at({'r': rows}, 'r', '스토어 버전 목록'):
            if isinstance(row, dict) and row.get('pageId') is not None:
                versions.append({'pageId': row['pageId'], 'name': row.get('pageName'),
                                 'package': package.get('pagePackageName'),
                                 'packageId': package['pagePackageId'],
                                 'live': str(row['pageId']) == str(package.get('currentPageId')),
                                 'published': row.get('pageDisplayStatus') == 'published',
                                 'released': row.get('publishTime'),
                                 'deletable': row.get('deletable') is not False,
                                 'commonPageType': home.get('commonPageType')})
    return versions


def read_message_settings(session):
    """메시지 설정 현재값 — 화면 HTML 의 window.ASSIGN_DATA 를 화면 번들(inquiry-assign)이 만드는 그대로 params 로."""
    response = session.request('GET', MESSAGE_SETTINGS_PAGE, {'source': 'portal'}, None)
    if response.get('status') != 200:
        raise SessionError('메시지 설정 읽기 실패: HTTP %s' % response.get('status'))
    data = assign_data(response['text'])
    members = data.get('inquiryAutoDistributeMemberList')
    if not isinstance(members, list):
        raise Unverified('메시지 설정 현재값(ASSIGN_DATA)을 읽지 못함 — 화면 구조 변경')
    default = next((m for m in members if isinstance(m, dict) and m.get('defaultConnect')), {})
    params = {'defaultContact': default.get('memberSeq'), 'cc': data.get('ccFlag'), 'tmCc': data.get('tmCcFlag'),
              'isCustomerFirst': data.get('isCustomerFirst'), 'rule': data.get('type'),
              'adminFeedback': {'autoDistribute': data.get('adminInquiryAutoDistribute'), 'accountStatus': [],
                                'hlbAccountStatus': []},
              'syncRuleToIm': data.get('syncRuleToIm'), 'syncAutoTurnToIm': data.get('syncAutoTurnToIm'),
              'subTransferRec': data.get('subTransferRec'), 'highLevelBuyerTurn': data.get('highLevelBuyerTurn')}
    return {'params': params,
            'members': [m.get('memberSeq') for m in members if isinstance(m, dict) and m.get('memberSeq') is not None]}


def assign_data(html):
    """window.ASSIGN_DATA = { key: <JSON 또는 'a' == 'b'>, ... } 를 dict 로(이름 등 값은 그대로 두고 쓰지 않는다)."""
    start = html.find('ASSIGN_DATA = {')
    end = html.find('</script>', start)
    if start < 0 or end < 0:
        raise Unverified('메시지 설정 현재값(ASSIGN_DATA)을 읽지 못함 — 화면 구조 변경')
    data = {}
    for line in html[start + len('ASSIGN_DATA = {'):end].splitlines():
        match = re.match(r'\s*([A-Za-z_]\w*)\s*:\s*(.*?)\s*,?\s*$', line)
        if not match:
            continue
        key, raw = match.groups()
        compare = re.fullmatch(r"'([^']*)'\s*==\s*'([^']*)'", raw)
        if compare:
            data[key] = compare.group(1) == compare.group(2)
            continue
        if raw.startswith("'") and raw.endswith("'"):
            data[key] = raw[1:-1]
            continue
        try:
            data[key] = json.loads(raw)
        except ValueError:
            data[key] = None
    return data


class _PrivacyForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = None
        self.radios, self.children = [], []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if self.depth is None:
            if tag == 'form' and attr.get('id') == 'J-form':
                self.depth = 0
            return
        if tag == 'form':
            self.depth += 1
        if tag == 'input' and attr.get('type') == 'radio' and attr.get('name'):
            self.radios.append(attr)
        if attr.get('data-parent') is not None and attr.get('data-key') is not None:
            self.children.append(attr)

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        if self.depth is not None and tag == 'form':
            self.depth = None if self.depth == 0 else self.depth - 1


def read_privacy(session):
    """개인정보 설정 — 화면 폼(#J-form) 라디오를 화면 스크립트(privacy_2json)와 같은 규칙으로 privaceSetting 으로.
    privaceSetting = {data-key: {선택값: {자식 data-key: data-value}}}, 자식 = data-parent '<name>-<값>'."""
    response = session.request('GET', PRIVACY_PAGE, {}, None)
    if response.get('status') != 200:
        raise SessionError('개인정보 설정 읽기 실패: HTTP %s' % response.get('status'))
    form = _PrivacyForm()
    form.feed(response['text'])
    if not form.radios:
        raise Unverified('개인정보 설정 현재값(#J-form 라디오)을 읽지 못함 — 화면 구조 변경')
    options, names, current = {}, {}, {}
    for radio in form.radios:
        key, value = radio.get('data-key'), radio.get('value')
        if key is None or value is None:
            continue
        names[key] = radio['name']
        children = {c['data-key']: c.get('data-value') for c in form.children
                    if c.get('data-parent') == '%s-%s' % (radio['name'], value)}
        options.setdefault(key, {})[value] = children
        if 'checked' in radio:
            current[key] = value
    setting = {key: {value: options[key][value]} for key, value in current.items()}
    return {'setting': setting, 'options': options}


# ---- 계획 공통 ----------------------------------------------------------------------------------

def width(text):
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)


def pad(text, size):
    return text + ' ' * (size - width(text))


def grade_line(plan):
    grade = plan['grade']
    if plan.get('grade_note'):
        return '등급: %s' % plan['grade_note']
    if grade == REVERSIBLE:
        return '등급: %s — restore <백업> 으로 원값을 되돌린다' % grade
    if grade == COST:
        return '등급: %s — %s' % (grade, plan.get('effect') or '매출·외부에 영향')
    return '등급: %s — %s' % (grade, plan.get('effect') or '단방향 변경')


def make_plan(action, *, target, rows, requests, reads, before, after, restore=None, effect=None,
              grade_note=None, warnings=None, blocked=None, readback=None):
    grade, source = ACTIONS[action]
    plan = {'action': action, 'grade': grade, 'source': source, 'target': target, 'rows': rows,
            'requests': requests, 'reads': reads, 'before': before, 'after': after,
            'restore': restore or {'note': IRREVERSIBLE_NOTE.get(action, '불가: 되돌릴 요청 형식 없음')},
            'effect': effect, 'grade_note': grade_note, 'warnings': warnings or [], 'blocked': blocked,
            'readback': readback}
    return plan


def write_backup(account, plan, *, root=None):
    directory = Path(root) if root else backup_root(account)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%dT%H%M%S') + '-%06d' % (int(time.time() * 1000000) % 1000000)
    stem = '%s-%s-%s' % (account['name'], plan['action'].replace('.', '-'), stamp)
    target, n = directory / (stem + '.json'), 1
    while target.exists():
        n += 1
        target = directory / ('%s-%d.json' % (stem, n))
    record = {'account': account['name'], 'action': plan['action'], 'target': plan['target'],
              'createdAt': time.strftime('%Y-%m-%dT%H:%M:%S'), 'before': plan['before'],
              'after': plan['after'], 'request': printable(plan['requests']), 'restore': plan['restore'],
              'note': 'restore <이 파일> 로 원값 복원(같은 규칙: 기본 dry-run, --yes).'}
    target.write_text(json.dumps(record, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return target


def backup_root(account):
    base = Path(account['outDir']) if account.get('outDir') else Path.home() / '30_Assets' / account['name'] / 'seller-report'
    return base / 'manage-backups'


def list_backups(account, *, root=None):
    directory = Path(root) if root else backup_root(account)
    if not directory.is_dir():
        return []
    rows = []
    for file in sorted(directory.glob('*.json'), reverse=True):
        try:
            record = json.loads(file.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            rows.append({'file': str(file), 'note': '읽기 실패'})
            continue
        rows.append({'file': str(file), 'account': record.get('account'), 'action': record.get('action'),
                     'target': record.get('target'), 'createdAt': record.get('createdAt'),
                     'restorable': 'note' not in (record.get('restore') or {'note': ''})})
    return rows


# ---- 실행 --------------------------------------------------------------------------------------

def show_plan_or_run(account, plan, session, *, dry_run):
    print(grade_line(plan))
    print('계획: %s — %s' % (plan['action'], plan['target']))
    print(SOURCE_LABEL[plan['source']])
    print()
    shown = [(str(r['field']), '—' if r['current'] is None else str(r['current']),
              '—' if r['after'] is None else str(r['after'])) for r in plan['rows']]
    widths = [max([width(h)] + [width(row[i]) for row in shown]) for i, h in enumerate(('필드', '현재'))]
    print('%s | %s | %s' % (pad('필드', widths[0]), pad('현재', widths[1]), '변경후'))
    for field, current, after in shown:     # 값은 자르지 않는다(무엇이 바뀌는지 그대로 보이게)
        print('%s | %s | %s' % (pad(field, widths[0]), pad(current, widths[1]), after))
    print()
    print('보낼 요청 (토큰 제외):')
    print(json.dumps(printable(plan['requests']), ensure_ascii=False, indent=1))
    print('보낼 뒤 읽을 곳 (리드백):')
    print(json.dumps([{'method': r['method'], 'url': r['url'], 'query': r['query']} for r in plan['reads']],
                     ensure_ascii=False, indent=1) if plan['reads'] else '(읽기 경로 미캡처 — 보낸 요청만 확인)')
    print()
    for warning in plan['warnings']:
        print('주의: ' + warning)
    if plan['restore'].get('note'):
        print('되돌리기: ' + plan['restore']['note'])
    elif plan['restore'].get('argv'):
        print('되돌리기: alibaba-manage %s  (또는 restore <백업>)' % ' '.join(plan['restore']['argv']))
    else:
        print('되돌리기: restore <백업> (원래 요청 본문 재전송)')
    print()
    if plan['blocked']:
        print('--yes 불가: ' + plan['blocked'])
    if dry_run:
        print('dry-run: 아무것도 보내지 않음. 실제로 보내려면 --yes.')
        return 0
    if plan['blocked']:
        raise Unverified(plan['blocked'])
    path = write_backup(account, plan)
    plan['backupPath'] = str(path)
    print('백업: %s' % path)
    print()
    code = execute(session, plan)
    if code == 0 and plan['action'] == 'promo.coupon.create':
        link_coupon_restore(session, plan)
    return code


def link_coupon_restore(session, plan):
    """생성 뒤에야 쿠폰 id 를 알 수 있다 — 백업의 restore 를 '중지' 요청으로 채워 둔다."""
    path = plan.get('backupPath')
    name = plan['after'].get('name')
    if not path or not name:
        return
    try:
        coupons = read_coupons(session)
    except SessionError as error:
        print('되돌리기 준비 실패: 쿠폰 목록을 다시 읽지 못함(%s) — promo coupon stop --id <id> 로 직접 중지' % error)
        return
    chosen = [c for c in coupons if (c.get('name') or '') == name and c.get('id') is not None]
    if not chosen:
        print('되돌리기 준비 실패: 목록에서 새 쿠폰을 찾지 못함 — promo coupon stop --id <id> 로 직접 중지')
        return
    record = json.loads(Path(path).read_text(encoding='utf-8'))
    record['restore'] = {'argv': ['promo', 'coupon', 'stop', '--id', str(chosen[-1]['id'])]}
    record['after']['couponTemplateId'] = chosen[-1]['id']
    Path(path).write_text(json.dumps(record, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('되돌리기 준비: promo coupon stop --id %s (백업에 기록)' % chosen[-1]['id'])


def execute(session, plan):
    error_request, error = run_requests(session, plan['requests'])
    if error:
        print('실패: %s 거부 — %s' % (error_request['label'], error))
        return 1
    if not plan['reads'] and not plan.get('readback'):
        print('리드백: 캡처에 읽기 경로 없음 — 보낸 요청만 확인(화면에서 확인 필요)')
        return 0
    return (plan.get('readback') or (lambda session, plan: readback_skip(session, plan)))(session, plan)


def readback_skip(session, plan):
    print('리드백: 이 조작은 읽기 경로가 캡처에 없다 — 보낸 요청만 확인')
    return 0


def readback_diff(rows):
    return {k: v for k, v in rows.items() if v}


def report_readback(plan, view, diff):
    print('리드백 (%s):' % plan['target'])
    print(json.dumps(view, ensure_ascii=False, indent=1))
    if diff:
        print('리드백 불일치: ' + json.dumps(diff, ensure_ascii=False))
        return 3
    print('리드백 일치: 변경 반영 확인')
    return 0


# ---- products ----------------------------------------------------------------------------------

def norm_status(value):
    """목록 표시상태(displayStatus y/n)·요청값(on/off)을 on/off 로."""
    text = str(value).strip().lower() if value is not None else ''
    if text in ('y', 'on', 'online'):
        return 'on'
    if text in ('n', 'off', 'offline'):
        return 'off'
    return text or None


def product_list_read():
    return request('GET', PRODUCT_LIST, dict(PRODUCT_LIST_QUERY, page=1, size=PRODUCT_PAGE), label='상품 목록')


def product_plan(args, session):
    sub = args.sub
    action = {'offline': 'products.offline', 'online': 'products.online', 'group': 'products.group',
              'delete': 'products.delete'}[sub]
    ids = [positive_int(p, '상품ID') for p in args.ids]
    products = read_products(session, ids)
    found = []
    missing = []
    for pid in ids:
        row = product_status(products, pid)
        (found if row else missing).append(row or pid)
    if missing:
        raise Usage('상품 목록에 없는 id: %s (목록 %d개)' % (', '.join(str(m) for m in missing), len(products)))
    if sub in ('offline', 'online'):
        want = 'on' if sub == 'online' else 'off'
        same = [r['id'] for r in found if r.get('status') == want]
        if same:
            raise Usage('이미 %s 상태: %s' % ('온라인' if want == 'on' else '오프라인', ', '.join(map(str, same))))
        requests = [request('POST', PRODUCT + '/product/managementproducts/asyChangeDisplayStatus.do',
                            {'lang': 'en_US'}, {'productIds': ','.join(str(i) for i in ids), 'newStatus': want},
                            label='asyChangeDisplayStatus')]
        rows = [dict(field='상품 표시상태(%s)' % ','.join(map(str, ids)),
                     current=[{str(r['id']): r.get('status')} for r in found], after={'newStatus': want})]
        opposite = 'online' if sub == 'offline' else 'offline'
        return make_plan(action, target='상품 %s' % ','.join(map(str, ids)), rows=rows, requests=requests,
                         reads=[product_list_read()],
                         before={str(r['id']): r.get('status') for r in found},
                         after={str(i): want for i in ids},
                         restore={'argv': ['products', opposite, '--ids'] + [str(i) for i in ids]},
                         effect='바이어 노출 %s' % ('시작' if want == 'on' else '중단'),
                         readback=readback_products)
    if sub == 'group':
        if not args.group_name or not str(args.group_name).strip():
            raise Usage('products group: --group <그룹이름> 필요')
        groups = read_groups(session)
        match = [g for g in groups if g['name'] == args.group_name.strip()]
        if not match:
            raise Usage('그룹 이름이 목록에 없음: %s (있는 그룹: %s)'
                        % (args.group_name, ', '.join(g['name'] for g in groups) or '없음'))
        target = match[0]
        same = [r['id'] for r in found if str(r.get('groupId')) == str(target['id'])]
        if same:
            raise Usage('이미 그룹 %s 에 있음: %s' % (target['name'], ', '.join(map(str, same))))
        requests = [request('POST', PRODUCT + '/product/group_ajax.do', {'event': 'adjustGroup'},
                            {'productIdList': ','.join(str(i) for i in ids), 'groupId': str(target['id'])},
                            label='group_ajax(adjustGroup)', fill='product')]
        rows = [dict(field='그룹 groupId → %s(%s)' % (target['name'], target['id']),
                     current=[{str(r['id']): r.get('groupName') or ('미분류' if not r.get('groupId') else r['groupId'])}
                              for r in found],
                     after=target['name'])]
        originals = {str(r['id']): r.get('groupId') for r in found}
        return make_plan(action, target='상품 %s → 그룹 %s' % (','.join(map(str, ids)), target['name']),
                         rows=rows, requests=requests, reads=[product_list_read()], before=originals,
                         after={str(i): target['id'] for i in ids}, restore=_group_restore(originals, groups),
                         effect='스토어 분류만 바뀜', readback=readback_products_group,
                         warnings=['_csrf_token_ 은 보낼 때 상품 화면 초기값(asyQueryParameter)에서 읽어 붙인다.'])
    # delete
    requests = [request('POST', PRODUCT + '/product/managementproducts/asyDelete.do', {'lang': 'en_US'},
                        {'productIds': ','.join(str(i) for i in ids), 'deleteType': 'product'},
                        label='asyDelete')]
    rows = [dict(field='삭제할 상품', current=[r.get('subject') for r in found], after='휴지통으로 이동'),
            dict(field='복원', current=None, after='불가 — 24시간 뒤 화면에서만')]
    return make_plan(action, target='상품 %s' % ','.join(map(str, ids)), rows=rows, requests=requests,
                     reads=[product_list_read()],
                     before={str(r['id']): r.get('subject') for r in found}, after={'deleted': ids},
                     effect='상품 내려감(휴지통)', readback=readback_products_deleted)


def _group_restore(originals, groups):
    """원래 그룹으로 되돌리는 요청을 미리 만든다(같은 group_ajax adjustGroup, 원래 groupId).
    원래 미분류(groupId 0)인 상품이 있으면 되돌릴 수 없다 — 그룹 해제 요청은 캡처에 없다."""
    ungrouped = sorted(pid for pid, gid in originals.items() if not gid)
    if ungrouped:
        return {'note': '불가: 원래 미분류 상품(%s) — 그룹 해제 요청 형식 미캡처' % ', '.join(ungrouped)}
    by_id = {str(g['id']): g['name'] for g in groups}
    by_group = {}
    for pid, gid in originals.items():
        by_group.setdefault(str(gid), []).append(str(pid))
    requests = [request('POST', PRODUCT + '/product/group_ajax.do', {'event': 'adjustGroup'},
                        {'productIdList': ','.join(pids), 'groupId': gid},
                        label='group_ajax(원값 복원: %s)' % by_id.get(gid, gid), fill='product')
                for gid, pids in by_group.items()]
    return {'requests': requests, 'action': 'products.group', 'target': '원래 그룹으로 복원'}


def readback_products(session, plan):
    products = read_products(session, list(plan['after']))
    view = {str(p['id']): p.get('status') for p in products}
    diff = {}
    for pid, want in plan['after'].items():
        got = view.get(str(pid))
        if got != str(want):
            diff[str(pid)] = {'기대': want, '실제': got}
    return report_readback(plan, {k: view.get(k) for k in plan['after']}, readback_diff(diff))


def readback_products_group(session, plan):
    products = read_products(session, list(plan['after']))
    view = {str(p['id']): p.get('groupId') for p in products if str(p['id']) in plan['after']}
    diff = {pid: {'기대': want, '실제': view.get(pid)} for pid, want in plan['after'].items()
            if str(view.get(pid)) != str(want)}
    return report_readback(plan, view, diff)


def readback_products_deleted(session, plan):
    products = read_products(session)
    have = {str(p['id']) for p in products}
    left = [str(i) for i in plan['after']['deleted'] if str(i) in have]
    return report_readback(plan, {'남은 상품 수': len(have)}, {'아직 목록에 있음': left} if left else {})


# ---- showcase ----------------------------------------------------------------------------------

def showcase_plan(args, session):
    sub = args.sub
    action = {'add': 'showcase.add', 'remove': 'showcase.remove', 'replace': 'showcase.replace',
              'sort': 'showcase.sort'}[sub]
    cid = showcase_campaign(session, getattr(args, 'campaign', None))
    groups = read_showcase_groups(session, cid)
    base = WWW2 + '/api/campaign/%d/adgroup/' % cid
    envelope = lambda data: {'ads': SHOWCASE_ADS, 'type': 'normal', 'data': compact(data)}

    def post(kind, data):
        return request('POST', base + kind, {}, envelope(data), label='adgroup/' + kind, referer=SHOWCASE_REFERER)

    reads = [request('POST', WWW2 + '/api/campaign/%d/adgroup' % cid, {},
                     {'ads': SHOWCASE_ADS, 'type': 'normal', 'data': SHOWCASE_LIST_DATA}, label='쇼케이스 목록')]
    if sub == 'add':
        ids = [positive_int(p, '상품ID') for p in args.ids]
        have = [g['productId'] for g in groups]
        if any(i in have for i in ids):
            raise Usage('이미 쇼케이스에 있는 상품: %s' % ', '.join(str(i) for i in ids if i in have))
        if len(groups) + len(ids) > SHOWCASE_SLOTS:
            raise Usage('쇼케이스 슬롯 한도 %d 초과: 지금 %d개 + 추가 %d개' % (SHOWCASE_SLOTS, len(groups), len(ids)))
        data = {'adgroupStatus': 1, 'campaignType': 9,
                'adGroupOperationList': [{'productId': i, 'onlineStatus': 1} for i in ids]}
        rows = [dict(field='추가할 상품', current=len(groups), after=ids),
                dict(field='슬롯(현재/한도)', current='%d/%d' % (len(groups), SHOWCASE_SLOTS),
                     after='%d/%d' % (len(groups) + len(ids), SHOWCASE_SLOTS))]
        return make_plan(action, target='쇼케이스 %s 상품 %s' % (cid, ','.join(map(str, ids))), rows=rows,
                         requests=[post('create', data)], reads=reads, before={'products': have},
                         after={'products': have + ids},
                         restore={'argv': ['showcase', 'remove', '--campaign', str(cid), '--ids'] + [str(i) for i in ids]},
                         effect='노출 슬롯 사용', readback=readback_showcase)
    if sub == 'remove':
        ids = [positive_int(p, '상품ID') for p in args.ids]
        by_product = {}
        for group in groups:
            by_product.setdefault(str(group['productId']), []).append(group['id'])
        missing = [i for i in ids if str(i) not in by_product]
        if missing:
            raise Usage('쇼케이스에 없는 상품: %s (있는 상품: %s)'
                        % (', '.join(map(str, missing)), ', '.join(sorted(by_product)) or '없음'))
        adgroup_ids = [gid for i in ids for gid in by_product[str(i)]]
        data = {'onlineStatus': 1, 'adGroupOperationList': [{'id': gid} for gid in adgroup_ids]}
        rows = [dict(field='제거할 광고그룹', current='노출 %d개' % len(groups), after=adgroup_ids),
                dict(field='남는 슬롯', current='%d/%d' % (len(groups), SHOWCASE_SLOTS),
                     after='%d/%d' % (len(groups) - len(adgroup_ids), SHOWCASE_SLOTS))]
        return make_plan(action, target='쇼케이스 %s 상품 %s' % (cid, ','.join(map(str, ids))), rows=rows,
                         requests=[post('delete', data)], reads=reads,
                         before={'adgroups': adgroup_ids, 'products': [g['productId'] for g in groups]},
                         after={'products': [g['productId'] for g in groups
                                             if str(g['productId']) not in {str(i) for i in ids}]},
                         restore={'argv': ['showcase', 'add', '--campaign', str(cid), '--ids'] + [str(i) for i in ids]},
                         effect='노출 슬롯 비움', readback=readback_showcase)
    if sub == 'replace':
        gid = positive_int(args.ids[0] if isinstance(args.ids, list) else args.ids, '광고그룹ID')
        new = positive_int(args.to, '상품ID')
        group = next((g for g in groups if str(g['id']) == str(gid)), None)
        if group is None:
            raise Usage('쇼케이스 광고그룹 id 가 목록에 없음: %s' % gid)
        old = group.get('productId')
        if str(old) == str(new):
            raise Usage('이미 그 상품이 들어 있음: %s' % new)
        data = {'adgroupStatus': 1, 'id': gid, 'oldEntityId': old, 'newEntityId': new}
        rows = [dict(field='광고그룹 %s 노출 상품' % gid, current=old, after=new)]
        return make_plan(action, target='쇼케이스 %s 슬롯 %s' % (cid, gid), rows=rows,
                         requests=[post('replace', data)], reads=reads,
                         before={'adgroupId': gid, 'productId': old}, after={'productId': new},
                         restore={'argv': ['showcase', 'replace', '--campaign', str(cid), '--ids', str(gid),
                                           '--to', str(old)]},
                         effect='노출 상품 변경', readback=readback_showcase)
    # sort
    gid = positive_int(args.ids[0] if isinstance(args.ids, list) else args.ids, '광고그룹ID')
    group = next((g for g in groups if str(g['id']) == str(gid)), None)
    if group is None:
        raise Usage('쇼케이스 광고그룹 id 가 목록에 없음: %s' % gid)
    target_index = positive_int(args.to_index, '옮길 순번')
    if target_index > len(groups):
        raise Usage('--to-index 는 1~%d: %r' % (len(groups), args.to_index))
    data = {'onlineStatus': 1, 'id': gid, 'srcIndex': group['index'], 'targetIndex': target_index}
    rows = [dict(field='광고그룹 %s 순서' % gid, current=group['index'], after=target_index)]
    return make_plan(action, target='쇼케이스 %s 슬롯 %s' % (cid, gid), rows=rows,
                     requests=[post('sort', data)], reads=reads,
                     before={'index': group['index'], 'adgroupId': gid}, after={'index': target_index},
                     restore={'argv': ['showcase', 'sort', '--campaign', str(cid), '--ids', str(gid),
                                       '--to-index', str(group['index'])]},
                     effect='노출 순서', readback=readback_showcase)


def readback_showcase(session, plan):
    # 계획의 읽기 URL 에서 캠페인 id 를 꺼낸다(하드코딩 없이 그 계획이 읽은 곳).
    match = re.search(r'/api/campaign/(\d+)/adgroup', plan['reads'][0]['url']) if plan.get('reads') else None
    cid = int(match.group(1)) if match else showcase_campaign(session)
    groups = read_showcase_groups(session, cid)
    view = {'adgroups': [{'id': g['id'], 'productId': g['productId'], 'index': g['index']} for g in groups],
            'slots': '%d/%d' % (len(groups), SHOWCASE_SLOTS)}
    diff = {}
    if 'products' in plan['after']:
        have = [str(g['productId']) for g in groups]
        want = [str(p) for p in plan['after']['products']]
        if sorted(have) != sorted(want):
            diff['상품'] = {'기대': want, '실제': have}
    if 'productId' in plan['after']:
        group = next((g for g in groups if str(g['id']) == str(plan['before'].get('adgroupId'))), None)
        got = None if group is None else group.get('productId')
        if str(got) != str(plan['after']['productId']):
            diff['상품'] = {'기대': plan['after']['productId'], '실제': got}
    if 'index' in plan['after']:
        group = next((g for g in groups if str(g['id']) == str(plan['before'].get('adgroupId'))), None)
        if group is None or int(group['index']) != int(plan['after']['index']):
            diff['순서'] = {'기대': plan['after']['index'], '실제': None if group is None else group['index']}
    return report_readback(plan, view, diff)


# ---- rfq ---------------------------------------------------------------------------------------

def search_list_read():
    return request('GET', SEARCH_LIST, {}, label='RFQ 저장 검색 목록')


def search_ajax(kind, params, label, **extra):
    """rfq_my_search_ajax(JSONP GET): params = 검색 조건 JSON 문자열, type = new|delete.
    _csrf_token_·identityCode 는 보낼 때 내 검색 화면(hidden input)에서 읽어 붙인다."""
    query = {'params': compact(params), 'type': kind}
    query.update(extra)
    return request('GET', SEARCH_AJAX, query, label=label, referer=RFQ_PAGE, fill='rfq')


def pick_search(have, wanted):
    wanted = str(wanted or '').strip()
    chosen = [s for s in have if str(s['id']) == wanted] or \
             [s for s in have if (s.get('keyword') or '').lower() == wanted.lower()]
    if len(chosen) != 1:
        raise Usage('저장 검색을 하나로 정하지 못함: %r (있는 검색: %s)'
                    % (wanted, ', '.join('%s(%s)' % (s.get('keyword'), s.get('id')) for s in have) or '없음'))
    return chosen[0]


def rfq_plan(args, session):
    sub = args.sub
    if sub == 'search-add':
        keyword = str(args.keyword or '').strip()
        if not keyword:
            raise Usage('rfq search-add: --keyword 필요')
        have = read_saved_searches(session)
        if any((s.get('keyword') or '').lower() == keyword.lower() for s in have):
            raise Usage('이미 저장된 검색: %s' % keyword)
        params = {'keyword': keyword, 'categoryId': '', 'dateApproved': '', 'region': '', 'country': '',
                  'quantityMin': '', 'quantityMax': ''}
        rows = [dict(field='저장 검색', current='%d개' % len(have), after=keyword)]
        return make_plan('rfq.search-add', target='검색어 %r' % keyword, rows=rows,
                         requests=[search_ajax('new', params, 'rfq_my_search_ajax(type=new)')],
                         reads=[search_list_read()], before={'searches': len(have)}, after={'keyword': keyword},
                         restore={'argv': ['rfq', 'search-delete', '--search', keyword]}, effect='저장 검색 생성',
                         warnings=['_csrf_token_·identityCode 는 보낼 때 내 검색 화면에서 읽어 붙인다.'],
                         readback=readback_searches)
    if sub == 'search-delete':
        have = read_saved_searches(session)
        if not have:
            raise Usage('저장 검색이 없다(내 검색 0)')
        item = pick_search(have, args.search)
        rows = [dict(field='지울 저장 검색', current='%s(%s)' % (item.get('keyword'), item['id']), after='삭제'),
                dict(field='저장 검색 수', current=len(have), after=len(have) - 1)]
        return make_plan('rfq.search-delete', target='저장 검색 %s' % item.get('keyword'), rows=rows,
                         requests=[search_ajax('delete', {'id': str(item['id'])}, 'rfq_my_search_ajax(type=delete)',
                                               tracelog='list_del_my_search')],
                         reads=[search_list_read()], before={'id': item['id'], 'keyword': item.get('keyword')},
                         after={'deleted': item['id']}, effect='저장 검색 삭제',
                         warnings=['_csrf_token_·identityCode 는 보낼 때 내 검색 화면에서 읽어 붙인다.'],
                         readback=readback_searches)
    if sub == 'search-notify':
        want = 'Y' if args.state == 'on' else 'N'
        previous = read_search_notify(session)
        if previous == want:
            raise Usage('이미 알림 %s' % args.state)
        rows = [dict(field='내 검색 알림(TradeManager, 계정 전체)', current=previous, after=want)]
        return make_plan('rfq.search-notify', target='내 검색 알림', rows=rows,
                         requests=[request('GET', SEARCH_NOTIFY, {'op': 'update', 'atm': want},
                                           label='RfqMySearchNotifyAjax(op=update)')],
                         reads=[request('GET', SEARCH_NOTIFY, {'op': 'view'}, label='알림 현재값(op=view)')],
                         before={'atm': previous}, after={'atm': want},
                         restore={'argv': ['rfq', 'search-notify', 'on' if previous == 'Y' else 'off']},
                         effect='알림 메시지(판매자)', readback=readback_search_notify)
    if sub == 'quote':
        payload = quote_payload(args)
        req = request('POST', RFQPOST + '/quotation/ajax/rfq_quotation_post_ajax.do', {},
                      {'formData': compact(payload)}, label='rfq_quotation_post_ajax')
        rows = [dict(field='RFQ id', current=None, after=payload['rfqId']),
                dict(field='상품·가격', current=None,
                     after=payload['product'][0]['itemName'] + ' / ' + str(payload['product'][0]['price'])),
                dict(field='납기·결제·항구', current=None,
                     after='%s %s / %s / %s' % (args.leadtime_days, '일', args.payment, args.port))]
        return make_plan('rfq.quote', target='RFQ %s 견적' % payload['rfqId'], rows=rows, requests=[req],
                         reads=[], before={}, after={'rfqId': payload['rfqId']},
                         effect='바이어에게 견적 발송 · 견적 권한 1회 소모',
                         grade_note='되돌릴 수 없음 · 외부 발송 — 바이어에게 발송되고 견적 권한 1회를 쓴다',
                         warnings=['캡처에서 rfq{...}·uuid·pageId 값이 가려져 있다 — 필수값이면 서버가 거부한다(종료 1).'])
    # quote-template save
    payload = quote_payload(args)
    req = request('POST', RFQPOST + '/quotation/ajax/rfq_quotation_draft_post_ajax.do', {},
                  {'formData': compact(payload), 'method': 'insert'}, label='rfq_quotation_draft_post_ajax')
    rows = [dict(field='견적 양식 이름', current=None, after=args.name or payload['product'][0]['itemName']),
            dict(field='내용', current=None, after=payload['product'][0]['price'])]
    return make_plan('rfq.quote-template', target='견적 양식 %s' % (args.name or ''), rows=rows, requests=[req],
                     reads=[], before={}, after={'name': args.name}, effect='없음(템플릿)')


def quote_payload(args):
    rfq_id = positive_int(args.rfq, 'RFQ id')
    if not args.price:
        raise Usage('rfq quote: --price 필요')
    money(args.price, '--price')
    quantity = positive_int(args.quantity, '--quantity') if args.quantity else 1
    expiry = args.expiry or time.strftime('%Y-%m-%d')
    item = {'expiryFormatDate': expiry,
            'price': [{'quantity': quantity, 'fobPrice': float(args.price)}],
            'shippingTerms': args.shipping_terms or 'FOB',
            'quantityUnit': args.unit or 'Pieces',
            'fobPriceUnit': args.price_unit or 'USD',
            'paymentTerms': args.payment or 'T/T',
            'itemName': args.product_name or '',
            'remark': args.remark or '',
            'port': args.port or ''}
    return {'drafe': '', 'draftId': '', 'product': [item], 'other': {'details': args.remark or ''},
            'quoOpen': 'N', 'isMultiLang': 'N', 'industry': 'others', 'rfq': {'rfqId': rfq_id},
            'rfqId': rfq_id, 'umid_token': '', 'collina_ua': '', 'uuid': '', 'pageId': ''}


def readback_searches(session, plan):
    searches = read_saved_searches(session)
    view = {'저장 검색 수': len(searches), '검색': [s.get('keyword') for s in searches]}
    diff = {}
    if 'keyword' in plan['after']:
        want = str(plan['after']['keyword']).lower()
        if not any((s.get('keyword') or '').lower() == want for s in searches):
            diff['keyword'] = {'기대': plan['after']['keyword'], '실제': view['검색']}
    if 'deleted' in plan['after'] and any(str(s['id']) == str(plan['after']['deleted']) for s in searches):
        diff['삭제'] = {'기대': '목록에서 사라짐', '실제': '아직 있음'}
    return report_readback(plan, view, diff)


def readback_search_notify(session, plan):
    got = read_search_notify(session)
    diff = {} if got == plan['after']['atm'] else {'atm': {'기대': plan['after']['atm'], '실제': got}}
    return report_readback(plan, {'sendAtm': got}, diff)


# ---- promo -------------------------------------------------------------------------------------

def promo_plan(args, session):
    sub, sub2 = args.sub, getattr(args, 'sub2', None)
    if sub == 'coupon' and sub2 == 'create':
        if not args.name or not args.name.strip():
            raise Usage('promo coupon create: --name 필요')
        if not args.shop:
            raise Usage('promo coupon create: --shop <participateId> 필요(쿠폰 화면의 상점 id)')
        start = args.start or time.strftime('%Y-%m-%d 00:00:00')
        end = args.end or time.strftime('%Y-%m-%d 23:59:59')
        money(args.start_money, '--start-money')
        money(args.discount_money, '--discount-money')
        total = positive_int(args.total or '10', '--total')
        params = {'name': args.name.strip(), 'applyPlace': '-1', 'activityStartTime': start,
                  'activityEndTime': end, 'couponRangeType': 'shop', 'couponPerson': 'all',
                  'publishType': args.publish or 'IMMEDIATELY', 'totalCount': total,
                  'couponType': args.coupon_type or 'FULL_REDUCE_COUPON',
                  'couponParticipateList': compact([{'rangeType': 'shop', 'participateId': int(args.shop)}]),
                  'startMoney': int(round(float(args.start_money) * 100)),
                  'currencyCode': args.currency or 'USD',
                  'discountMoney': int(round(float(args.discount_money) * 100))}
        req = request('POST', MARKETING + '/coupon/v2/createSellerCouponAjax.do', {}, params,
                      label='createSellerCouponAjax')
        rows = [dict(field='쿠폰 이름', current=None, after=params['name']),
                dict(field='조건·할인', current=None, after='%s %s 이상 %s %s 할인'
                     % (params['currencyCode'], params['startMoney'] / 100, params['currencyCode'],
                        params['discountMoney'] / 100)),
                dict(field='기간·수량', current=None, after='%s ~ %s / %s장' % (start, end, total))]
        return make_plan('promo.coupon.create', target='쿠폰 %r' % params['name'], rows=rows, requests=[req],
                         reads=[request('GET', COUPON_LIST, COUPON_QUERY, label='쿠폰 목록')],
                         before={}, after={'name': params['name']},
                         restore={'note': '쿠폰 id 는 생성 뒤에만 알 수 있다 — promo coupon stop --id <쿠폰 목록의 '
                                         'couponTemplateId> 로 중지(그 전에는 되돌릴 대상이 없다)'},
                         effect='바이어에게 보이는 할인 쿠폰 발급(매출 할인)',
                         grade_note='비용/외부 영향 — 바이어가 쓰면 매출이 할인된다',
                         readback=readback_coupons)
    if sub == 'coupon' and sub2 == 'stop':
        cid = positive_int(args.id, '쿠폰 템플릿ID')
        req = request('GET', MARKETING + '/coupon/v2/terminateApplyAjax.do', {'couponTemplateId': cid}, None,
                      label='terminateApplyAjax')
        rows = [dict(field='쿠폰 템플릿 %s' % cid, current=None, after='중지(발급 종료)')]
        return make_plan('promo.coupon.stop', target='쿠폰 %s' % cid, rows=rows, requests=[req],
                         reads=[request('GET', COUPON_LIST, COUPON_QUERY, label='쿠폰 목록')],
                         before={}, after={'status': 'terminated'}, effect='쿠폰 발급 종료',
                         readback=readback_coupons)
    if sub == 'discount' and sub2 == 'end':
        aid = positive_int(args.id, '할인 activityId')
        req = request('POST', MARKETING + '/promotion/earlyTerminateActivityAjax.do', {},
                      {'activityId': aid}, label='earlyTerminateActivityAjax')
        rows = [dict(field='할인 activityId', current=aid, after='일찍 종료')]
        return make_plan('promo.discount.end', target='할인 %s' % aid, rows=rows, requests=[req],
                         reads=[request('GET', ACTIVITY_LIST, ACTIVITY_QUERY, label='할인 목록')],
                         before={}, after={'activityId': aid, 'status': 'ended'}, effect='할인 종료',
                         readback=readback_activities)
    # event enroll
    topic = positive_int(args.topic, '행사 topicId')
    protocol = positive_int(args.protocol, '약관 protocolId')
    req = request('GET', MARKETING + '/enroll/sign.do', {'topicId': topic, 'protocolId': protocol},
                  None, label='enroll/sign.do')
    rows = [dict(field='행사 topicId', current=None, after=topic),
            dict(field='약관 protocolId', current=None, after=protocol),
            dict(field='효과', current=None, after='행사 약관 서명(가격 이력에 영향 문구)')]
    return make_plan('promo.event.enroll', target='행사 %s' % topic, rows=rows, requests=[req],
                     reads=[], before={}, after={'topicId': topic, 'protocolId': protocol},
                     effect='행사 약관 서명 · 외부 영향', readback=readback_skip)


def readback_coupons(session, plan):
    coupons = read_coupons(session)
    view = {'쿠폰 수': len(coupons), '쿠폰': [c.get('name') for c in coupons]}
    diff = {}
    if 'name' in plan['after']:
        if not any((c.get('name') or '') == plan['after']['name'] for c in coupons):
            diff['쿠폰'] = {'기대': plan['after']['name'], '실제': [c.get('name') for c in coupons]}
    if 'status' in plan['after']:
        cid = plan['target'].split()[-1]
        got = next((c.get('status') for c in coupons if str(c.get('id')) == str(cid)), None)
        if got is not None and 'TERMINAT' not in str(got).upper() and 'STOP' not in str(got).upper():
            diff['status'] = {'기대': '중지', '실제': got}
    return report_readback(plan, view, diff)


def readback_activities(session, plan):
    activities = read_activities(session)
    aid = str(plan['after'].get('activityId'))
    got = next((a.get('status') for a in activities if str(a.get('id')) == aid), None)
    diff = {}
    if got is not None and 'END' not in str(got).upper() and 'TERMINAT' not in str(got).upper():
        diff['status'] = {'기대': '종료', '실제': got}
    return report_readback(plan, {'할인 수': len(activities), aid: got}, diff)


# ---- shipping ----------------------------------------------------------------------------------

UPDATE_TEMPLATE = TEMPLATE + '/luyou/ggs/updateTemplate.json'
SET_DEFAULT = TEMPLATE + '/luyou/settemplateDefault.json'


def template_id(value):
    return int(value) if str(value).isdigit() else value


def default_request(ft, label):
    """캡처: JSON 본문 {"json": "{\"id\": <ftId 숫자>}"}."""
    return request('POST', SET_DEFAULT, {}, {'json': compact({'id': template_id(ft)})}, encoding='json', label=label)


def shipping_plan(args, session):
    sub = args.sub
    templates = read_templates(session)
    if sub == 'add-template':
        title = str(args.title or '').strip()
        if not title:
            raise Usage('shipping add-template: --title 필요')
        if any(t['title'] == title for t in templates):
            raise Usage('같은 이름의 템플릿이 이미 있음: %s' % title)
        body = new_template_body(args, title)
        req = request('POST', TEMPLATE + '/luyou/ggs/addTemplate.json', {}, {'json': compact(body)},
                      label='addTemplate.json')
        rows = [dict(field='새 템플릿 이름', current=[t['title'] for t in templates], after=title),
                dict(field='유형 templateType', current=None, after='ordinary'),
                dict(field='배송 국가', current=None, after=args.countries)]
        return make_plan('shipping.add-template', target='템플릿 %r' % title, rows=rows, requests=[req],
                         reads=[request('GET', TEMPLATE_LIST, {}, label='템플릿 목록')],
                         before={'templates': [t['title'] for t in templates]}, after={'title': title},
                         effect='새 배송 템플릿', readback=readback_templates)
    name = str(args.template or '').strip()
    match = [t for t in templates if t['title'] == name or str(t['ftId']) == name]
    if len(match) != 1:
        raise Usage('템플릿을 하나로 정하지 못함: %r (있는 템플릿: %s)'
                    % (name, ', '.join('%s(%s)' % (t['title'], t['ftId']) for t in templates) or '없음'))
    current = match[0]
    overview = read_template_body(session, current)
    body = template_update_body(overview)
    fares = unverified_fares(body)
    fare_block = ('요금표(무게·수량·주문별) 칸이 있는 배송사 %s — 캡처 본문이 그 앞에서 잘려 형식 미검증. '
                  '화면에서 바꿔야 한다' % ', '.join(fares)) if fares else None
    original = {'requests': [request('POST', UPDATE_TEMPLATE, {}, {'json': compact(body)},
                                     label='updateTemplate(원값 복원)')],
                'action': 'shipping.restore', 'target': '템플릿 %r' % current['title']}
    reads = [request('GET', TEMPLATE_OVERVIEW, {'ftId': str(current['ftId']),
                                                'dispatchLocation': current.get('dispatchLocation') or ''},
                     label='getTemplateInfoOverview')]
    update = lambda new_body: request('POST', UPDATE_TEMPLATE, {}, {'json': compact(new_body)},
                                      label='updateTemplate.json')
    kept = dict(field='나머지 필드', current='원값 유지', after='읽은 원본(getTemplateInfoOverview)에서 화면과 같게 조립')
    smart = current.get('templateType') == 'smartEntrust'
    unconfirmed = ['스마트(smartEntrust) 템플릿의 updateTemplate 반영은 실계정 미확인 — 같은 경로의 이름 바꾸기는 '
                   '서버가 받고도 반영하지 않았다(실계정 2026-11-18). 리드백을 꼭 확인.'] if smart else []
    target = '템플릿 %r(%s)' % (current['title'], current['ftId'])

    if sub == 'rename':
        to = str(args.to or '').strip()
        if not to:
            raise Usage('shipping rename: --to 필요')
        if any(t['title'] == to for t in templates):
            raise Usage('같은 이름의 템플릿이 이미 있음: %s' % to)
        rows = [dict(field='템플릿 이름 title', current=body['title'], after=to), kept]
        smart_block = ('스마트(smartEntrust) 템플릿 이름 바꾸기: 서버가 받지만({"code":0}) 이름이 안 바뀜 '
                       '(실계정 2026-11-18). 캡처는 일반(ordinary) 템플릿에서 한 것') if smart else None
        return make_plan('shipping.rename', target=target, rows=rows, requests=[update(dict(body, title=to))],
                         reads=reads, before={'ftId': current['ftId'], 'title': body['title']},
                         after={'ftId': current['ftId'], 'title': to}, restore=original, effect='없음(이름만)',
                         blocked=smart_block or fare_block, readback=readback_templates)
    if sub == 'default':
        if current['isDefault']:
            raise Usage('이미 기본 템플릿: %s' % current['title'])
        previous = next((t for t in templates if t['isDefault']), None)
        rows = [dict(field='기본 템플릿', current=previous['title'] if previous else '(없음)', after=current['title'])]
        restore = {'requests': [default_request(previous['ftId'], 'settemplateDefault(원값 복원)')],
                   'action': 'shipping.restore', 'target': '기본 템플릿 %r' % previous['title']} if previous else \
            {'note': '불가: 지금 기본 템플릿이 없다 — 기본값 해제 요청 형식 미캡처'}
        return make_plan('shipping.default', target=target, rows=rows,
                         requests=[default_request(current['ftId'], 'settemplateDefault.json')],
                         reads=[request('GET', TEMPLATE_LIST, {}, label='템플릿 목록')],
                         before={'ftId': current['ftId'], 'previousDefault': previous and previous['ftId']},
                         after={'ftId': current['ftId'], 'default': current['title']},
                         restore=restore, effect='상품 기본 템플릿', readback=readback_templates)
    if sub == 'price-ratio':
        ratio = int(round(money(args.ratio, '--ratio') * 100))
        if ratio == body.get('discount'):
            raise Usage('이미 가격 조정 비율 %s' % ratio)
        rows = [dict(field='가격 조정 비율 discount(100 = 그대로)', current=body.get('discount'), after=ratio), kept]
        return make_plan('shipping.price-ratio', target=target, rows=rows,
                         requests=[update(dict(body, discount=ratio))], reads=reads,
                         before={'ftId': current['ftId'], 'discount': body.get('discount')},
                         after={'ftId': current['ftId'], 'discount': ratio}, restore=original, effect='배송비 표시',
                         blocked=fare_block, readback=readback_templates, warnings=unconfirmed)
    if sub == 'auto-route':
        want = args.state == 'on'
        rows = [dict(field='새 노선 자동 반영 insertCarriers', current=body.get('insertCarriers'), after=want)]
        return make_plan('shipping.auto-route', target=target, rows=rows, requests=[], reads=reads,
                         before={'insertCarriers': body.get('insertCarriers')}, after={'insertCarriers': want},
                         effect='배송 노선', warnings=unconfirmed,
                         blocked='스위치를 바꾼 캡처 본문이 요금표 앞에서 잘려 바뀐 칸을 확인하지 못했다(형식 미검증) '
                                 '— 화면에서 바꿔야 한다')
    # carrier add
    countries = [c.strip().upper() for c in str(args.countries or '').split(',') if c.strip()]
    if not countries:
        raise Usage('shipping carrier add: --countries CA,US,… 필요')
    entry = new_carrier_entry(args, body, countries)
    before_codes = [c['carrierCode'] for c in body['carrierList']]
    new_body = dict(body, carrierList=[entry] + body['carrierList'])     # 캡처: 새 배송사가 맨 앞
    rows = [dict(field='추가할 배송사', current=before_codes, after=entry['carrierCode']),
            dict(field='국가', current=None, after=countries), kept]
    return make_plan('shipping.carrier-add', target=target, rows=rows,
                     requests=[request('POST', TEMPLATE + '/web/ggs/logistics/shippingTemplates/verify.json', {},
                                       {'json': {'basicRule': entry}}, encoding='json', label='verify.json'),
                               update(new_body)], reads=reads,
                     before={'ftId': current['ftId'], 'carrierList': before_codes},
                     after={'ftId': current['ftId'], 'carrierList': [entry['carrierCode']] + before_codes},
                     restore=original, effect='배송 가능 국가·요금', readback=readback_templates,
                     blocked='verify.json 의 basicRule 본문이 캡처에 칸 이름만 있다(형식 미검증)'
                             + ('; ' + fare_block if fare_block else ''))


def new_carrier_entry(args, body, countries):
    return {'carrierCode': 'GGS_SELLER_LOGISTICS_OTHER',
            'dispatchZipCode': body.get('dispatchZipCode') or '',
            'dispatchLocation': body.get('dispatchLocation') or '',
            'hasSurcharge': False,
            'detailList': [{'freightType': args.freight_type or 'byWeight', 'countries': countries,
                            'minFreightTime': int(args.min_days or 3), 'maxFreightTime': int(args.max_days or 10),
                            'shippingType': None, 'isAliCarrier': False, 'destinations': None,
                            'enableDeliveryGuarantee': None, 'id': None,
                            'freightDetailsDTO': {'currency': 'USD', 'discount': 100}}],
            'dropShipping': None, 'tradeTerm': 'DAP'}


def new_template_body(args, title):
    countries = [c.strip().upper() for c in str(args.countries or '').split(',') if c.strip()]
    if not countries:
        raise Usage('shipping add-template: --countries CA,US,… 필요')
    return {'title': title, 'ftId': None, 'dispatchLocation': args.location or 'KR',
            'dispatchZipCode': args.zip or '',
            'templateType': 'ordinary',
            'carrierList': [{'carrierCode': 'GGS_SELLER_LOGISTICS_OTHER',
                             'dispatchZipCode': args.zip or '', 'dispatchLocation': args.location or 'KR',
                             'hasSurcharge': False,
                             'detailList': [{'freightType': 'byWeight', 'countries': countries,
                                             'minFreightTime': int(args.min_days or 3),
                                             'maxFreightTime': int(args.max_days or 10),
                                             'shippingType': None, 'isAliCarrier': False,
                                             'destinations': None, 'enableDeliveryGuarantee': None, 'id': None,
                                             'freightDetailsDTO': {'currency': 'USD', 'discount': 100}}],
                             'dropShipping': None, 'tradeTerm': 'DAP'}]}


def readback_templates(session, plan):
    templates = read_templates(session)
    view = [{'ftId': t['ftId'], 'title': t['title'], 'isDefault': t['isDefault']} for t in templates]
    after, diff = plan['after'], {}
    if 'title' in after and not any(t['title'] == after['title'] for t in templates):
        diff['제목'] = {'기대': after['title'], '실제': [t['title'] for t in templates]}
    if after.get('default'):
        got = [t['title'] for t in templates if t['isDefault']]
        if got != [after['default']]:
            diff['기본값'] = {'기대': after['default'], '실제': got}
    if 'discount' in after or 'carrierList' in after:
        template = next((t for t in templates if str(t['ftId']) == str(after['ftId'])), None)
        if template is None:
            diff['템플릿'] = {'기대': after['ftId'], '실제': None}
        else:
            body = template_update_body(read_template_body(session, template))
            if 'discount' in after and body.get('discount') != after['discount']:
                diff['discount'] = {'기대': after['discount'], '실제': body.get('discount')}
            codes = [c['carrierCode'] for c in body['carrierList']]
            if 'carrierList' in after and sorted(codes) != sorted(after['carrierList']):
                diff['배송사'] = {'기대': after['carrierList'], '실제': codes}
    return report_readback(plan, view, diff)


# ---- store -------------------------------------------------------------------------------------

def pick_version(versions, wanted):
    wanted = str(wanted or '').strip()
    chosen = [v for v in versions if str(v['pageId']) == wanted] or [v for v in versions if v['name'] == wanted]
    if len(chosen) != 1:
        listing = ', '.join('%s(%s%s)' % (v['name'], v['pageId'], ' 라이브' if v['live'] else '') for v in versions)
        raise Usage('%s: %r (버전: %s)' % ('이름이 같은 버전이 여럿 — --version 에 pageId 를 쓴다' if chosen
                                          else '버전이 목록에 없음', wanted, listing or '없음'))
    return chosen[0]


def store_plan(args, session):
    sub2 = getattr(args, 'sub2', None)
    versions = read_versions(session)
    current = pick_version(versions, args.version)
    reads = [request('GET', VERSION_LIST, {'pagePackageId': current['packageId']}, label='버전 목록')]
    page = {'pageType': VERSION_HOME_TYPE, 'pageId': current['pageId']}
    target = '버전 %r(%s)' % (current['name'], current['pageId'])
    if sub2 == 'rename':
        to = str(args.to or '').strip()
        if not to:
            raise Usage('store version rename: --to 필요')
        if to == current['name']:
            raise Usage('이미 그 이름: %s' % to)
        req = request('POST', SITEADMIN + '/page/editPage', {}, dict(page, pageName=to), label='page/editPage')
        rows = [dict(field='버전 이름 pageName', current=current['name'], after=to)]
        return make_plan('store.version.rename', target=target, rows=rows, requests=[req], reads=reads,
                         before={'pageId': current['pageId'], 'pageName': current['name']},
                         after={'pageId': current['pageId'], 'pageName': to},
                         restore={'argv': ['store', 'version', 'rename', '--version', str(current['pageId']),
                                           '--to', current['name']]},
                         effect='없음', readback=readback_versions)
    if sub2 == 'delete':
        if current['live']:
            raise Usage('라이브(게시 중) 버전은 지울 수 없다: %s' % target)
        if not current['deletable']:
            raise Usage('화면이 삭제를 막은 버전(deletable=false): %s' % target)
        req = request('POST', SITEADMIN + '/page/deletePage', {}, page, label='page/deletePage')
        rows = [dict(field='지울 버전', current=target, after='삭제(복원 불가)'),
                dict(field='버전 수', current=len(versions), after=len(versions) - 1)]
        return make_plan('store.version.delete', target=target, rows=rows, requests=[req], reads=reads,
                         before={'pageId': current['pageId'], 'pageName': current['name']},
                         after={'pageId': current['pageId'], 'deleted': True},
                         effect='버전 영구 삭제', readback=readback_versions)
    # publish: 쿼리만, 본문 없음(cToken·csrf 는 세션이 붙인다)
    if current['live']:
        raise Usage('이미 라이브 버전: %s' % target)
    live = next((v for v in versions if v['live'] and v['packageId'] == current['packageId']), None)
    req = request('POST', SITEADMIN + '/publish', dict(page, commonPageType=current['commonPageType']), None,
                  label='publish')
    restore = {'argv': ['store', 'version', 'publish', '--version', str(live['pageId'])]} if live \
        else {'note': '불가: 지금 라이브 버전을 목록에서 정하지 못함'}
    rows = [dict(field='라이브 버전', current='%s(%s)' % (live['name'], live['pageId']) if live else '(없음)',
                 after='%s(%s)' % (current['name'], current['pageId']))]
    return make_plan('store.version.publish', target=target, rows=rows, requests=[req], reads=reads,
                     before={'pageId': live and live['pageId']},
                     after={'pageId': current['pageId'], 'live': True},
                     restore=restore, effect='바이어가 보는 스토어가 바뀐다',
                     grade_note='되돌릴 수 있음 · 외부 노출 — 직전 라이브 버전을 다시 게시해 되돌린다',
                     readback=readback_versions)


def readback_versions(session, plan):
    versions = read_versions(session)
    view = [{'pageId': v['pageId'], 'name': v['name'], 'live': v['live']} for v in versions]
    after = plan['after']
    got = next((v for v in versions if str(v['pageId']) == str(after['pageId'])), None)
    diff = {}
    if after.get('deleted'):
        if got is not None:
            diff['삭제'] = {'기대': '목록에서 사라짐', '실제': '아직 있음'}
    elif got is None:
        diff['버전'] = {'기대': after['pageId'], '실제': None}
    elif 'pageName' in after and got['name'] != after['pageName']:
        diff['이름'] = {'기대': after['pageName'], '실제': got['name']}
    elif after.get('live') and not got['live']:
        diff['라이브'] = {'기대': True, '실제': False}
    return report_readback(plan, view, diff)


# ---- settings ----------------------------------------------------------------------------------

MESSAGE_UPDATE = MESSAGE + '/feedbackset/index.htm'
PRIVACY_UPDATE = PROFILE + '/ajax/privacy/privacy_setting_update.do'


def message_request(params, label):
    return request('POST', MESSAGE_UPDATE, {}, {'json': compact({'cmd': 'updateForm', 'params': params})},
                   label=label, fill='message')


def settings_plan(args, session):
    if args.sub == 'message':
        current = read_message_settings(session)
        original = current['params']
        params = json.loads(json.dumps(original))
        changed = []
        if args.contact:
            contact = positive_int(args.contact, '--contact')
            if contact not in current['members']:
                raise Usage('--contact 는 문의 배분 담당자 memberSeq 중 하나: %s'
                            % ', '.join(str(m) for m in current['members']))
            params['defaultContact'] = contact
        for flag, keys in (('cc', ('cc', 'tmCc')), ('sync_to_im', ('syncRuleToIm', 'syncAutoTurnToIm'))):
            state = getattr(args, flag, None)
            if state is not None:
                for key in keys:
                    params[key] = state == 'on'
        if getattr(args, 'auto_distribute', None) is not None:
            params['adminFeedback']['autoDistribute'] = args.auto_distribute == 'on'
        flat = lambda p: dict({k: v for k, v in p.items() if k != 'adminFeedback'},
                              autoDistribute=p['adminFeedback']['autoDistribute'])
        before, after = flat(original), flat(params)
        changed = [dict(field=k, current=before[k], after=after[k]) for k in after if before[k] != after[k]]
        if not changed:
            raise Usage('settings message: 바뀌는 칸이 없다(--contact/--cc/--auto-distribute/--sync-to-im)')
        return make_plan('settings.message', target='메시지 설정', rows=changed,
                         requests=[message_request(params, 'feedbackset(updateForm)')],
                         reads=[request('GET', MESSAGE_SETTINGS_PAGE, {'source': 'portal'}, label='메시지 설정 화면')],
                         before={k: before[k] for k in after if before[k] != after[k]},
                         after={k: after[k] for k in after if before[k] != after[k]},
                         restore={'requests': [message_request(original, 'feedbackset(원값 복원)')],
                                  'action': 'settings.message', 'target': '메시지 설정'},
                         effect='문의 배분·사본 메일', readback=readback_message,
                         warnings=['params 안 _csrf_token_ 은 보낼 때 메시지 설정 화면(csrfTokenVal)에서 읽어 붙인다.'])
    privacy = read_privacy(session)
    original, setting = privacy['setting'], json.loads(json.dumps(privacy['setting']))
    rows = []
    for item in args.set or []:
        key, _, value = str(item).partition('=')
        options = privacy['options'].get(key.strip())
        if options is None:
            raise Usage('settings privacy --set: 항목 %r 없음 (항목: %s)' % (key, ', '.join(sorted(privacy['options']))))
        if value.strip() not in options:
            raise Usage('settings privacy --set %s: 값은 %s 중 하나' % (key, ', '.join(sorted(options))))
        key, value = key.strip(), value.strip()
        setting[key] = {value: options[value]}
        rows.append(dict(field='항목 %s' % key, current=next(iter(original.get(key) or {}), None), after=value))
    if not rows:
        raise Usage('settings privacy: --set 항목=값 필요 (항목: %s)' % ', '.join(sorted(privacy['options'])))
    if setting == original:
        raise Usage('settings privacy: 바뀌는 칸이 없다')
    update = lambda value, label: request('POST', PRIVACY_UPDATE, {}, {'privaceSetting': compact(value)}, label=label)
    return make_plan('settings.privacy', target='개인정보 설정', rows=rows,
                     requests=[update(setting, 'privacy_setting_update')],
                     reads=[request('GET', PRIVACY_PAGE, {}, label='개인정보 설정 화면')],
                     before={'privaceSetting': original}, after={'privaceSetting': setting},
                     restore={'requests': [update(original, 'privacy_setting_update(원값 복원)')],
                              'action': 'settings.privacy', 'target': '개인정보 설정'},
                     effect='프로필 공개 범위', readback=readback_privacy)


def readback_message(session, plan):
    params = read_message_settings(session)['params']
    got = dict(params, autoDistribute=params['adminFeedback']['autoDistribute'])
    diff = {k: {'기대': v, '실제': got.get(k)} for k, v in plan['after'].items() if got.get(k) != v}
    return report_readback(plan, {k: got.get(k) for k in plan['after']}, diff)


def readback_privacy(session, plan):
    got = read_privacy(session)['setting']
    want = plan['after']['privaceSetting']
    diff = {k: {'기대': v, '실제': got.get(k)} for k, v in want.items() if got.get(k) != v}
    return report_readback(plan, {k: next(iter(v), None) for k, v in got.items()}, diff)


# ---- reviews -----------------------------------------------------------------------------------

def review_plan(args, session):
    order = positive_int(args.order, '주문번호')
    comment = args.comment if args.comment is not None else ''
    req = request('POST', FB + '/review/action/inviteReviewAction.do',
                  {'reviewEntity': 'TAOrder', 'entityId': order}, {'inviteComment': comment},
                  label='inviteReviewAction')
    rows = [dict(field='주문번호 entityId', current=None, after=order),
            dict(field='요청 문구 inviteComment', current=None, after=comment or '(빈 값)')]
    return make_plan('reviews.invite', target='주문 %s' % order, rows=rows, requests=[req], reads=[],
                     before={}, after={'entityId': order}, effect='바이어에게 리뷰 요청 메일 발송',
                     grade_note='되돌릴 수 없음 · 외부 발송 — 바이어에게 메일이 나간다')


# ---- restore / backups -------------------------------------------------------------------------

RESTORE_READBACK = {
    'settings.message': readback_message,
    'settings.privacy': readback_privacy,
    'shipping.rename': readback_templates,
    'shipping.price-ratio': readback_templates,
}


def restore_plan(args, session):
    try:
        record = json.loads(Path(args.file).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise Usage('백업 파일을 읽을 수 없음: %s (%s)' % (args.file, error))
    if not isinstance(record, dict) or not record.get('action'):
        raise Usage('백업 형식 아님(action/before/restore 필요): %s' % args.file)
    restore = record.get('restore') or {}
    print('복원 대상: %s / %s / 백업 %s (%s)'
          % (record['action'], record.get('target'), args.file, record.get('createdAt')))
    if restore.get('note'):
        raise Unverified('%s — %s' % (record['action'], restore['note']))
    if restore.get('argv'):
        plan = plan_for(restore['argv'], session)
        plan['rows'] = [dict(field='원값 복원', current=plan['before'], after=plan['after'])] + plan['rows']
        return plan
    if restore.get('requests'):
        action = restore.get('action') or record['action']
        source = ACTIONS.get(action, (REVERSIBLE, 'captured'))
        # 백업의 before 가 그 action 의 after 와 같은 모양인 것만 원값 리드백(나머지는 보낸 요청만 확인).
        readback = RESTORE_READBACK.get(action) if record.get('before') else None
        return {'action': action, 'grade': REVERSIBLE, 'source': source[1], 'readback': readback,
                'target': restore.get('target') or record.get('target'), 'rows':
                    [dict(field='원래 요청 본문 재전송', current=None, after='백업 시점 값')],
                'requests': restore['requests'], 'reads': [], 'before': record.get('after'),
                'after': record.get('before') if readback else record.get('after'),
                'restore': {'note': '불가: 복원 요청을 다시 되돌리는 형식은 없다'},
                'effect': '백업 값으로 되돌림', 'grade_note': None, 'warnings': [], 'blocked': None}
    raise Usage('백업에 restore 정보(요청 또는 명령)가 없음: %s' % args.file)


def show_backups(account, args):
    rows = list_backups(account)
    if not rows:
        print('백업 없음: %s' % backup_root(account))
        return 0
    print('%-19s %-26s %-34s %s' % ('시각', '조작', '대상', '파일'))
    for row in rows:
        print('%-19s %-26s %-34s %s' % (row.get('createdAt') or '?', row.get('action') or '?',
                                        row.get('target') or '', row.get('file')))
    return 0


# ---- CLI ---------------------------------------------------------------------------------------

def positive_int(value, what):
    if not re.fullmatch(r'[1-9][0-9]*', str(value)):
        raise Usage('%s 는 1 이상 정수: %r' % (what, value))
    return int(value)


MONEY = re.compile(r'[0-9]+(\.[0-9]+)?')


def money(value, what):
    if value is None or not MONEY.fullmatch(str(value)) or float(value) <= 0:
        raise Usage('%s 는 0보다 큰 숫자: %r' % (what, value))
    return float(value)


GLOBAL = argparse.ArgumentParser(add_help=False, conflict_handler='resolve')


def sub_parser(parent, name, **kwargs):
    return parent.add_parser(name, parents=[GLOBAL], **kwargs)


def build_parser():
    if not GLOBAL._actions:
        GLOBAL.add_argument('--account')
        GLOBAL.add_argument('--config')
    ap = argparse.ArgumentParser(prog='alibaba-manage', description=__doc__.splitlines()[0], parents=[GLOBAL])
    sub = ap.add_subparsers(dest='group')

    def group(name, help_text):
        return sub.add_parser(name, help=help_text).add_subparsers(dest='sub')

    products = group('products', '상품 표시(온라인/오프라인)·그룹·삭제')
    for name in ('offline', 'online', 'delete'):
        p = sub_parser(products, name)
        p.add_argument('--ids', nargs='+', required=True, help='상품 id')
    pgroup = sub_parser(products, 'group')
    pgroup.add_argument('--ids', nargs='+', required=True)
    pgroup.add_argument('--group', dest='group_name', required=True, help='그룹 이름')

    showcase = group('showcase', '쇼케이스 추가·제거·교체·순서')
    for name in ('add', 'remove'):
        p = sub_parser(showcase, name)
        p.add_argument('--ids', nargs='+', required=True, help='상품 id')
        p.add_argument('--campaign', help='쇼케이스 캠페인 id(기본: 읽기로 찾기)')
    preplace = sub_parser(showcase, 'replace')
    preplace.add_argument('--ids', nargs=1, required=True, metavar='ADGROUP_ID')
    preplace.add_argument('--to', required=True, help='새 상품 id')
    preplace.add_argument('--campaign')
    psort = sub_parser(showcase, 'sort')
    psort.add_argument('--ids', nargs=1, required=True, metavar='ADGROUP_ID')
    psort.add_argument('--to-index', dest='to_index', required=True)
    psort.add_argument('--campaign')

    rfq = group('rfq', 'RFQ 저장 검색·알림·견적')
    padd = sub_parser(rfq, 'search-add')
    padd.add_argument('--keyword', required=True)
    pdelete = sub_parser(rfq, 'search-delete')
    pdelete.add_argument('--search', required=True, help='저장 검색 keyword 또는 id')
    pnotify = sub_parser(rfq, 'search-notify', help='내 검색 알림(계정 전체 스위치 하나)')
    pnotify.add_argument('state', choices=('on', 'off'))
    pquote = sub_parser(rfq, 'quote')
    quote_args(pquote)
    ptemplate = sub_parser(rfq, 'quote-template').add_subparsers(dest='sub2')
    psave = sub_parser(ptemplate, 'save')
    quote_args(psave, template=True)

    promo = group('promo', '쿠폰·할인·행사')
    coupon = promo.add_parser('coupon').add_subparsers(dest='sub2')
    pcreate = sub_parser(coupon, 'create')
    pcreate.add_argument('--name', required=True)
    pcreate.add_argument('--shop', required=True, help='쿠폰 적용 상점 id(participateId, 쿠폰 화면 값)')
    pcreate.add_argument('--start')
    pcreate.add_argument('--end')
    pcreate.add_argument('--total')
    pcreate.add_argument('--start-money', dest='start_money', default='100')
    pcreate.add_argument('--discount-money', dest='discount_money', default='5')
    pcreate.add_argument('--currency', default='USD')
    pcreate.add_argument('--coupon-type', dest='coupon_type', default='FULL_REDUCE_COUPON')
    pcreate.add_argument('--publish', default='IMMEDIATELY')
    pstop = sub_parser(coupon, 'stop')
    pstop.add_argument('--id', required=True, help='couponTemplateId')
    discount = promo.add_parser('discount').add_subparsers(dest='sub2')
    pend = sub_parser(discount, 'end')
    pend.add_argument('--id', required=True, help='activityId')
    event = promo.add_parser('event').add_subparsers(dest='sub2')
    penroll = sub_parser(event, 'enroll')
    penroll.add_argument('--topic', required=True)
    penroll.add_argument('--protocol', required=True)

    shipping = group('shipping', '배송 템플릿(이름·기본값·할인율·배송사·새 템플릿)')
    prename = sub_parser(shipping, 'rename')
    prename.add_argument('--template', required=True)
    prename.add_argument('--to', required=True)
    pdefault = sub_parser(shipping, 'default')
    pdefault.add_argument('--template', required=True)
    pratio = sub_parser(shipping, 'price-ratio')
    pratio.add_argument('--template', required=True)
    pratio.add_argument('--ratio', required=True, help='가격 조정 배수(1.2 = 120%%, 1 = 그대로)')
    proute = sub_parser(shipping, 'auto-route')
    proute.add_argument('state', choices=('on', 'off'))
    proute.add_argument('--template', required=True)
    carrier = shipping.add_parser('carrier').add_subparsers(dest='sub2')
    pcarrier = sub_parser(carrier, 'add')
    pcarrier.add_argument('--template', required=True)
    pcarrier.add_argument('--countries', required=True)
    pcarrier.add_argument('--freight-type', dest='freight_type', default='byWeight')
    pcarrier.add_argument('--min-days', dest='min_days')
    pcarrier.add_argument('--max-days', dest='max_days')
    paddt = sub_parser(shipping, 'add-template')
    paddt.add_argument('--title', required=True)
    paddt.add_argument('--countries', required=True)
    paddt.add_argument('--location')
    paddt.add_argument('--zip')
    paddt.add_argument('--min-days', dest='min_days')
    paddt.add_argument('--max-days', dest='max_days')

    store = group('store', '스토어 버전 이름·게시·삭제')
    version = store.add_parser('version').add_subparsers(dest='sub2')
    for name in ('rename', 'publish', 'delete'):
        p = sub_parser(version, name)
        p.add_argument('--version', required=True, help='버전 이름 또는 pageId(이름이 겹치면 pageId)')
        if name == 'rename':
            p.add_argument('--to', required=True)

    settings = group('settings', '메시지·개인정보 설정')
    pmessage = sub_parser(settings, 'message')
    pmessage.add_argument('--contact')
    pmessage.add_argument('--cc', choices=('on', 'off'))
    pmessage.add_argument('--auto-distribute', dest='auto_distribute', choices=('on', 'off'))
    pmessage.add_argument('--sync-to-im', dest='sync_to_im', choices=('on', 'off'))
    pprivacy = sub_parser(settings, 'privacy')
    pprivacy.add_argument('--set', action='append', dest='set', metavar='KEY=VALUE',
                          help='항목(data-key)=선택값(라디오 value), 여러 번 가능')

    reviews = group('reviews', '바이어 리뷰 요청')
    pinvite = sub_parser(reviews, 'invite')
    pinvite.add_argument('--order', required=True)
    pinvite.add_argument('--comment')

    restore_p = sub_parser(sub, 'restore')
    restore_p.add_argument('file')
    sub_parser(sub, 'backups')
    return ap


def quote_args(parser, template=False):
    parser.add_argument('--rfq', required=True, help='RFQ id')
    parser.add_argument('--price', required=True, help='단가 fobPrice')
    parser.add_argument('--quantity')
    parser.add_argument('--unit', help='수량 단위(기본 Pieces)')
    parser.add_argument('--price-unit', dest='price_unit', help='통화(기본 USD)')
    parser.add_argument('--payment', help='결제 조건(기본 T/T)')
    parser.add_argument('--shipping-terms', dest='shipping_terms', help='인코텀즈(기본 FOB)')
    parser.add_argument('--port')
    parser.add_argument('--leadtime-days', dest='leadtime_days', type=int)
    parser.add_argument('--product-name', dest='product_name')
    parser.add_argument('--remark')
    parser.add_argument('--expiry', help='견적 유효일 YYYY-MM-DD')
    if template:
        parser.add_argument('--name', help='양식 이름')


def plan_for(argv, session):
    args = build_parser().parse_args(list(argv))
    return plan_from_args(args, session)


def plan_from_args(args, session):
    if args.group == 'products':
        return product_plan(args, session)
    if args.group == 'showcase':
        return showcase_plan(args, session)
    if args.group == 'rfq':
        return rfq_plan(args, session)
    if args.group == 'promo':
        return promo_plan(args, session)
    if args.group == 'shipping':
        return shipping_plan(args, session)
    if args.group == 'store':
        return store_plan(args, session)
    if args.group == 'settings':
        return settings_plan(args, session)
    if args.group == 'reviews':
        return review_plan(args, session)
    raise Usage('명령 필요: products|showcase|rfq|promo|shipping|store|settings|reviews | restore | backups')


def execute_plan(session, plan, account, dry_run):
    """계획을 출력하고, --yes 면 백업→전송→리드백."""
    return show_plan_or_run(account, plan, session, dry_run=dry_run)


def run(argv, session=None, *, dry_run=True):
    try:
        args = build_parser().parse_args(list(argv))
    except SystemExit as exit_:
        if exit_.code in (None, 0):
            raise
        raise Usage('인자 해석 실패(필수 인자·하위 명령 확인)')
    if args.group is None:
        raise Usage('명령 필요: products|showcase|rfq|promo|shipping|store|settings|reviews | restore | backups')
    if args.group in ('products', 'showcase', 'rfq', 'promo', 'shipping', 'store', 'settings', 'reviews') \
            and getattr(args, 'sub', None) is None:
        raise Usage('%s: 하위 명령 필요' % args.group)
    if args.group in ('promo', 'store') and getattr(args, 'sub2', None) is None:
        raise Usage('%s %s: 하위 명령 필요' % (args.group, args.sub))
    if not args.account:
        raise Usage('--account 가 필요하다')
    if args.account in BLOCKED_ACCOUNTS:
        raise Blocked(args.account)
    if not args.config:
        args.config = DEFAULT_CONFIG
    account = load_account(args.config, args.account)
    if account['name'] in BLOCKED_ACCOUNTS:
        raise Blocked(account['name'])
    account['outDir'] = account.get('outDir') or str(Path(args.config).resolve().parent)
    if args.group == 'backups':
        return show_backups(account, args)
    session = session or ApiSession(account, args.config, refresh_saved=False)
    if args.group == 'restore':
        plan = restore_plan(args, session)
    else:
        plan = plan_from_args(args, session)
    return execute_plan(session, plan, account, dry_run)


def cli(argv=None, session=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    yes = '--yes' in argv
    try:
        return run([a for a in argv if a != '--yes'], session=session, dry_run=not yes) or 0
    except Usage as error:
        print('사용법: %s' % error, file=sys.stderr)
        return 64
    except Unverified as error:
        print('중단 — %s' % error, file=sys.stderr)
        return 64
    except Blocked as error:
        print('거부: %s' % error, file=sys.stderr)
        return 77
    except SessionError as error:
        text = str(error)
        print('실패: %s' % text, file=sys.stderr)
        if any(hint in text for hint in ('transport', 'timeout', 'session_expired_or_captcha', 'saved_cookie')):
            print('세션 만료/연결 실패: 새 로그인·인증은 하지 않는다 — 계정 설정/로그인 상태를 확인.', file=sys.stderr)
            return 2
        return 1
    except SystemExit as exit_:
        return 0 if exit_.code in (None, 0) else 1


if __name__ == '__main__':
    sys.exit(cli())
