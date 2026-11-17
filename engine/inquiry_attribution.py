#!/usr/bin/env python3
"""Product ID -> inquiries, over one seller_central_raw.py output folder.

Inputs (raw/):
  inquiries.jsonl        inquiry list: id, time, source, feedbackType, targetIds (product on product inquiries)
  ads-report.jsonl       ads report product table per product per day (range tag *.product):
                         impsCnt, clickCnt, mcCnt (ad inquiries), atmSessionCnt (ad TM chats), orderCnt
  analytics-product.jsonl  per-product monthly stats (titles, organic + ad impressions/clicks, inquiries)

Outputs (analysis/):
  lead_sources.csv           one row per inquiry: lead type, entry point (source code), device, ad flag, product
  lead_source_summary.csv    inquiries per lead type x entry x device x ad flag
  inquiry_by_product.csv     one row per inquiry: inflow = product ID (product inquiries) or "TM 유입" (TM chats, no product in the inquiry)
  inquiry_by_product_counts.csv  inquiries per product ID / "TM 유입"
  product_inquiries_ad_match.csv one row per product x inquiry incl. TM chats matched to products by ad TM chats that day
  product_ad_summary.csv     one row per product: ad impressions, clicks, ad inquiries, ad TM chats, orders, matched inquiries
  inquiry_attribution.csv    one row per inquiry: matched product(s) and match type

Matching. The inquiry list names the product only on product inquiries (feedbackType 1). TM chats carry no
product. The ads report has, per product and day, the number of ad inquiries (mcCnt) and ad TM chats
(atmSessionCnt). Ads report days are US Pacific dates (`timeUs`), so inquiry times are converted to that date.
  direct            product inquiry: the product is in the inquiry itself
  direct+ad         product inquiry, and that product had an ad inquiry/TM chat that day
  ad-day-only       no product in the inquiry; exactly one product had an ad inquiry/TM chat that day
  ad-day-candidates no product in the inquiry; several products had one that day (all listed)
  none              no product in the inquiry and no ad inquiry/TM chat that day (organic or other channel)
"""
import argparse
import collections
import csv
import datetime as dt
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pacific_tz import pacific  # noqa: E402

US = pacific()


def rows(path):
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def num(v):
    if isinstance(v, dict):
        v = v.get("main")
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def main_val(v):
    return v.get("main") if isinstance(v, dict) else v


def load_inquiries(raw):
    out = {}
    for r in rows(os.path.join(raw, "inquiries.jsonl")):
        if (r.get("replay") or {}).get("kind") == "inquiry":
            for it in (r.get("json") or {}).get("list") or []:
                out[it.get("id")] = it
    return list(out.values())


def load_ad_products(raw):
    """{(product_id, 'YYYY-MM-DD'): Counter} and {product_id: subject} from ads report product tables."""
    days, titles, seen = collections.defaultdict(collections.Counter), {}, set()
    for r in rows(os.path.join(raw, "ads-report.jsonl")):
        rp = r.get("replay") or {}
        if rp.get("kind") != "range" or "product" not in (rp.get("table") or ""):
            continue
        for d in ((((r.get("json") or {}).get("data") or {}).get("queryResult") or {}).get("detailList")) or []:
            pid, day = str(main_val(d.get("productId")) or ""), str(main_val(d.get("timeUs")) or "")[:10]
            if not pid or not day:
                continue
            key = (rp.get("table"), pid, day)
            if key in seen:  # the same table can be replayed twice (page load + rangeTables)
                continue
            seen.add(key)
            c = days[(pid, day)]
            for col, name in (("impsCnt", "ad_impressions"), ("clickCnt", "ad_clicks"), ("mcCnt", "ad_inquiries"),
                              ("atmSessionCnt", "ad_tm_chats"), ("orderCnt", "ad_orders")):
                c[name] += num(d.get(col))
            if main_val(d.get("subject")):
                titles[pid] = main_val(d.get("subject"))
    return days, titles


def load_titles(raw, titles):
    for r in rows(os.path.join(raw, "analytics-product.jsonl")):
        if ((r.get("replay") or {}).get("tag") or "").startswith("product_stats"):
            for p in (((r.get("json") or {}).get("value") or {}).get("products") or {}).get("data") or []:
                titles.setdefault(str(p.get("id")), p.get("subject") or "")


