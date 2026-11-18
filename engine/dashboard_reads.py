"""Read-only product, funds, promotion, store and knowledge dashboard contracts."""
import re
import time

from api_session import PUNISH_URL
from http_collect import get_path

TABLE_NAMES = {
    'products-effect': ('product_effect', 'product_lock', 'product_radar'),
    'products-growth': ('products_growth',),
    'funds-balance': ('funds_fee_status', 'funds_wallets', 'funds_balance', 'funds_pending'),
    'promotions-events': ('promotion_available', 'promotion_enrolled'),
    'store-tasks': ('growth_tasks', 'star_tasks', 'punishment_scores'),
    'knowledge-files': ('knowledge_files',),
}
PRODUCT = 'https://us-productposting.alibaba.com'
PRODUCT_LIST = '/product/managementproducts/asyQueryProductsList.do'
PRODUCT_REF = 'https://i.alibaba.com/products/list-manage'
SELLER = 'https://i.alibaba.com'
BIZ = 'https://biz.alibaba.com'
ONETALK = 'https://onetalk.alibaba.com/message/weblitePWA.htm'


def products_growth(read):
    url = 'https://productcenter.alibaba.com/product/management/query-incubation-product.json'
    read.add('products_growth', read.pages(lambda page: read.post(url, {
        'page': page, 'pageSize': 10, 'tradeType': 'rts',
        'chanceSource': 'GGS_GLOBAL_INSIGHT_PRODUCT_CLUSTERS_RECOMMEND',
        'isExpiredHighDemandProduct': False,
    }, encoding='json', referer=SELLER + '/products/collect-product'), rows='content.contents', total='content.totalCount'))


def products_effect(read):
    items = read.pages(lambda page: read.get(PRODUCT + PRODUCT_LIST, {
        'statisticsType': 'month', 'repositoryType': 'all', 'imageType': 'all',
        'status': 'all', 'page': page, 'size': 100, 'lang': 'en_US',
    }, referer=PRODUCT_REF), rows='products', total='count')
    ids = []
    for item in items:
        product_id = item.get('id') if isinstance(item, dict) else None
        if (type(product_id) is int and product_id > 0 or
                isinstance(product_id, str) and product_id.isascii() and product_id.isdigit()):
            ids.append(str(product_id))
        else:
            read.partial('products: a row is missing its product ID')
    if len(set(ids)) != len(ids):
        read.partial('products: duplicate product ID')
        ids = list(dict.fromkeys(ids))
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        query = {'productIds': ','.join(batch), 'lang': 'en_US'}
        effects = read.get(PRODUCT + '/product/managementproducts/asyQueryProductEffect.do',
            {**query, 'statisticsType': 'month'}, referer=PRODUCT_REF,
            check=lambda v: isinstance(v.get('productEffect'), dict))['productEffect']
        read.add('product_effect', [
            {'productId': pid, **effect} if isinstance(effect, dict) else {'productId': pid, 'effect': effect}
            for pid, effect in effects.items() if pid in batch
        ])
        locks = read.get(PRODUCT + '/product/managementproducts/asyQueryProductLock.do',
            {**query, 'locale': 'ko_KR'}, referer=PRODUCT_REF)
        read.add('product_lock', read.field(locks, 'data', list))
    radar = read.get(PRODUCT + '/product/product_radar/radar_summary.do', {
        'callback': 'dashboardCallback', '_': time.time_ns() // 1_000_000,
    }, referer=SELLER + '/', send_origin=False)
    read.add('product_radar', read.field(radar, 'todoDetail', dict))


def funds_balance(read):
    fee = read.get(BIZ + '/ggs/web/fee/getFeeStatusView.json', referer=SELLER + '/trade/fund/fees.htm')
    member = get_path(fee, 'data.memberType')
    read.add('funds_fee_status', [{**row, 'memberType': member}
             for row in read.field(fee, 'data.statusItemList', list) if isinstance(row, dict)])
    withdraw = SELLER + '/trade/fund/startWithdraw.htm'
    wallets = read.get(BIZ + '/ggs/web/funds/queryWalletList.json', {
        'subBizScene': 'TA_GGS_DEFAULT', 'accountType': 'COMPANY',
    }, referer=withdraw)
    read.add('funds_wallets', read.field(wallets, 'data', list))
    balance = read.get(BIZ + '/ggs/web/withdraw/queryBalance.json',
                       {'json': {'accountCategory': 'CUSTOMER_SELF', 'tags': None}}, referer=withdraw)
    read.add('funds_balance', read.field(balance, 'data', list))
    pending = read.get('https://fund.alibaba.com/seller/apollo/forex/queryWaitRelateAmountEveryCcy.json',
                       referer=SELLER + '/trade/fund/collectedList.htm')
    read.add('funds_pending', read.field(pending, 'data', list))


