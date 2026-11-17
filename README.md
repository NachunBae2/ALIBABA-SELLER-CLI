# alibaba-seller-cli

알리바바닷컴 셀러센터 데이터를 명령 하나로 뽑는 CLI. 문의 목록·광고 리포트·키워드·입찰가를 Aside 브라우저(셀러 로그인 세션)로 받아 CSV/JSON 으로 내고, 상품별 광고비·문의 1건 비용·바이어 품질 분석표를 만든다. 데이터 명령은 읽기 전용이다. 광고·입찰·예산을 바꾸는 코드는 없다. 문의창(OneTalk) 답장·명함·파일·상품·템플릿·태그 명령은 기본 dry-run 이고, 실발송은 오너 승인 환경변수와 `--send` 가 둘 다 있을 때만 한다.

```sh
alibaba-seller ads analyze --account <계정키> --start 2026-06-01 --end 2026-11-15
```

## 설치 (다른 Mac)

필요한 것: Node 20 이상, python3(3.9 이상), Aside CLI(`aside`), 셀러 계정 설정 파일. 외부 패키지 설치 없음.

```sh
git clone https://github.com/NachunBae2/ALIBABA-SELLER-CLI.git ~/Documents/Codex/repos/alibaba-seller-cli
cd ~/Documents/Codex/repos/alibaba-seller-cli
npm link                       # alibaba-seller 명령이 PATH 에 생김
alibaba-seller --help
npm test                       # 오프라인 테스트(브라우저·네트워크 안 씀)
```

`npm link` 를 못 쓰면 `node ~/Documents/Codex/repos/alibaba-seller-cli/bin/alibaba-seller.mjs …` 로 바로 부르거나 `alias alibaba-seller=…` 를 건다.

## Aside 연결

- 실데이터 명령은 `aside --host local --account <u0|u1…> repl "<js>"` 로 셀러센터 탭을 열어 그 페이지가 부르는 API 응답을 저장한다. 먼저 `aside --host local repl "console.log(1)"` 이 되는지 확인한다.
- Aside 호출은 한 번에 하나만. 같은 Aside 를 다른 세션·사람이 쓰는 중이면 기다린다(동시 호출 금지).
- Aside 프로필(`u0`, `u1` …)에 그 셀러로 로그인돼 있어야 한다. 세션이 끝났으면 `alibaba-seller login --account <계정키>`.

## 계정 설정 (고객사마다 1줄, 레포 밖)

`~/.aliops-ops/seller-report/accounts.json` (다른 경로는 `--config`):

```json
{
  "<계정키>": {
    "loginId": "<셀러 로그인 ID, 셀러센터 쿠키에 보이는 값>",
    "outDir": "/Users/<나>/30_Assets/<고객사>/seller-report",
    "asideAccount": "u0",
    "keychainService": "aliops-<고객사>-seller",
    "keychainAccount": "<로그인 이메일>"
  }
}
```

```sh
chmod 600 ~/.aliops-ops/seller-report/accounts.json
security add-generic-password -s aliops-<고객사>-seller -a <로그인 이메일> -w     # 비밀번호는 키체인에만
alibaba-seller login --account <계정키>
```

- `loginId` 가 비어 있으면 거부한다. Aside 쿠키에 `loginId` 가 없으면 다른 셀러로 보고 멈춘다(종료 코드 3).
- 비밀번호는 키체인 → 이 프로세스 → Aside stdin → 입력칸으로만 간다. 파일·인자·로그에 남지 않는다.
- 고객사 이름은 코드에 없다. 계정키로만 구분한다.

## 기간

모든 기간 명령은 `--start YYYY-MM-DD --end YYYY-MM-DD` 를 받아 그 값을 광고 리포트 API 의 `beginDateTime`/`endDateTime` 에 그대로 넣는다.

- `--days N` = 오늘 포함 최근 N일, `--weeks N` = 완료된 최근 N주(PST 일~토), `--all` = 최근 181일(셀러센터 효과 리포트 조회 가능 범위). 둘 다 내부에서 start/end 로 바뀐다.
- 형식이 틀리거나 start > end 면 거부. 미래 end 는 오늘로 자르고 경고한다.
- 91일보다 긴 범위는 91일 창으로 쪼개 창마다 덤프하고(`<덤프>/w-<start>_<end>/`), 보고서에서 합친다(같은 상품·캠페인·날짜 행은 한 번만).
- 출력 행마다 `query_start`, `query_end`(실제로 쿼리한 범위)가 붙는다. 광고 날짜는 미국 태평양 날짜(`timeUs`)다.