# Lead source. feedbackType is the inquiry folder (PRODUCT / COMPANY / CONTACT(Other) / QUOTATION(RFQ) in the
# message center); the numbers were matched to source codes on 2026-11-17: 1 = product page, 2 = company page
# (minisite), 6 = TM/IM chat. `source` says where on Alibaba the buyer pressed contact; unknown codes are kept as is.
LEAD_TYPE = {"1": "상품 문의", "2": "회사(미니사이트) 문의", "6": "TM/IM 대화"}
ENTRY = (("main_en_detail", "PC 상품상세"), ("main_app_detail", "앱 상품상세"), ("main_app_esite", "앱 미니사이트"),
         ("main_en_esite", "PC 미니사이트"), ("CONTACT_MKT_IMOpportunity", "TM/IM 대화 시작(마케팅 접점)"), ("rfq", "RFQ"),
         ("RFQ", "RFQ"), ("QUOTATION", "RFQ"))


def lead_source(it, match):
    ft, src, app = str(it.get("feedbackType")), str(it.get("source") or ""), str(it.get("appFrom") or "")
    lead_type = LEAD_TYPE.get(ft) or ("RFQ" if "rfq" in src.lower() or "quot" in src.lower() else "기타(유형 %s)" % ft)
    entry = next((name for code, name in ENTRY if src.startswith(code)), "기타:" + src)
    device = "앱" if any(k in app.lower() for k in ("ios", "android", "iphone")) else ("웹" if app.startswith("web") else "기타:" + app)
    ad = {"direct+ad": "광고(그 상품 그날 광고 문의·대화)", "ad-day-only": "광고 추정(그날 광고 대화 상품 1개)",
          "ad-day-candidates": "광고 추정(후보 여러 상품)", "direct": "자연(광고 기록 없음)", "none": "자연(광고 기록 없음)"}[match]
    return lead_type, entry, device, ad


def ad_days_by_day(ad_days):
    """US date -> products with an ad inquiry or ad TM chat that day."""
    by_day = collections.defaultdict(list)
    for (pid, day), c in ad_days.items():
        if c["ad_inquiries"] > 0 or c["ad_tm_chats"] > 0:
            by_day[day].append(pid)
    return by_day


def match_inquiry(it, by_day):
    """-> (US Pacific datetime, US date, direct product id, match type, matched products)."""
    ts = it.get("createTime")
    t = dt.datetime.fromtimestamp(ts / 1000, US) if isinstance(ts, (int, float)) and ts else None
    day = t.strftime("%Y-%m-%d") if t else ""
    direct = str((it.get("targetIds") or [""])[0]) if str(it.get("feedbackType")) == "1" else ""
    cands = sorted(by_day.get(day, []))
    if direct:
        return t, day, direct, ("direct+ad" if direct in cands else "direct"), [direct]
    if len(cands) == 1:
        return t, day, direct, "ad-day-only", cands
    return t, day, direct, ("ad-day-candidates" if cands else "none"), cands


