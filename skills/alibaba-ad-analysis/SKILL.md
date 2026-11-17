---
name: alibaba-ad-analysis
description: "알리바바닷컴 셀러 광고 분석: L1+ 바이어 클릭 비율, 문의 기준 분석, 입찰가 분석, 전환율(문의÷클릭)로 문의 1건 비용 역산, 전환 바이어 등급·국가로 상위 상품 고르기. alibaba-data CLI(이 레포)로 뽑아 표와 요약으로 낸다. '광고 분석', '상품별 광고비', '전환비용', '문의 1건 비용', '입찰가 분석', 'L1+ 클릭', '바이어 품질', '600달러 써서 21개 전환이면 얼마' 같은 요청에 사용. 읽기 전용."
---

# alibaba-ad-analysis

데이터는 `alibaba-data` CLI 가 뽑고 계산한다. 이 스킬은 CLI 를 부르고 결과를 읽어 보고한다. 스킬은 CLI 레포 안(`skills/alibaba-ad-analysis/`)에 있어 같은 레포 하나로 설치된다.

## 준비

레포 README 맨 위 "노트북 설치" 1~6 단계. 요약:

```sh
git clone https://github.com/NachunBae2/ALIBABA-SELLER-CLI.git ~/Documents/Codex/repos/alibaba-seller-cli
cd ~/Documents/Codex/repos/alibaba-seller-cli && npm link                      # Node 20+, python3
ln -sfn "$PWD/skills/alibaba-ad-analysis" ~/.claude/skills/alibaba-ad-analysis
```

계정 설정 `~/.aliops-ops/seller-report/accounts.json`(loginId·outDir·asideAccount·keychain). 고객사 이름은 계정키로만 쓴다.

## 실행

```sh
S=~/Documents/Codex/repos/alibaba-seller-cli/skills/alibaba-ad-analysis/scripts
python3 $S/analyze.py --account <계정키> --start 2026-06-01 --end 2026-11-15 --out <고객사 자산 폴더>/seller-report/ad_analysis-20261115
python3 $S/analyze.py --from <기존 덤프 폴더> --out /tmp/ad          # 이미 받은 덤프로 다시 계산(브라우저 안 씀)
```

- `--account` 는 Aside 로 셀러센터를 연다. Aside 호출은 한 번에 하나만.
- 기간은 `--start/--end`(미국 태평양 날짜)가 기본. 91일보다 길면 CLI 가 창으로 나눠 받아 합친다. 결과 첫 줄에 실제 쿼리 기간이 나온다.
- 출력은 고객사 자산 폴더나 임시 폴더에 둔다. 레포·채팅에 원문 표를 붙이지 않는다.
- 표 하나만: `alibaba-data ads quality|ads products|ads conversions|bids|keywords|coverage …`.

## 분석 순서 (이 순서로 보고)

1. **바이어 품질 = L1+ 바이어 클릭 비율 먼저** — `buyer_quality.csv`(`alibaba-data ads quality`).
   - 캠페인별(`level=campaign`)·월별(`month`)·기간 합(`total`), Manual / AI Smart 구분(`campaign_kind`).
   - `l1plus_click_pct` = L1+ 클릭(fiGrowthClickCnt) 합 ÷ 클릭 합 × 100. 일별 비율의 평균이 아니다. 화면 L1+ Buyer Click Percentage 와 같은 정의.
   - 상품별 값은 `product_ad_performance.csv` 의 `l1plus_click_pct`(첫 지표 열).
2. **문의 기준 분석** — 문의 = 광고표 `cvr3`(비즈니스 기회). 문의 원장(목록·국가·스팸·본 상품)은 `alibaba-data inquiries list|by-product`(데이터분석 CLI). `alibaba-inquiry` 는 OneTalk 답장·명함·파일 발송 전용이라 분석에 쓰지 않는다. 상품별 문의·L3+ 문의·연결 문의(`inquiry_quality.csv`: 직접 / 광고일 추정 / AI 전환 명세 실측의 등급·국가·스팸·답장·앱/웹).
3. **전환율 → 문의 1건 비용 역산** — `conversion_rate` = 문의 ÷ 클릭. `cost_per_inquiry` = CPC ÷ conversion_rate = 광고비 ÷ 문의. CPC·전환율·문의 1건 비용 세 열을 같이 말한다.
4. **입찰가 분석** — `bid_analysis.csv`: 캠페인·키워드 입찰가, 전환율과 그 수준(`rate_level` keyword→product→account, 클릭 20 이상), 예측 문의 비용 = 입찰가 ÷ 전환율. AI 캠페인 입찰가 = 캠페인 `maxPrice`(전환 1건 목표가).
5. **상위 상품 고르기** — `top_products.csv`: 문의 1건 비용 낮은 순 + 문의 많은 순, L1+ 클릭 비율, 전환 바이어 등급(`converted_buyer_levels`)·국가(`converted_buyer_countries`, AI 전환 명세 실측), 품질 점수(제안).