## 명령

```sh
alibaba-seller inquiry list   --account A --start 2026-11-01 --end 2026-11-15 [--unread] [--type product|company|tm] [--country US] [--status 0] [--limit 20]
alibaba-seller inquiry stats  --account A --start 2026-11-01 --end 2026-11-15
alibaba-seller inquiry by-product --account A --start 2026-06-01 --end 2026-11-15
alibaba-seller inquiry fields --account A --days 7          # 응답 키 이름만(값 없음): 바이어 레벨 필드 찾기
alibaba-seller ads report     --account A --start 2026-06-01 --end 2026-11-15 --out ./ads   # 상품×일·캠페인×일·계정×일 CSV
alibaba-seller ads products   --account A --start 2026-06-01 --end 2026-11-15
alibaba-seller ads analyze    --account A --start 2026-06-01 --end 2026-11-15 [--out DIR]
alibaba-seller ads conversions --account A --start 2026-06-28 --end 2026-11-15
alibaba-seller keywords       --account A --start 2026-06-01 --end 2026-11-15
alibaba-seller bids           --account A --start 2026-06-01 --end 2026-11-15
alibaba-seller coverage       --account A --start 2026-06-01 --end 2026-11-15
alibaba-seller analytics products --account A --start 2026-10-01 --end 2026-11-14 [--granularity week|month]
alibaba-seller analytics products --account A --weeks 4
alibaba-seller dump           --account A --start 2026-06-01 --end 2026-11-15 [--pages ads-report,inquiries] [--resume]
alibaba-seller pages
```

- 출력: 기본은 표(앞 30행), `--json`, `--csv`. `--out DIR` 이면 표마다 CSV 파일.
- 이미 받은 덤프로 다시 계산: `--account` 대신 `--from <덤프 폴더>`(창이 여럿이면 여러 번). 브라우저를 안 쓴다.
  `alibaba-seller ads analyze --from ~/30_Assets/<고객사>/seller-report/sellercentral-raw-20261117 --out /tmp/ad`
- 라이브 실행은 필요한 페이지만 덤프한다(예: `inquiry list` = `inquiries` 페이지, `ads analyze` = 문의·광고 리포트·캠페인·상품 분석·키워드). 덤프 위치: `<outDir>/sellercentral-raw-<start>_<end>/`(`--dump-dir` 로 변경).

### analytics products (Product Analyzer)

- 셀러센터 Analytics > Product Analyzer 와 같은 API(`mydata.alibaba.com/self/.json`, `getVipEffectiveProductsAndStats`, `vip/product/getProdcutSummary`)를 기간마다 부른다. 이 API 는 날짜를 받지 않고 `statisticsType=week|month` + `selected=N`(최신 완료 기간에서 N 기간 전)만 받는다.
- 그래서 `--start/--end` 를 PST 주(일~토, 기본) 또는 달로 덮어 기간마다 부르고 상품별로 더한다. 범위에 일부만 걸린 주·달도 통째로 들어가므로 출력에 `covered_start`/`covered_end`(실제 합산 기간)를 같이 적는다. `--weeks N` = 완료된 최근 N주.
- 최신 완료 주 = 오늘(PST) 직전 토요일로 끝나는 주(화요일 갱신). 요약 응답의 `statDate` 와 계산한 기간 시작일을 `analytics_periods.statDate_matches` 에 대조한다.
- 표: `analytics_summary`(합계), `analytics_periods`(기간별 상품 수·statDate 대조), `analytics_products`(상품별 Views·Clicks·CTR·Visitors·Add to favorite·Shared·Add to compare·Inquiries·Inquiry rate·담당자·P4P/KWA), `analytics_product_keywords`(상품×유입 검색어×기간: views·clicks·p4pViews·p4pClicks·p4pCampaignStatus).
- Visitors 는 기간별 방문자 수의 합이다(여러 기간에 온 같은 방문자는 중복). Inquiry rate = Inquiries ÷ Visitors.

### 지표 정의 (오너 확정)

