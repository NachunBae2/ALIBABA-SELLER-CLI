"""광고 캠페인의 이름·상태·예산·국가·상품을 바꿀 때. 기본 dry-run, --yes 로만 반영, 쓰기 전 자동 백업.

Contracts come from the 2026-11-18 screen captures (specs.json, reads-full.json):
every write is POST https://www2.alibaba.com<path>, form body
ads={"productLineId":<pl>}&type=normal&data=<JSON>&_csrf=<token>, success = status 1000.
The token comes from the same POST csrf bootstrap the ads reads use (api_session.py).

stdout: the report (before/after table, requests to send, cost warning, backup path).
stderr: progress. Exit: 0 ok/dry-run, 1 request failed, 2 unreachable/session expired,
3 read-back mismatch, 64 usage or request shape unverified (no screen capture), 77 refused account.
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
PRODUCT_LINE = 110106
BLOCKED_ACCOUNTS = ('wonnischoice', 'linea')
COST_RISE = '비용 증가 가능'
UNVERIFIED = '요청 형식 미검증(화면 캡처 없음: 수동 캠페인 0개)'
# The three actions a screen capture recorded (specs.json source=captured). The rest are app-code
# shapes: they still run, but say so; keyword ops refuse outright — the request bodies were never seen.
VERIFIED_ACTIONS = ('campaign.rename', 'campaign.countries', 'product.add')
CAMPAIGN_UPDATE = '/api/campaign/update'
CAMPAIGN_PATHS = {'campaign.rename': CAMPAIGN_UPDATE, 'campaign.status': CAMPAIGN_UPDATE,
                  'campaign.budget': CAMPAIGN_UPDATE, 'campaign.countries': '/api/campaign/update/batch'}
PRODUCT_PATHS = {'product.add': '/api/campaign/{id}/adgroup/create',
                 'product.remove': '/api/campaign/{id}/adgroup/delete'}
STATUS_LABEL = {1: '온라인(1)', 0: '꺼짐(0)', 9: '출시 종료(9)'}


class Usage(Exception):
    pass


class Unverified(Exception):
    pass


class Blocked(Exception):
    """Account refused for ad writes (exit 77)."""
    pass


# ---- transport -----------------------------------------------------------------------------------

def send(session, path, data, *, cid=None):
    """One request in the captured envelope; data is the `ads[...data]` JSON."""
    target = path.format(id=cid) if cid is not None else path
    params = {'ads': {'productLineId': PRODUCT_LINE}, 'type': 'normal', 'data': data}
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


def read_campaign(session, cid):
    response = send(session, '/api/campaign/{id}', {}, cid=cid)
    value = parsed(response)
    error = read_failure(response, value, '캠페인')
    if error:
        raise SessionError(error)
    data = value.get('data') if value else None
    if not isinstance(data, dict):
        raise SessionError('캠페인 읽기 실패: data 없음')
    return data


def read_adgroups(session, cid):
    response = send(session, '/api/campaign/{id}/adgroup/batch', {}, cid=cid)
    value = parsed(response)
    error = read_failure(response, value, '상품 목록')
    if error:
        raise SessionError(error)
    data = value.get('data') if value else None
    if not isinstance(data, list):
        raise SessionError('상품 목록 읽기 실패: data 배열 아님')
    return data


def adgroup_count(session, cid):
    response = send(session, '/api/campaign/adgroup/count', {'campaignIdList': [cid], 'adgroupOnlineStatus': 1})
    value = parsed(response)
    data = value.get('data') if value else None
    return data.get(str(cid)) if isinstance(data, dict) else None


# ---- current values ------------------------------------------------------------------------------

def get_in(value, *keys):
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def campaign_view(campaign):
    props = campaign.get('properties') or {}
    scope = get_in(props, 'operationScope', 'campaignScope')
    regions = scope.get('regions') if isinstance(scope, dict) else None
    return {'type': campaign.get('type'), 'title': campaign.get('title'), 'onlineStatus': campaign.get('onlineStatus'),
            'settleStatus': campaign.get('settleStatus'), 'budget': props.get('budget'), 'regions': regions}


def country_count(code):
    return 1 if code in (None, 'all') else len([c for c in str(code).split(',') if c])


def equal(field, expected, actual):
    if field == 'budget':
        try:
            return math.isclose(float(expected), float(actual), rel_tol=0, abs_tol=1e-9)
        except (TypeError, ValueError):
            return False
    if field == 'onlineStatus':
        try:
            return int(expected) == int(actual)
        except (TypeError, ValueError):
            return False
    return expected == actual


# ---- plans ---------------------------------------------------------------------------------------

def campaign_before(action, view):
    if action == 'campaign.rename':
        return {'title': view['title']}
    if action == 'campaign.status':
        return {'onlineStatus': view['onlineStatus'], 'settleStatus': view['settleStatus']}
    if action == 'campaign.budget':
        return {'budget': view['budget']}
    return {'regions': view['regions']}


def campaign_after(action, opts):
    if action == 'campaign.rename':
        return {'title': opts.name}
    if action == 'campaign.status':
        return {'onlineStatus': 1 if opts.status == 'on' else 0}
    if action == 'campaign.budget':
        return {'budget': float(opts.budget)}
    return {'regions': None if opts.countries == 'all' else opts.countries}


def campaign_rows(action, view, opts):
    if action == 'campaign.rename':
        return [dict(field='캠페인 이름 title', current=view['title'], after=opts.name)]
    if action == 'campaign.status':
        return [dict(field='상태 onlineStatus', current=STATUS_LABEL.get(view['onlineStatus'], view['onlineStatus']),
                     after=STATUS_LABEL[1 if opts.status == 'on' else 0]),
                dict(field='settleStatus', current=view['settleStatus'], after='변경 안 함(형식 미확인)')]
    if action == 'campaign.budget':
        return [dict(field='예산 budget', current=view['budget'], after=float(opts.budget))]
    return [dict(field='국가 regions', current='전체 국가' if view['regions'] is None else view['regions'],
                 after='전체 국가' if opts.countries == 'all' else opts.countries),
            dict(field='국가 수', current=country_count(view['regions']), after=country_count(opts.countries))]


def cost_lines(action, before, after):
    """Cost warning for both dry-run and real runs: the commands stay allowed, but say it."""
    if action == 'campaign.status':
        if after.get('onlineStatus') != 1:
            return ['비용 감소 방향: 캠페인을 끄면 광고비가 멈춤']
        if before.get('onlineStatus') in (1, '1'):
            return ['비용: 이미 온라인 — 변화 없음 예상']
        return [COST_RISE + ': 캠페인을 켜면 노출·클릭이 시작됨']
    if action == 'campaign.budget':
        try:
            up = float(after['budget']) > float(before.get('budget') or 0)
        except (TypeError, ValueError):
            up = True
        return [COST_RISE + ': 예산을 올리면 상한이 커짐'] if up else ['비용 감소 방향: 예산을 내림']
    if action == 'campaign.countries':
        now, want = country_count(before.get('regions')), country_count(after.get('regions'))
        if want > now:
            return [COST_RISE + ': 국가를 추가하면 노출 범위가 커짐 (%d → %d)' % (now, want)]
        if want < now:
            return ['비용 감소 방향: 국가를 줄임 (%d → %d)' % (now, want)]
        return ['비용: 국가 수 그대로 — 변화 없음 예상']
    if action == 'product.add':
        return [COST_RISE + ': 상품을 추가하면 광고 대상이 늘어남']
    if action == 'product.remove':
        return ['비용 감소 방향: 상품을 빼면 광고 대상이 줄어듦']
    return []


def req(path, data, cid=None):
    return {'method': 'POST', 'url': HOST + (path.format(id=cid) if cid else path),
            'contentType': 'application/x-www-form-urlencoded; charset=UTF-8',
            'ads': {'productLineId': PRODUCT_LINE}, 'type': 'normal', 'data': data}


def plan_requests(plan):
    action, cid, opts = plan['action'], plan['campaignId'], plan['opts']
    if action == 'product.add':
        return [req('/api/campaign/{id}/adgroup/can/create', {'adGroupOperationList': [{'productId': p} for p in opts.products]}, cid),
                req(PRODUCT_PATHS[action], {'adGroupOperationList': [{'productId': p, 'onlineStatus': 1} for p in opts.products]}, cid)]
    if action == 'product.remove':
        return [req(PRODUCT_PATHS[action], {'adGroupOperationList': [{'id': i} for i in plan['adgroups']]}, cid)]
    if action == 'campaign.rename':
        # captured 2026-11-18 (detail page pencil): {id, title}
        return [req(CAMPAIGN_PATHS[action], {'id': cid, 'title': opts.name})]
    if action == 'campaign.status':
        return [req(CAMPAIGN_PATHS[action], {'id': cid, 'onlineStatus': 1 if opts.status == 'on' else 0})]
    if action == 'campaign.budget':
        return [req(CAMPAIGN_PATHS[action], {'id': cid, 'budget': float(opts.budget)})]
    operation = {'id': cid}
    if opts.countries != 'all':
        operation['syncPromotionRegionConf'] = opts.countries
    return [req(CAMPAIGN_PATHS[action], {'campaignOperationList': [operation]})]


def plan_reads(plan):
    cid = plan['campaignId']
    if plan['action'] in PRODUCT_PATHS:
        return [req('/api/campaign/{id}/adgroup/batch', {}, cid),
                req('/api/campaign/adgroup/count', {'campaignIdList': [cid], 'adgroupOnlineStatus': 1})]
    return [req('/api/campaign/{id}', {}, cid)]


# ---- backups -------------------------------------------------------------------------------------

def backup_root(account):
    """Backups live where the report CLI already writes for this account."""
    base = Path(account['outDir']) if account.get('outDir') else Path.home() / '30_Assets' / account['name'] / 'seller-report'
    return base / 'ads-backups'


def write_backup(account, plan, requests, *, root=None):
    directory = Path(root) if root else backup_root(account)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%dT%H%M%S')
    stem = '%s-%s-%s' % (account['name'], plan['campaignId'], stamp)
    target, n = directory / (stem + '.json'), 1
    while target.exists():
        n += 1
        target = directory / ('%s-%d.json' % (stem, n))
    record = {'account': account['name'], 'campaignId': plan['campaignId'], 'action': plan['action'],
              'createdAt': time.strftime('%Y-%m-%dT%H:%M:%S'),
              'before': dict(plan['before'], **({'adgroups': plan['adgroups']} if plan.get('adgroups') else {})),
              'request': requests, 'note': 'restore <이 파일> 로 원값 복원(같은 규칙: 기본 dry-run, --yes).'}
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


# ---- run -----------------------------------------------------------------------------------------

def show_plan_or_run(account, plan, session, *, dry_run):
    """Print the before/after table + request, then write only with --yes."""
    verified = plan['action'] in VERIFIED_ACTIONS
    print('계획: %s — 캠페인 %s (%s)' % (plan['action'], plan['campaignId'],
                                       '검증됨: 화면 캡처' if verified else '앱 코드 모양: 캡처 없음'))
    print()
    print('%-22s %-44s %s' % ('필드', '현재', '변경후'))
    for row in plan['rows']:
        # 값은 자르지 않는다: 이름·국가 목록이 잘리면 무엇이 바뀌는지 확인할 수 없다.
        print('%-22s %s' % (row['field'], row['current'] if row['current'] is not None else '—'))
        print('%-22s %s' % ('', '→ ' + str(row['after']) if row['after'] is not None else '→ —'))
    print()
    print('보낼 요청 (토큰 제외):')
    print(json.dumps(plan_requests(plan), ensure_ascii=False, indent=1))
    print('보낼 뒤 읽을 곳 (리드백):')
    print(json.dumps(plan_reads(plan), ensure_ascii=False, indent=1))
    print()
    if not verified:
        print('경고: %s — %s. 응답 1000 이어도 반영은 리드백으로만 확인.' % (plan['action'], UNVERIFIED))
    for text in cost_lines(plan['action'], plan['before'], plan['after']):
        print(text)
    print()
    blocked = send_block_reason(plan)
    if blocked:
        print('--yes 불가: ' + blocked)
    if dry_run:
        print('dry-run: 아무것도 보내지 않음(값 변화 없음). 실제로 보내려면 --yes.')
        return 0
    if blocked:
        raise Unverified(blocked)
    path = write_backup(account, plan, plan_requests(plan))
    print('백업: %s' % path)
    print()
    return execute(session, plan)


# Campaign types whose write envelope a screen capture showed (productLineId 110106: AI shop 51 / product 47).
CAPTURED_TYPES = (51, 47)


def send_block_reason(plan):
    """Why --yes must not send this plan (None = may send). Only request shapes a screen capture
    recorded are sent, and only changes the CLI can undo: a guessed body could change something else."""
    action = plan['action']
    if action not in VERIFIED_ACTIONS:
        return '%s: 요청 형식 미검증(화면 캡처 없음, 앱 코드 모양뿐) — 계획만' % action
    ctype = plan.get('campaignType')
    if ctype is not None and ctype not in CAPTURED_TYPES:
        return '캠페인 유형 %s: 쓰기 요청(productLineId)은 유형 51·47 만 캡처됨' % ctype
    if action == 'campaign.countries':
        if plan['opts'].countries == 'all':
            return '전체 국가로 되돌리는 요청 형식 미검증(캡처는 국가 목록만)'
        if plan['before'].get('regions') is None:
            return '지금 전체 국가: 목록으로 줄이면 전체 국가로 되돌릴 요청 형식이 없어 restore 불가'
    return None


def execute(session, plan):
    action, cid, opts = plan['action'], plan['campaignId'], plan['opts']
    if action in PRODUCT_PATHS:
        return execute_products(session, plan)
    if action == 'campaign.rename':
        response = send(session, CAMPAIGN_UPDATE, {'id': cid, 'title': opts.name})
        error = write_failure(response, parsed(response))
    elif action == 'campaign.status':
        response = send(session, CAMPAIGN_UPDATE, {'id': cid, 'onlineStatus': 1 if opts.status == 'on' else 0})
        error = write_failure(response, parsed(response))
    elif action == 'campaign.budget':
        response = send(session, CAMPAIGN_UPDATE, {'id': cid, 'budget': float(opts.budget)})
        error = write_failure(response, parsed(response))
    else:
        operation = {'id': cid}
        if opts.countries != 'all':
            operation['syncPromotionRegionConf'] = opts.countries
        response = send(session, CAMPAIGN_PATHS[action], {'campaignOperationList': [operation]})
        error = write_failure(response, parsed(response))
    if error:
        print('실패: 서버가 요청을 거부함 — %s' % error)
        return 1
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


def execute_products(session, plan):
    cid, opts = plan['campaignId'], plan['opts']
    if plan['action'] == 'product.remove':
        response = send(session, PRODUCT_PATHS['product.remove'],
                        {'adGroupOperationList': [{'id': i} for i in plan['adgroups']]}, cid=cid)
        error = write_failure(response, parsed(response))
        if error:
            print('실패: 서버가 요청을 거부함 — %s' % error)
            return 1
    else:
        check = send(session, '/api/campaign/{id}/adgroup/can/create',
                     {'adGroupOperationList': [{'productId': p} for p in opts.products]}, cid=cid)
        value = parsed(check)
        inner = get_in(value, 'data', 'status')
        error = write_failure(check, value)
        if error is None and inner not in (None, 1000, '1000'):
            error = '사전확인 can/create status %s (1001=다른 캠페인과 중복, 1002=추가 불가)' % inner
        if error:
            print('실패: 사전확인 거부 — %s' % error)
            return 1
        response = send(session, PRODUCT_PATHS['product.add'],
                        {'adGroupOperationList': [{'productId': p, 'onlineStatus': 1} for p in opts.products]}, cid=cid)
        error = write_failure(response, parsed(response))
        if error:
            print('실패: 서버가 요청을 거부함 — %s' % error)
            return 1
    groups, count = read_adgroups(session, cid), adgroup_count(session, cid)
    now = [g.get('productId') for g in groups]
    print('리드백 (/api/campaign/%d/adgroup/batch):' % cid)
    print(json.dumps({'products': now, 'adgroupCount': count}, ensure_ascii=False, indent=1))
    expected = plan['after']['products']
    if sorted(map(str, now)) != sorted(map(str, expected)):
        print('리드백 불일치: ' + json.dumps({'상품': {'기대': expected, '실제': now}}, ensure_ascii=False))
        return 3
    print('리드백 일치: 변경 반영 확인')
    return 0


# ---- CLI -----------------------------------------------------------------------------------------

def build_parser():
    """Global options ride on every subparser, so they work before or after the command."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--account')
    common.add_argument('--config')
    common.add_argument('--format', choices=('table', 'json'), default='table')
    ap = argparse.ArgumentParser(prog='alibaba-ads', description=__doc__.splitlines()[0], parents=[common])
    sub = ap.add_subparsers(dest='group')

    def add(name, help_text):
        return sub.add_parser(name, help=help_text, parents=[common]).add_subparsers(dest='sub')

    campaign = add('campaign', '캠페인 설정 변경(이름·상태·예산·국가)')
    cset = campaign.add_parser('set', parents=[common])
    cset.add_argument('campaignId')
    cset.add_argument('--name')
    cset.add_argument('--status', choices=('on', 'off'))
    cset.add_argument('--budget')
    cset.add_argument('--countries')

    products = add('products', '캠페인 상품(광고그룹) 추가·삭제')
    for name in ('add', 'remove'):
        p = products.add_parser(name, parents=[common])
        p.add_argument('campaignId')
        p.add_argument('products', nargs='+')

    keywords = add('keywords', '키워드 추가·삭제·입찰가(요청 형식 미검증: 계획만)')
    for name in ('add', 'remove', 'bid'):
        p = keywords.add_parser(name, parents=[common])
        p.add_argument('campaignId')
        p.add_argument('items', nargs='+')
        if name == 'bid':
            p.add_argument('--price')

    restore_p = sub.add_parser('restore', help='백업 파일의 원값으로 되돌림', parents=[common])
    restore_p.add_argument('file')
    backups_p = sub.add_parser('backups', help='백업 목록', parents=[common])
    return ap


