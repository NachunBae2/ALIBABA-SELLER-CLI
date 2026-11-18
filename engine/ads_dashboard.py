"""Read the showcase and brand dashboards using their captured form contracts."""
from http_collect import get_path

ADS_TABLES = {
    'ads-adgroups': ('adgroups', 'adgroup_summary'),
    'ads-showcase': ('showcase_order_counts', 'showcase_orders', 'showcase_overview',
                     'showcase_effect_total', 'showcase_effect_trend', 'showcase_effect_interval',
                     'showcase_member'),
    'ads-brand': ('brand_campaigns', 'brand_word_packages', 'brand_excluded_products',
                  'brand_excluded_words', 'brand_creatives', 'brand_keyword_effect'),
    'ads-creatives': ('creative_summary', 'creatives'),
}
ADS_HOST = 'https://www2.alibaba.com'
CREATIVE_HOST = 'https://creative.alibaba.com'
SHOWCASE = {'productLineId': 110101, 'productId': 110101009}
BRAND = {'productLineId': 110102, 'productId': 110102006}
CREATIVE = {'productLineId': 110102, 'productId': 110102001}
NAPI = {'productLineId': 110101}


def referer(path):
    if path.startswith('/api/showcase/order/'):
        return ADS_HOST + '/showcase/order_list.htm'
    if path.startswith(('/api/campaign/', '/api/showcase/')):
        return ADS_HOST + '/showcase/index.htm'
    if path.startswith('/brand/napi/creative/'):
        return ADS_HOST + '/ads/brand_card/index.htm'
    if path.startswith('/brand/napi/dataReport/'):
        return ADS_HOST + '/brand/report/store-summary.htm'
    return ADS_HOST + '/supereme/index.htm'


def post(read, path, ads, data, *, host=ADS_HOST):
    def success(value):
        if value.get('status') not in (1000, '1000'):
            return False
        if path == '/brand/napi/creative/getCreativeList':
            return (value.get('success') is True and get_path(value, 'data.success') is True
                    and get_path(value, 'data.code') in (200, '200')
                    and not get_path(value, 'data.errorCode'))
        if path.startswith('/brand/napi/'):
            return value.get('success') is True
        return value.get('ok') is True

    return read.post(host + path, {'ads': ads, 'type': 'normal', 'data': data}, referer=referer(path), check=success)


def campaign_id(read):
    campaign = post(read, '/api/campaign/find/showcase', SHOWCASE, {}).get('data')
    if (type(campaign) is int and campaign > 0 or
            isinstance(campaign, str) and campaign.isascii() and campaign.isdigit() and int(campaign) > 0):
        return int(campaign)
    read.fail('showcase campaign ID missing or invalid')


def adgroups(read):
    path = f'/api/campaign/{campaign_id(read)}/adgroup'
    rows = read.pages(lambda p: post(read, path, SHOWCASE, {
        'orderBy': 'ad_group_sort', 'order': 'desc', 'adgroupOnlineStatus': 1, 'page': p, 'size': 50,
    }), rows='data', total='totalCount')
    read.add('adgroups', rows)
    for status in (1, 0):
        value = read.field(post(read, path + '/summary', SHOWCASE, {'adgroupOnlineStatus': status}), 'data', dict)
        read.add('adgroup_summary', {**value, 'adgroupOnlineStatus': status})


def showcase(read):
    counts = post(read, '/api/showcase/order/count', SHOWCASE, {})
    read.add('showcase_order_counts', read.field(counts, 'data', dict))
    for status in ('execute', 'issue_ready', 'service', 'suspend'):
        value = post(read, '/api/showcase/order/list', SHOWCASE, {'type': status})
        orders = read.field(value, 'data', list)
        read.add('showcase_orders', [
            {**row, 'type': status} if isinstance(row, dict) else {'value': row, 'type': status}
            for row in orders])
    overview = post(read, '/api/showcase/overview', SHOWCASE, {'campaignId': campaign_id(read)})
    read.add('showcase_overview', read.field(overview, 'data', dict))
    effect = {'dateBegin': read.start, 'dateEnd': read.end, 'type': 'week'}
    total = post(read, '/api/showcase/effect/total', SHOWCASE, effect)
    read.add('showcase_effect_total', read.field(total, 'data', dict))
    trend = post(read, '/api/showcase/effect/trend', SHOWCASE, effect)
    read.add('showcase_effect_trend', read.field(trend, 'data', list))
    interval = post(read, '/api/showcase/effect/interval', SHOWCASE, {})
    read.add('showcase_effect_interval', read.field(interval, 'data.weekList', list))
    member = post(read, '/api/account/member/showcase', SHOWCASE, {'scene': 'name'})
    # The scene=name lookup answers with member display names, the point of this table.
    read.add('showcase_member', read.field(member, 'data', dict), public_name=True)


def brand(read):
    def packages(page):
        value = post(read, '/api/brand/campaign/listSupremeWordPackage', CREATIVE, {'pageSize': 2, 'pageIndex': page})
        # This endpoint returns null totalCount and an explicit zero totalPages for an empty catalog.
        if (page == 1 and value.get('totalCount') is None and
                type(value.get('totalPages')) is int and value['totalPages'] == 0 and value.get('data') == []):
            return {**value, 'totalCount': 0}
        return value

    campaigns = post(read, '/api/brand/campaign/getFlowCampaign', BRAND, {
        'sub_type': 'supreme_flow_supplement', 'onlineStatus': [1],
    })
    read.add('brand_campaigns', read.field(campaigns, 'data', list), public_name=True)
    read.add('brand_word_packages', read.pages(packages, rows='data', total='totalCount'))
    products = post(read, '/api/brand/supreme/flow/black/getBlackProductList', BRAND, {})
    read.add('brand_excluded_products', read.field(products, 'data', list))
    value = post(read, '/api/brand/supreme/flow/black/getBlackWordsList', BRAND, {})
    read.add('brand_excluded_words', read.field(value, 'data.blackWordList', list))
    read.add('brand_creatives', read.pages(lambda p: post(read, '/brand/napi/creative/getCreativeList', NAPI, {
        'pageSize': 10, 'productId': 110102023, 'unQueryBind': True, 'onlineStatus': 1,
        'pageIndex': p, 'name': '', 'channel': 'ALL',
    }), rows='data.result', total='data.totalCount'), public_name=True)
    read.add('brand_keyword_effect', read.pages(lambda p: post(read, '/brand/napi/dataReport/findKeywordEffect', NAPI, {
        'date': [read.start, read.end], 'pageIndex': p, 'pageSize': 10, 'resourceLockIdList': [],
        'kwStatus': '', 'channel': '', 'productIdList': [110102001, 110102004],
    }), rows='data.list', total='data.totalCount'))


def creatives(read):
    summary = post(read, '/api/creative/getSummaryInfo', CREATIVE, {
        'campaignType': 12,
    }, host=CREATIVE_HOST)
    read.add('creative_summary', read.field(summary, 'data', dict))
    read.add('creatives', read.pages(lambda p: post(read, '/api/creative/listCreative', CREATIVE, {
        'onlineStatus': 1, 'pageSize': 8, 'pageIndex': p, 'name': '', 'bindQuery': 'undefined', 'channel': '',
    }, host=CREATIVE_HOST), rows='data', total='totalCount'), public_name=True)


ADS_HANDLERS = {
    'ads-adgroups': adgroups, 'ads-showcase': showcase, 'ads-brand': brand, 'ads-creatives': creatives,
}