6. **키워드 추천** — 오너가 상품을 물으면 그 상품만 추천: `alibaba-data keywords recommend --account <계정키> --start … --end … --product <상품ID 또는 이름 일부>` → 그 상품 키워드 10개, 숫자만(키워드 | 점수 | 검색량 | 경쟁 | 입찰가 | 노출 | 클릭 | CTR | 문의 | L1+), 맨 아래 점수 공식 한 줄. 표와 공식 줄을 그대로 보여 주고, 빈칸은 빈칸 그대로(추정·말 풀이 붙이지 말 것). 상품을 안 정했으면 `--product` 없이 불러 나온 상품 목록에서 고르게 한다. 전 상품 일괄은 오너가 전부를 원할 때만 `--all-products`.

## 결과 (`--out` 폴더)

| 파일 | 내용 |
|---|---|
| `buyer_quality.csv` | L1+ 클릭 비율·L1+ 클릭·클릭·광고비·문의·L3+ 문의·L3+ 비율·CPC·전환율·문의 1건 비용 — 캠페인별·월별·기간 합 |
| `product_ad_performance.csv` | 상품별 L1+ 클릭 비율, 광고비(`spend_actual_usd` 실측 / `spend_estimated_usd` 추정, 출처 열), 노출, 클릭, CTR, 문의(`inquiries_cvr3`), `cpc_usd`, `conversion_rate`, `cost_per_inquiry_usd`, `cost_per_inquiry_by_cpc_usd`, L3+ 비율, 주문, 입찰가, AI 전환 명세 실측 수·등급·국가 |
| `bid_analysis.csv` | 입찰가·전환율 수준·예측 문의 비용 |
| `inquiry_quality.csv` | 상품별 연결 문의의 바이어 등급·국가·스팸·답장·앱/웹 |
| `top_products.csv` | 상위 상품 + L1+·전환 바이어 등급·국가·품질 점수 |
| `summary.md` | 기간, L1+ 비율, 광고비 합, 상위 10개 문장 |

## 정의 (오너 확정)

- 문의 = 광고 상품표 `cvr3`. `conversion_rate` = 문의 ÷ 클릭. `cost_per_inquiry` = 광고비 ÷ 문의 = CPC ÷ conversion_rate. 문의 0 이면 전환율 0, 비용 빈칸, 출처 `no_inquiry`.
- 검산: 광고비 $600, 문의 21건 → 문의 1건 $28.57.
- AI(GGS) 캠페인 상품별 광고비는 셀러센터에 없다 → 추정 = 그날 캠페인 일별 CPC × 상품 클릭. 출처 열에 "추정"이 있다. 상품별 전환 수·바이어 등급·국가는 AI 전환 명세(`conv_detail_*`)로 실측이다.
- 바이어 이름·연락처·대화 내용은 저장하지 않는다(CLI 가 브라우저 안에서 버린다).

## 품질 점수 규칙 (제안)

`quality_score = 100 × (0.6 × L3+ 비율 + 0.4 × min(문의 수, 10) / 10)`. L3+ 비율은 `l3PlusCvr3 ÷ cvr3`. 순위는 문의 1건 비용 오름차순, 같으면 문의 많은 순. 문의 1~2건 상품은 비용이 우연히 낮게 나오므로 문의 수와 L1+ 클릭 비율을 같이 말한다.

## 보고

- `summary.md` 의 문장과 표의 숫자를 그대로 쓴다. 표에 없는 숫자를 만들지 않는다.
- 광고비가 추정이면 "추정"이라고 말한다. 실측과 섞어 더하지 않는다.
- 빠진 항목은 `alibaba-data coverage` 결과(항목·행 수·출처·사유)로 말한다. 빈 값은 빈칸 그대로, 추정해서 채우지 않는다.
