"""광고 밖 셀러센트럴 조작(상품 표시·그룹·삭제, 쇼케이스, RFQ, 쿠폰·할인·행사, 배송 템플릿, 스토어 버전, 설정, 리뷰 요청).

기본 dry-run, --yes 로만 전송, 보내기 전 자동 백업(manage-backups), 보낸 뒤 리드백 대조.
요청 형상은 2026-11-18 thedozers 화면 캡처(specs.json 69건 / captured 36·captured-xform 2)에서
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

PRODUCT_LINE = 110106          # 광고(퍼포먼스) 쓰기 요청의 productLineId(읽기 명령과 같은 값)
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
    'rfq.search-add': (IRREVERSIBLE, 'captured'),
    'rfq.search-notify': (REVERSIBLE, 'captured'),
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
    'settings.message': (REVERSIBLE, 'captured'),
    'settings.privacy': (REVERSIBLE, 'captured'),
    'reviews.invite': (IRREVERSIBLE, 'captured'),
}
# 되돌릴 수 없는 action 의 restore 문구(그대로 출력한다).
IRREVERSIBLE_NOTE = {
    'products.delete': '불가: 휴지통 복원 요청 형식 미캡처 — 24시간이 지나야 화면에서만 복원된다',
    'rfq.search-add': '불가: 저장 검색 삭제 UI 미발견(specs not-capturable) — 화면에서 직접 지워야 한다',
    'rfq.quote': '불가: 바이어에게 이미 발송됨(견적 권한 1회 소모)',
    'promo.coupon.stop': '불가: 중지한 쿠폰은 다시 발급할 수 없다(새 쿠폰을 만들어야 한다)',
    'promo.discount.end': '불가: 일찍 종료한 할인은 되살릴 수 없다(새 할인을 만들어야 한다)',
    'promo.event.enroll': '불가: 행사 약관 서명은 되돌리는 형식이 캡처에 없다',
    'shipping.add-template': '불가: 템플릿 삭제 버튼 UI 없음(specs not-capturable)',
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


def rows_of(value, keys=('rows', 'data', 'list', 'result', 'products')):
    """목록 응답에서 행 배열을 꺼낸다(한 겹만)."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in keys:
            inner = value.get(key)
            if isinstance(inner, list):
                return inner
            if isinstance(inner, dict):
                for inner_key in ('rows', 'list', 'data'):
                    if isinstance(inner.get(inner_key), list):
                        return inner[inner_key]
    return None


def read_json(session, method, url, query, params, what, encoding='form'):
    response = session.request(method, url, query, params, encoding)
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
    if value is None:
        return None                      # HTML 확인 페이지 — 2xx 면 접수로 본다
    keys = ('status', 'code', 'ok', 'success', 'result', 'error', 'errorVO', 'message', 'msg')
    rejected = (value.get('ok') is False or value.get('success') is False or value.get('result') is False
                or bool(value.get('errorVO')) or value.get('error') not in (None, False, '', 0))
    for key in ('status', 'code'):
        flag = value.get(key)
        if flag is not None and str(flag).lower() not in ('200', '1000', '0', 'ok', 'success'):
            rejected = True
    if not rejected:
        return None
    return short({k: value[k] for k in keys if k in value}) or 'HTTP %s 거부' % status


def flat_args(**kwargs):
    return argparse.Namespace(**kwargs)


def request(method, url, query=None, params=None, encoding='form', label=None):
    return {'method': method, 'url': url, 'query': query or {}, 'params': params or {},
            'encoding': encoding, 'label': label or url}


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
    return session.request(req['method'], req['url'], dict(req['query']), dict(req['params']), req['encoding'])


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

def read_products(session):
    """상품 관리 목록(페이지 캡처의 asyQueryProductsList). id·이름·표시상태·그룹을 돌려준다."""
    value = read_json(session, 'GET', PRODUCT + '/product/managementproducts/asyQueryProductsList.do',
                      {'statisticsType': 'month', 'repositoryType': 'all', 'imageType': 'all', 'status': 'all',
                       'page': 1, 'size': 100, 'lang': 'en_US'}, None, '상품 목록')
    rows = rows_of(value)
    if rows is None:
        raise SessionError('상품 목록 읽기 실패: rows 없음')
    products = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = row.get('id') or row.get('productId')
        if pid is None:
            continue
        products.append({'id': pid, 'subject': row.get('subject') or row.get('productName') or row.get('title'),
                         'status': row.get('status') or row.get('displayStatus'),
                         'groupId': row.get('groupId') or get_in(row, 'group', 'id'),
                         'groupName': row.get('groupName') or get_in(row, 'group', 'name')})
    return products


def product_status(products, pid):
    for row in products:
        if str(row['id']) == str(pid):
            return row
    return None


def read_groups(session):
    value = read_json(session, 'GET', PRODUCT + '/product/group_query_ajax.do',
                      {'event': 'listGroupCombine', 'lang': 'en_US'}, None, '상품 그룹')
    rows = rows_of(value, ('data', 'rows', 'list'))
    if rows is None:
        raise SessionError('상품 그룹 읽기 실패: 목록 없음')
    groups = []
    for row in rows:
        if isinstance(row, dict):
            gid = row.get('id') or row.get('groupId')
            name = row.get('name') or row.get('groupName')
            if gid is not None and name:
                groups.append({'id': gid, 'name': name})
    return groups