- 문의(inquiries) = 광고 상품표 `cvr3`(알리바바 비즈니스 기회 수, 화면 Conversions). `mcCnt`(광고 문의)·`atmSessionCnt`(광고 TM 대화)는 내역 열로 따로 둔다.
- `conversion_rate` = 문의 ÷ 클릭. `cost_per_inquiry_usd` = 광고비 ÷ 문의, `cost_per_inquiry_by_cpc_usd` = CPC ÷ conversion_rate. 둘이 다르면 둘 다 남는다.
- 문의 0 이면 conversion_rate 0, 비용은 빈칸, 출처 칸 `no_inquiry`.
- 예: 광고비 $600, 문의 21건 → 문의 1건 $28.57.

### 상품별 광고비

- 상품 광고표(`component_ggs_whole_site_offline_product`)에는 `cost` 가 없다. 덤프 전체에서 날짜가 있는 `productId`+`cost` 표를 찾으면 그 값이 `spend_actual_usd`(출처 = 그 표).
- 없으면 `spend_estimated_usd` = 그날 캠페인 일별표 cost÷clickCnt(CPC) × 그 상품 그날 클릭. 캠페인 일별 클릭이 없는 날은 계정 일별표, 그것도 없으면 기간 평균 CPC. 출처 칸에 어느 표인지 적는다. 실측과 추정은 열이 다르다.
- 기간 없는 productId+cost 표(예: showcase 광고그룹 스냅샷)는 기간을 몰라 쓰지 않고 `coverage` 에 적는다.

### AI 캠페인 전환 명세 (ads conversions)

- 캠페인 상세의 전환 목록 팝업과 같은 표(`component_ggs_ad_shop_conv_detail`, productLineId 110101, campaignType 51)를 `--start/--end` 로 페이지 끝까지 받는다. 캠페인ID 필터가 없는 상점 전체 AI 전환이다.
- 저장하는 필드는 이것뿐: 전환 시각(timeUs/chatTime), 광고 상품(productId·productName), 전환 상품(fbProdId·fbProductName), 국가(countryId), 유입(channelDisplay), 전환 유형(fbType), 바이어 등급(buyerLevel·senderMbrGrowthLevel), isRepeat·isLowQuality·isPunish. 바이어 이름·아바타·프로필 URL·대화 내용·회원번호·fbId·주문 URL 은 브라우저 RAM 에서 버린다(`engine/pages.json` 의 `allow`).
- `ads analyze` 상품표의 `conv_detail_*` 열과 `inquiry_quality` 의 "AI 전환 명세(실측)" 행에 들어간다. AI 캠페인 상품별 광고비는 추정이어도, 상품별 전환 수·바이어 등급은 이 명세로 실측이다(출처 열에 구분).
- `conversion_summary.spend_per_conversion_usd` = 캠페인 일별표 광고비 합 ÷ 명세 건수.

### 입찰가

- AI 캠페인(GGS, 유형 51): `ads-campaigns` 응답 `properties.maxPrice` = 전환 1건 목표가(bidType 4, optimizeTarget whole_site_fb). 예측 문의 비용 = 목표가 그대로.
- 키워드: 광고 키워드 표에 입찰가 열이 있으면 그 값, 없으면 상품(또는 계정) 평균 CPC 를 입찰가 자리에 쓰고 출처에 "입찰가 없음"이라 적는다. 예측 문의 비용 = 입찰가 ÷ 전환율, 전환율은 키워드 → 상품 → 계정 순(클릭 20 이상일 때만), 어느 수준을 썼는지 `rate_level` 칸.
- Manual 캠페인(유형 1,2) 표는 `engine/pages.json` 의 `ads-report.rangeTables` 중 `manual.*` 항목이다. 상품별 cost/bid 가 나오는 계정에서 `coverage` 가 `component_ggs_offline_product`·`component_ggs_offline_keyword` 를 "없음"으로 보이면: 셀러센터 광고 리포트 Manual 탭을 Aside 로 열고 `alibaba-seller dump --pages ads-report` 로 그 탭이 부른 `component/data` 요청의 `tableName`·`filters`·`extendParams.context` 를 `raw/ads-report.jsonl` 에서 읽어 `manual.*` 항목에 넣는다. 코드 수정은 필요 없다.

## 문의창(OneTalk) 조작

