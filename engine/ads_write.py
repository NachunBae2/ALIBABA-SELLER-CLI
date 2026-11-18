"""광고 캠페인의 이름·상태·예산·국가·상품을 바꿀 때(입찰가·시간대·키워드·새 캠페인 포함).

기본 dry-run, --yes 로만 반영, 쓰기 전 자동 백업. 요청 형상은 2026-11-18 화면 캡처(specs.json 26건)에서 나온 것만 쓴다.
모든 쓰기는 POST https://www2.alibaba.com<path>, form 본문
ads={"productLineId":<pl>}&type=normal&data=<JSON>&_csrf=<token>, 성공 = 응답 status 1000.
토큰은 읽기 명령과 같은 부트스트랩(api_session.py)에서 받는다.

stdout: 보고(전/후 표, 보낼 요청, 비용 영향, 형식 출처, 백업 경로, 리드백).
stderr: 진행·오류. 종료: 0 정상/dry-run, 1 요청 실패, 2 연결 불가·세션 만료,
3 리드백 불일치, 64 사용법 오류·형식 미검증·되돌릴 수 없는 변경, 77 차단 계정.
"""
import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

from api_session import ApiSession
from http_session import SessionError, compact, scrub
from seller_central_raw import DEFAULT_CONFIG, load_account

HOST = 'https://www2.alibaba.com'
ADS_PAGE = HOST + '/supereme/index.htm'
PRODUCT_LINE = 110106            # 이미 운영 중인 캠페인의 쓰기 요청에 쓰는 productLineId
MANUAL_PRODUCT_LINE = 110101     # 생성 화면(create.htm?type=customized) 고정값
BLOCKED_ACCOUNTS = ('wonnischoice', 'linea')
COST_RISE = '비용 증가 가능'
COST_DOWN = '비용 감소/없음'

CAMPAIGN_UPDATE = '/api/campaign/update'
CAMPAIGN_UPDATE_BATCH = '/api/campaign/update/batch'
CAMPAIGN_PRECHECK = '/api/campaign/update/precheck'
CAMPAIGN_CAN_STARTUP = '/api/campaign/can/startup'
CAMPAIGN_CAN_CREATE = '/api/campaign/can/create'
CAMPAIGN_CREATE = '/api/campaign/create'
ADGROUP = '/api/campaign/{id}/adgroup'
BIDWORD = '/api/campaign/{id}/bidword'

# ---- 화면 캡처에서 나온 요청 형상 ---------------------------------------------------------------
# key = 우리 action 이름, value = 형식 출처 + 캡처 파일
VERIFIED = {
    'campaign.rename': 'captured', 'campaign.countries': 'captured', 'product.add': 'captured',
    'campaign.status': 'captured-xform', 'campaign.budget': 'captured-xform', 'campaign.bid': 'captured-xform',
    'campaign.weekBudget': 'captured-xform', 'campaign.hours': 'captured-xform',
    'product.remove': 'captured-xform', 'product.pause': 'captured-xform', 'product.resume': 'captured-xform',
    'keyword.add': 'captured-xform', 'keyword.remove': 'captured-xform', 'keyword.pause': 'captured-xform',
    'keyword.resume': 'captured-xform', 'keyword.bid': 'captured-xform', 'campaign.create': 'captured',
}
# 형식 출처 문구는 dry-run/계획 출력에 그대로 찍는다.
SOURCE_LABEL = {
    'captured': '형식 출처: 화면 캡처',
    'captured-xform': '형식 출처: 화면 캡처(읽기 응답 변형)',
}
# 캠페인 유형별로 --yes 를 허용하는 action. 화면 캡처가 그 유형(productLineId)에서 나온 것만 넣는다.
# 2(수동, 110101): 운영 조작은 읽기 응답 변형 캡처(m12·m18~m37), 이름은 같은 /api/campaign/update {id,title}.
# 51·47(자동 배송 계획, 110106): 1차 캡처(이름·국가·상품 추가)만. 예산은 패키지 결제라 화면에 편집이 없다.
CAPTURED_TYPES = {
    2: ('campaign.rename', 'campaign.status', 'campaign.budget', 'campaign.bid', 'campaign.weekBudget',
        'campaign.hours', 'product.add', 'product.remove', 'product.pause', 'product.resume',
        'keyword.add', 'keyword.remove', 'keyword.pause', 'keyword.resume', 'keyword.bid'),
    51: ('campaign.rename', 'campaign.countries', 'product.add'),
    47: ('campaign.rename', 'campaign.countries', 'product.add'),
}
TYPE_LABEL = {2: '수동', 47: '제품 프로모션', 51: '가게 프로모션'}
STATUS_LABEL = {1: '온라인(1)', 0: '꺼짐(0)', 9: '출시 종료(9)'}
DEFAULT_HOURS = list(range(24))       # 생성 화면 기본값: 하루 24시간
# 생성 화면 고정값(카메라 캡처 w9.out): 언어·지역·스마트 제목/사진 칸은 화면 그대로 보낸다.
CREATE_SITES = ('ES,FR,PT,DE,IT,JA,RU,AR,KO,TH,VI,TR,NL,HE,ID,HI,EN')
CREATE_KEYWORD_SOURCE = 'customer'  # 직접 입력한 키워드(m28 캡처). 추천 목록에서 고르면 화면은 'recommend'


class Usage(Exception):
    pass


class Unverified(Exception):
    pass


class Blocked(Exception):
    """Account refused for ad writes (exit 77)."""


# ---- transport -----------------------------------------------------------------------------------

def send(session, path, data, *, cid=None, product_line=None):
    """One request in the captured envelope; data is the `ads[...data]` JSON."""
    target = path.format(id=cid) if cid is not None else path
    params = {'ads': {'productLineId': PRODUCT_LINE if product_line is None else product_line},
              'type': 'normal', 'data': data}
    return session.request('POST', HOST + target, None, params, 'form', referer=ADS_PAGE)