def inflow_of(it, direct):
    if direct:
        return direct
    return "TM 유입" if str(it.get("feedbackType")) == "6" or str(it.get("source") or "").startswith("CONTACT") else "기타:" + str(it.get("source"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out_dir", help="seller_central_raw.py output folder")
    a = ap.parse_args(argv)
    raw = os.path.join(a.out_dir, "raw")
    inqs = load_inquiries(raw)
    ad_days, titles = load_ad_products(raw)
    load_titles(raw, titles)
    if not inqs and not ad_days:
        print("no inquiries and no ads product rows in %s" % raw, file=sys.stderr)
        return 1
    by_day = ad_days_by_day(ad_days)

    pairs, per_inq, simple, leads = [], [], [], []
    for it in sorted(inqs, key=lambda x: x.get("createTime") or 0):
        t, day, direct, match, prods = match_inquiry(it, by_day)
        inflow = inflow_of(it, direct)
        base = {"inquiry_id": it.get("id"), "created_us_pacific": t.isoformat(timespec="minutes") if t else "",
                "ad_day": day, "source": it.get("source"), "feedback_type": it.get("feedbackType"),
                "buyer_country": it.get("buyerCountry"), "reply_status": it.get("replyStatus"), "match": match}
        per_inq.append({**base, "inflow": inflow, "products": " ".join(prods), "candidates": len(prods)})
        lt, le, dev, adf = lead_source(it, match)
        leads.append({"inquiry_id": it.get("id"), "created_us_pacific": base["created_us_pacific"], "lead_type": lt, "lead_entry": le,
                      "device": dev, "ad": adf, "product_id": direct, "product_title": titles.get(direct, "") if direct else "",
                      "tm_ad_day_candidates": "" if direct else " ".join(prods), "source_code": it.get("source"),
                      "feedback_type": it.get("feedbackType"), "app_from": it.get("appFrom"), "buyer_country": it.get("buyerCountry"),
                      "reply_status": it.get("replyStatus")})
        simple.append({"inflow": inflow, "product_title": titles.get(direct, "") if direct else "", "inquiry_id": it.get("id"),
                       "created_us_pacific": base["created_us_pacific"], "source": it.get("source"), "buyer_country": it.get("buyerCountry"),
                       "reply_status": it.get("replyStatus"), "tm_ad_day_candidates": "" if direct else " ".join(prods)})
        for pid in prods:
            c = ad_days.get((pid, day), collections.Counter())
            pairs.append({"product_id": pid, "product_title": titles.get(pid, ""), **base,
                          "ad_inquiries_that_day": c["ad_inquiries"], "ad_tm_chats_that_day": c["ad_tm_chats"],
                          "ad_clicks_that_day": c["ad_clicks"], "ad_impressions_that_day": c["ad_impressions"]})

    summ = collections.defaultdict(collections.Counter)
    for (pid, _day), c in ad_days.items():
        summ[pid].update(c)
    matched = collections.defaultdict(collections.Counter)
    for p in pairs:
        matched[p["product_id"]][p["match"]] += 1
    prod_rows = []
    for pid in set(summ) | set(matched):
        s, m = summ[pid], matched[pid]
        if not any(s.values()) and not m:
            continue
        prod_rows.append({"product_id": pid, "product_title": titles.get(pid, ""), "ad_impressions": s["ad_impressions"],
                          "ad_clicks": s["ad_clicks"], "ad_inquiries": s["ad_inquiries"], "ad_tm_chats": s["ad_tm_chats"],
                          "ad_orders": s["ad_orders"], "inquiries_direct": m["direct"] + m["direct+ad"],
                          "inquiries_ad_day_only": m["ad-day-only"], "inquiries_ad_day_candidate": m["ad-day-candidates"]})
    prod_rows.sort(key=lambda r: (-(r["ad_inquiries"] + r["ad_tm_chats"]), -r["inquiries_direct"], -r["ad_clicks"]))

    adir = os.path.join(a.out_dir, "analysis")
    os.makedirs(adir, exist_ok=True)
    counts = collections.Counter(r["inflow"] for r in simple)
    count_rows = [{"inflow": k, "product_title": titles.get(k, ""), "inquiries": n} for k, n in counts.most_common()]
    lsum = collections.Counter((r["lead_type"], r["lead_entry"], r["device"], r["ad"]) for r in leads)
    lsum_rows = [{"lead_type": a, "lead_entry": b, "device": c, "ad": d, "inquiries": n} for (a, b, c, d), n in lsum.most_common()]
    for name, data in (("lead_sources.csv", leads), ("lead_source_summary.csv", lsum_rows), ("inquiry_by_product.csv", simple), ("inquiry_by_product_counts.csv", count_rows),
                       ("product_inquiries_ad_match.csv", pairs), ("product_ad_summary.csv", prod_rows), ("inquiry_attribution.csv", per_inq)):
        with open(os.path.join(adir, name), "w", newline="") as fh:
            if data:
                w = csv.DictWriter(fh, fieldnames=list(data[0].keys()))
                w.writeheader()
                w.writerows(data)
    print(json.dumps({"inquiries": len(inqs), "lead_type": collections.Counter(r["lead_type"] for r in leads),
                      "lead_entry": collections.Counter(r["lead_entry"] for r in leads), "ad": collections.Counter(r["ad"] for r in leads),
                      "device": collections.Counter(r["device"] for r in leads), "by_inflow": dict(counts.most_common(15)), "match": collections.Counter(x["match"] for x in per_inq),
                      "ad_products": len(summ), "products_with_ad_inquiry_or_chat": sum(1 for r in prod_rows if r["ad_inquiries"] + r["ad_tm_chats"] > 0),
                      "ad_inquiries": sum(r["ad_inquiries"] for r in prod_rows), "ad_tm_chats": sum(r["ad_tm_chats"] for r in prod_rows),
                      "out": adir}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