def positive_int(value, what):
    if not re.fullmatch(r'[1-9][0-9]*', str(value)):
        raise Usage('%s 는 1 이상 정수: %r' % (what, value))
    return int(value)


def campaign_opts(args):
    given = [k for k in ('name', 'status', 'budget', 'countries') if getattr(args, k) is not None]
    if not given:
        raise Usage('campaign set: --name/--status/--budget/--countries 중 하나 이상 필요')
    if len(given) > 1:
        raise Usage('campaign set: 한 번에 한 필드만(캡처 확인: 필드별 요청이 따로) — 받은 %s'
                    % ', '.join('--' + k for k in given))
    args.campaignId = positive_int(args.campaignId, '캠페인ID')
    if args.status is not None:
        return 'campaign.status'
    if args.budget is not None:
        if not re.fullmatch(r'[0-9]+(\.[0-9]+)?', str(args.budget)) or float(args.budget) <= 0:
            raise Usage('--budget 는 0보다 큰 숫자: %r' % args.budget)
        return 'campaign.budget'
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
    view = campaign_view(read_campaign(session, args.campaignId))
    return dict(action=action, campaignId=args.campaignId, opts=args, before=campaign_before(action, view),
                after=campaign_after(action, args), rows=campaign_rows(action, view, args), adgroups=None,
                campaignType=view.get('type'))