def promotions_events(read):
    host = 'https://marketing.alibaba.com'
    context = {'referer': host + '/enroll/home.htm', 'send_origin': False}
    available = read.get(host + '/enroll/GGSAcitities.do', {'topicBusinessType': 'all', 'isSeriousShow': 'true'}, **context)
    read.add('promotion_available', read.field(available, 'topicDatas', list))
    read.add('promotion_enrolled', read.pages(lambda p: read.get(host + '/enroll/myEnrollActivityList.do', {
        'pageNo': p, 'pageSize': 10,
    }, **context), rows='enrolledActivityList', total='total'))


def store_tasks(read):
    progress = read.get(SELLER + '/ajax/ggsTaskProgress.do', send_origin=False)
    read.add('growth_tasks', read.field(progress, 'data.taskList', list))
    star = read.get('https://merchant-rating.alibaba.com/task/ggsFetchStarTaskV2.jsonp', {
        'callback': 'dashboardCallback', '_': time.time_ns() // 1_000_000,
    }, referer=SELLER + '/data-page/star-rating', send_origin=False)
    read.add('star_tasks', read.field(star, 'values', list))
    html = read.html(ONETALK)
    match = re.search(r'''(?:\baliId\b|\baliID\b)["']?\s*[:=]\s*["']?([0-9]+)\b''', html)
    if not match:
        read.fail('onetalk_ali_id_missing')
    scores = read.mtop(PUNISH_URL, {
        'jsv': '2.7.2', 'appKey': '24889839', 'api': 'mtop.alibaba.camel.antioffline.ggs.punish.get',
        'v': '1.0', 'dataType': 'json', 'ecode': '1', 'type': 'originaljson',
    }, {'aliId': match[1]}, referer=ONETALK, check=lambda v: get_path(v, 'data.code') in (0, '0')
        and isinstance(v.get('ret'), list)
        and any(isinstance(s, str) and s.startswith('SUCCESS::') for s in v['ret']))
    fields = ('punishEnabled', 'punishResult0Score', 'punishResult3Score')
    value = read.field(scores, 'data.data', dict)
    read.add('punishment_scores', {k: value[k] for k in fields if k in value})


def knowledge_files(read):
    url = 'https://aipro.alibaba.com/ai/knowledgeBaseQuery/queryKnowledgeFoldersOrFiles.do'
    rows = read.pages(lambda p: read.get(url, {'page': p, 'pageSize': 10},
                      referer='https://aipro.alibaba.com/ai/knowledgeBase/index.htm', send_origin=False),
                      rows='data.dataList', total='data.totalCount')
    for row in rows:
        if not isinstance(row, dict):
            read.partial('knowledge metadata row is not an object')
            continue
        columns = ('id', 'name', 'fileName', 'folderName', 'size', 'gmtCreate', 'gmtModified')
        def fields(obj, keys):
            return {k: obj[k] for k in keys if k in obj and isinstance(obj[k], (str, int, float, bool, type(None)))}

        metadata = fields(row, (*columns, 'type', 'setTop'))
        for key in ('knowledgeBaseDTO', 'knowledgeFolderDTO', 'knowledgeFileDTO'):
            dto = row.get(key)
            if isinstance(dto, dict):
                metadata.update(fields(dto, (*columns, 'ownerType', 'quoteNum')))
        read.add('knowledge_files', metadata, public_name=True)


HANDLERS = {'products-effect': products_effect, 'products-growth': products_growth,
            'funds-balance': funds_balance, 'promotions-events': promotions_events,
            'store-tasks': store_tasks, 'knowledge-files': knowledge_files}