```sh
alibaba-seller ui probe     --account A                                   # 툴바 아이콘 hover 툴팁·역할(클릭 0회)
alibaba-seller ui probe     --account A --allow-open                      # + 더보기 메뉴를 열어 항목 텍스트
alibaba-seller tags list    --account A
alibaba-seller template list --account A --allow-open
alibaba-seller draft        --account A --inquiry-id N --allow-open [--button copilot|"draft summary"|"content enhancer"]
alibaba-seller reply        --account A --inquiry-id N --text-file reply.txt          # dry-run
alibaba-seller send-card    --account A --trade-id N
alibaba-seller send-file    --account A --inquiry-id N --path catalog.pdf
alibaba-seller send-product --account A --inquiry-id N --product-id 1600000000000
alibaba-seller template send --account A --inquiry-id N --template "Welcome"
alibaba-seller tag add|remove --account A --inquiry-id N --tag "VIP"
ALIBABA_SELLER_ALLOW_SEND=owner-approved alibaba-seller reply --account A --inquiry-id N --text-file reply.txt --send   # 실발송
```

- 기본은 dry-run: OneTalk 탭을 열어 경고 팝업 X·대화 행·입력칸·Send·툴바 아이콘 위치만 찾고, 클릭·입력·업로드는 0회. 할 일은 결과 `plan` 에 나온다(hover 는 함).
- `--allow-open`: 팝업 닫기·대화 열기·패널/메뉴 열기만 한다(입력·전송 없음). 읽기 명령(template list, draft, ui probe)용.
- 실발송·태그 변경은 `--send` 와 `ALIBABA_SELLER_ALLOW_SEND=owner-approved` 가 둘 다 있어야 한다. 하나라도 없으면 Aside 를 부르기 전에 거부(종료 코드 77).
- 대화 지정은 `--inquiry-id`/`--trade-id`. 먼저 문의 목록 API(읽기)로 그 대화를 찾고, 안 읽은 대화면 `--allow-mark-read` 없이는 거부한다(열면 읽음 처리되므로). 문의 목록은 임시 폴더에 받아 쓰고 지운다.
- 아이콘은 hover 툴팁·aria·title·class·svg 이름으로 찾는다(좌표 고정 없음). 대화 행은 행 속성 값에 문의/거래 ID 가 들어 있는지로 찾는다. 못 찾으면 `conversation_not_located` 와 행 속성 이름 목록을 낸다 → `ui probe` 결과로 맞춘다.
- 답장 본문은 실발송 때만, Aside stdin 으로만 넘긴다(프로세스 인자·로그·결과에 없음). 결과에는 위치·라벨·개수만 나온다.
- 이 조작부는 셀러센터 화면 구조에 기대므로 라이브 확인 전까지 실험 단계다. 먼저 `ui probe` 로 확인한다.

## 바이어 정보

- 문의 목록은 `message.alibaba.com/message/ajax/feedback/subjectList.htm` 만 부른다. 대화를 열지 않아 읽음 처리가 바뀌지 않는다.
- 브라우저 안에서 허용 필드만 남긴다: id·tradeId·시각·출처 코드·유형·상품ID·국가·답장 상태·안읽음·스팸·기기, 그리고 `level/grade` 이름의 짧은 코드 값(예 `L3`). 이름·이메일·전화·제목·본문은 저장하지 않는다. 응답의 키 이름 목록(값 없음)은 `inquiry fields` 로 본다.
- 덤프 결과에는 주문·RFQ·방문자 국가 같은 바이어 데이터가 들어간다. 결과는 고객사 자산 폴더에만 두고 레포·채팅에 원문을 붙이지 않는다.

## 종료 코드

0 정상 · 1 문의창 조작 실패(결과 status) · 2 세션 만료(`login` 후 재실행) · 3 다른 셀러 로그인 · 4 토큰 형태 문자열 발견(결과 삭제됨) · 64 인자 오류 · 77 안전 관문 거부

## 구조

- `bin/alibaba-seller.mjs`, `lib/cli.mjs` 명령 표·계정·기간·창 나누기, `lib/dates.mjs` 기간 해석.
- `engine/seller_central_raw.py` + `engine/capture.js` + `engine/pages.json` 덤프 엔진(Aside repl). `engine/login.js` 로그인.
- `engine/report.py` 덤프 폴더 → 표(문의·광고·키워드·입찰가·분석·coverage). `engine/inquiry_attribution.py` 문의 → 상품 연결 규칙.
- 테스트: `test/*.test.mjs`(node --test), `test/test_*.py`(unittest). 픽스처는 합성 데이터.