def product_plan(args, session):
    add = args.sub == 'add'
    action = 'product.add' if add else 'product.remove'
    cid = positive_int(args.campaignId, '캠페인ID')
    products = [positive_int(p, '상품ID') for p in args.products]
    args.products = products
    groups = read_adgroups(session, cid)
    ctype = campaign_view(read_campaign(session, cid)).get('type')
    have = [g.get('productId') for g in groups]
    by_product = {}
    for group in groups:
        by_product.setdefault(group.get('productId'), []).append(group.get('id'))
    if add:
        if any(p in have for p in products):
            raise Usage('이 캠페인에 이미 있는 상품: %s' % ', '.join(str(p) for p in products if p in have))
        after = have + products
        ids, rows = None, [dict(field='상품(광고그룹)', current=have, after=after)]
    else:
        missing = [p for p in products if p not in by_product]
        if missing:
            raise Usage('삭제할 상품이 이 캠페인 광고그룹에 없음: %s (있는 상품: %s)' % (
                ', '.join(map(str, missing)), ', '.join(map(str, by_product)) or '없음'))
        after = [p for p in have if p not in products]
        ids = [i for p in products for i in by_product[p]]
        rows = [dict(field='상품(광고그룹)', current=have, after=after),
                dict(field='삭제할 광고그룹 id', current=None, after=ids)]
    return dict(action=action, campaignId=cid, opts=args, before={'products': have},
                after={'products': after}, rows=rows, adgroups=ids, campaignType=ctype)