def showcase_campaign(session, explicit=None):
    """쇼케이스 캠페인 id — engine/ads_dashboard.py 와 같은 읽기 계약(ads 봉투 POST)."""
    if explicit is not None:
        return int(explicit)
    value = read_json(session, 'POST', WWW2 + '/api/campaign/find/showcase', {},
                      {'ads': SHOWCASE_ADS, 'type': 'normal', 'data': {}}, '쇼케이스 캠페인')
    data = value.get('data') if isinstance(value, dict) else None
    if isinstance(data, bool):
        data = None
    if isinstance(data, int):
        return data
    if isinstance(data, str) and data.isdigit():
        return int(data)
    found = []

    def walk(node):
        if isinstance(node, dict):
            ctype = node.get('type') if node.get('type') is not None else node.get('campaignType')
            cid = node.get('id') if node.get('id') is not None else node.get('campaignId')
            if cid is not None and str(ctype) in ('9', 'showcase'):
                found.append(cid)
            for inner in node.values():
                walk(inner)
        elif isinstance(node, list):
            for inner in node:
                walk(inner)

    walk(data)
    found = list(dict.fromkeys(found))
    if len(found) != 1:
        raise Usage('쇼케이스 캠페인 id 를 하나로 정하지 못함(%d개) — --campaign 으로 지정' % len(found))
    return int(found[0])


def read_showcase_groups(session, cid):
    value = read_json(session, 'POST', WWW2 + '/api/campaign/%d/adgroup' % cid, {},
                      {'ads': SHOWCASE_ADS, 'type': 'normal',
                       'data': {'orderBy': 'ad_group_sort', 'order': 'desc',
                                'adgroupOnlineStatus': 1, 'page': 1, 'size': 50}}, '쇼케이스 목록')
    rows = rows_of(value)
    if rows is None:
        raise SessionError('쇼케이스 목록 읽기 실패: 목록 없음')
    groups = []
    for index, row in enumerate(rows):
        if isinstance(row, dict) and row.get('id') is not None:
            groups.append({'id': row.get('id'), 'productId': row.get('productId'),
                           'onlineStatus': row.get('onlineStatus'), 'index': index + 1})
    return groups


def read_saved_searches(session):
    value = read_json(session, 'GET', SOURCING + '/rfq/ajax/my_search_list_ajax.do', {}, None, 'RFQ 저장 검색')
    rows = rows_of(value)
    if rows is None:
        raise SessionError('RFQ 저장 검색 읽기 실패: 목록 없음')
    searches = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        keyword = row.get('keyword')
        if not keyword:
            params = row.get('params') or row.get('searchParams')
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except ValueError:
                    params = None
            keyword = get_in(params or {}, 'keyword')
        searches.append({'id': row.get('id') or row.get('searchId'), 'keyword': keyword,
                         'atm': row.get('atm') or row.get('notify')})
    return searches


def read_coupons(session):
    value = read_json(session, 'GET', MARKETING + '/coupon/v2/querySellerCouponListAjax.do', {}, None, '쿠폰 목록')
    rows = rows_of(value)
    if rows is None:
        raise SessionError('쿠폰 목록 읽기 실패: 목록 없음')
    return [{'id': r.get('couponTemplateId') or r.get('id'), 'name': r.get('name'),
             'status': r.get('status') or r.get('couponStatus')} for r in rows if isinstance(r, dict)]


def read_templates(session):
    value = read_json(session, 'GET', TEMPLATE + '/luyou/ggs/getTemplateList.json', {'lang': 'en_US'}, None,
                      '배송 템플릿 목록')
    rows = rows_of(value)
    if rows is None:
        raise SessionError('배송 템플릿 목록 읽기 실패: 목록 없음')
    templates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ft = row.get('ftId') or row.get('id') or row.get('templateId')
        title = row.get('title') or row.get('name') or row.get('templateName')
        if ft is not None and title:
            templates.append({'ftId': ft, 'title': title,
                              'isDefault': row.get('isDefault') or row.get('default') or row.get('defaultFlag')})
    return templates


def read_template_body(session, ft_id, what='배송 템플릿'):
    value = read_json(session, 'GET', TEMPLATE + '/luyou/ggs/shippingTemplates/getTemplateInfoOverview.json',
                      {'ftId': str(ft_id)}, None, what)
    body = value.get('data') if isinstance(value, dict) and isinstance(value.get('data'), dict) else value
    if not isinstance(body, dict):
        raise SessionError('%s 본문 읽기 실패: 템플릿 JSON 없음' % what)
    return body


def read_versions(session, keyword=''):
    value = read_json(session, 'GET', SITEADMIN + '/page/list/keyword/packageId',
                      {'keyword': keyword, 'pageType': 6}, None, '스토어 버전 목록')
    rows = rows_of(value)
    if rows is None:
        raise SessionError('스토어 버전 목록 읽기 실패: 목록 없음')
    versions = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        page = row.get('pageId') or row.get('id')
        name = row.get('pageName') or row.get('name')
        if page is not None and name:
            versions.append({'pageId': page, 'name': name,
                             'released': row.get('releaseTime') or row.get('releasedTime') or row.get('publishTime'),
                             'online': row.get('online') or row.get('published') or row.get('status')})
    return versions


