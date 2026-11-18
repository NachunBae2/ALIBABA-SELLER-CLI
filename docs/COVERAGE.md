# 셀러센트럴 커버리지

셀러센트럴 화면이 부르는 데이터를 이 CLI가 얼마나 받는지, 빠진 것은 왜 빠졌는지 적은 표입니다.
실측 계정은 thedozers 하나입니다. 기존 쿠키 세션으로 읽기만 했습니다(2026-11-18).

## 1. 화면 인벤토리

메뉴 트리는 셀러센트럴의 메뉴 API(`i.alibaba.com/ajax/menu/listMenus.do`, `tp_simplified_menu_tree`)에서 그대로 가져왔습니다.
화면 안의 탭은 하나씩 눌러서, 그때 나가는 요청을 캡처했습니다.

| 1단 메뉴 | 화면 | 탭 | 화면 이름 |
|---|---|---|---|
| Home | 1 | 2 | Home |
| Messages | 2 | 0 | Messages, Settings |
| Request for Quotation | 2 | 2 | Request for Quotation, My quotes |
| Orders | 5 | 5 | Orders, All reviews, Refund requests, Credit limit management, Trade service center |
| Products | 5 | 21 | Products, Bulk upload products, Product growth center, High demand products, Tools |
| Logistics | 3 | 2 | Logistics, Logistic orders, Shipping template |
| Store | 3 | 9 | Store, Company profile, Membership Center |
| Growth | 3 | 0 | Growth, Star Ratings, Learning Center |
| Analytics | 5 | 7 | Analytics, Market Analyzer, Visitor Analyzer, Product Analyzer, Keyword Analyzer |
| Marketing | 11 | 37 | Marketing, Performance Ads, Sponsored Brands, Premium Sponsored Ads, Super Brand Card, Search Suggestion Ads, Display Ads Report, Display Ads Diagnosis, Brand Card, Report, Tools |
| Promotions | 6 | 14 | Promotions, Online event, Product Discount, Promotion Coupon, Showcases, Tips video |
| Funds | 5 | 3 | Funds, Collected payments, Withdrawals, Fees, Invoice |
| Knowledge Base | 1 | 3 | Knowledge Base |
| Compliance Management | 3 | 7 | Compliance Management, Certifications, EU Responsible Person |
| Settings | 1 | 0 | Settings |
| **합계** | **56** | **112** | |

API 수는 이렇게 줄여 셌습니다.

- 화면들이 부른 고유 API는 362개입니다.
- 여기서 데이터가 아닌 98개를 뺐습니다. 공통 설정·알림·트래킹·스크립트가 여기에 들어갑니다.
- 남은 **데이터 API는 264개**입니다.

## 2. 커버리지: 데이터 API 264개

| 구분 | 개수 | 내용 |
|---|---|---|
| 명령 있음 | **169** | 읽기 명령 76개(`alibaba-data`)와 문의 명령(`alibaba-inquiry`)이 받음 |
| 제외(이유 있음) | **95** | 아래 표 |

명령 있음 169는 이렇게 계산했습니다.

- 처음 인벤토리 때 명령이 있던 API가 148개였습니다.
- 새 읽기 명령(#34)이 27개를 더했습니다.
- 명령 정리 때 5개를 뺐습니다(아래 표의 '명령 정리' 줄).
- 쇼케이스 회원 API 1개를 뺐습니다(계정 담당자 실명을 돌려줌).
- 148 + 27 − 5 − 1 = 169.

명령 있음 169 중 실계정 실행 결과는 다음과 같습니다.

- 168개는 받습니다.
- 1개(`funds settlements`)는 서버 오류를 냅니다. 화면도 같은 오류(`site config exception.`)를 냅니다. CLI는 원문 그대로 출력하고 실패로 끝납니다.

### 제외 95개와 이유

| 이유 | 개수 |
|---|---|
| 모든 화면에 공통으로 붙는 껍데기(회원 등급, AI 진입 여부 등). 데이터 아님 | 21 |
| 화면 장식·설정용 메타 | 28 |
| 권한·회색 배포·진입 여부 플래그 | 13 |
| 내부 추적·토큰·로그용 | 10 |
| 업로드·내보내기 도구 상태 | 6 |
| 같은 데이터를 다른 경로로 받음(중복) | 5 |
| 빈 응답만 옴(데이터 없음) | 3 |
| 개인정보(연락처) | 1 |
| 설정을 기록하는 요청(읽기 아님) | 1 |
| 개인정보 + 웹소켓 | 1 |
| 개인정보: 쇼케이스 회원 API(`account/member/showcase`)가 계정 담당자 실명을 돌려줌 | 1 |
| 명령 정리: 별점 국가별 규칙, 등급별 혜택 안내, RFQ 저장 검색 조건, 지식베이스 저장 용량, 콘텐츠 채널 개통 여부. 계정 운영 데이터가 아닌 안내·설정 | 5 |
| **합계** | **95** |

### 명령 단위로 뺀 것

명령 단위로 뺀 것은 `lib/bundles.json`의 `excluded`에 이유와 함께 있습니다(19개).
이 중 18개는 명령을 지웠습니다.
`ads competition-regions`는 명령으로 만들지 않았습니다. 화면에서 쓰지 않는 API이고, 브라우저에서 직접 불러도 `isp.internal-error`가 납니다.
API는 남았지만 명령을 뺀 것은 이유가 둘 중 하나입니다.

- 다른 명령과 같은 데이터: `store trends`, `ads campaign-type-count`, `ads premium-industry`, `coverage`, `ads analyze`
- 진단용이라 키 이름만 출력함: `inquiries fields`, `ads conversion-fields`

## 3. 경로 수에 안 잡히는 추가분

- **광고 보고서 하위 탭**은 같은 경로(`component/data`)에 컴포넌트 이름만 바꿔 부릅니다. 그래서 위 264 안에서는 1개로 셉니다. 탭별로 따로 받습니다.
  - 지역·시간대·타겟: `ads segments`
  - 업계 보고서·상품 진단: `ads industry`
  - 7월 화면 값과 비교했고 같습니다. 지역은 96행, 노출 64005, 클릭 1036입니다.
- **쇼케이스 기간 성과**(total·trend·interval)는 `ads showcase`에 표로 있습니다.
- **쓰기**는 `alibaba-ads`가 합니다. 캠페인 이름·상태·예산·국가, 상품 추가·삭제를 바꿉니다.
  - 기본은 dry-run입니다. `--yes`를 줘야 반영되고, 쓰기 전에 자동으로 백업하고, `restore`로 되돌립니다.
  - 실계정에서는 이름 변경 → 원복 → 리드백 일치까지만 시험했습니다. 비용이 드는 조작은 dry-run만 했습니다.

## 4. 서버가 정한 한계 (화면도 같음)

| 데이터 | 한계 | CLI 동작 |
|---|---|---|
| Product Analyzer 상품 목록 | 월 단위는 최근 3개월, 주 단위는 최근 12주 | 범위 밖 기간은 `available=false`, 요약 상품 수만 표시, 종료코드 3 |
| Product Analyzer | 마감 전 기간은 없음 | 마감된 기간만 집계하고 안내 한 줄 |
| 상점 방문 요약·상세 | 약 43일 | 서버가 주는 범위만 받음 |
| 광고 변경 이력 | 2개월 보존 | 기간 안의 이력만 받음 |
| 정산(`funds settlements`) | 화면도 `site config exception.` | 원문 출력, 실패 종료 |
