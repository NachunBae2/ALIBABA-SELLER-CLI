# 셀러센트럴 커버리지

셀러센트럴 화면이 부르는 데이터를 이 CLI가 얼마나 받는지, 빠진 것은 왜 빠졌는지 적은 표입니다.
실측 계정은 셀러 계정 1개입니다. 기존 쿠키 세션으로 읽기만 했습니다(2026-11-18).

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
- **쓰기**는 `alibaba-ads`(광고)와 `alibaba-manage`(광고 외)가 있습니다.
  - `alibaba-ads`(광고 조작: 캠페인 이름·상태·예산·국가, 상품 추가·삭제 등): 오너 지시(2026-11-19)로 전부 잠금·이용 불가. `--yes`·미리보기 모두 종료 69 로 거절하고 아무것도 보내지 않습니다. `campaign create`는 하드 차단입니다. `alibaba-ads backups`(로컬 백업 목록)만 동작합니다.
  - `alibaba-manage`(광고 외: 상품 표시·쇼케이스·RFQ·쿠폰 등)는 기본이 dry-run입니다. `--yes`를 줘야 반영되고, 쓰기 전에 자동으로 백업하고, `restore`로 되돌립니다.
  - 잠금 전 기록(실계정): `alibaba-ads`로 이름 변경 → 원복 → 리드백 일치까지만 시험했습니다. 비용이 드는 조작은 dry-run만 했습니다.

## 4. 셀러센트럴 쓰기: 명령으로 만들지 않은 조작

`alibaba-manage`는 캡처된 요청 형상만 씁니다. captured 로 찍혔어도 아래는 만들지 않았습니다.

| 조작 | 형식 | 이유 |
|---|---|---|
| 상품 편집 제출 / 임시 저장 / 일괄 수정 제출 | captured | 상품 내용 수정은 공식 ICBU API(발행 도구)로 이미 함 — 중복 |
| 일괄 수정 화면 열기 | captured | 화면 열기뿐(요청 없음). 편집은 위와 같은 이유로 제외 |
| 소유자 지정 | captured | 하위 계정 담당자(실명) 관리라 제외 |
| 회사 정보 저장(임시) | captured | 초안 저장만 캡처, 제출 미캡처 — 반영 효과 없음 |
| 지식 기반 파일 업로드 / 외부 웹사이트 동기화 | captured | 파일 삭제·동기화 해제 미캡처 — 원복 불가 |
| 인증서 편집 저장(초안) | captured | 초안 저장만 캡처, 제출 미캡처 — 반영 효과 없음 |
| RFQ 구독 추가 | captured | 원복 형식 미확인 |
| 스타 등급 판매 과제 등록 | captured | 원복 형식 미확인 |
| 쇼케이스 `유효하지 않은 제품 전체 제거` | not-capturable | 버튼 disabled(무효 상품 0개) |
| 배송 템플릿 `신규 노선 자동 반영 스위치` | captured | 캡처 본문이 요금표 앞에서 잘려 바뀐 칸(`insertCarriers` 추정) 미확인 — dry-run 만, `--yes` 64 |
| 스토어 버전 복사 | not-capturable | 요청 미기록 |

### 캡처하지 못한 29개 (not-capturable) 와 사유

| 업무 | 조작 | 사유(specs.json notes) |
|---|---|---|
| 상품 | 기존 상품 복제 등록 | 화면 검증(이미지 미업로드·그룹 미선택)에서 멈춤; 이미지 업로드 자체가 쓰기 |
| 상품 | AI 배치 최적화 제목 적용 | 패널(aipro) 제안 요청부터 차단돼 "제품명에 적용" 단계까지 못 감 |
| 상품 | 일괄 업데이트 RTS / 휴지통 복원 / 재고 | RTS 화면 미로딩, 휴지통 0건, 재고 입력칸 미발견 |
| 쇼케이스 | 유효하지 않은 제품 전체 제거 | 버튼 disabled(무효 상품 0개) |
| 메시지 | 폴더 이동 / 태그 붙이기 / 스팸 처리 / 대화 삭제 / 즐겨찾기 (5) | 목록 hover·우클릭·대화창 더보기에서 해당 컨트롤 미발견 |
| 메시지 | 답장·명함·파일·상품카드 | `alibaba-inquiry` 에 이미 있음(이번 범위 밖) |
| 프로모션 | 제품 할인 만들기 제출 | 상품 선택창 목록 0개("거래 상품만 지원"); 상품 없이 제출은 화면 검증에서 멈춤(요청 0) |
| 배송 | 템플릿 삭제 | 삭제 버튼 UI 없음 |
| 스토어 | 버전 복사 | 실제 서버 반영 1건, 요청은 기록 못 함 |
| 스토어 | 스토어 내 탐색 표시 스위치 | 클릭해도 상태 안 바뀜(요청 0) |
| 스토어 | 새 스마트/수동 리노베이션 만들기·편집기 저장 | 시도 안 함(편집기 별도 앱, 시간) |
| 회사 프로필 | 회사 정보 제출 | "제출" 클릭 반응 없음(요청·검증 메시지 0) |
| 지식 기반 | 지식 기반 파일 삭제 | 파일 0개 |
| 인증서 | 인증서 제출 | "연결할 카테고리 선택" 필수 검증에서 멈춤(요청 0) |
| 인증서 | 인증서 삭제 | "삭제해주세요" 클릭 반응 없음(요청·확인창 0) |
| 인증서 | 인증서 업로드(신규) | 시도 안 함(편집 저장과 같은 화면형) |
| 설정 | 이메일 수신 설정 | 화면 컨트롤 0개로 로딩 |
| 설정 | 이메일·비밀번호 변경 / 하위 계정 추가 | 보안 인증 흐름(인증코드) — 브리프 금지라 시도 안 함 |
| 주문 | 주문 만들기 / Modify Contract / 배정하기 / 환불 응답 | 실주문 있음(미결제 1·완료 3·종료 6, 환불 해결 1). 바이어 대상 실주문이라 시도 안 함; 배정하기 disabled |
| 자금 | 인출(철수) / T/T 계정 / 대량 결제 / 송장 발행 / 주문에 링크 / 내보내기 (6) | 브리프: 목록만, 클릭 금지 |

## 5. 서버가 정한 한계 (화면도 같음)

| 데이터 | 한계 | CLI 동작 |
|---|---|---|
| Product Analyzer 상품 목록 | 월 단위는 최근 3개월, 주 단위는 최근 12주 | 범위 밖 기간은 `available=false`, 요약 상품 수만 표시, 종료코드 3 |
| Product Analyzer | 마감 전 기간은 없음 | 마감된 기간만 집계하고 안내 한 줄 |
| 상점 방문 요약·상세 | 약 43일 | 서버가 주는 범위만 받음 |
| 광고 변경 이력 | 2개월 보존 | 기간 안의 이력만 받음 |
| 정산(`funds settlements`) | 화면도 `site config exception.` | 원문 출력, 실패 종료 |