def read_privacy(session):
    response = session.request('GET', PROFILE + '/privacy/privacySettingShow.htm', {}, None)
    if response.get('status') != 200:
        raise SessionError('개인정보 설정 읽기 실패: HTTP %s' % response.get('status'))
    text = response['text']
    marker = re.search(r'privaceSetting', text)
    if not marker:
        raise Unverified('개인정보 설정 현재값(privaceSetting JSON)을 읽지 못함 — 화면 구조 변경')
    start = text.find('{', marker.end())
    if start < 0:
        raise Unverified('개인정보 설정 현재값(privaceSetting JSON)을 읽지 못함 — 화면 구조 변경')
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        raise Unverified('개인정보 설정 현재값 파싱 실패 — privaceSetting JSON 아님')
    if not isinstance(value, dict):
        raise Unverified('개인정보 설정 현재값이 JSON 객체가 아님')
    return value


# ---- 계획 공통 ----------------------------------------------------------------------------------

def grade_line(plan):
    grade = plan['grade']
    if plan.get('grade_note'):
        return '등급: %s — %s' % (grade, plan['grade_note'])
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
    if plan['action'] == 'shipping.auto-route':
        print('주의: 새 노선 자동 반영 스위치의 요청 필드 이름이 캡처에 없다 — 계획만 보여준다.')
    print()
    print('%-34s %-40s %s' % ('필드', '현재', '변경후'))
    for row in plan['rows']:
        print('%-34s %s' % (row['field'], row['current'] if row['current'] is not None else '—'))
        print('%-34s %s' % ('', '→ ' + str(row['after']) if row['after'] is not None else '→ —'))
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
    if not plan['reads']:
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
    text = str(value).strip().lower()
    if text in ('on', 'online', '1', 'true', 'display'):
        return 'on'
    if text in ('off', 'offline', '0', 'false'):
        return 'off'
    return text or None


