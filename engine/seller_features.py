#!/usr/bin/env python3
"""Read-only shipping/store gaps. Output is deliberately allowlisted."""
import argparse
import json

from api_session import ApiSession
from manage_write import load_account, read_template_body, read_templates, read_versions


def shipping(session, ems_only=False):
    result = []
    for template in read_templates(session):
        body = read_template_body(session, template)
        carriers, countries = set(), set()
        for carrier in body.get('carrierList') or []:
            for detail in carrier.get('freightTemplateDetailList') or []:
                info = carrier.get('carrierInfoMap', {}).get('wtd') or {}
                name = info.get('carrierName') or info.get('carrierCode') or detail.get('carrierCode')
                if name:
                    carriers.add(str(name))
                countries.update(str(x) for x in (detail.get('countries') or []) if x)
        row = {'template': template['title'], 'default': template['isDefault'],
               'countries': sorted(countries), 'carriers': sorted(carriers)}
        if not ems_only or any('EMS' in value.upper() for value in carriers):
            result.append(row)
    return result


def main():
    parser = argparse.ArgumentParser(description='배송 템플릿 커버리지·EMS 및 미니사이트 게시 상태 읽기')
    parser.add_argument('command', choices=('logistics coverage', 'logistics ems', 'minisite pages'))
    parser.add_argument('--account', required=True)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    account = load_account(args.config, args.account)
    session = ApiSession(account, args.config, refresh_saved=False)
    if args.command.startswith('logistics'):
        rows = shipping(session, args.command.endswith('ems'))
    else:
        rows = [{'pageId': row['pageId'], 'name': row['name'], 'package': row['package'],
                 'live': row['live'], 'published': row['published'], 'released': row['released']}
                for row in read_versions(session)]
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