def parsed(response):
    try:
        value = json.loads(response['text'])
    except (ValueError, KeyError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def short(value):
    return compact(scrub(value))[:200] if value is not None else ''


def read_failure(response, value, what):
    if response.get('status') != 200 or not isinstance(value, dict):
        return '%s 읽기 실패: HTTP %s %s' % (what, response.get('status'), short(value))
    if value.get('status') not in (1000, '1000'):
        return '%s 읽기 실패: %s' % (what, short({k: value[k] for k in ('status', 'code', 'message', 'msg') if k in value}))
    return None


def write_failure(response, value):
    """None when the server accepted the write; else a short reason (no token, no raw body)."""
    keys = ('status', 'code', 'ok', 'success', 'error', 'errorVO', 'message', 'msg')
    if response.get('status') == 200 and isinstance(value, dict):
        rejected = (value.get('ok') is False or value.get('success') is False or value.get('successed') is False
                    or value.get('errorVO') or value.get('error') not in (None, False, '', 0)
                    or value.get('status') not in (1000, '1000', None))
        if not rejected:
            return None
        return short({k: value[k] for k in keys if k in value}) or 'HTTP %s 거부' % response.get('status')
    return 'HTTP %s: %s' % (response.get('status'), short(value) or 'JSON 응답 아님')


def read_data(session, path, data, what, *, cid=None):
    response = send(session, path, data, cid=cid)
    value = parsed(response)
    error = read_failure(response, value, what)
    if error:
        raise SessionError(error)
    return value.get('data') if value else None


def read_campaign(session, cid):
    data = read_data(session, '/api/campaign/{id}', {}, '캠페인', cid=cid)
    if not isinstance(data, dict):
        raise SessionError('캠페인 읽기 실패: data 없음')
    return data


def read_adgroups(session, cid):
    data = read_data(session, ADGROUP + '/batch', {}, '상품 목록', cid=cid)
    if not isinstance(data, list):
        raise SessionError('상품 목록 읽기 실패: data 배열 아님')
    return data


def read_keywords(session, cid):
    """bidword/query — 키워드 단어를 id 로 바꾸고 리드백에 쓴다."""
    data = read_data(session, BIDWORD + '/query', {}, '키워드 목록', cid=cid)
    if isinstance(data, dict):
        data = data.get('list') or data.get('keywordList') or data.get('data')
    return data if isinstance(data, list) else []


def adgroup_count(session, cid):
    response = send(session, '/api/campaign/adgroup/count', {'campaignIdList': [cid], 'adgroupOnlineStatus': 1})
    value = parsed(response)
    data = value.get('data') if value else None
    return data.get(str(cid)) if isinstance(data, dict) else None


# ---- 현재 값 -----------------------------------------------------------------------------------

def get_in(value, *keys):
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def campaign_scope(campaign):
    scope = get_in(campaign, 'properties', 'operationScope', 'campaignScope')
    return scope if isinstance(scope, dict) else {}


def with_scope(campaign, **fields):
    """배치 응답에는 갱신된 캠페인이 없어 단건 읽기로 리드백한다 — 그 응답에 시간대를 반영."""
    props = campaign.setdefault('properties', {})
    conf = props.setdefault('operationScope', {}).setdefault('campaignScope', {})
    for key in ('syncPromotionHourConf', 'syncPromotionRegionConf'):
        if key in fields and isinstance(fields[key], list):
            fields[key] = ','.join(str(v) for v in fields[key])   # 화면은 "0,1,2" 문자열로 돌려준다
    conf.update(fields)
    return campaign


def hours_text(campaign):
    raw = campaign_scope(campaign).get('syncPromotionHourConf')
    return raw if isinstance(raw, str) and raw else None


def campaign_view(campaign):
    props = campaign.get('properties') or {}
    scope = campaign_scope(campaign)
    return {'type': campaign.get('type'), 'title': campaign.get('title'), 'onlineStatus': campaign.get('onlineStatus'),
            'settleStatus': campaign.get('settleStatus'), 'budget': props.get('budget'), 'bid': props.get('maxPrice'),
            'bidType': props.get('bidType'), 'weekBudget': props.get('weekBudgetStatus'),
            'hours': hours_text(campaign), 'regions': scope.get('regions') if scope else None}


def country_count(code):
    return 1 if code in (None, 'all') else len([c for c in str(code).split(',') if c])


def as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def hours_list(text):
    """'0,1,…' → [0,1,…]; None/빈 값은 None."""
    if text in (None, ''):
        return None
    parts = text if isinstance(text, (list, tuple)) else str(text).split(',')
    return [int(h) for h in parts if str(h).strip().isdigit()]


def as_list(text):
    """'0,1,2' / [0,1,2] / [None] → [0,1,2]; None·빈 값은 None."""
    if text in (None, ''):
        return None
    if isinstance(text, str):
        parts = [p.strip() for p in text.split(',')]
    elif isinstance(text, (list, tuple)):
        parts = [str(p).strip() for p in text]
    else:
        return None
    if not parts or all(p in ('', 'None') for p in parts):
        return None
    return [int(p) for p in parts if p.isdigit()]


def equal(field, expected, actual):
    if field in ('budget', 'bid', 'price'):
        want, got = as_float(expected), as_float(actual)
        return want is not None and got is not None and math.isclose(want, got, rel_tol=0, abs_tol=1e-9)
    if field in ('onlineStatus', 'bidType'):
        try:
            return int(expected) == int(actual)
        except (TypeError, ValueError):
            return False
    if field == 'hours':
        if expected is None:
            return actual in (None, '')
        return as_list(expected) == as_list(actual)
    if field in ('products', 'keywords', 'keywordPrices'):
        return expected == actual
    return expected == actual


# ---- 계획 ---------------------------------------------------------------------------------------

def campaign_before(action, view):
    if action == 'campaign.rename':
        return {'title': view['title']}
    if action == 'campaign.status':
        return {'onlineStatus': view['onlineStatus'], 'settleStatus': view['settleStatus']}
    if action == 'campaign.budget':
        return {'budget': view['budget']}
    if action == 'campaign.bid':
        return {'bid': view['bid'], 'bidType': view['bidType']}
    if action == 'campaign.weekBudget':
        return {'weekBudget': view['weekBudget']}
    if action == 'campaign.hours':
        return {'hours': view['hours']}
    return {'regions': view['regions']}


def campaign_after(action, opts):
    if action == 'campaign.rename':
        return {'title': opts.name}
    if action == 'campaign.status':
        return {'onlineStatus': 1 if opts.status == 'on' else 0}
    if action == 'campaign.budget':
        return {'budget': float(opts.budget)}
    if action == 'campaign.bid':
        return {'bid': float(opts.bid)}
    if action == 'campaign.weekBudget':
        return {'weekBudget': 'on' if opts.week_budget == 'on' else 'off'}
    if action == 'campaign.hours':
        return {'hours': list(opts.hours)}
    return {'regions': None if opts.countries == 'all' else opts.countries}


def campaign_rows(action, view, opts):
    if action == 'campaign.rename':
        return [dict(field='캠페인 이름 title', current=view['title'], after=opts.name)]
    if action == 'campaign.status':
        return [dict(field='상태 onlineStatus', current=STATUS_LABEL.get(view['onlineStatus'], view['onlineStatus']),
                     after=STATUS_LABEL[1 if opts.status == 'on' else 0]),
                dict(field='settleStatus', current=view['settleStatus'], after='변경 안 함(요청에 없음)')]
    if action == 'campaign.budget':
        return [dict(field='일 예산 budget', current=view['budget'], after=float(opts.budget))]
    if action == 'campaign.bid':
        return [dict(field='캠페인 입찰가 maxPrice', current=view['bid'], after=float(opts.bid)),
                dict(field='입찰 방식 bidType', current=view['bidType'], after=view['bidType'])]
    if action == 'campaign.weekBudget':
        return [dict(field='주간 예산 weekBudgetStatus', current=view['weekBudget'], after=opts.week_budget)]
    if action == 'campaign.hours':
        return [dict(field='시간대 syncPromotionHourConf', current=view['hours'] or '(없음: 24시간)', after=opts.hours)]
    return [dict(field='국가 regions', current='전체 국가' if view['regions'] is None else view['regions'],
                 after='전체 국가' if opts.countries == 'all' else opts.countries),
            dict(field='국가 수', current=country_count(view['regions']), after=country_count(opts.countries))]


def cost_lines(action, before, after):
    """Cost direction for both dry-run and real runs: the commands stay allowed, but say it."""
    if action == 'campaign.status':
        if after.get('onlineStatus') != 1:
            return [COST_DOWN + ': 캠페인을 끄면 광고비가 멈춤']
        if before.get('onlineStatus') in (1, '1'):
            return ['비용: 이미 온라인 — 변화 없음 예상']
        return [COST_RISE + ': 캠페인을 켜면 노출·클릭이 시작됨']
    if action in ('campaign.budget', 'campaign.bid', 'keyword.bid'):
        key = 'price' if action == 'keyword.bid' else ('bid' if action == 'campaign.bid' else 'budget')
        now, want = as_float(before.get(key)), as_float(after.get(key))
        if now is not None and want is not None and want > now:
            return [COST_RISE + ': %s 를 올리면 노출·클릭 상한이 커짐' % key]
        if now is not None and want is not None and want < now:
            return [COST_DOWN + ': %s 를 내림' % key]
        return ['비용: 값 그대로 — 변화 없음 예상']
    if action == 'campaign.weekBudget':
        return [COST_RISE + ': 주간 예산을 켜면 하루 최대 +20% 까지 더 쓸 수 있음'] if after.get('weekBudget') == 'on' \
            else [COST_DOWN + ': 주간 예산을 끄면 하루 예산까지만 씀']
    if action == 'campaign.hours':
        now, want = hours_list(before.get('hours')) or DEFAULT_HOURS, hours_list(after.get('hours')) or []
        if len(want) > len(now):
            return [COST_RISE + ': 시간대를 늘리면 (%d → %d시간) 노출이 늘어남' % (len(now), len(want))]
        if len(want) < len(now):
            return [COST_DOWN + ': 시간대를 줄임 (%d → %d시간)' % (len(now), len(want))]
        return ['비용: 시간대 길이 그대로 — 변화 없음 예상']
    if action == 'campaign.countries':
        now, want = country_count(before.get('regions')), country_count(after.get('regions'))
        if want > now:
            return [COST_RISE + ': 국가를 추가하면 노출 범위가 커짐 (%d → %d)' % (now, want)]
        if want < now:
            return [COST_DOWN + ': 국가를 줄임 (%d → %d)' % (now, want)]
        return ['비용: 국가 수 그대로 — 변화 없음 예상']
    if action in ('product.add', 'product.resume'):
        return [COST_RISE + ': 상품을 광고에 넣으면 노출 대상이 늘어남']
    if action in ('product.remove', 'product.pause'):
        return [COST_DOWN + ': 상품을 광고에서 빼면 대상이 줄어듦']
    if action == 'keyword.add':
        return [COST_RISE + ': 키워드를 추가하면 노출·클릭이 늘 수 있음']
    if action == 'keyword.resume':
        return [COST_RISE + ': 키워드를 다시 켜면 노출이 시작됨']
    if action in ('keyword.remove', 'keyword.pause'):
        return [COST_DOWN + ': 키워드를 %s 하면 노출이 줄어듦' % ('삭제' if action == 'keyword.remove' else '중단')]
    if action == 'campaign.create':
        return [COST_RISE + ': 캠페인을 만들면 즉시 켜지고 하루 예산만큼 광고비가 나갈 수 있음']
    return []


def req(path, data, cid=None, product_line=None):
    return {'method': 'POST', 'url': HOST + (path.format(id=cid) if cid else path),
            'contentType': 'application/x-www-form-urlencoded; charset=UTF-8',
            'ads': {'productLineId': PRODUCT_LINE if product_line is None else product_line},
            'type': 'normal', 'data': data}


def price_update(plan, *, modify=None):
    """화면 캡처(m36.out): 예산·입찰은 한 요청에 현재값 4개를 함께 실어 보낸다."""
    view = plan['view']
    data = {'id': plan['campaignId'], 'maxPrice': float(plan['target']['bid']),
            'bidType': view.get('bidType') if view.get('bidType') is not None else 2,
            'budget': float(plan['target']['budget']),
            'weekBudgetStatus': 'on' if (view.get('weekBudget') or 'on') == 'on' else 'off',
            'needUpdateCampaignIds': []}
    if modify:
        data['modifyNum'] = modify
    return data


def target_prices(plan):
    """바꾼 값은 새 값, 나머지는 현재값(캡처 형식: 한 요청에 함께)."""
    view = plan['view']
    bid = view.get('bid')
    budget = view.get('budget')
    if plan['action'] == 'campaign.bid':
        bid = float(plan['opts'].bid)
    if plan['action'] == 'campaign.budget':
        budget = float(plan['opts'].budget)
    return {'bid': bid if bid is not None else 0.5, 'budget': budget if budget is not None else 20}


def plan_requests(plan):
    action, cid, opts = plan['action'], plan['campaignId'], plan['opts']
    if action == 'campaign.create':
        return [req(CAMPAIGN_CAN_CREATE, plan['create']['data'], product_line=MANUAL_PRODUCT_LINE),
                req(CAMPAIGN_CREATE, plan['create']['data'], product_line=MANUAL_PRODUCT_LINE)]
    if action in ('campaign.budget', 'campaign.bid'):
        requests = []
        if plan['modify']:
            # 내리기만 precheck(module price)를 먼저 보낸다 — 현재 maxPrice 를 싣는다(m36·m37).
            requests.append(req(CAMPAIGN_PRECHECK, {'id': cid, 'module': 'price',
                                                    'params': {'bidType': str(plan['view'].get('bidType') or 2),
                                                               'maxPrice': as_float(plan['view'].get('bid'))}}))
        requests.append(req(CAMPAIGN_UPDATE, price_update(plan, modify=1 if plan['modify'] else None)))
        return requests
    if action == 'campaign.rename':
        return [req(CAMPAIGN_UPDATE, {'id': cid, 'title': opts.name})]
    if action == 'campaign.status':
        data = {'id': cid, 'onlineStatus': 1 if opts.status == 'on' else 0, 'productLineId': PRODUCT_LINE}
        requests = [req(CAMPAIGN_CAN_STARTUP, {'id': cid, 'onlineStatus': 1, 'settleStatus': 1})] if opts.status == 'on' else []
        requests.append(req(CAMPAIGN_UPDATE, data))
        return requests
    if action == 'campaign.weekBudget':
        return [req(CAMPAIGN_UPDATE, {'id': cid, 'weekBudgetStatus': 'on' if opts.week_budget == 'on' else 'off'})]
    if action == 'campaign.hours':
        operation = {'id': cid, 'syncMultipleSiteConf': CREATE_SITES,
                     'syncPromotionHourConf': ','.join(str(h) for h in opts.hours)}
        return [req(CAMPAIGN_UPDATE_BATCH, {'campaignOperationList': [operation]})]
    if action == 'product.add':
        return [req(ADGROUP + '/can/create', {'adGroupOperationList': [{'productId': p} for p in opts.products]}, cid),
                req(ADGROUP + '/create', {'adGroupOperationList': [{'productId': p, 'onlineStatus': 1}
                                                                   for p in opts.products]}, cid)]
    if action == 'product.remove':
        pairs = [{'id': i, 'productId': p} for i, p in plan['pairs']]
        return [req(ADGROUP + '/delete', {'adGroupOperationList': pairs}, cid)]
    if action in ('product.pause', 'product.resume'):
        status = 1 if action == 'product.resume' else 0
        groups = [{'id': i, 'onlineStatus': status} for i in plan['adgroups']]
        requests = [req(ADGROUP + '/can/startup', {'adGroupOperationList': [{'id': g['id']} for g in groups]}, cid)] \
            if action == 'product.resume' else []
        requests.append(req(ADGROUP + '/update', {'adGroupOperationList': groups}, cid))
        return requests
    if action == 'keyword.add':
        items = [{'word': k['word'], 'price': float(k['price']), 'source': k.get('source') or CREATE_KEYWORD_SOURCE}
                 for k in plan['keywords']]
        return [req(BIDWORD + '/create', {'keywordList': items}, cid)]
    if action == 'keyword.remove':
        return [req(BIDWORD + '/delete', {'keywordList': [{'id': k['id']} for k in plan['keywords']]}, cid)]
    if action in ('keyword.pause', 'keyword.resume'):
        return [req(BIDWORD + '/update', {'updateType': 'update_status',
                                          'keywordList': [{'id': k['id']} for k in plan['keywords']],
                                          'updateInfo': {'onlineStatus': 1 if action == 'keyword.resume' else 0}}, cid)]
    if action == 'keyword.bid':
        return [req(BIDWORD + '/update', {'updateType': 'add_price',
                                          'keywordList': [{'id': k['id'], 'price': float(k['price'])}
                                                          for k in plan['keywords']]}, cid)]
    # 국가
    operation = {'id': cid}
    if opts.countries != 'all':
        operation['syncPromotionRegionConf'] = opts.countries
    return [req(CAMPAIGN_UPDATE_BATCH, {'campaignOperationList': [operation]})]


def plan_reads(plan):
    cid = plan['action'] == 'campaign.create' and None or plan['campaignId']
    action = plan['action']
    if action == 'campaign.create':
        return [req('/api/campaign/{id}', {}, plan['need_id']) if plan.get('need_id') else
                req('/api/campaign/type/allList', {'paging': False, 'typeList': [1, 2, 5, 9, 47, 51]})]
    if action.startswith('keyword.'):
        return [req('/api/campaign/{id}', {}, cid), req(BIDWORD + '/query', {'paging': False}, cid)]
    if action.startswith('product.'):
        return [req(ADGROUP + '/batch', {}, cid),
                req('/api/campaign/adgroup/count', {'campaignIdList': [cid], 'adgroupOnlineStatus': 1})]
    return [req('/api/campaign/{id}', {}, cid)]


# ---- 백업 --------------------------------------------------------------------------------------

def backup_root(account):
    """Backups live where the report CLI already writes for this account."""
    base = Path(account['outDir']) if account.get('outDir') else Path.home() / '30_Assets' / account['name'] / 'seller-report'
    return base / 'ads-backups'


def write_backup(account, plan, requests, *, root=None):
    directory = Path(root) if root else backup_root(account)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%dT%H%M%S') + '-%06d' % (int(time.time() * 1000000) % 1000000)
    stem = '%s-%s-%s' % (account['name'], plan.get('campaignId') or 'new', stamp)
    target, n = directory / (stem + '.json'), 1
    while target.exists():
        n += 1
        target = directory / ('%s-%d.json' % (stem, n))
    before = dict(plan['before'])
    for key in ('adgroups', 'keywords', 'products'):
        if plan.get(key):
            before[key] = plan[key]
    record = {'account': account['name'], 'campaignId': plan.get('campaignId'), 'action': plan['action'],
              'createdAt': time.strftime('%Y-%m-%dT%H:%M:%S'), 'before': before, 'request': requests,
              'note': 'restore <이 파일> 로 원값 복원(같은 규칙: 기본 dry-run, --yes).'}
    target.write_text(json.dumps(record, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return target


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
        rows.append({'file': str(file), 'account': record.get('account'), 'campaignId': record.get('campaignId'),
                     'action': record.get('action'), 'createdAt': record.get('createdAt')})
    return rows


# ---- 실행 --------------------------------------------------------------------------------------

def show_plan_or_run(account, plan, session, *, dry_run):
    """Print the before/after table + request, then write only with --yes."""
    action = plan['action']
    source = VERIFIED.get(action)
    if action == 'campaign.create':
        print('경고: 생성 즉시 캠페인이 켜지고 하루 예산만큼 과금이 시작된다. 끈 상태(초안)로 저장하는 필드는')
        print('      캡처에 없다. --yes 만으로는 보내지 않는다: --yes --start-billing 두 개가 모두 있어야 전송하고,')
        print('      전송 직후 --keep-on 이 없으면 바로 끄는 요청(onlineStatus 0)을 보낸다(그 몇 초는 켜져 있었다).')
    head = '계획: %s — %s' % (action, '캠페인 %s' % plan['campaignId'] if plan.get('campaignId') else '새 캠페인')
    print(head)
    if source:
        print(SOURCE_LABEL[source])
    print()
    print('%-26s %-44s %s' % ('필드', '현재', '변경후'))
    for row in plan['rows']:
        # 값은 자르지 않는다: 이름·국가·키워드 목록이 잘리면 무엇이 바뀌는지 확인할 수 없다.
        print('%-26s %s' % (row['field'], row['current'] if row['current'] is not None else '—'))
        print('%-26s %s' % ('', '→ ' + str(row['after']) if row['after'] is not None else '→ —'))
    print()
    print('보낼 요청 (토큰 제외):')
    print(json.dumps(plan_requests(plan), ensure_ascii=False, indent=1))
    print('보낼 뒤 읽을 곳 (리드백):')
    print(json.dumps(plan_reads(plan), ensure_ascii=False, indent=1))
    print()
    for text in cost_lines(action, plan['before'], plan['after']):
        print(text)
    print()
    blocked = send_block_reason(plan)
    if blocked:
        print('--yes 불가: ' + blocked)
    if dry_run:
        print('dry-run: 아무것도 보내지 않음. 실제로 보내려면 --yes.')
        return 0
    if blocked:
        raise Unverified(blocked)
    path = write_backup(account, plan, plan_requests(plan))
    print('백업: %s' % path)
    print()
    return execute(session, plan)


def send_block_reason(plan):
    """Why --yes must not send this plan (None = may send). 화면 캡처가 있는 형상만 보내고,
    되돌릴 수 없는 변경은 거부한다(추측 본문이 다른 것까지 바꿀 수 있다)."""
    action = plan['action']
    if action not in VERIFIED:
        return '%s: 요청 형식 미검증(화면 캡처 없음) — 계획만' % action
    if action == 'campaign.create':
        missing = [] if plan['opts'].start_billing else ['--start-billing(생성은 즉시 과금)']
        missing += [] if plan['create'].get('products') else ['--products']
        missing += [] if plan['create'].get('keywords') else ['--keywords']
        if missing:
            return '캠페인 생성은 %s 없이는 보내지 않는다' % ', '.join(missing)
        return None
    ctype = plan.get('campaignType')
    if ctype is None or action not in CAPTURED_TYPES.get(ctype, ()):
        return '캠페인 유형 %s(%s): %s 요청은 이 유형 화면에서 캡처되지 않음 — 계획만' % (
            ctype, TYPE_LABEL.get(ctype, '?'), action)
    if action == 'campaign.countries':
        if plan['opts'].countries == 'all':
            return '전체 국가로 되돌리는 요청 형식 미검증(캡처는 국가 목록만)'
        if plan['before'].get('regions') is None:
            return '지금 전체 국가: 목록으로 줄이면 전체 국가로 되돌릴 요청 형식이 없어 restore 불가'
    return None


def stop(request, error, what='요청'):
    print('실패: %s 거부 — %s' % (what, error))
    return 1


def execute(session, plan):
    action, cid, opts = plan['action'], plan.get('campaignId'), plan['opts']
    if action == 'campaign.create':
        return execute_create(session, plan)
    if action in ('campaign.budget', 'campaign.bid'):
        for request in plan_requests(plan):
            data = request['data']
            response = send(session, request['url'].split('www2.alibaba.com')[1], data)
            value = parsed(response)
            error = write_failure(response, value)
            if error:
                return stop(request, error)
        return readback_campaign(session, plan)
    for request in plan_requests(plan):
        path = request['url'].split('www2.alibaba.com')[1]
        response = send(session, path, request['data'], cid=cid)
        value = parsed(response)
        error = write_failure(response, value)
        if action == 'product.add' and path.endswith('/can/create'):
            inner = get_in(value, 'data', 'status')
            if error is None and inner not in (None, 1000, '1000'):
                error = '사전확인 can/create status %s (1001=다른 캠페인과 중복, 1002=추가 불가)' % inner
            if error:
                return stop(request, error, '사전확인')
            continue
        if error:
            return stop(request, error)
    if action.startswith('keyword.'):
        return readback_keywords(session, plan)
    if action.startswith('product.'):
        return readback_products(session, plan)
    return readback_campaign(session, plan)


def readback_campaign(session, plan, tries=3):
    cid, expected = plan['campaignId'], dict(plan['after'])
    view, diff = {}, {}
    for attempt in range(tries):
        view = campaign_view(read_campaign(session, cid))
        diff = {k: {'기대': v, '실제': view.get(k)} for k, v in expected.items() if not equal(k, v, view.get(k))}
        if not diff or attempt == tries - 1:
            break
        time.sleep(0.5)
    print('리드백 (/api/campaign/%d):' % cid)
    print(json.dumps(view, ensure_ascii=False, indent=1))
    if diff:
        print('리드백 불일치: ' + json.dumps(diff, ensure_ascii=False))
        return 3
    print('리드백 일치: 변경 반영 확인')
    return 0


def readback_products(session, plan):
    cid = plan['campaignId']
    groups, count = read_adgroups(session, cid), adgroup_count(session, cid)
    by_id = {g.get('id'): g for g in groups}
    print('리드백 (/api/campaign/%d/adgroup/batch):' % cid)
    if plan['action'] in ('product.remove', 'product.pause', 'product.resume'):
        now = [{'id': g.get('id'), 'onlineStatus': g.get('onlineStatus')} for g in groups
               if g.get('id') in set(plan['adgroups'])]
    else:
        now = [{'productId': g.get('productId')} for g in groups]
    print(json.dumps({'adgroups': now, 'adgroupCount': count}, ensure_ascii=False, indent=1))
    expected = plan['after']['products'] if plan['action'] == 'product.add' else plan['expected_adgroups']
    if plan['action'] == 'product.add':
        have = [g.get('productId') for g in groups]
        if sorted(map(str, have)) != sorted(map(str, expected)):
            print('리드백 불일치: ' + json.dumps({'상품': {'기대': expected, '실제': have}}, ensure_ascii=False))
            return 3
    else:
        diff = {}
        for want in expected:
            got = by_id.get(want['id'])
            if got is None or int(got.get('onlineStatus', -1)) != int(want['onlineStatus']):
                diff[str(want['id'])] = {'기대': want['onlineStatus'],
                                         '실제': None if got is None else got.get('onlineStatus')}
        if plan['action'] == 'product.remove':
            extra = [i for i in plan['adgroups'] if i in by_id]
            if extra:
                diff['남아 있음'] = extra
        if diff:
            print('리드백 불일치: ' + json.dumps(diff, ensure_ascii=False))
            return 3
    print('리드백 일치: 변경 반영 확인')
    return 0


def readback_keywords(session, plan):
    cid = plan['campaignId']
    rows = read_keywords(session, cid)
    view = {k['word']: {'id': k.get('id'), 'price': k.get('price'), 'onlineStatus': k.get('onlineStatus')}
            for k in rows if isinstance(k, dict) and k.get('word')}
    print('리드백 (/api/campaign/%d/bidword/query):' % cid)
    print(json.dumps(view, ensure_ascii=False, indent=1))
    action, diff = plan['action'], {}
    if action == 'keyword.add':
        for k in plan['keywords']:
            got = view.get(k['word'])
            if got is None or not equal('price', k['price'], got.get('price')):
                diff[k['word']] = {'기대': k['price'], '실제': None if got is None else got.get('price')}
    elif action in ('keyword.remove', 'keyword.pause', 'keyword.resume', 'keyword.bid'):
        want = {(k.get('id'), k.get('word')) for k in plan['keywords']}
        for kid, word in want:
            got = view.get(word)
            if action == 'keyword.remove':
                if got is not None:
                    diff[word] = {'기대': '없음', '실제': got}
                continue
            if got is None:
                diff[word] = {'기대': '있음', '실제': '없음'}
                continue
            if action == 'keyword.bid':
                price = next(k['price'] for k in plan['keywords'] if k['word'] == word)
                if not equal('price', price, got.get('price')):
                    diff[word] = {'기대': price, '실제': got.get('price')}
            else:
                status = 1 if action == 'keyword.resume' else 0
                if not equal('onlineStatus', status, got.get('onlineStatus')):
                    diff[word] = {'기대': status, '실제': got.get('onlineStatus')}
    if diff:
        print('리드백 불일치: ' + json.dumps(diff, ensure_ascii=False))
        return 3
    print('리드백 일치: 변경 반영 확인')
    return 0


def new_campaign_id(session, before_ids):
    """생성 뒤 리드백: 목록에서 새로 생긴 수동 캠페인 id 를 고른다."""
    data = read_data(session, '/api/campaign/type/allList', {'paging': False, 'typeList': [2]}, '캠페인 목록')
    rows = []
    if isinstance(data, dict):
        for value in data.values():
            rows.extend(value if isinstance(value, list) else [])
    elif isinstance(data, list):
        rows = data
    fresh = [r.get('id') or r.get('campaignId') for r in rows
             if isinstance(r, dict) and (r.get('id') or r.get('campaignId')) not in before_ids]
    return fresh[0] if fresh else None


def execute_create(session, plan):
    for request in plan_requests(plan)[:-1]:
        path = request['url'].split('www2.alibaba.com')[1]
        response = send(session, path, request['data'], product_line=MANUAL_PRODUCT_LINE)
        value = parsed(response)
        error = write_failure(response, value)
        inner = get_in(value, 'data', 'status') if isinstance(get_in(value, 'data'), dict) else None
        if error is None and inner not in (None, 1000, '1000'):
            error = '사전확인 can/create status %s' % inner
        if error:
            return stop(request, error, '사전확인')
    request = plan_requests(plan)[-1]
    response = send(session, request['url'].split('www2.alibaba.com')[1], request['data'],
                    product_line=MANUAL_PRODUCT_LINE)
    value = parsed(response)
    cid = get_in(value, 'data', 'id') or get_in(value, 'data', 'campaignId')
    if not isinstance(cid, int):
        cid = new_campaign_id(session, plan['before'].get('campaignIds') or [])
    error = write_failure(response, value)
    print('생성 응답: %s' % (short(value) or 'JSON 아님'))
    if error:
        return stop(request, error)
    if cid is None:
        print('생성 응답에 캠페인 id 가 없다 — 목록에서도 새 캠페인을 못 찾음. 지금 상태 확인 필요.')
        return 3
    plan['campaignId'] = cid
    print('새 캠페인 id: %s — 생성 즉시 켜졌다(과금 시작).' % cid)
    if not plan['opts'].keep_on:
        off = send(session, CAMPAIGN_UPDATE, {'id': cid, 'onlineStatus': 0, 'productLineId': PRODUCT_LINE})
        error = write_failure(off, parsed(off))
        if error:
            print('끄기 실패: %s — 생성된 캠페인이 켜진 채 남아 있다. 화면에서 확인.' % error)
            return 1
        print('끄기 요청 전송: onlineStatus 0 (생성과 끄기 사이 몇 초는 켜져 있었다).')
    view, diff = {}, {}
    for attempt in range(3):
        view = campaign_view(read_campaign(session, cid))
        expected = {'title': plan['after']['title']}
        if not plan['opts'].keep_on:
            expected['onlineStatus'] = 0
        diff = {k: {'기대': v, '실제': view.get(k)} for k, v in expected.items() if not equal(k, v, view.get(k))}
        if not diff or attempt == 2:
            break
        time.sleep(0.5)
    print('리드백 (/api/campaign/%d):' % cid)
    print(json.dumps(view, ensure_ascii=False, indent=1))
    if diff:
        print('리드백 불일치: ' + json.dumps(diff, ensure_ascii=False))
        return 3
    print('리드백 일치: 생성·%s 확인' % ('유지(켜짐)' if plan['opts'].keep_on else '끄기'))
    return 0


# ---- CLI --------------------------------------------------------------------------------------

def sub_parser(parent, name, **kwargs):
    """Leaf subcommand: inherits the global options so `--account` works before or after the command."""
    return parent.add_parser(name, parents=[GLOBAL], **kwargs)


GLOBAL = argparse.ArgumentParser(add_help=False, conflict_handler='resolve')


def build_parser():
    """Global options live on GLOBAL; every command parser inherits them, so order does not matter."""
    if not GLOBAL._actions:
        GLOBAL.add_argument('--account')
        GLOBAL.add_argument('--config')
        GLOBAL.add_argument('--format', choices=('table', 'json'), default='table')
    ap = argparse.ArgumentParser(prog='alibaba-ads', description=__doc__.splitlines()[0], parents=[GLOBAL])
    sub = ap.add_subparsers(dest='group')

    def add(name, help_text):
        # 그룹 파서는 부모(dest group) 파서에서 갈라지므로 --account 를 다시 받지 않는다.
        return sub.add_parser(name, help=help_text).add_subparsers(dest='sub')

    campaign = add('campaign', '캠페인 설정 변경(이름·상태·예산·입찰가·주간예산·시간대·국가)')
    cset = sub_parser(campaign, 'set')
    cset.add_argument('campaignId')
    cset.add_argument('--name')
    cset.add_argument('--status', choices=('on', 'off'))
    cset.add_argument('--budget', help='일 예산(최소 $20)')
    cset.add_argument('--bid', help='캠페인 입찰가 maxPrice(최소 0.45)')
    cset.add_argument('--week-budget', choices=('on', 'off'), dest='week_budget')
    cset.add_argument('--hours', help='노출 시간대 "0,1,…,23" (빈 값이면 24시간)', dest='hours_list')
    cset.add_argument('--countries')

    ccreate = sub_parser(campaign, 'create', help='수동 캠페인 생성(즉시 켜짐·과금 시작)')
    ccreate.add_argument('--name')
    ccreate.add_argument('--budget', help='일 예산(최소 $20)')
    ccreate.add_argument('--bid', help='캠페인 입찰가 maxPrice(최소 0.45)')
    ccreate.add_argument('--products', nargs='+', help='상품 id')
    ccreate.add_argument('--keywords', nargs='+', help='키워드 단어')
    ccreate.add_argument('--keyword-price', help='키워드에 넣을 입찰가(기본 1.30)')
    ccreate.add_argument('--hours', help='노출 시간대 "0,1,…,23" (기본 24시간)')
    ccreate.add_argument('--week-budget', choices=('on', 'off'), dest='week_budget')
    ccreate.add_argument('--start-billing', action='store_true', dest='start_billing',
                         help='즉시 과금을 승인한다(--yes 와 함께여야 전송)')
    ccreate.add_argument('--keep-on', action='store_true', dest='keep_on',
                         help='생성 후 끄지 않는다(기본은 바로 끄기)')

    products = add('products', '캠페인 상품(광고그룹) 추가·삭제·중단·재개')
    for name in ('add', 'remove', 'pause', 'resume'):
        p = sub_parser(products, name)
        p.add_argument('campaignId')
        p.add_argument('products', nargs='+')

    keywords = add('keywords', '키워드 추가·삭제·중단·재개·입찰가(단어로 받고 id 는 목록에서 찾는다)')
    for name in ('add', 'remove', 'pause', 'resume', 'bid'):
        p = sub_parser(keywords, name)
        p.add_argument('campaignId')
        p.add_argument('items', nargs='+')
        if name == 'bid':
            p.add_argument('--price')
        if name == 'add':
            p.add_argument('--price', help='키워드 입찰가(하나면 모두, 여러 개면 단어 수만큼)')

    restore_p = sub_parser(sub, 'restore', help='백업 파일의 원값으로 되돌림')
    restore_p.add_argument('file')
    sub_parser(sub, 'backups', help='백업 목록')
    return ap


def positive_int(value, what):
    if not re.fullmatch(r'[1-9][0-9]*', str(value)):
        raise Usage('%s 는 1 이상 정수: %r' % (what, value))
    return int(value)


MONEY = re.compile(r'[0-9]+(\.[0-9]+)?')


def money(value, what):
    if value is None or not MONEY.fullmatch(str(value)) or float(value) <= 0:
        raise Usage('%s 는 0보다 큰 숫자: %r' % (what, value))
    return float(value)


def hours_option(text):
    if text is None:
        return None
    parts = [p.strip() for p in str(text).split(',') if p.strip()]
    if not parts or any(not p.isdigit() or not 0 <= int(p) <= 23 for p in parts):
        raise Usage('--hours 는 0~23 정수 목록: %r' % text)
    values = [int(p) for p in parts]
    if len(values) != len(set(values)):
        raise Usage('--hours 에 중복: %s' % text)
    return sorted(values)


def campaign_opts(args):
    given = [k for k in ('name', 'status', 'budget', 'bid', 'week_budget', 'hours_list', 'countries')
             if getattr(args, k, None) is not None]
    if not given:
        raise Usage('campaign set: --name/--status/--budget/--bid/--week-budget/--hours/--countries 중 하나 필요')
    # 예산과 입찰가는 화면이 한 창·한 요청으로 보낸다(m12) — 둘만 같이 받는다.
    if len(given) > 1 and set(given) != {'budget', 'bid'}:
        raise Usage('campaign set: 한 번에 한 필드만(예산+입찰가만 함께): 받은 %s'
                    % ', '.join('--' + k.replace('_', '-') for k in given))
    args.campaignId = positive_int(args.campaignId, '캠페인ID')
    if args.status is not None:
        return 'campaign.status'
    if args.budget is not None and args.bid is not None:
        money(args.budget, '--budget')
        money(args.bid, '--bid')
        # 한 요청에 둘 다 싣는다(price_update) — 입찰가를 올리면 비용 증가로 본다.
        return 'campaign.bid'
    if args.budget is not None:
        money(args.budget, '--budget')
        return 'campaign.budget'
    if args.bid is not None:
        money(args.bid, '--bid')
        return 'campaign.bid'
    if args.week_budget is not None:
        return 'campaign.weekBudget'
    if args.hours_list is not None:
        args.hours = hours_option(args.hours_list)
        return 'campaign.hours'
    if args.countries is not None:
        if args.countries != 'all':
            codes = [c.strip().upper() for c in args.countries.split(',') if c.strip()]
            if not codes or any(not re.fullmatch(r'[A-Z]{2,6}', c) for c in codes):
                raise Usage('--countries 는 all 또는 CA,US,… 국가코드: %r' % args.countries)
            if len(codes) != len(set(codes)):
                raise Usage('--countries 에 중복 코드: %s' % args.countries)
            args.countries = ','.join(codes)
        return 'campaign.countries'
    if not str(args.name).strip():
        raise Usage('--name 이 비었다')
    return 'campaign.rename'


def campaign_plan(args, session, action=None):
    action = action or campaign_opts(args)
    args.campaignId = positive_int(args.campaignId, '캠페인ID')
    raw = read_campaign(session, args.campaignId)
    view = campaign_view(raw)
    plan = dict(action=action, campaignId=args.campaignId, opts=args, before=campaign_before(action, view),
                after=campaign_after(action, args), rows=campaign_rows(action, view, args), adgroups=None,
                campaignType=view.get('type'), view=view)
    if action in ('campaign.budget', 'campaign.bid'):
        plan['target'] = target_prices(plan)
        now = view.get('bid') if action == 'campaign.bid' else view.get('budget')
        want = plan['target']['bid'] if action == 'campaign.bid' else plan['target']['budget']
        plan['modify'] = as_float(now) is not None and as_float(want) < as_float(now)
    return plan


def keyword_prices(args, words):
    if args.price is None:
        return [1.3] * len(words)
    prices = [money(p, '--price') for p in str(args.price).split(',') if p.strip()]
    if len(prices) == 1:
        return prices * len(words)
    if len(prices) != len(words):
        raise Usage('--price 개수(%d)가 키워드 수(%d)와 다름' % (len(prices), len(words)))
    return prices


def keyword_plan(args, session):
    sub = args.sub
    action = {'add': 'keyword.add', 'remove': 'keyword.remove', 'pause': 'keyword.pause',
              'resume': 'keyword.resume', 'bid': 'keyword.bid'}[sub]
    cid = positive_int(args.campaignId, '캠페인ID')
    words = [w.strip() for w in args.items if w.strip()]
    if not words:
        raise Usage('키워드 단어가 비었다')
    if sub == 'bid':
        if args.price is None:
            raise Usage('keywords bid: --price 필요(단어 수만큼 쉼표로 또는 하나면 전부)')
    have = read_keywords(session, cid)
    ctype = campaign_view(read_campaign(session, cid)).get('type')
    by_word = {}
    for row in have:
        if isinstance(row, dict) and row.get('word'):
            by_word.setdefault(row['word'], []).append(row)
    if action == 'keyword.add':
        prices = keyword_prices(args, words)
        if any(w in by_word for w in words):
            raise Usage('이 캠페인에 이미 있는 키워드: %s' % ', '.join(w for w in words if w in by_word))
        items = [dict(word=w, price=p, source=CREATE_KEYWORD_SOURCE) for w, p in zip(words, prices)]
        after = [{'word': k['word'], 'price': k['price']} for k in items]
        rows = [dict(field='추가할 키워드(단어, 입찰가)', current=None, after=after),
                dict(field='지금 키워드', current=sorted(by_word), after=None)]
        return dict(action=action, campaignId=cid, opts=args, before={}, after={'keywords': after}, rows=rows,
                    keywords=items, campaignType=ctype)
    missing = [w for w in words if w not in by_word]
    if missing:
        raise Usage('이 캠페인 키워드 목록에 없음: %s (있는 키워드: %s)' % (
            ', '.join(missing), ', '.join(sorted(by_word)) or '없음'))
    chosen = [row for w in words for row in by_word[w]]
    if action == 'keyword.bid':
        prices = keyword_prices(args, words)
        price_by_word = dict(zip(words, prices))
        items = [{'id': row.get('id'), 'word': row['word'], 'price': price_by_word[row['word']]} for row in chosen]
        before = [{'word': w, 'price': by_word[w][0].get('price')} for w in words]
        after = [{'word': k['word'], 'price': k['price']} for k in items]
        rows = [dict(field='키워드 입찰가(현재)', current=before, after=after)]
    elif action == 'keyword.remove':
        items = [{'id': row.get('id'), 'word': row['word'], 'price': row.get('price')} for row in chosen]
        rows = [dict(field='삭제할 키워드(id, 단어)', current=None,
                     after=[{'id': k['id'], 'word': k['word']} for k in items]),
                dict(field='삭제 뒤 남는 키워드', current=sorted(by_word), after=sorted(w for w in by_word if w not in words))]
    else:
        items = [{'id': row.get('id'), 'word': row['word'], 'price': row.get('price')} for row in chosen]
        status = 1 if action == 'keyword.resume' else 0
        rows = [dict(field='키워드(id, 단어)', current=[k['word'] for k in items], after=None),
                dict(field='상태 onlineStatus', current=[k.get('onlineStatus') for k in chosen], after=[status] * len(items))]
    if action == 'keyword.remove':
        before = {}
    elif action == 'keyword.bid':
        before = {'price': before[0]['price'] if len(before) == 1 else None}
    else:
        before = {'onlineStatus': chosen[0].get('onlineStatus') if chosen else None}
    after = {'keywords': [k['word'] for k in items]}
    if action == 'keyword.remove':
        # restore 가 원래 입찰가로 되살리도록 지금 값을 백업에 남긴다(원값 = 지금 값).
        # 백업 before 는 각 원소가 바로 이 items 여야 한다 — restore 가 raw['keywords'] 의
        # word·price 를 그대로 읽는다(단어 문자열만 남기면 입찰가를 잃는다).
        after['keywords'] = items
    if action == 'keyword.bid':
        after['price'] = items[0]['price'] if len(items) == 1 else None
    return dict(action=action, campaignId=cid, opts=args, before=before, after=after, rows=rows,
                keywords=items, campaignType=ctype)


def product_plan(args, session):
    action = {'add': 'product.add', 'remove': 'product.remove', 'pause': 'product.pause',
              'resume': 'product.resume'}[args.sub]
    cid = positive_int(args.campaignId, '캠페인ID')
    products = [positive_int(p, '상품ID') for p in args.products]
    args.products = products
    groups = read_adgroups(session, cid)
    ctype = campaign_view(read_campaign(session, cid)).get('type')
    have = [g.get('productId') for g in groups]
    by_product = {}
    for group in groups:
        by_product.setdefault(group.get('productId'), []).append(group.get('id'))
    by_id = {g.get('id'): g for g in groups}
    if action == 'product.add':
        if any(p in have for p in products):
            raise Usage('이 캠페인에 이미 있는 상품: %s' % ', '.join(str(p) for p in products if p in have))
        after = have + products
        return dict(action=action, campaignId=cid, opts=args, before={'products': have},
                    after={'products': after}, rows=[dict(field='상품(광고그룹)', current=have, after=after)],
                    adgroups=None, campaignType=ctype)
    missing = [p for p in products if p not in by_product]
    if missing:
        raise Usage('이 캠페인 광고그룹에 없는 상품: %s (있는 상품: %s)' % (
            ', '.join(map(str, missing)), ', '.join(map(str, by_product)) or '없음'))
    pairs = [(i, p) for p in products for i in by_product[p]]
    ids = [i for i, _ in pairs]
    if action == 'product.remove':
        after = [p for p in have if p not in products]
        rows = [dict(field='상품(광고그룹)', current=have, after=after),
                dict(field='삭제할 광고그룹 id', current=None, after=ids)]
        return dict(action=action, campaignId=cid, opts=args, before={'products': have},
                    after={'products': after}, rows=rows, adgroups=ids, pairs=pairs, campaignType=ctype,
                    expected_adgroups=[])
    status = 1 if action == 'product.resume' else 0
    current = [(i, by_id[i].get('onlineStatus')) for i in ids]
    rows = [dict(field='상품(광고그룹)', current=have, after=None),
            dict(field='대상 광고그룹(상태)', current=current, after=[(i, status) for i in ids])]
    return dict(action=action, campaignId=cid, opts=args,
                before={'products': have, 'onlineStatus': [c[1] for c in current]},
                after={'onlineStatus': [status] * len(ids)}, rows=rows, adgroups=ids, pairs=pairs,
                campaignType=ctype, expected_adgroups=[{'id': i, 'onlineStatus': status} for i in ids])


def create_plan(args):
    action = 'campaign.create'
    if not args.name or not args.name.strip():
        raise Usage('campaign create: --name 필요')
    args.name = args.name.strip()
    name = args.name
    budget = money(args.budget if args.budget is not None else '20', '--budget')
    bid = args.bid if args.bid is not None else '0.5'
    money(bid, '--bid')
    products = [positive_int(p, '상품ID') for p in (args.products or [])]
    words = [w.strip() for w in (args.keywords or []) if w.strip()]
    prices = keyword_prices(argparse.Namespace(price=args.keyword_price), words) if words else []
    hours = hours_option(args.hours) if args.hours is not None else list(DEFAULT_HOURS)
    week = args.week_budget or 'on'
    data = {'title': name, 'type': 2, 'subType': None, 'budget': budget, 'weekBudgetStatus': week,
            'bidType': 2, 'maxPrice': str(bid), 'syncMultipleSiteConf': CREATE_SITES,
            'syncPromotionRegionConf': 'QTQT', 'syncPromotionHourConf': ','.join(str(h) for h in hours),
            'smartTitle': 'on', 'smartPicture': 'on',
            'properties': {'createMode': 'async',
                           'adgroupBatchOperation': {'adGroupOperationList': [
                               {'productId': p, 'onlineStatus': 1} for p in products]},
                           'keywordBatchOperation': {'keywordList': [
                               {'word': w, 'price': p, 'source': CREATE_KEYWORD_SOURCE} for w, p in zip(words, prices)]}}}
    rows = [dict(field='이름 title', current=None, after=name),
            dict(field='유형 type', current=None, after='2 (수동, productLineId %d)' % MANUAL_PRODUCT_LINE),
            dict(field='일 예산 budget', current=None, after=budget),
            dict(field='입찰가 maxPrice / bidType', current=None, after='%s / 2' % bid),
            dict(field='주간 예산 weekBudgetStatus', current=None, after=week),
            dict(field='시간대', current=None, after='%d시간' % len(hours)),
            dict(field='상품 productId', current=None, after=products),
            dict(field='키워드(단어, 입찰가)', current=None, after=[(w, p) for w, p in zip(words, prices)]),
            dict(field='생성 직후', current=None, after='유지(켜짐)' if args.keep_on else '끄기 요청 전송')]
    return dict(action=action, campaignId=None, opts=args, before={}, after={'title': name}, rows=rows,
                create={'data': data, 'products': products, 'keywords': words}, campaignType=2)


# ---- restore -----------------------------------------------------------------------------------

def restore_plan(args, session):
    """Plan that brings back the values a backup recorded: same rules (dry-run, --yes, backup first)."""
    try:
        record = json.loads(Path(args.file).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise Usage('백업 파일을 읽을 수 없음: %s (%s)' % (args.file, error))
    if not isinstance(record, dict) or not isinstance(record.get('before'), dict) or not record.get('action'):
        raise Usage('백업 형식 아님(account/campaignId/action/before 필요): %s' % args.file)
    action, raw = record['action'], record['before']
    if action not in VERIFIED:
        raise Unverified('백업 조작 %s 는 되돌릴 요청 형식이 없다: restore 미지원' % action)
    opts = argparse.Namespace(campaignId=record.get('campaignId'), name=None, status=None, budget=None,
                              bid=None, week_budget=None, hours=None, countries=None, products=None,
                              keyword_price=None, hours_list=None, items=None, price=None)
    cid = None if record.get('campaignId') is None else positive_int(record.get('campaignId'), '백업 campaignId')
    if action == 'campaign.create':
        if cid is None:
            raise Usage('백업에 campaignId 없음(생성 응답에서 id 를 못 얻은 백업은 restore 불가)')
        opts.status = 'off'
        action = 'campaign.status'
        target = campaign.status
        rows = [dict(field='생성 원복(끄기)', current=cid, after='onlineStatus 0 (삭제 요청 형식 미캡처: 끄기까지만)')]
        view = campaign_view(read_campaign(session, cid))
        print('복원 대상: campaign.create → 끄기 / 캠페인 %s / 백업 %s (%s)' % (cid, args.file, record.get('createdAt')))
        return dict(action='campaign.status', campaignId=cid, opts=opts, before=campaign_before('campaign.status', view),
                    after=campaign_after('campaign.status', opts), rows=rows, adgroups=None,
                    campaignType=view.get('type'), view=view)
    if cid is None:
        raise Usage('백업에 campaignId 없음')
    view = campaign_view(read_campaign(session, cid))
    if action == 'campaign.rename':
        opts.name = raw.get('title')
        if not isinstance(opts.name, str) or not opts.name.strip():
            raise Usage('백업에 title 없음')
        plan = campaign_plan(argparse.Namespace(campaignId=str(cid), name=opts.name, status=None, budget=None,
                                                bid=None, week_budget=None, hours=None, countries=None),
                             session, action=action)
    elif action in ('campaign.status', 'campaign.budget', 'campaign.bid', 'campaign.weekBudget',
                    'campaign.hours', 'campaign.countries'):
        if action == 'campaign.status':
            opts.status = 'on' if raw.get('onlineStatus') in (1, '1') else 'off'
        elif action == 'campaign.budget':
            opts.budget = str(raw.get('budget'))
            money(opts.budget, '백업 budget')
        elif action == 'campaign.bid':
            opts.bid = str(raw.get('bid') if raw.get('bid') is not None else raw.get('maxPrice'))
            money(opts.bid, '백업 maxPrice')
        elif action == 'campaign.weekBudget':
            opts.week_budget = 'on' if raw.get('weekBudget') == 'on' else 'off'
        elif action == 'campaign.hours':
            opts.hours = hours_option(raw.get('hours') or ','.join(str(h) for h in DEFAULT_HOURS))
        else:
            regions = raw.get('regions')
            opts.countries = 'all' if regions in (None, '') else ','.join(str(regions).split(','))
        plan = campaign_plan(argparse.Namespace(campaignId=str(cid), name=None, status=opts.status,
                                                budget=opts.budget, bid=opts.bid, week_budget=opts.week_budget,
                                                hours=None if opts.hours is None else ','.join(map(str, opts.hours)),
                                                countries=opts.countries), session, action=action)
        plan['rows'] = [dict(field='원값 복원', current=plan['before'], after=plan['after'])]
    elif action == 'product.add':
        groups = read_adgroups(session, cid)
        by_product = {}
        for group in groups:
            by_product.setdefault(group.get('productId'), []).append(group.get('id'))
        pairs = [(i, p) for p in raw.get('products') or [] for i in by_product.get(p, [])]
        if not pairs:
            raise Unverified('원복 대상 상품이 지금 캠페인에 없음(이미 삭제됨): restore 불가 — 상품을 다시 추가하세요')
        opts.products = [p for _, p in pairs]
        return dict(action='product.remove', campaignId=cid, opts=opts, before={'products': list(by_product)},
                    after={'products': [p for p in by_product if p not in opts.products]},
                    rows=[dict(field='추가했던 상품 삭제', current=None, after=[{'id': i, 'productId': p} for i, p in pairs])],
                    adgroups=[i for i, _ in pairs], pairs=pairs, campaignType=view.get('type'), expected_adgroups=[])
    elif action == 'product.remove':
        # product.remove 원복 = 지운 상품을 다시 추가(adgroup/create).
        products = [p for p in (raw.get('products') or []) if p] \
            or [p for p, _ in (raw.get('pairs') or []) if p]
        if not products:
            raise Usage('백업에 products 없음(지운 상품을 알 수 없다)')
        opts.products = [positive_int(p, '상품ID') for p in products]
        opts.items = None
        have = [g.get('productId') for g in read_adgroups(session, cid)]
        return dict(action='product.add', campaignId=cid, opts=opts, before={'products': have},
                    after={'products': have + opts.products},
                    rows=[dict(field='삭제했던 상품 다시 추가', current=have, after=have + opts.products)],
                    adgroups=None, campaignType=view.get('type'), products=opts.products)
    elif action in ('product.pause', 'product.resume'):
        ids = raw.get('adgroups') or []
        if not ids:
            raise Usage('백업에 adgroups 없음')
        opts.products = []
        status = 1 if action == 'product.pause' else 0   # pause 원복 = 재개
        return dict(action='product.resume' if status == 1 else 'product.pause', campaignId=cid, opts=opts,
                    before={'onlineStatus': [status] * len(ids)}, after={'onlineStatus': [status] * len(ids)},
                    rows=[dict(field='광고그룹 상태 원복', current=None, after=[{'id': i, 'onlineStatus': status} for i in ids])],
                    adgroups=ids, campaignType=view.get('type'),
                    expected_adgroups=[{'id': i, 'onlineStatus': status} for i in ids])
    elif action == 'keyword.add':
        # keyword.add 원복 = 추가했던 단어를 삭제. 백업에 id 가 있으면 그대로 쓰고, 없으면 단어로 다시 찾는다.
        items = [k for k in (raw.get('keywords') or []) if k.get('word')]
        words = [k['word'] for k in items]
        have = {r.get('word'): r for r in read_keywords(session, cid) if isinstance(r, dict)}
        ids = [k['id'] if k.get('id') is not None else (have.get(k['word']) or {}).get('id') for k in items]
        chosen = [{'id': i, 'word': w} for i, w in zip(ids, words) if i is not None]
        if not chosen:
            raise Unverified('추가했던 키워드가 지금 없다(이미 삭제됨): restore 불가')
        opts.items = [c['word'] for c in chosen]
        return dict(action='keyword.remove', campaignId=cid, opts=opts, before={},
                    after={'keywords': opts.items},
                    rows=[dict(field='추가했던 키워드 삭제', current=None, after=opts.items)],
                    keywords=chosen, campaignType=view.get('type'), products=[])
    elif action == 'keyword.remove':
        items = raw.get('keywords') or []
        if not items:
            raise Usage('백업에 keywords 없음')
        opts.items = [k['word'] for k in items]
        opts.price = ','.join(str(k.get('price')) for k in items)
        prices = [money(k.get('price'), '백업 keyword price') for k in items]
        return dict(action='keyword.add', campaignId=cid, opts=opts, before={},
                    after={'keywords': [(k['word'], p) for k, p in zip(items, prices)]},
                    rows=[dict(field='삭제했던 키워드 다시 추가', current=None, after=[(k['word'], p) for k, p in zip(items, prices)])],
                    keywords=[{'word': k['word'], 'price': p} for k, p in zip(items, prices)],
                    campaignType=view.get('type'))
    elif action == 'keyword.bid':
        items = raw.get('keywords') or []
        price = raw.get('price') if raw.get('price') is not None else (items[0].get('price') if items else None)
        money(price, '백업 keyword price')
        opts.items = [k.get('word') for k in items]
        opts.price = str(price)
        have = {r.get('word'): r for r in read_keywords(session, cid) if isinstance(r, dict)}
        chosen = [{'id': have[k['word']].get('id'), 'word': k['word'], 'price': float(price)}
                  for k in items if k.get('word') in have]
        if not chosen:
            raise Unverified('입찰가를 되돌릴 키워드가 지금 없다: restore 불가')
        return dict(action='keyword.bid', campaignId=cid, opts=opts, before={'price': price}, after={'price': float(price)},
                    rows=[dict(field='키워드 입찰가 원복', current=None, after=[(c['word'], c['price']) for c in chosen])],
                    keywords=chosen, campaignType=view.get('type'))
    elif action in ('keyword.pause', 'keyword.resume'):
        items = raw.get('keywords') or []
        status = 1 if action == 'keyword.pause' else 0    # pause 원복 = resume
        target = 'keyword.resume' if status == 1 else 'keyword.pause'
        have = {r.get('word'): r for r in read_keywords(session, cid) if isinstance(r, dict)}
        chosen = [{'id': have[k['word']].get('id'), 'word': k['word']} for k in items if k.get('word') in have]
        if not chosen:
            raise Unverified('상태를 되돌릴 키워드가 지금 없다: restore 불가')
        opts.items = [c['word'] for c in chosen]
        return dict(action=target, campaignId=cid, opts=opts, before={}, after={},
                    rows=[dict(field='키워드 상태 원복', current=None, after=[(c['word'], status) for c in chosen])],
                    keywords=chosen, campaignType=view.get('type'))
    else:
        raise Unverified('%s 는 원복 요청을 만들 수 없다: restore 미지원' % action)
    print('복원 대상: %s / 캠페인 %s / 백업 %s (%s)' % (record['action'], cid, args.file, record.get('createdAt')))
    return plan


def show_backups(account, args):
    rows = list_backups(account)
    if args.format == 'json':
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print('백업 없음: %s' % backup_root(account))
        return 0
    print('%-19s %-60s %-18s %s' % ('시각', '파일', '조작', '캠페인'))
    for row in rows:
        print('%-19s %-60s %-18s %s' % (row.get('createdAt') or '?', row.get('file'), row.get('action') or '?',
                                        row.get('campaignId')))
    return 0


def run(argv, session=None, *, dry_run=True):
    """Parse and run one command. Global options may appear before or after the subcommand."""
    try:
        args = build_parser().parse_args(list(argv))
    except SystemExit as exit_:
        raise Usage('인자 해석 실패') if exit_.code else exit_
    if args.group is None:
        raise Usage('명령 필요: campaign set|create | products add|remove|pause|resume | '
                    'keywords add|remove|pause|resume|bid | restore | backups')
    if args.group in ('campaign', 'products', 'keywords') and getattr(args, 'sub', None) is None:
        raise Usage('%s: 하위 명령 필요' % args.group)
    if not args.account:
        raise Usage('--account 가 필요하다')
    if args.account in BLOCKED_ACCOUNTS:
        raise Blocked(args.account)
    # Reject a bad request shape before the account file or a network call is touched.
    if args.group == 'campaign' and args.sub == 'create':
        create_plan(args)
    elif args.group == 'campaign':
        args.campaignId = positive_int(args.campaignId, '캠페인ID')
        campaign_opts(args)
    elif args.group == 'products':
        args.campaignId = positive_int(args.campaignId, '캠페인ID')
        args.products = [positive_int(p, '상품ID') for p in args.products]
    elif args.group == 'keywords':
        args.campaignId = positive_int(args.campaignId, '캠페인ID')
        if args.sub == 'bid':
            money(args.price, '--price')
    if not args.config:
        # Same accounts file the reading CLI uses (lib/cli.mjs DEFAULT_CONFIG); --config overrides.
        args.config = DEFAULT_CONFIG
    account = load_account(args.config, args.account)
    if account['name'] in BLOCKED_ACCOUNTS:
        raise Blocked(account['name'])
    # Backups land beside the account config (~/.aliops-ops/seller-report by default).
    account['outDir'] = account.get('outDir') or os.path.dirname(os.path.abspath(args.config))
    if args.group == 'backups':
        return show_backups(account, args)
    session = session or ApiSession(account, args.config, refresh_saved=False)
    if args.group == 'restore':
        plan = restore_plan(args, session)
    elif args.group == 'products':
        plan = product_plan(args, session)
    elif args.group == 'keywords':
        plan = keyword_plan(args, session)
    elif args.sub == 'create':
        plan = create_plan(args)
    else:
        plan = campaign_plan(args, session)
    return show_plan_or_run(account, plan, session, dry_run=dry_run)


def cli(argv=None, session=None):
    """Entry point: --yes turns the dry-run into a real write; exceptions map to exit codes."""
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
    except SystemExit as exit_:
        return 0 if exit_.code in (None, 0) else 1
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


if __name__ == '__main__':
    sys.exit(cli())