def product_plan(args, session):
    sub = args.sub
    action = {'offline': 'products.offline', 'online': 'products.online', 'group': 'products.group',
              'delete': 'products.delete'}[sub]
    ids = [positive_int(p, '상품ID') for p in args.ids]
    products = read_products(session)
    found = []
    missing = []
    for pid in ids:
        row = product_status(products, pid)
        (found if row else missing).append(row or pid)
    if missing:
        raise Usage('상품 목록에 없는 id: %s' % ', '.join(str(m) for m in missing))
    if sub in ('offline', 'online'):
        want = 'on' if sub == 'online' else 'off'
        same = [r['id'] for r in found if norm_status(r.get('status')) == want]
        if same:
            raise Usage('이미 %s 상태: %s' % ('온라인' if want == 'on' else '오프라인', ', '.join(map(str, same))))
        requests = [request('POST', PRODUCT + '/product/managementproducts/asyChangeDisplayStatus.do',
                            {'lang': 'en_US'}, {'productIds': ','.join(str(i) for i in ids), 'newStatus': want},
                            label='asyChangeDisplayStatus')]
        rows = [dict(field='상품 표시상태(%s)' % ','.join(map(str, ids)),
                     current=[{str(r['id']): r.get('status')} for r in found], after={'newStatus': want})]
        opposite = 'online' if sub == 'offline' else 'offline'
        return make_plan(action, target='상품 %s' % ','.join(map(str, ids)), rows=rows, requests=requests,
                         reads=[request('GET', PRODUCT + '/product/managementproducts/asyQueryProductsList.do',
                                        {'status': 'all', 'page': 1, 'size': 100, 'lang': 'en_US'},
                                        label='상품 목록')],
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
        requests = [request('POST', PRODUCT + '/product/group_ajax.do', {'event': 'adjustGroup'},
                            {'productIdList': ','.join(str(i) for i in ids), 'groupId': str(target['id'])},
                            label='group_ajax(adjustGroup)')]
        rows = [dict(field='그룹 groupId → %s(%s)' % (target['name'], target['id']),
                     current=[{str(r['id']): r.get('groupName') or r.get('groupId')} for r in found],
                     after=target['name'])]
        originals = {}
        for row in found:
            if row.get('groupId') is None:
                raise Unverified('상품 %s 의 원래 그룹 id 를 목록 응답에서 읽지 못함 — restore 불가' % row['id'])
            originals[str(row['id'])] = row['groupId']
        return make_plan(action, target='상품 %s → 그룹 %s' % (','.join(map(str, ids)), target['name']),
                         rows=rows, requests=requests, reads=[], before=originals, after={'groupId': target['id']},
                         restore=_group_restore(originals, groups), effect='스토어 분류만 바뀜')
    # delete
    requests = [request('POST', PRODUCT + '/product/managementproducts/asyDelete.do', {'lang': 'en_US'},
                        {'productIds': ','.join(str(i) for i in ids), 'deleteType': 'product'},
                        label='asyDelete')]
    rows = [dict(field='삭제할 상품', current=[r.get('subject') for r in found], after='휴지통으로 이동'),
            dict(field='복원', current=None, after='불가 — 24시간 뒤 화면에서만')]
    return make_plan(action, target='상품 %s' % ','.join(map(str, ids)), rows=rows, requests=requests,
                     reads=[request('GET', PRODUCT + '/product/managementproducts/asyQueryProductsList.do',
                                    {'status': 'all', 'page': 1, 'size': 100, 'lang': 'en_US'}, label='상품 목록')],
                     before={str(r['id']): r.get('subject') for r in found}, after={'deleted': ids},
                     effect='상품 내려감(휴지통)', readback=readback_products_deleted)


def _group_restore(originals, groups):
    """원래 그룹으로 되돌리는 요청을 미리 만든다(같은 group_ajax adjustGroup, 원래 groupId)."""
    by_id = {str(g['id']): g['name'] for g in groups}
    original = {str(pid): str(gid) for pid, gid in originals.items()}
    by_group = {}
    for pid, gid in original.items():
        by_group.setdefault(gid, []).append(pid)
    requests = [request('POST', PRODUCT + '/product/group_ajax.do', {'event': 'adjustGroup'},
                        {'productIdList': ','.join(pids), 'groupId': gid},
                        label='group_ajax(원값 복원: %s)' % by_id.get(gid, gid))
                for gid, pids in by_group.items()]
    return {'requests': requests, 'action': 'products.group', 'target': '원래 그룹으로 복원'}


def readback_products(session, plan):
    products = read_products(session)
    view = {str(p['id']): p.get('status') for p in products}
    diff = {}
    for pid, want in plan['after'].items():
        got = view.get(str(pid))
        if norm_status(got) != str(want):
            diff[str(pid)] = {'기대': want, '실제': got}
    return report_readback(plan, view, readback_diff(diff))


def readback_products_deleted(session, plan):
    products = read_products(session)
    have = {str(p['id']) for p in products}
    left = [str(i) for i in plan['after']['deleted'] if str(i) in have]
    return report_readback(plan, {'남은 상품': sorted(have)}, {'아직 목록에 있음': left} if left else {})


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
        return request('POST', base + kind, {}, envelope(data), label='adgroup/' + kind)

    reads = [request('POST', WWW2 + '/api/campaign/%d/adgroup' % cid, {},
                     {'ads': SHOWCASE_ADS, 'type': 'normal',
                      'data': {'orderBy': 'ad_group_sort', 'order': 'desc',
                               'adgroupOnlineStatus': 1, 'page': 1, 'size': 50}}, label='쇼케이스 목록')]
    if sub == 'add':
        ids = [positive_int(p, '상품ID') for p in args.ids]
        have = [g['productId'] for g in groups]
        if any(i in have for i in ids):
            raise Usage('이미 쇼케이스에 있는 상품: %s' % ', '.join(str(i) for i in ids if i in have))
        if len(groups) + len(ids) > SHOWCASE_SLOTS:
            raise Usage('쇼케이스 슬롯 한도 %d 초과: 지금 %d개 + 추가 %d개' % (SHOWCASE_SLOTS, len(groups), len(ids)))
        data = {'adGroupStatus': 1, 'campaignType': 9,
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
        rows = [dict(field='제거할 광고그룹', current=[g['id'] for g in groups], after=adgroup_ids),
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

def rfq_plan(args, session):
    sub, sub2 = args.sub, getattr(args, 'sub2', None)
    if sub == 'search-add':
        keyword = str(args.keyword or '').strip()
        if not keyword:
            raise Usage('rfq search-add: --keyword 필요')
        params = {'keyword': keyword, 'categoryId': '', 'dateApproved': '', 'region': '', 'country': '',
                  'quantityMin': '', 'quantityMax': ''}
        req = request('GET', MYSOURCING + '/rfq/mySearch/rfq_my_search_ajax.do',
                      {'params': compact(params), 'type': 'new', 'identityCode': ''},
                      label='rfq_my_search_ajax(type=new)')
        rows = [dict(field='저장 검색 keyword', current=None, after=keyword)]
        return make_plan('rfq.search-add', target='검색어 %r' % keyword, rows=rows, requests=[req],
                         reads=[request('GET', SOURCING + '/rfq/ajax/my_search_list_ajax.do', {},
                                        label='저장 검색 목록')],
                         before={}, after={'keyword': keyword}, effect='저장 검색 생성',
                         warnings=['캡처에서 identityCode·callback 값이 가려져 있다 — 서버가 거부하면 종료 1.'],
                         readback=readback_searches)
    if sub == 'search-notify':
        want = 'Y' if args.state == 'on' else 'N'
        have = read_saved_searches(session)
        if not have:
            raise Usage('저장 검색이 없다 — 먼저 rfq search-add')
        chosen = [s for s in have if str(s['id']) == str(args.search)] or \
                 [s for s in have if (s.get('keyword') or '').lower() == str(args.search).lower()]
        if len(chosen) != 1:
            raise Usage('저장 검색을 하나로 정하지 못함: %r (있는 검색: %s)'
                        % (args.search, ', '.join('%s(%s)' % (s.get('keyword'), s.get('id')) for s in have)))
        item = chosen[0]
        req = request('GET', MYSOURCING + '/rfq/mySearch/RfqMySearchNotifyAjax.do',
                      {'op': 'update', 'atm': want, 'searchId': item['id']}, label='RfqMySearchNotifyAjax')
        previous = (item.get('atm') or '').upper()
        rows = [dict(field='알림(%s)' % item.get('keyword'), current=previous or '(미확인)', after=want)]
        restore = {'argv': ['rfq', 'search-notify', 'off' if previous == 'Y' else 'on', '--search', str(item['id'])]} \
            if previous in ('Y', 'N') else {'note': '불가: 지금 알림 값을 읽지 못함'}
        return make_plan('rfq.search-notify', target='저장 검색 %s' % item.get('keyword'), rows=rows,
                         requests=[req], reads=[request('GET', SOURCING + '/rfq/ajax/my_search_list_ajax.do', {},
                                                        label='저장 검색 목록')],
                         before={'atm': previous or None, 'id': item['id']}, after={'atm': want}, restore=restore,
                         effect='알림 메일(판매자)', warnings=['캡처에서 searchId 파라미터 이름이 가려져 있다.'],
                         readback=readback_searches)
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
    view = [{str(s['id']): s.get('keyword')} for s in searches]
    diff = {}
    if 'keyword' in plan['after']:
        want = str(plan['after']['keyword']).lower()
        if not any((s.get('keyword') or '').lower() == want for s in searches):
            diff['keyword'] = {'기대': plan['after']['keyword'], '실제': [s.get('keyword') for s in searches]}
    if 'atm' in plan['after']:
        got = [s.get('atm') for s in searches if str(s['id']) == str(plan['before'].get('id'))]
        if got and str(got[0]).upper() != str(plan['after']['atm']).upper():
            diff['atm'] = {'기대': plan['after']['atm'], '실제': got[0]}
    return report_readback(plan, view, diff)


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
                         reads=[request('GET', MARKETING + '/coupon/v2/querySellerCouponListAjax.do', {},
                                        label='쿠폰 목록')],
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
                         reads=[request('GET', MARKETING + '/coupon/v2/querySellerCouponListAjax.do', {},
                                        label='쿠폰 목록')],
                         before={}, after={'status': 'terminated'}, effect='쿠폰 발급 종료',
                         readback=readback_coupons)
    if sub == 'discount' and sub2 == 'end':
        aid = positive_int(args.id, '할인 activityId')
        req = request('POST', MARKETING + '/promotion/earlyTerminateActivityAjax.do', {},
                      {'activityId': aid}, label='earlyTerminateActivityAjax')
        rows = [dict(field='할인 activityId', current=aid, after='일찍 종료')]
        return make_plan('promo.discount.end', target='할인 %s' % aid, rows=rows, requests=[req],
                         reads=[request('GET', MARKETING + '/promotion/querySellerActivityListAjax.do', {},
                                        label='할인 목록')],
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
    view = coupons
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
    value = read_json(session, 'GET', MARKETING + '/promotion/querySellerActivityListAjax.do', {}, None, '할인 목록')
    rows = rows_of(value) or []
    view = rows
    aid = str(plan['after'].get('activityId'))
    got = None
    for row in rows:
        if isinstance(row, dict) and str(row.get('id') or row.get('activityId')) == aid:
            got = row.get('status') or row.get('activityStatus')
    diff = {}
    if got is not None and 'END' not in str(got).upper() and 'TERMINAT' not in str(got).upper():
        diff['status'] = {'기대': '종료', '실제': got}
    return report_readback(plan, view, diff)


# ---- shipping ----------------------------------------------------------------------------------

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
                         reads=[request('GET', TEMPLATE + '/luyou/ggs/getTemplateList.json', {'lang': 'en_US'},
                                        label='템플릿 목록')],
                         before={'templates': [t['title'] for t in templates]}, after={'title': title},
                         effect='새 배송 템플릿')
    name = str(args.template or '').strip()
    match = [t for t in templates if t['title'] == name]
    if not match:
        raise Usage('템플릿 이름이 목록에 없음: %r (있는 템플릿: %s)'
                    % (name, ', '.join(t['title'] for t in templates) or '없음'))
    current = match[0]
    body = read_template_body(session, current['ftId'])
    original = {'requests': [request('POST', TEMPLATE + '/luyou/ggs/updateTemplate.json', {},
                                     {'json': compact(body)}, label='updateTemplate(원값 복원)')],
                'action': 'shipping.restore', 'target': '템플릿 %r' % name}
    reads = [request('GET', TEMPLATE + '/luyou/ggs/shippingTemplates/getTemplateInfoOverview.json',
                     {'ftId': str(current['ftId'])}, label='getTemplateInfoOverview')]
    update = lambda new_body, label='updateTemplate.json': request(
        'POST', TEMPLATE + '/luyou/ggs/updateTemplate.json', {}, {'json': compact(new_body)}, label=label)

    if sub == 'rename':
        to = str(args.to or '').strip()
        if not to:
            raise Usage('shipping rename: --to 필요')
        new_body = dict(body, title=to)
        rows = [dict(field='템플릿 이름 title', current=body.get('title'), after=to),
                dict(field='나머지 필드', current='원값 유지', after='원값 유지(읽은 본문 그대로)')]
        return make_plan('shipping.rename', target='템플릿 %r → %r' % (name, to), rows=rows,
                         requests=[update(new_body)], reads=reads, before={'title': body.get('title')},
                         after={'title': to}, restore=original, effect='없음(이름만)',
                         readback=readback_templates)
    if sub == 'default':
        req = request('POST', TEMPLATE + '/luyou/settemplateDefault.json', {}, {'json': compact({'id': current['ftId']})},
                      encoding='json', label='settemplateDefault.json')
        rows = [dict(field='기본 템플릿', current=[t['title'] for t in templates if str(t.get('isDefault')) in ('1', 'true', 'True')] or '(미확인)',
                     after=name)]
        previous = previous_default(templates, current['ftId'])
        restore = {'requests': [request('POST', TEMPLATE + '/luyou/settemplateDefault.json', {},
                                        {'json': compact({'id': previous})},
                                        encoding='json', label='settemplateDefault(원값 복원)')]} if previous else \
            {'note': '불가: 지금 기본 템플릿을 목록에서 읽지 못함'}
        return make_plan('shipping.default', target='템플릿 %r' % name, rows=rows, requests=[req], reads=reads,
                         before={'ftId': current['ftId'], 'previousDefault': previous},
                         after={'ftId': current['ftId'], 'default': name},
                         restore=restore, effect='상품 기본 템플릿', readback=readback_templates)
    if sub == 'price-ratio':
        ratio = money(args.ratio, '--ratio')
        changed = set_discount(body, ratio)
        if not changed:
            raise Unverified('템플릿 본문에 freightDetailsDTO.discount 경로가 없다 — 원값 보존 불가')
        rows = [dict(field='배송비 할인율 discount', current=changed['before'], after=changed['after']),
                dict(field='나머지 필드', current='원값 유지', after='원값 유지(읽은 본문 그대로)')]
        return make_plan('shipping.price-ratio', target='템플릿 %r' % name, rows=rows,
                         requests=[update(body)], reads=reads, before={'discount': changed['before']},
                         after={'discount': changed['after']}, restore=original, effect='배송비 표시',
                         readback=readback_templates)
    if sub == 'auto-route':
        rows = [dict(field='새 노선 자동 반영 스위치', current='(현재값 미확인)', after=args.state)]
        return make_plan('shipping.auto-route', target='템플릿 %r' % name, rows=rows, requests=[], reads=reads,
                         before={}, after={'autoRoute': args.state}, effect='배송 노선',
                         blocked='스위치 요청 필드 이름이 캡처에 없다(형식 미검증) — 화면에서 바꿔야 한다')
    # carrier add
    countries = [c.strip().upper() for c in str(args.countries or '').split(',') if c.strip()]
    if not countries:
        raise Usage('shipping carrier add: --countries CA,US,… 필요')
    entry = new_carrier_entry(args, body, countries)
    carriers = list(body.get('carrierList') or [])
    before_codes = [c.get('carrierCode') for c in carriers]
    body = dict(body, carrierList=carriers + [entry])
    verify = request('POST', TEMPLATE + '/web/ggs/logistics/shippingTemplates/verify.json', {},
                     {'json': compact(body)}, encoding='json', label='verify.json')
    rows = [dict(field='추가할 배송사', current=before_codes, after=entry['carrierCode']),
            dict(field='국가', current=None, after=countries),
            dict(field='나머지 필드', current='원값 유지', after='원값 유지(읽은 본문 그대로)')]
    return make_plan('shipping.carrier-add', target='템플릿 %r' % name, rows=rows,
                     requests=[verify, update(body)], reads=reads,
                     before={'ftId': current['ftId'], 'carrierList': before_codes},
                     after={'ftId': current['ftId'], 'carrierList': before_codes + [entry['carrierCode']]},
                     restore=original, effect='배송 가능 국가·요금', readback=readback_templates)


def previous_default(templates, current_ft):
    for template in templates:
        if str(template.get('isDefault')) in ('1', 'true', 'True') and str(template['ftId']) != str(current_ft):
            return template['ftId']
    return current_ft


def set_discount(body, ratio):
    """바꿀 칸만 덮어쓴다: carrierList[].detailList[].freightDetailsDTO.discount → ratio*100."""
    before, after = [], []
    value = int(round(ratio * 100))
    for carrier in body.get('carrierList') or []:
        for detail in carrier.get('detailList') or []:
            freights = detail.get('freightDetailsDTO')
            if isinstance(freights, dict) and 'discount' in freights:
                before.append(freights.get('discount'))
                freights['discount'] = value
                after.append(value)
    return {'before': before, 'after': after}


def new_carrier_entry(args, body, countries):
    return {'selectedWarehouse': None, 'carrierCode': 'GGS_SELLER_LOGISTICS_OTHER',
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
    view = [{'ftId': t['ftId'], 'title': t['title'], 'isDefault': t.get('isDefault')} for t in templates]
    diff = {}
    if 'title' in plan['after']:
        if not any(t['title'] == plan['after']['title'] for t in templates):
            diff['제목'] = {'기대': plan['after']['title'], '실제': [t['title'] for t in templates]}
    if plan['after'].get('default'):
        got = next((t['title'] for t in templates if str(t.get('isDefault')) in ('1', 'true', 'True')), None)
        if got is not None and got != plan['after']['default']:
            diff['기본값'] = {'기대': plan['after']['default'], '실제': got}
    if 'carrierList' in plan['after']:
        got = next((t for t in templates if str(t['ftId']) == str(plan['before'].get('ftId') or plan['after'].get('ftId'))), None)
        if got is None:
            diff['템플릿'] = {'기대': plan['after']['carrierList'], '실제': None}
    return report_readback(plan, view, diff)


# ---- store -------------------------------------------------------------------------------------

def store_plan(args, session):
    sub, sub2 = args.sub, getattr(args, 'sub2', None)
    versions = read_versions(session)
    match = [v for v in versions if v['name'] == args.version]
    if not match:
        raise Usage('버전 이름이 목록에 없음: %r (있는 버전: %s)'
                    % (args.version, ', '.join(v['name'] for v in versions) or '없음'))
    current = match[0]
    reads = [request('GET', SITEADMIN + '/page/list/keyword/packageId', {'keyword': '', 'pageType': 6},
                     label='버전 목록')]
    if sub == 'version' and sub2 == 'rename':
        to = str(args.to or '').strip()
        if not to:
            raise Usage('store version rename: --to 필요')
        req = request('POST', SITEADMIN + '/page/editPage', {},
                      {'pageType': 6, 'pageId': current['pageId'], 'pageName': to}, label='page/editPage')
        rows = [dict(field='버전 이름 pageName', current=current['name'], after=to)]
        return make_plan('store.version.rename', target='버전 %r' % current['name'], rows=rows, requests=[req],
                         reads=reads, before={'pageName': current['name']}, after={'pageName': to},
                         restore={'argv': ['store', 'version', 'rename', '--version', to, '--to', current['name']]},
                         effect='없음', readback=readback_versions)
    # publish: csrf 는 계획 단계에서 읽는다(읽기). 출력에서는 printable() 이 값을 가린다.
    csrf = publish_csrf(session)
    req = request('POST', SITEADMIN + '/publish',
                  {'pageId': current['pageId'], 'pageType': 6, 'commonPageType': 100,
                   csrf['name']: csrf['token'], 'cToken': csrf['token']}, None, label='publish')
    live = next((v for v in versions
                 if str(v.get('online')).lower() in ('1', 'true') or v.get('released')
                 or str(v.get('status')).lower() in ('online', 'published', 'released')), None)
    restore = {'argv': ['store', 'version', 'publish', '--version', live['name']]} if live and live['name'] != args.version \
        else {'note': '불가: 직전 라이브 버전을 목록에서 정하지 못함(지금 라이브와 같은 버전이면 되돌릴 대상이 없다)'}
    rows = [dict(field='게시할 버전', current=current['name'], after='라이브(바이어 화면)'),
            dict(field='직전 라이브', current=None, after=live['name'] if live else '(미확인)')]
    return make_plan('store.version.publish', target='버전 %r' % current['name'], rows=rows, requests=[req],
                     reads=reads, before={'pageId': current['pageId']}, after={'pageName': current['name'],
                                                                            'published': True},
                     restore=restore, effect='바이어가 보는 스토어가 바뀐다',
                     grade_note='되돌릴 수 있음 · 외부 노출 — 직전 라이브 버전을 다시 게시해 되돌린다',
                     readback=readback_versions)


def publish_csrf(session):
    """siteadmin 은 자체 csrf 부트스트랩을 쓴다: GET /csrf/ajax?cToken=<ctoken 쿠키>."""
    token = session.cookie('ctoken', SITEADMIN + '/') or ''
    value = read_json(session, 'GET', SITEADMIN + '/csrf/ajax', {'cToken': token}, None, '스토어 csrf')
    name = value.get('parameterName') or value.get('csrfParameterName')
    token = value.get('token') or value.get('csrfToken')
    if not name or not token:
        raise SessionError('스토어 csrf 부트스트랩 실패: parameterName/token 없음')
    return {'name': name, 'token': token}


def readback_versions(session, plan):
    versions = read_versions(session)
    view = [{'pageId': v['pageId'], 'name': v['name'], 'online': v.get('online'), 'released': v.get('released')}
            for v in versions]
    diff = {}
    if plan['action'] == 'store.version.rename':
        if not any(v['name'] == plan['after']['pageName'] for v in versions):
            diff['이름'] = {'기대': plan['after']['pageName'], '실제': [v['name'] for v in versions]}
    return report_readback(plan, view, diff)


# ---- settings ----------------------------------------------------------------------------------

MESSAGE_PARAMS = {
    'defaultContact': None, 'cc': True, 'tmCc': True, 'isCustomerFirst': False, 'rule': 'product',
    'adminFeedback': {'autoDistribute': False, 'accountStatus': [], 'hlbAccountStatus': []},
    'syncRuleToIm': True, 'syncAutoTurnToIm': True, 'subTransferRec': False, 'highLevelBuyerTurn': False,
}


def settings_plan(args, session):
    sub = args.sub
    if sub == 'message':
        params = json.loads(json.dumps(MESSAGE_PARAMS))
        changed = []
        if args.contact:
            params['defaultContact'] = positive_int(args.contact, '--contact')
            changed.append('defaultContact=%s' % params['defaultContact'])
        for flag in ('cc', 'auto_distribute', 'sync_to_im'):
            state = getattr(args, flag, None)
            if state is None:
                continue
            value = state == 'on'
            if flag == 'cc':
                params['cc'] = params['tmCc'] = value
            elif flag == 'auto_distribute':
                params['adminFeedback']['autoDistribute'] = value
            else:
                params['syncRuleToIm'] = params['syncAutoTurnToIm'] = value
            changed.append('%s=%s' % (flag, state))
        if not changed:
            raise Usage('settings message: --contact/--cc/--auto-distribute/--sync-to-im 중 하나 필요')
        body = {'cmd': 'updateForm', 'params': params}
        req = request('POST', MESSAGE + '/feedbackset/index.htm', {}, {'json': compact(body)}, label='feedbackset')
        rows = [dict(field='메시지 설정 변경', current=None, after=changed),
                dict(field='나머지 필드', current='capture 본문 값 그대로', after='그대로(바꾼 칸만 덮어씀)')]
        return make_plan('settings.message', target='메시지 설정', rows=rows, requests=[req], reads=[],
                         before={'params': params}, after={'params': params},
                         restore={'requests': [request('POST', MESSAGE + '/feedbackset/index.htm', {},
                                                      {'json': compact({'cmd': 'updateForm',
                                                                        'params': json.loads(json.dumps(MESSAGE_PARAMS))})},
                                                      label='feedbackset(원값 복원)')]},
                         effect='문의 배분·사본 메일',
                         warnings=['현재값 읽기 경로가 캡처에 없다 — 캡처 본문 값을 기준으로 바꾼 칸만 덮어쓴다(토큰은 세션이 붙임).'])
    privacy = read_privacy(session)
    original = json.loads(json.dumps(privacy))
    changed = []
    for item in args.set or []:
        if '=' not in item:
            raise Usage('settings privacy --set 은 경로=값 형식: %r' % item)
        path, raw = item.split('=', 1)
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        set_path(privacy, path, value)
        changed.append('%s=%s' % (path, value))
    if not changed:
        raise Usage('settings privacy: --set 경로=값 필요')
    req = request('POST', PROFILE + '/ajax/privacy/privacy_setting_update.do', {},
                  {'privaceSetting': compact(privacy)}, label='privacy_setting_update')
    rows = [dict(field='개인정보 공개 범위', current=changed, after='위 값으로 저장(나머지 칸 원값 유지)')]
    return make_plan('settings.privacy', target='개인정보 설정', rows=rows, requests=[req], reads=[],
                     before={'privaceSetting': original}, after={'privaceSetting': privacy},
                     restore={'requests': [request('POST', PROFILE + '/ajax/privacy/privacy_setting_update.do', {},
                                                   {'privaceSetting': compact(original)},
                                                   label='privacy_setting_update(원값 복원)')]},
                     effect='프로필 공개 범위')


def set_path(tree, path, value):
    parts = [p for p in str(path).split('.') if p != '']
    if not parts:
        raise Usage('--set 경로가 비었다')
    node = tree
    for part in parts[:-1]:
        if isinstance(node, list):
            index = int(part)
            node = node[index]
        elif isinstance(node, dict):
            node = node.setdefault(part, {})
        else:
            raise Usage('--set 경로가 현재 JSON 구조와 다름: %s' % path)
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = value
    elif isinstance(node, dict):
        node[last] = value
    else:
        raise Usage('--set 경로가 현재 JSON 구조와 다름: %s' % path)


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
        return {'action': action, 'grade': REVERSIBLE, 'source': source[1],
                'target': restore.get('target') or record.get('target'), 'rows':
                    [dict(field='원래 요청 본문 재전송', current=None, after='백업 시점 값')],
                'requests': restore['requests'], 'reads': [], 'before': record.get('before'),
                'after': record.get('after'),
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
    pnotify = sub_parser(rfq, 'search-notify')
    pnotify.add_argument('state', choices=('on', 'off'))
    pnotify.add_argument('--search', required=True, help='저장 검색 keyword 또는 id')
    pquote = sub_parser(rfq, 'quote')
    quote_args(pquote)
    ptemplate = sub_parser(rfq, 'quote-template').add_subparsers(dest='sub2')
    psave = sub_parser(ptemplate, 'save')
    quote_args(psave, template=True)

    promo = group('promo', '쿠폰·할인·행사')
    coupon = promo.add_parser('coupon').add_subparsers(dest='sub2')
    pcreate = sub_parser(coupon, 'create')
    pcreate.add_argument('--name', required=True)
    pcreate.add_argument('--shop', required=True, help='쿠폰 적용 상점 id(participateId)')
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
    pratio.add_argument('--ratio', required=True, help='할인율 배수(1.2 = 120%)')
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

    store = group('store', '스토어 버전 이름·게시')
    version = store.add_parser('version').add_subparsers(dest='sub2')
    pvrename = sub_parser(version, 'rename')
    pvrename.add_argument('--version', required=True)
    pvrename.add_argument('--to', required=True)
    pvpublish = sub_parser(version, 'publish')
    pvpublish.add_argument('--version', required=True)

    settings = group('settings', '메시지·개인정보 설정')
    pmessage = sub_parser(settings, 'message')
    pmessage.add_argument('--contact')
    pmessage.add_argument('--cc', choices=('on', 'off'))
    pmessage.add_argument('--auto-distribute', dest='auto_distribute', choices=('on', 'off'))
    pmessage.add_argument('--sync-to-im', dest='sync_to_im', choices=('on', 'off'))
    pprivacy = sub_parser(settings, 'privacy')
    pprivacy.add_argument('--set', action='append', dest='set', metavar='PATH=VALUE')

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