def restore_plan(args, session):
    """Plan that brings back the values a backup recorded: same rules (dry-run, --yes, backup first)."""
    try:
        record = json.loads(Path(args.file).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise Usage('백업 파일을 읽을 수 없음: %s (%s)' % (args.file, error))
    if not isinstance(record, dict) or not isinstance(record.get('before'), dict) or not record.get('action'):
        raise Usage('백업 형식 아님(account/campaignId/action/before 필요): %s' % args.file)
    action = record['action']
    if action not in CAMPAIGN_PATHS:
        raise Unverified('백업 조작 %s 는 되돌릴 요청 형식 미검증(수정·삭제 요청 화면 캡처 없음): restore 미지원' % action)
    cid = positive_int(record.get('campaignId'), '백업 campaignId')
    raw = record['before']
    opts = argparse.Namespace(campaignId=cid, name=None, status=None, budget=None, countries=None)
    if action == 'campaign.rename':
        opts.name = raw.get('title')
        if not isinstance(opts.name, str) or not opts.name.strip():
            raise Usage('백업에 title 없음')
    elif action == 'campaign.status':
        opts.status = 'on' if raw.get('onlineStatus') in (1, '1') else 'off'
    elif action == 'campaign.budget':
        opts.budget = str(raw.get('budget'))
        if not re.fullmatch(r'[0-9]+(\.[0-9]+)?', opts.budget):
            raise Usage('백업의 budget 값이 숫자 아님: %r' % raw.get('budget'))
    else:
        regions = raw.get('regions')
        opts.countries = 'all' if regions in (None, '') else ','.join(str(regions).split(','))
    view = campaign_view(read_campaign(session, cid))
    after = campaign_after(action, opts)
    rows = [dict(field=action.replace('campaign.', ''), current=v,
                 after=after.get(k)) for k, v in campaign_before(action, view).items() if k != 'settleStatus']
    print('복원 대상: %s / 캠페인 %s / 백업 %s (%s)' % (action, cid, args.file, record.get('createdAt')))
    return dict(action=action, campaignId=cid, opts=opts, before=campaign_before(action, view), after=after,
                rows=rows, adgroups=None, campaignType=view.get('type'))


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
        raise Usage('명령 필요: campaign set | products add|remove | keywords add|remove|bid | restore | backups')
    if args.group in ('campaign', 'products', 'keywords') and getattr(args, 'sub', None) is None:
        raise Usage('%s: 하위 명령 필요(set|add|remove|bid)' % args.group)
    if args.group == 'keywords':
        raise Unverified(UNVERIFIED + ' — keywords %s 는 계획만: 요청 본문 키를 확인 못 함' % args.sub)
    if not args.account:
        raise Usage('--account 가 필요하다')
    if args.account in BLOCKED_ACCOUNTS:
        raise Blocked(args.account)
    # Reject a bad request shape before the account file or a network call is touched.
    if args.group == 'campaign':
        args.campaignId = positive_int(args.campaignId, '캠페인ID')
        campaign_opts(args)
    elif args.group == 'products':
        args.campaignId = positive_int(args.campaignId, '캠페인ID')
        args.products = [positive_int(p, '상품ID') for p in args.products]
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
    plan = restore_plan(args, session) if args.group == 'restore' else (
        product_plan(args, session) if args.group == 'products' else campaign_plan(args, session))
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
