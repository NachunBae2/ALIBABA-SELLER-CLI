// Command registry and runner. Read commands = (seller-center pages to dump) + (report over the dump).
// With --from <dump folder> nothing is fetched; otherwise the engine dumps those pages for the
// requested range (split into windows of at most MAX_WINDOW_DAYS) and the report merges the windows.
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { readFileSync, existsSync, statSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { resolveRange, splitRange } from "./dates.mjs";
import { ONETALK, onetalkMain } from "./onetalk.mjs";
import { formatSends, readSends } from "./sendlog.mjs";
import { flowMain } from "./flow.mjs";
import { loadBundles as bundlesLoad, pickBundles } from "./bundles.mjs";
import { runBundles as bundleRun, formatList as bundleList, bundleSpecs } from "./bundle.mjs";
import { periodGroups, resolveQuery, planWindows, excludeOpenTail, cacheDirs, readAdsBounds, completeAdsCache, CATALOG } from "./periods.mjs";
import { combineMain } from "./combine.mjs";
import { pythonCmd, asideCmd } from "./platform.mjs";
import { latestReportDay, iso } from "./dates.mjs";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
export const ENGINE = path.join(ROOT, "engine");
export const DEFAULT_CONFIG = path.join(homedir(), ".aliops-ops/seller-report/accounts.json");

const INQ = ["inquiries"];
const ADS = ["ads-report", "ads-campaigns"];
const ANALYZE = ["inquiries", "ads-report", "ads-campaigns", "analytics-product", "analytics-keyword"];

export const READ_COMMANDS = {
  "ads adgroups": { liveRead: "ads-adgroups", help: "쇼케이스 광고그룹별 노출·클릭·비용·주문을 볼 때 씁니다." },
  "ads showcase": { liveRead: "ads-showcase", help: "쇼케이스 주문 현황과 운영 슬롯을 볼 때 씁니다." },
  "ads brand": { liveRead: "ads-brand", help: "브랜드 캠페인·소재·제외 설정과 키워드 성과를 볼 때 씁니다." },
  "ads creatives": { liveRead: "ads-creatives", help: "광고 소재의 심사 및 게시 상태를 볼 때 씁니다." },
  "products effect": { liveRead: "products-effect", help: "전 상품의 월간 효과·잠금 상태와 레이더 요약을 볼 때 씁니다." },
  "products growth": { liveRead: "products-growth", help: "육성 대상 상품 목록을 볼 때 씁니다." },
  "funds balance": { liveRead: "funds-balance", help: "수수료 상태·지갑·잔액·미연결 외화를 볼 때 씁니다." },
  "promotions events": { liveRead: "promotions-events", help: "참가 가능한 프로모션과 내 참가 내역을 볼 때 씁니다." },
  "store tasks": { liveRead: "store-tasks", help: "성장·스타 과제 진행과 제재 점수를 볼 때 씁니다." },
  "knowledge files": { liveRead: "knowledge-files", help: "지식베이스 폴더·파일의 메타데이터를 볼 때 씁니다." },

  "funds invoice-bills": { pages: ["funds-invoice-bills"], report: "funds-invoice-bills", menuRead: true, help: "미청구 인보이스 목록 raw; 현재 조회·날짜 옵션 미적용" },
  "funds invoice-history": { pages: ["funds-invoice-history"], report: "funds-invoice-history", menuRead: true, help: "인보이스 발행 이력 raw; 현재 조회·날짜 옵션 미적용" },
  "certifications suggestions": { pages: ["certifications-suggestions"], report: "certifications-suggestions", menuRead: true, help: "권장 인증 목록 raw; 현재 조회·날짜 옵션 미적용" },

  "reviews counts": { pages: ["reviews-counts"], report: "reviews-counts", menuRead: true, help: "리뷰 탭별 건수 raw (리뷰 본문·평점 미포함); 현재 조회·날짜 옵션 미적용" },
  "orders refunds": { pages: ["orders-refunds"], report: "orders-refunds", menuRead: true, help: "환불 사례 목록 raw (바이어 정보 제외); 현재 조회·날짜 옵션 미적용" },
  "orders chargebacks": { pages: ["orders-chargebacks"], report: "orders-chargebacks", menuRead: true, help: "차지백 분쟁 목록 raw; 현재 조회·날짜 옵션 미적용" },
  "promotions discounts": { pages: ["promotions-discounts"], report: "promotions-discounts", menuRead: true, help: "상품 할인 활동 목록 raw; 현재 조회·날짜 옵션 미적용" },
  "promotions coupons": { pages: ["promotions-coupons"], report: "promotions-coupons", menuRead: true, help: "프로모션 쿠폰 목록 raw; 현재 조회·날짜 옵션 미적용" },
  "logistics orders": { pages: ["logistics-orders"], report: "logistics-orders", menuRead: true, help: "물류 주문 목록 raw (주소·연락처 제외); 현재 조회·날짜 옵션 미적용" },
  "funds collected": { pages: ["funds-collected"], report: "funds-collected", menuRead: true, help: "입금 내역 raw (은행·결제 식별 정보 제외); 현재 조회·날짜 옵션 미적용" },
  "funds fees": { pages: ["funds-fees"], report: "funds-fees", menuRead: true, help: "수수료 내역 raw; 현재 조회·날짜 옵션 미적용" },
  "funds withdrawals": { pages: ["funds-withdrawals"], report: "funds-withdrawals", menuRead: true, help: "출금 내역 raw (출금 실행 없음); 현재 조회·날짜 옵션 미적용" },
  "funds settlements": { pages: ["funds-settlements"], report: "funds-settlements", menuRead: true, help: "정산 내역 조회 (서버 오류 시 원문·실패 종료); 현재 조회·날짜 옵션 미적용" },
  "certifications list": { pages: ["certifications-list"], report: "certifications-list", menuRead: true, help: "인증 목록 조회 (빈 본문·서버 오류 시 실패 종료); 현재 조회·날짜 옵션 미적용" },
  "content status": { pages: ["content-status"], report: "content-status", menuRead: true, help: "콘텐츠 역할별 개통 상태 raw; 현재 조회·날짜 옵션 미적용" },

  "keywords index-detail": { pages: ["keyword-index-detail"], report: "keywords-index-detail", discovery: true, required: { flag: "keyword" }, help: "검색어 앞뒤·연관어·국가·추세 raw --keyword '…' [--days 7|30]" },
  "keywords category-top": { pages: ["keyword-category-top"], report: "keywords-category-top", discovery: true, help: "카테고리 인기 검색어 raw [--category-id N] [--days 7|30]" },
  "keywords breakout": { pages: ["keyword-breakout"], report: "keywords-breakout", discovery: true, help: "급상승 검색어 raw [--category-id N] [--days 90|180]" },
  "analytics visitor-keywords": { pages: ["visitor-keywords"], report: "visitor-keywords", discovery: true, help: "방문 바이어 검색어 raw [--days 7|30|90]" },
  "analytics visitor-products": { pages: ["visitor-products"], report: "visitor-products", discovery: true, help: "방문 관심 상품 raw [--days 7|30|90]" },
  "analytics visitor-categories": { pages: ["visitor-categories"], report: "visitor-categories", discovery: true, help: "방문 바이어 카테고리 선호 raw [--days 7|30|90]" },
  "analytics visitor-market-products": { pages: ["visitor-market-products"], report: "visitor-market-products", discovery: true, help: "방문 바이어 전체 관심 상품 raw [--days 7|30|90]" },
  "analytics visitor-groups": { pages: ["visitor-groups"], report: "visitor-groups", discovery: true, help: "바이어 그룹 raw" },
  "analytics visitor-pinned-groups": { pages: ["visitor-pinned-groups"], report: "visitor-pinned-groups", discovery: true, help: "상단 바이어 그룹 raw" },
  "analytics market-categories": { pages: ["market-categories"], report: "market-categories", discovery: true, help: "시장 카테고리 경로 raw" },
  "analytics market-keywords": { pages: ["market-keywords"], report: "market-keywords", discovery: true, help: "시장 검색어 순위 raw [--category-id N] [--days 30]" },
  "analytics market-countries": { pages: ["market-countries"], report: "market-countries", discovery: true, help: "시장 국가 순위 raw [--category-id N] [--days 30]" },
  "analytics market-summary": { pages: ["market-summary"], report: "market-summary", discovery: true, help: "시장 요약 raw [--category-id N]" },
  "analytics market-trend": { pages: ["market-trend"], report: "market-trend", discovery: true, help: "시장 추세 raw [--category-id N]" },
  "analytics seller-benchmark": { pages: ["seller-benchmark"], report: "seller-benchmark", discovery: true, help: "판매자 비교 raw [--category-id N]" },
  "analytics market-products": { pages: ["market-products"], report: "market-products", discovery: true, help: "시장 상위 상품 raw [--category-id N] [--days 30]" },
  "store summary": { pages: ["store-summary"], report: "store-summary", help: "상점 트래픽 요약 raw (일별 기간 선택)" },
  "store trends": { pages: ["store-trends"], report: "store-trends", help: "상점 트래픽 추세 raw (서버 기본 기간; 날짜 옵션 미적용)" },
  "store visitors": { pages: ["store-visitors"], report: "store-visitors", help: "상점 페이지 유형별 방문 상세 raw (일별 기간 선택)" },
  "store today": { pages: ["store-today"], report: "store-today", help: "상점 관리 오늘 지표 raw (현재 스냅샷)" },
  "store rating": { pages: ["store-rating"], report: "store-rating", help: "Star Rating 등급 raw (현재 스냅샷)" },
  "store health": { pages: ["store-health"], report: "store-health", help: "Star Rating 건강도 raw (현재 스냅샷)" },
  "store indicators": { pages: ["store-indicators"], report: "store-indicators", help: "Star Rating 지표 raw (현재 스냅샷)" },
  "store thresholds": { pages: ["store-thresholds"], report: "store-thresholds", help: "Star Rating 등급별 기준 raw (현재 스냅샷)" },
  "store country-rules": { pages: ["store-country-rules"], report: "store-country-rules", help: "Star Rating 국가별 규칙 raw (현재 스냅샷)" },
  "store benefits": { pages: ["store-benefits"], report: "store-benefits", help: "Star Rating 등급별 혜택 raw (현재 스냅샷)" },

  "ads competition-keywords": { pages: ["ads-competition-keywords"], report: "ads-competition-keywords", help: "경쟁 키워드 raw" },
  "ads competition-regions": { pages: ["ads-competition-regions"], report: "ads-competition-regions", help: "경쟁 지역 raw (서버 업무 오류도 표시)" },
  "ads industry-compare": { pages: ["ads-industry-compare"], report: "ads-industry-compare", help: "캠페인 업종 비교 raw" },
  "ads product-benchmark": { pages: ["ads-product-benchmark"], report: "ads-product-benchmark", help: "캠페인별 상품 업종 비교 raw" },
  "ads campaign-countries": { pages: ["ads-campaign-countries"], report: "ads-campaign-countries", help: "캠페인 국가 raw" },
  "ads campaign-advice": { pages: ["ads-campaign-advice"], report: "ads-campaign-advice", help: "캠페인 추천 raw (조회만)" },
  "ads campaign-type-count": { pages: ["ads-campaign-type-count"], report: "ads-campaign-type-count", help: "캠페인 유형별 개수 raw" },
  "ads guaranteed-performance": { pages: ["ads-guaranteed-performance"], report: "ads-guaranteed-performance", help: "캠페인 성과 보장 조건 raw" },
  "ads premium-industry": { pages: ["ads-premium-industry"], report: "ads-premium-industry", help: "추천 상품(최대 20개)의 업종별 집계 raw" },
  "ads recommended-products": { pages: ["ads-recommended-products"], report: "ads-recommended-products", help: "추천 상품 raw (최대 20개)" },
  "ads brand-accounts": { pages: ["ads-brand-accounts"], report: "ads-brand-accounts", help: "브랜드 계정 필터 raw" },
  "ads brand-groups": { pages: ["ads-brand-groups"], report: "ads-brand-groups", help: "브랜드 상품 그룹 raw" },
  "ads brand-report-categories": { pages: ["ads-brand-report-categories"], report: "ads-brand-report-categories", help: "브랜드 보고서 카테고리 raw" },
  "ads product-groups": { pages: ["ads-product-groups"], report: "ads-product-groups", help: "상품 그룹 트리 raw" },
  "inquiries folders": { pages: ["misc-inquiry-folders"], report: "inquiry-folders", help: "문의 폴더·유형 이름/개수 raw (개수 미반환은 빈칸)" },
  "products risk-counts": { pages: ["misc-product-negative"], report: "products-risk-counts", help: "상품 위반·개선 필요 집계 raw" },
  "products power-score": { pages: ["misc-product-power"], report: "products-power-score", help: "상품 성장 점수 전체 목록 raw; 서버 count 대조" },
  "products risk-tasks": { pages: ["misc-product-risk-tasks"], report: "products-risk-tasks", help: "전체 상품 ID를 조회해 위험 작업 집계 raw" },
  "rfq saved-searches": { pages: ["misc-rfq-searches"], report: "rfq-saved-searches", help: "RFQ 저장 검색 전체 목록" },
  "orders list": { pages: ["misc-orders-list"], report: "orders-list", help: "전체 주문 목록 raw (바이어 개인정보 제외); total 대조" },
  "knowledge metrics": { pages: ["misc-knowledge-metrics"], report: "knowledge-metrics", help: "AI 지식베이스 호출·파일 지표 raw" },
  "knowledge storage": { pages: ["misc-knowledge-storage"], report: "knowledge-storage", help: "AI 지식베이스 저장 용량 raw (단위 추정 없음)" },
  "inquiries list": { pages: INQ, report: "inquiry-list", help: "문의 목록(허용 필드만) [--unread] [--type product|company|tm] [--country XX] [--status N] [--limit N]" },
  "inquiries stats": { pages: INQ, report: "inquiry-stats", help: "유형·진입점·기기·국가·답장 상태·일자별 문의 수" },
  "inquiries by-product": { pages: ["inquiries", "ads-report", "analytics-product"], report: "inquiry-by-product", help: "상품ID → 문의 (TM 은 'TM 유입')" },
  "inquiries fields": { pages: INQ, report: "inquiry-fields", help: "문의 목록 응답의 키 이름(값 없음): 바이어 레벨 필드 찾기" },
  "ads daily": { pages: [...ADS, "ads-history"], report: "ads-daily", help: "일자 raw 광고비·노출·클릭·문의·L1+ [--level campaign|product|keyword]" },
  "ads campaigns": { pages: ["ads-campaigns"], report: "ads-campaigns", help: "캠페인 현재 설정 raw: 상태·기간·예산·입찰가·상품·키워드" },
  "ads history": { pages: ["ads-history"], report: "ads-history", help: "기간 내 광고 변경 이력 raw (셀러센터 보존 2개월; 이전 상태 복원 불가)" },
  "ads product-inquiries": { pages: ["ads-report", "inquiries"], report: "ads-product-inquiries", help: "상품 광고 일별 합 → 상품 ID가 일치하는 문의 ID·국가·바이어 등급" },
  "ads report": { pages: ["ads-report"], report: "ads-report", help: "광고 리포트 표: 상품×일, 캠페인×일, 계정×일" },
  "ads tables": { pages: ADS, report: "ads-tables", help: "광고표 전부 그대로: AI·Manual 계정/캠페인/상품/키워드/검색어/전환 명세 표 전 열 + 캠페인 목록 + 상품별 광고비 표, 표마다 받은 행 vs API totalRows(completeness). 덜 받았으면 exit 3, 다시 돌리면 이어 받음" },
  "ads products": { pages: [...ADS, "analytics-product"], report: "ads-products", help: "상품별 일자 raw 광고비·노출·클릭·CPC·전환율·문의 1건 비용" },
  "ads conversions": { pages: ["ads-report", "analytics-product"], report: "ads-conversions", help: "AI 캠페인 전환 명세(상점 전체, 유형 51): 시각·광고/전환 상품·국가·바이어 등급 2종·유형 + 화면 값·광고비÷전환 (상품 이름은 Product Analyzer 제목)" },
  "ads conversion-fields": { pages: ["ads-report"], report: "ads-conversion-fields", help: "AI 전환 명세 응답의 키 이름(값 없음): 유입 채널 열 찾기" },
  "ads quality": { pages: ADS, report: "ads-quality", help: "바이어 품질: L1+ 바이어 클릭 비율(먼저)·문의·L3+·CPC·전환율·문의 1건 비용, 캠페인별·월별·기간 합 (Manual·AI)" },
  "ads analyze": { pages: ANALYZE, report: "ads-analyze", help: "광고 분석 CSV 5개 + summary.md (스킬 alibaba-ad-analysis 가 부름)" },
  keywords: { pages: ["ads-report", "analytics-keyword", "analytics-product"], report: "keywords", kwIndex: true,
    help: "키워드 raw: 광고 Word Report(키워드·검색어)·Product Analyzer 유입 검색어·Keyword Index(검색지수·판매자 규모) [--seed 'serum' …] [--nd 30d] [--no-index]" },
  "keywords recommend": { pages: ["ads-report", "analytics-product"], report: "keywords-recommend", kwIndex: true,
    tables: ["ai.product", "ai.keyword", "ai.searchword", "manual.product", "manual.keyword", "manual.searchword"],
    help: "상품 하나의 추천 키워드 10개, 숫자만(검색량·경쟁·입찰가·노출·클릭·CTR·문의·L1+ → 점수 순) --product <상품ID 또는 이름 일부> (없으면 상품 목록, 전 상품 = --all-products) [--top 10] [--seed …] [--nd 30d]" },
  bids: { pages: ADS, report: "bids", help: "캠페인 입찰가(목표 전환단가)·키워드 입찰가 필드" },
  "analytics products": { pages: ["analytics-product"], report: "analytics-products", granularity: "week",
    help: "Product Analyzer: 상품별 Views·Clicks·CTR·Visitors·찜·공유·비교·문의·문의율·담당자·P4P/KWA + 유입 검색어 [--granularity week|month]" },
  coverage: { pages: [...ANALYZE, "showcase"], report: "coverage", help: "항목별 추출됨(행 수·출처)/없음(사유) 표" },
};

// --transport accio (Accio Work gateway) per alibaba-data command: why it cannot answer. Live check
// 2026-11-17 on the configured seller: the ads, data-advisor, product-metrics and keyword tools answer
// -32003 (seller not verified for the Accio account), and the gateway has no inquiry-list, Keyword Index
// or ad-change-history tool. Only alibaba-inquiry conversations|messages|buyer take accio.
const NOT_VERIFIED = (tools) => `셀러 미인증(-32003): ${tools}`;
const NO_INQ_LIST = "문의 목록 도구 없음(게이트웨이는 OneTalk 대화만: alibaba-inquiry conversations --transport accio)";
const ADS_REPORT = NOT_VERIFIED("icbu_ads_accio_report·icbu_ads_hateoas_query");
export const ACCIO_UNSUPPORTED = {
  "bundle": "번들 실행기는 자식 명령을 api 로 돌린다; Accio 조회 계약 미지원",
  ...Object.fromEntries(Object.entries(READ_COMMANDS).filter(([, spec]) => spec.discovery || spec.menuRead || spec.liveRead)
    .map(([command]) => [command, "저장 쿠키 HTTP 계약(api)으로 구현됨; Accio 조회 계약 미지원"])),
  "store summary": "상점 지표는 저장 쿠키 HTTP 계약만 지원(--transport api)",
  "store trends": "상점 지표는 저장 쿠키 HTTP 계약만 지원(--transport api)",
  "store visitors": "상점 지표는 저장 쿠키 HTTP 계약만 지원(--transport api)",
  "store today": "상점 지표는 저장 쿠키 HTTP 계약만 지원(--transport api)",
  "store rating": "상점 지표는 저장 쿠키 HTTP 계약만 지원(--transport api)",
  "store health": "상점 지표는 저장 쿠키 HTTP 계약만 지원(--transport api)",
  "store indicators": "상점 지표는 저장 쿠키 HTTP 계약만 지원(--transport api)",
  "store thresholds": "상점 지표는 저장 쿠키 HTTP 계약만 지원(--transport api)",
  "store country-rules": "상점 지표는 저장 쿠키 HTTP 계약만 지원(--transport api)",
  "store benefits": "상점 지표는 저장 쿠키 HTTP 계약만 지원(--transport api)",
  "ads competition-keywords": "저장 쿠키 HTTP 전용; 대응 Accio 조회 계약 미검증",
  "ads competition-regions": "저장 쿠키 HTTP 전용; 대응 Accio 조회 계약 미검증",
  "ads industry-compare": "저장 쿠키 HTTP 전용; 대응 Accio 조회 계약 미검증",
  "ads product-benchmark": "저장 쿠키 HTTP 전용; 대응 Accio 조회 계약 미검증",
  "ads campaign-countries": "저장 쿠키 HTTP 전용; 대응 Accio 조회 계약 미검증",
  "ads campaign-advice": "저장 쿠키 HTTP 전용; 대응 Accio 조회 계약 미검증",
  "ads campaign-type-count": "저장 쿠키 HTTP 전용; 대응 Accio 조회 계약 미검증",
  "ads guaranteed-performance": "저장 쿠키 HTTP 전용; 대응 Accio 조회 계약 미검증",
  "ads premium-industry": "저장 쿠키 HTTP 전용; 대응 Accio 조회 계약 미검증",
  "ads recommended-products": "저장 쿠키 HTTP 전용; 대응 Accio 조회 계약 미검증",
  "ads brand-accounts": "저장 쿠키 HTTP 전용; 대응 Accio 조회 계약 미검증",
  "ads brand-groups": "저장 쿠키 HTTP 전용; 대응 Accio 조회 계약 미검증",
  "ads brand-report-categories": "저장 쿠키 HTTP 전용; 대응 Accio 조회 계약 미검증",
  "ads product-groups": "저장 쿠키 HTTP 전용; 대응 Accio 조회 계약 미검증",

  "inquiries folders": "문의 폴더 계약은 저장 쿠키 HTTP API 전용; --transport api 사용",
  "products risk-counts": "상품 위험 집계 계약은 저장 쿠키 HTTP API 전용; --transport api 사용",
  "products power-score": "상품 성장 점수 계약은 저장 쿠키 HTTP API 전용; --transport api 사용",
  "products risk-tasks": "상품 위험 작업 계약은 저장 쿠키 HTTP API 전용; --transport api 사용",
  "rfq saved-searches": "RFQ 저장 검색 계약은 저장 쿠키 HTTP API 전용; --transport api 사용",
  "orders list": "전체 주문 목록 계약은 저장 쿠키 HTTP API 전용; --transport api 사용",
  "knowledge metrics": "지식베이스 지표 계약은 저장 쿠키 HTTP API 전용; --transport api 사용",
  "knowledge storage": "지식베이스 용량 계약은 저장 쿠키 HTTP API 전용; --transport api 사용",
  "inquiries list": NO_INQ_LIST, "inquiries stats": NO_INQ_LIST + "; query_ggs_shop_summary 는 상점 주 합계(지표 다름)",
  "inquiries fields": NO_INQ_LIST, "inquiries by-product": NO_INQ_LIST + "; " + NOT_VERIFIED("data_advisor_shop_product"),
  "ads daily": ADS_REPORT, "ads campaigns": NOT_VERIFIED("icbu_ads_accio_campaign_list·icbu_ads_hateoas_query"),
  "ads history": "광고 변경 이력 도구 없음; " + NOT_VERIFIED("icbu_ads_hateoas_query"),
  "ads product-inquiries": ADS_REPORT + "; " + NO_INQ_LIST, "ads report": ADS_REPORT, "ads tables": ADS_REPORT,
  "ads products": ADS_REPORT, "ads conversions": ADS_REPORT, "ads conversion-fields": ADS_REPORT,
  "ads quality": ADS_REPORT, "ads analyze": ADS_REPORT + "; " + NO_INQ_LIST,
  keywords: NOT_VERIFIED("searchKeywordList·accio_find_keyword_effect") + "; Keyword Index 도구 없음",
  "keywords recommend": NOT_VERIFIED("searchKeywordList·accio_find_keyword_effect") + "; Keyword Index 도구 없음",
  bids: NOT_VERIFIED("icbu_ads_hateoas_query·icbu_ads_accio_campaign_list"),
  "analytics products": NOT_VERIFIED("data_advisor_shop_product·query_product_metrics"),
  coverage: "모든 페이지 합본: 광고·상품 도구 셀러 미인증(-32003), 문의 목록 도구 없음",
  dump: "셀러센터 페이지 덤프: 광고·상품 도구 셀러 미인증(-32003), 문의 목록 도구 없음",
};

// Old names of the inquiry data commands (they lived in alibaba-inquiry before 2026-11-17).
// Still accepted by every CLI; they run the alibaba-data command and say so on stderr.
export const LEGACY = {
  "inquiry list": "inquiries list", "inquiry stats": "inquiries stats",
  "inquiry by-product": "inquiries by-product", "inquiry fields": "inquiries fields",
};

const FLAGS_WITH_VALUE = new Set(["account", "start", "end", "days", "weeks", "granularity", "out", "from", "from-b", "pages", "config", "type", "country", "status", "limit", "max-pages", "dump-dir",
  "text", "text-file", "path", "product-id", "template", "tag", "inquiry-id", "trade-id", "draft-kind",
  "keyword", "category-id", "seed", "nd", "top", "max-seeds", "kw-pages", "product", "level", "flows-dir", "buyer-ali-id", "count", "cursor", "transport", "browser", "profile", "user-data-dir"]);

export function parseArgs(argv) {
  const pos = [], opt = { from: [], seed: [], transport: "api" };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) { pos.push(a); continue; }
    const [k, inline] = a.slice(2).split(/=(.*)/s, 2);
    if (["runner", "shot", "button"].includes(k)) throw new Error(`삭제된 UI 옵션: --${k}`);
    if (FLAGS_WITH_VALUE.has(k)) {
      const v = inline ?? argv[++i];
      if (v === undefined) throw new Error(`--${k} 값이 없다`);
      if (k === "from" || k === "seed") opt[k].push(v); else opt[k] = v;
    } else opt[k] = true;
  }
  if (opt.transport && !["api", "repl", "auto", "accio"].includes(opt.transport)) throw new Error("--transport 는 api|repl|auto|accio");
  return { pos, opt };
}

export function loadAccount(configPath, name) {
  if (!name) throw new Error("--account <계정키> 가 필요하다 (accounts.json 의 키)");
  if (!existsSync(configPath)) throw new Error(`계정 설정 없음: ${configPath} (README 의 accounts.json 절)`);
  const all = JSON.parse(readFileSync(configPath, "utf8"));
  const acc = all[name];
  if (!acc) throw new Error(`계정 ${name} 이 ${configPath} 에 없다 (있는 키: ${Object.keys(all).sort().join(", ")})`);
  if (typeof acc.loginId !== "string" || !acc.loginId.trim()) throw new Error("loginId 가 비어 있다");
  const asideAccount = acc.asideAccount ?? "u0";
  if (!/^u\d+$/.test(asideAccount)) throw new Error("asideAccount 는 u0, u1 … 형식");
  return { ...acc, asideAccount, name };
}

const python = (args, run, stdio = "inherit") => { const [exe, ...pre] = pythonCmd(); return run(exe, [...pre, ...args], { stdio }); };
// Collection progress and summaries belong on stderr; stdout is the final report only.
const pythonDump = (args, run) => python(args, run, ["inherit", 2, "inherit"]);

export function defaultCapture(cmd, args) {
  const r = spawnSync(cmd, args, { encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] });
  if (r.error) throw r.error;
  return { code: r.status ?? 1, stdout: r.stdout || "" };
}

// Keyword Index needs seeds: --seed values, else head terms from our own dump (report.py keyword-seeds).
// The index page is dumped into the last window folder so the report reads it with the rest.
export function dumpKeywordIndex({ account, configPath, dirs, range, opt, run, capture, err }) {
  let seeds = opt.seed.map((x) => x.trim()).filter(Boolean);
  if (!seeds.length) {
    const [exe, ...pre] = pythonCmd();
    const r = capture(exe, [...pre, path.join(ENGINE, "report.py"), "keyword-seeds", ...dirs.flatMap((d) => ["--from", d]),
      "--format", "json", ...(opt["max-seeds"] ? ["--max-seeds", String(opt["max-seeds"])] : []),
      ...(opt.product ? ["--product", String(opt.product)] : [])]);
    if (r.code !== 0) return r.code === 64 ? 0 : r.code; // product not resolved: the report prints the product list
    const rows = (JSON.parse(r.stdout || "{}").keyword_seeds) || [];
    rows.forEach((x) => err(`시드: ${x.seed} — ${x.basis}`));
    seeds = rows.map((x) => x.seed);
  }
  if (!seeds.length) { err("Keyword Index 건너뜀: 시드 없음(--seed 로 지정)"); return 0; }
  if (seeds.some((x) => x.includes("|"))) throw new Error("--seed 에 | 는 못 쓴다");
  // Keep seeded Word Report scopes in separate manifests, alongside the full-range dump.
  const seedRoot = path.join(dirs[0], "keyword-ad-seeds");
  for (const window of splitRange(range.start, range.end)) {
    const directory = path.join(seedRoot, `w-${window.start}_${window.end}`);
    const code = pythonDump([path.join(ENGINE, "seller_central_raw.py"), "--account", account.name, "--config", configPath,
      "--start", window.start, "--end", window.end, "--out", directory, "--pages", "ads-report",
      "--tables", "ai.keyword,ai.searchword,manual.keyword,manual.searchword", "--skip-requests", "--seeds", seeds.join("|"),
      ...(opt.transport ? ["--transport", opt.transport] : [])], run);
    if (code !== 0) return code;
    dirs.push(directory);
  }
  const last = path.join(dirs[0], "keyword-index");
  dirs.push(last);
  const w = range;
  const args = [path.join(ENGINE, "seller_central_raw.py"), "--account", account.name, "--config", configPath,
    "--start", w.start, "--end", w.end, "--out", last, "--pages", "keyword-index", "--seeds", seeds.join("|"),
    "--kw-nd", opt.nd || "30d"];
  if (opt["kw-pages"]) args.push("--kw-pages", String(opt["kw-pages"]));
  if (opt.transport) args.push("--transport", opt.transport);
  if (!opt.fresh && cachedPages(last, w, ["keyword-index"], { seeds, nd: opt.nd || "30d" }).length) {
    err("캐시 사용: keyword-index(같은 시드) — 새로 받으려면 --fresh");
    return 0;
  }
  return pythonDump(args, run);
}

export function defaultRun(cmd, args, opts) {
  const r = spawnSync(cmd, args, opts);
  if (r.error) throw r.error;
  return r.status ?? 1;
}

const localDay = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
export const freshBoundsTimestamp = (fetched, now=new Date()) => {
  const date = new Date(fetched);
  return Number.isFinite(date.getTime()) && localDay(date)===localDay(now) && iso(latestReportDay(date))===iso(latestReportDay(now));
};

// Pages of an earlier dump in `dir` that answer this request again: same page, status ok, same
// granularity / range tables / keyword seeds, and fetched today or >= 3 days after the window end
// (Alibaba stats settle in 1-2 days). --fresh turns it off.
export function cachedPages(dir, w, pages, { granularity, tables, extras, requests, seeds, nd, now = new Date() } = {}) {
  const mp = path.join(dir, "manifest.json");
  if (!existsSync(mp)) return [];
  let man;
  try { man = JSON.parse(readFileSync(mp, "utf8")); } catch (e) { return []; }
  const today = localDay(now);
  const settled = new Date(w.end + "T00:00:00Z");
  settled.setUTCDate(settled.getUTCDate() + 3);
  return (pages || []).filter((key) => {
    const e = (man.pages || []).find((x) => x.key === key);
    if (!e || e.status !== "ok" || !(e.fetchedAt || man.generatedAt) || !existsSync(path.join(dir, "raw", key + ".jsonl"))) return false;
    if (man.start && man.end && (man.start !== w.start || man.end !== w.end)) return false;
    if (key === "ads-report" && man.start && !completeAdsCache(dir, tables, w.start, w.end)) return false;
    if (e.rangeIncomplete && !(key === "ads-report" && tables && man.start)) return false;  // ads tables short of totalRows: fetch again (continues them)
    const day = localDay(new Date(e.fetchedAt || man.generatedAt));
    const page = CATALOG.pages.find(p => p.key === key);
    if ((page?.storeContract || page?.menuRead) && day !== today) return false;
    if (day !== today && day < settled.toISOString().slice(0, 10)) return false;
    if (key === "analytics-product" && (e.granularity || "month") !== (granularity || "month")) return false;
    if (e.rangeTablesFiltered && !(tables && tables.every((t) => (e.rangeTables || []).includes(t)))) return false;
    if (requests && e.requestsFiltered && !requests.every(t => (e.requests || []).includes(t))) return false;
    if (extras && e.extrasFiltered && !extras.every(t => (e.extras || []).includes(t))) return false;
    if (key === "keyword-index" && (JSON.stringify(e.seeds || []) !== JSON.stringify(seeds || []) || e.nd !== nd)) return false;
    return true;
  });
}

// Pages whose last dump in `dir` stopped short of the API totalRows (continued with --resume).
export function incompletePages(dir, pages) {
  const mp = path.join(dir, "manifest.json");
  if (!existsSync(mp)) return [];
  try {
    const man = JSON.parse(readFileSync(mp, "utf8"));
    return (pages || []).filter((key) => (man.pages || []).some((x) => x.key === key && x.rangeIncomplete));
  } catch (e) { return []; }
}

// Live dump of `pages` over the range, one engine run per window. -> list of dump folders.
// Pages already in the window folder (cachedPages) are not fetched again unless --fresh.
export function dumpWindows({ account, configPath, pages, range, root, opt, run, granularity, tables, err = () => {}, today = latestReportDay() }) {
  const dirs = [], selection = [], excluded = [];
  const announced = new Set();
  const available = opt.fresh ? [] : cacheDirs(root);
  const candidatesFor = group => available.flatMap(dir => {
      const selected = group.pages, selectedTables = group.tables.length ? group.tables : undefined;
      const extras = group.extras.length ? group.extras : undefined;
      const requests = group.requests?.length ? group.requests : undefined;
      try {
        const m = JSON.parse(readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
        const w = { start: m.start, end: m.end };
        if (!w.start || !w.end || cachedPages(dir, w, selected, { granularity, tables: selectedTables, extras, requests }).length !== selected.length) return [];
        return [{ ...w, dir }];
      } catch { return []; }
    });
  const groups = periodGroups(pages, tables)
    .filter(g => !["week", "month"].includes(g.period.mode) || g.period.mode === (granularity || "month"))
    .flatMap(group => {
      if (!group.tables.length) return [{ ...group, candidates: candidatesFor(group) }];
      // A complete table in an otherwise incomplete legacy dump remains reusable.
      // Tables sharing the same cached intervals can still be fetched in one engine call.
      const partitions = new Map();
      for (const table of group.tables) {
        const candidates = candidatesFor({ ...group, tables: [table] });
        const key = JSON.stringify(candidates);
        if (!partitions.has(key)) partitions.set(key, { ...group, tables: [], candidates });
        partitions.get(key).tables.push(table);
      }
      return [...partitions.values()];
    });
  for (const group of groups) {
    const selected = group.pages, selectedTables = group.tables.length ? group.tables : undefined;
    const extras = group.extras.length ? group.extras : undefined;
      const requests = group.requests?.length ? group.requests : undefined;
    const windows = planWindows(range, group.period, group.candidates);
    const planned = excludeOpenTail(windows, group.period, today);
    for (const w of planned.excluded) {
      const key = `${w.start}~${w.end}`;
      if (announced.has(key)) continue;
      announced.add(key);
      excluded.push(w);
      err(`안내: ${w.start}~${w.end} 은 아직 마감 전이라 Product Analyzer 미제공 → 제외(마감된 ${w.coverage_start}~${w.coverage_end} 만 집계)`);
    }
    for (const w of planned.windows) {
      const dir = w.dir || (groups.length === 1 && windows.length === 1 ? root : path.join(root, `w-${w.start}_${w.end}${groups.length > 1 ? "--" + (selectedTables?.[0] || extras?.[0] || requests?.[0] || selected[0]) : ""}`));
      selection.push({ dir, pages: selected, tables: selectedTables, extras, requests });
      const pages = selected, tables = selectedTables;
      const cached = opt.fresh ? [] : cachedPages(dir, w, pages, { granularity, tables, extras, requests });
      if (cached.length) err(`캐시 사용(${w.start}~${w.end}): ${cached.join(", ")} — 새로 받으려면 --fresh`);
      const todo = (pages || []).filter((k) => !cached.includes(k));
      if (pages && !todo.length) { dirs.push(dir); continue; }
      const args = [path.join(ENGINE, "seller_central_raw.py"), "--account", account.name, "--config", configPath,
        "--start", w.start, "--end", w.end, "--out", dir];
      if (pages) args.push("--pages", todo.join(","));
      if (tables) args.push("--tables", tables.join(","));
      if (extras) args.push("--extras", extras.join(","));
      if (requests) {
        args.push("--requests", requests.join(","));
        if (!extras) args.push("--skip-extras");
      }
      else if (extras) args.push("--skip-requests");
      if (granularity) args.push("--granularity", granularity);
      if (opt.resume || (!opt.fresh && incompletePages(dir, todo).length)) args.push("--resume");
      if (opt.transport) args.push("--transport", opt.transport);
      if (opt["max-pages"]) args.push("--max-pages", String(opt["max-pages"]));
      const code = pythonDump(args, run);
      if (code !== 0) return { dirs, code, failedWindow: w, excluded };
      dirs.push(dir);
    }
  }
  return { dirs: [...new Set(dirs)], selection, code: 0, excluded };
}

// Inquiry list rows (report.py inquiry-list) for flows: --from dumps, else a fresh inquiries dump
// for --days N (default 30) / --start --end / --all.
export function listInquiryRows({ account, configPath, opt, run, capture, err }) {
  let dirs = opt.from || [];
  const range = resolveRange(opt.start || opt.end || opt.days || opt.weeks || opt.all ? opt : { ...opt, days: "30" });
  if (!dirs.length) {
    const res = dumpWindows({ account, configPath, pages: INQ, range, root: dumpRoot(account, range, opt), opt, run, err });
    if (res.code !== 0) throw new Error(`문의 목록 덤프 실패(종료 코드 ${res.code})`);
    dirs = res.dirs;
  }
  const [exe, ...pre] = pythonCmd();
  const r = capture(exe, [...pre, path.join(ENGINE, "report.py"), "inquiry-list", ...dirs.flatMap((d) => ["--from", d]),
    "--start", range.start, "--end", range.end, "--format", "json"]);
  if (r.code !== 0) throw new Error(`문의 목록 읽기 실패(종료 코드 ${r.code})`);
  return JSON.parse(r.stdout || "{}").inquiries || [];
}

function dumpRoot(account, range, opt) {
  if (opt["dump-dir"]) return opt["dump-dir"];
  const base = account.outDir || path.join(homedir(), "30_Assets", account.name, "seller-report");
  return path.join(base, `sellercentral-raw-${range.start}_${range.end}`);
}

// Two CLIs share this runner. alibaba-inquiry (문의대응) = OneTalk conversation actions only
// (reply, card, file, product card, template, tag). alibaba-data (데이터분석) = every read and
// analysis command, inquiry data included (inquiries list|stats|by-product|fields).
// alibaba-seller (scope "all") keeps every command for old scripts.
export const SCOPES = {
  inquiry: { prog: "alibaba-inquiry", title: "문의대응", shorthand: ["list", "stats", "by-product", "fields"] },
  data: { prog: "alibaba-data", title: "데이터분석", shorthand: [] },
  all: { prog: "alibaba-seller", title: "전체", shorthand: [] },
};
const DATA_ONLY = ["dump", "pages", "combine"];

export function commandScope(cmd) {
  if (cmd === "login" || cmd === "session" || cmd === "help") return "both";
  if (ONETALK[cmd] || cmd === "log" || cmd === "flow") return "inquiry";
  if (cmd === "bundle") return "both";
  if (READ_COMMANDS[cmd] || DATA_ONLY.includes(cmd)) return "data";
  return null;
}

export function main(argv, deps = {}) {
  const { run = defaultRun, log = console.log, err = console.error, scope = "all", capture = defaultCapture } = deps;
  const { pos, opt } = parseArgs(argv);
  if (SCOPES[scope].shorthand.includes(pos[0])) pos.unshift("inquiry");
  const configPath = opt.config || DEFAULT_CONFIG;
  let name2 = pos.slice(0, 2).join(" ");
  const legacy = LEGACY[name2];
  if (legacy) {
    err(`옮김: '${name2}' → alibaba-data ${legacy} (옛 이름도 그대로 동작)`);
    name2 = legacy;
  }
  const cmd = READ_COMMANDS[name2] || ONETALK[name2] ? name2 : pos[0];

  if (!cmd || opt.help || cmd === "help" || (cmd === "bundle" && pos.length === 1)) {
    const helpCommand = cmd === "help" ? pos.slice(1, 3).join(" ") : cmd;
    const spec = scope !== "inquiry" && READ_COMMANDS[helpCommand];
    if (spec?.liveRead) {
      log(spec.help + "\n" + SCOPES[scope].prog + " " + helpCommand +
        " --account <계정키> [--transport api] [--json|--csv] [--out DIR]" +
        (spec.liveRead === "ads-brand" ? " [--start YYYY-MM-DD --end YYYY-MM-DD | --days N]" : ""));
    } else log(usage(scope));
    return 0;
  }
  const owner = commandScope(cmd);
  if (!legacy && scope !== "all" && owner && owner !== "both" && owner !== scope) {
    err(`'${pos.join(" ")}' 은 ${SCOPES[owner].prog}(${SCOPES[owner].title}) 명령이다: ${SCOPES[owner].prog} ${pos.join(" ")} …`);
    return 64;
  }
  if (cmd === "combine") return combineMain(pos.slice(1), opt, log);
  if (opt.transport === "accio" && !ONETALK[cmd]) {
    err(`--transport accio 안 됨(${cmd}): ${ACCIO_UNSUPPORTED[cmd] || "Accio 게이트웨이에 맞는 읽기 도구 없음"}`);
    return 64;
  }
  if (!READ_COMMANDS[cmd]?.liveRead && !deps.run && (["auto", "repl"].includes(opt.transport) || (cmd === "login" && (!opt.browser || opt.browser === "aside")))) {
    process.env.ALIBABA_SELLER_ASIDE ||= asideCmd();
  }
  if (cmd === "pages") return python([path.join(ENGINE, "seller_central_raw.py"), "--list-pages"], run);
  if (cmd === "login" || cmd === "session") {
    loadAccount(configPath, opt.account);
    const base = [path.join(ENGINE, "seller_central_raw.py"), "--account", opt.account, "--config", configPath];
    if (cmd === "session") {
      if (pos[1] !== "status") { err("session 하위 명령은 status 뿐: session status --account <계정키>"); return 64; }
      return python([...base, "--session-status"], run);
    }
    if (!opt.browser) return python([...base, "--login"], run);
    return python([...base, "--browser-login", String(opt.browser), ...(opt.profile ? ["--profile", String(opt.profile)] : []),
      ...(opt["user-data-dir"] ? ["--user-data-dir", String(opt["user-data-dir"])] : []),
      ...(opt["via-cdp"] ? ["--via-cdp"] : []), ...(opt["sign-in"] ? ["--sign-in"] : [])], run);
  }
  const queryRange = (pages, tables) => {
    if (!opt.max && !opt.all) return resolveQuery(opt, pages, tables);
    if (opt.start || opt.end || opt.days != null || opt.weeks != null) throw new Error("--max 와 다른 기간 옵션 중 하나만 쓴다");
    const wanted = pages || CATALOG.pages.map(p => p.key);
    const unsupported = wanted.filter(k => !["ads-report", "ads-campaigns", "inquiries", "analytics-product", "analytics-keyword", "showcase"].includes(k));
    if (unsupported.length) return resolveQuery(opt, wanted, tables); // unknown earliest dates stay explicit
    const today = new Date();
    const day = localDay(today);
    const currentBounds = dir => {
      try {
        const manifest = JSON.parse(readFileSync(path.join(dir, "manifest.json"), "utf8"));
        const fetched = manifest.pages?.find(p => p.key === "ads-report")?.fetchedAt || manifest.generatedAt;
        return fetched && freshBoundsTimestamp(fetched, today) ? readAdsBounds(dir) : null;
      } catch { return null; }
    };
    let bound;
    if (opt.from.length) {
      for (const dir of opt.from) {
        const candidate = currentBounds(dir);
        if (candidate) bound = candidate;
      }
    } else {
      const account = loadAccount(configPath, opt.account);
      const base = account.outDir || path.join(homedir(), "30_Assets", account.name, "seller-report");
      const dir = path.join(base, `sellercentral-bounds-${day}`);
      bound = currentBounds(dir);
      if (opt.fresh || !bound) {
        const code = pythonDump([path.join(ENGINE, "seller_central_raw.py"), "--account", account.name, "--config", configPath,
          "--start", day, "--end", day, "--out", dir, "--pages", "ads-report", "--skip-tables", "--requests", "api.data.dynamic.filter.data.efcec3b1", ...(opt.transport ? ["--transport", opt.transport] : [])], run);
        if (code) throw new Error(`--max 날짜 필터 조회 실패: exit ${code}`);
        bound = readAdsBounds(dir);
      }
    }
    if (!bound) throw new Error('--max: 오늘의 셀러센터 날짜 필터 응답 없음; 최초 날짜 미확인');
    const range = resolveQuery({ ...opt, max: false, all: false, start: bound.start, end: day }, wanted, tables);
    if (bound.end < range.end) range.end = bound.end;
    return range;
  };
  if (cmd === "dump") {
    const account = loadAccount(configPath, opt.account);
    const pages = opt.pages && opt.pages !== "all" ? opt.pages.split(",").map((s) => s.trim()).filter(Boolean)
      : CATALOG.pages.filter(p => !p.discovery).map(p => p.key);
    if (pages.some(key => CATALOG.pages.find(p => p.key === key)?.discovery))
      throw new Error("검색어·방문자·시장 페이지는 조회 조건을 받는 keywords/analytics 전용 명령을 사용");
    const range = queryRange(pages);
    range.warnings.forEach((w) => err("주의: " + w));
    const res = dumpWindows({ account, configPath, pages, range, root: opt.out || dumpRoot(account, range, opt), opt, run, granularity: opt.granularity, err });
    log(JSON.stringify({ query_start: range.start, query_end: range.end, windows: res.dirs, ...(res.excluded?.length ? { excluded: res.excluded } : {}) }));
    return res.code;
  }
  if (cmd === "log") {
    const rows = readSends(loadAccount(configPath, opt.account), { days: opt.days ? Number(opt.days) : null });
    log(opt.json ? JSON.stringify(rows, null, 1) : formatSends(rows));
    return 0;
  }
  if (cmd === "flow") {
    const account = loadAccount(configPath, opt.account);
    const listInquiries = (o) => listInquiryRows({ account, configPath, opt: o, run, capture, err });
    return flowMain(pos.slice(1), opt, { account, configPath, enginePath: path.join(ENGINE, "seller_central_raw.py"), log, err, run, listInquiries, ...(deps.spawn ? { spawn: deps.spawn } : {}), ...(deps.flowDeps || {}) });
  }
  if (cmd === "bundle") {
    // 업무 단위 묶음: 정의는 lib/bundles.json 하나만. 실행은 각 명령을 자식 프로세스로 순서대로.
    const def = bundlesLoad();
    const chosen = pos.slice(1).filter((x) => x !== "list");
    if (pos[1] === "list") return bundleList(def, { json: !!opt.json, log });
    if (opt.resume && chosen.length) { err("--resume 은 그 폴더의 manifest 명령 목록을 쓴다: 번들 이름 없이 실행"); return 64; }
    const picked = pickBundles(def, chosen);
    if (picked.unknown.length) {
      err(`번들 이름: ${def.bundles.map((b) => b.name).join(", ")}, all (모르는 이름: ${picked.unknown.join(", ")})`);
      return 64;
    }
    const account = loadAccount(configPath, opt.account);
    const bundlesOpt = Object.fromEntries([["days", opt.days], ["start", opt.start], ["end", opt.end],
      ["max", opt.max || opt.all], ["fresh", opt.fresh], ["out", opt.out], ["resume", opt.resume],
      ...Object.values(READ_COMMANDS).flatMap((s) => (s.required?.flag ? [[s.required.flag, opt[s.required.flag]]] : []))]
      .filter(([, value]) => value != null && value !== false));
    return bundleRun({
      def, selected: picked.selected, specs: bundleSpecs(READ_COMMANDS, CATALOG.pages), opt: bundlesOpt, account, configPath, log, err,
      ...(deps.bundle ? { deps: deps.bundle } : {}),
    }).then((res) => res.code);
  }
  if (ONETALK[cmd]) {
    const account = loadAccount(configPath, opt.account);
    return onetalkMain(ONETALK[cmd], opt, { account, configPath, enginePath: path.join(ENGINE, "seller_central_raw.py"), run, log, err, ...(deps.spawn ? { spawn: deps.spawn } : {}) });
  }
  const spec = READ_COMMANDS[cmd];
  if (!spec) { err(`모르는 명령: ${pos.join(" ")}\n` + usage(scope)); return 64; }

  if (spec.liveRead) return liveRead(spec, opt, { configPath, run, err });
  if (spec.discovery) return discoveryRead(spec, opt, { configPath, run, err });

  if (cmd.startsWith("store ") || spec.menuRead) {
    if (opt.transport && opt.transport !== "api" && opt.transport !== "auto") { err("이 명령은 --transport api 전용"); return 64; }
    opt.transport = "api";
  }
  const granularity = spec.granularity ? (opt.granularity || spec.granularity) : null;
  if (granularity && !["week", "month"].includes(granularity)) throw new Error("--granularity 는 week 또는 month");
  let dirs = opt.from, range = null, selection = null, excluded = null;
  {
    range = queryRange(spec.pages, spec.tables);
    range.warnings.forEach((w) => err("주의: " + w));
  }
  if (!dirs.length) {
    const account = loadAccount(configPath, opt.account);
    if (!range) range = queryRange(spec.pages, spec.tables);
    const res = dumpWindows({ account, configPath, pages: spec.pages, range, root: dumpRoot(account, range, opt), opt, run, granularity, tables: spec.tables, err });
    if (res.code !== 0) { err(`덤프 실패(종료 코드 ${res.code}, 창 ${res.failedWindow.start}~${res.failedWindow.end})`); return res.code; }
    dirs = res.dirs;
    selection = res.selection;
    excluded = res.excluded?.length ? res.excluded : null;
    // keywords recommend without --product only lists products: no Keyword Index fetch.
    const pickOnly = spec.report === "keywords-recommend" && !opt.product && !opt["all-products"];
    if (spec.kwIndex && !opt["no-index"] && !pickOnly) {
      const code = dumpKeywordIndex({ account, configPath, dirs, range, opt, run, capture, err });
      if (code !== 0) { err(`Keyword Index 덤프 실패(종료 코드 ${code})`); return code; }
    }
  } else {
    for (const d of dirs) if (!existsSync(path.join(d, "raw")) || !statSync(path.join(d, "raw")).isDirectory()) throw new Error(`덤프 폴더가 아님(raw/ 없음): ${d}`);
  }
  const args = [path.join(ENGINE, "report.py"), spec.report, ...dirs.flatMap((d) => ["--from", d])];
  if (range) args.push("--start", range.start, "--end", range.end);
  if (selection) args.push("--selection", JSON.stringify(selection));
  if (excluded) args.push("--excluded", JSON.stringify(excluded));
  args.push("--format", opt.json ? "json" : opt.csv ? "csv" : "table");
  let out = opt.out;
  if (!out && spec.report === "ads-analyze") out = path.join(dirs[0], "analysis", "ad_analysis");
  if (out) args.push("--out", out);
  for (const k of ["limit", "type", "country", "status", "top", "max-seeds", "product", "level"]) if (opt[k] != null) args.push("--" + k, String(opt[k]));
  if (opt["all-products"]) args.push("--all-products");
  if (opt.unread) args.push("--unread");
  if (granularity) args.push("--granularity", granularity);
  return python(args, run);
}

function liveRead(spec, opt, { configPath, run, err }) {
  if (!["api", "auto"].includes(opt.transport) || opt.from.length || opt["dump-dir"]) {
    err("이 명령은 저장 쿠키 API 실조회 전용 (--from/--dump-dir 미지원)");
    return 64;
  }
  const account = loadAccount(configPath, opt.account);
  const args = [path.join(ENGINE, "extra_reads.py"), spec.liveRead, "--config", configPath,
    "--account", account.name, "--format", opt.json ? "json" : opt.csv ? "csv" : "table"];
  if (spec.liveRead === "ads-brand") {
    if (opt.max || opt.all) { err("--max/--all 미지원: --start/--end 또는 --days 지정"); return 64; }
    const range = resolveRange({ ...opt, days: opt.days ?? (opt.start || opt.end || opt.weeks ? undefined : 7) });
    range.warnings.forEach(w => err("주의: " + w));
    args.push("--start", range.start, "--end", range.end);
  } else if (["start", "end", "days", "weeks", "max", "all"].some(k => opt[k] != null)) {
    err("안내: 현재 상태 조회; 기간 옵션은 서버 요청에 적용되지 않음");
  }
  for (const flag of ["out", "limit", "max-pages"]) if (opt[flag] != null) args.push("--" + flag, String(opt[flag]));
  if (opt["max-pages"] != null && !/^[1-9]\d*$/.test(opt["max-pages"])) {
    err("--max-pages: 1 이상 정수 필요");
    return 64;
  }
  return python(args, run);
}

// These APIs select rolling windows (nd) or server snapshots, never historical date ranges.
function discoveryRead(spec, opt, { configPath, run, err }) {
  const page = CATALOG.pages.find(p => p.key === spec.pages[0]);
  const schema = page.discovery;
  for (const flag of ["start", "end", "weeks", "max", "all", "nd", "granularity"])
    if (opt[flag] != null) throw new Error(`--${flag} 미지원: 이 API는 --days 상대기간 또는 서버 스냅샷을 사용`);
  let dirs = opt.from;
  if (dirs.length) {
    for (const flag of ["keyword", "category-id", "days"])
      if (opt[flag] != null) throw new Error(`--from은 저장된 조회 조건을 재사용하므로 --${flag}와 함께 쓸 수 없음`);
    for (const d of dirs) if (!existsSync(path.join(d, "raw"))) throw new Error(`덤프 폴더가 아님(raw/ 없음): ${d}`);
  } else {
    if (opt.transport && opt.transport !== "api") throw new Error("이 명령은 --transport api만 지원");
    const query = {};
    if (opt.days != null || schema.defaultDays != null) {
      query.days = Number(opt.days ?? schema.defaultDays);
      if (!schema.days.includes(query.days)) throw new Error(`--days 지원: ${schema.days.join(", ") || "없음(서버 스냅샷)"}`);
    }
    if (schema.keyword) {
      if (typeof opt.keyword !== "string" || !opt.keyword.trim()) throw new Error("--keyword 필요");
      query.keyword = opt.keyword;
    } else if (opt.keyword != null) throw new Error("--keyword 미지원");
    if (opt["category-id"] != null) {
      if (!schema.category || !/^[1-9][0-9]*$/.test(opt["category-id"])) throw new Error("--category-id는 숫자 카테고리 노드 ID 필요");
      query.categoryId = opt["category-id"];
    }
    const account = loadAccount(configPath, opt.account);
    // Hash the full query so different keyword/category/nd requests never share cache files.
    const signature = createHash("sha256").update(JSON.stringify(query)).digest("hex").slice(0, 16);
    const range = resolveRange({ days: 1 });
    const dir = path.join(dumpRoot(account, range, opt), `${page.key}-${signature}`);
    if (opt.fresh || !cachedPages(dir, range, spec.pages).length) {
      const args = [path.join(ENGINE, "seller_central_raw.py"), "--account", account.name, "--config", configPath,
        "--transport", "api", "--start", range.start, "--end", range.end, "--out", dir,
        "--pages", page.key, "--discovery-options", JSON.stringify(query)];
      if (opt["max-pages"]) args.push("--max-pages", String(opt["max-pages"]));
      const code = pythonDump(args, run);
      if (code !== 0) return code;
    } else err(`캐시 사용: ${page.key} — 새로 받으려면 --fresh`);
    dirs = [dir];
  }
  const args = [path.join(ENGINE, "report.py"), spec.report, ...dirs.flatMap(d => ["--from", d]),
    "--format", opt.json ? "json" : opt.csv ? "csv" : "table"];
  for (const flag of ["out", "limit"]) if (opt[flag] != null) args.push("--"+flag, String(opt[flag]));
  return python(args, run);
}

export function usage(scope = "all") {
  const { prog, title } = SCOPES[scope];
  const inq = scope !== "data", data = scope !== "inquiry";
  const rows = data ? Object.entries(READ_COMMANDS).map(([k, v]) => `  ${k.padEnd(20)} ${v.help}`) : [];
  return [
    ...(data ? [
      `${prog} (${title}) <명령> --account <계정키> --start YYYY-MM-DD --end YYYY-MM-DD [--json|--csv] [--out DIR]`,
      "                       (기본 최근 1달(30일), --days N | --weeks N | --max(--all 별칭), 덤프가 있으면 --account 대신 --from <덤프 폴더> 여러 번)",
      "                       (표별 상한 자동 분할; --max 최초 날짜 미확인 표는 명시 기간 필요)",
      "                       (같은 기간 덤프 폴더에 오늘 받은 페이지가 있으면 다시 안 받음, 새로 받기 = --fresh)",
      "  bundle <ads|products|buyers|orders|store|market|all> --account <계정키> [기간] [--fresh] [--out DIR]",
      "                       업무 묶음 명령을 순서대로 실행해 한 폴더(bundle-<계정>-<기간>-<시각>/)에 모음 = manifest.json",
      "                       (번들 정의 lib/bundles.json · 번들 목록·명령·제외 사유 = bundle list [--json] · 중단 뒤 이어 = --resume <폴더>)",
    ] : [`${prog} (${title}) <명령> --account <계정키> (--inquiry-id N | --trade-id N) [--dry-run]`]),
    scope === "all" ? "  같은 명령을 둘로 나눈 것: alibaba-inquiry(문의대응) · alibaba-data(데이터분석)"
      : `  다른 쪽: ${scope === "inquiry" ? "alibaba-data(데이터분석: 문의 데이터·광고·키워드·입찰·Product Analyzer)" : "alibaba-inquiry(문의대응: OneTalk 답장·명함·PDF/파일·상품 카드·템플릿·태그)"}`,
    ...(data ? ["", "번들 조합 (네트워크 없이 읽고 <번들>/combine/에 JSON·CSV 저장):",
      "  combine products|keywords|daily --from <번들> [--json|--csv]",
      "  combine compare --from <번들 A> --from-b <번들 B> [--json|--csv]",
      "", "읽기·분석 명령 (문의 데이터 = inquiries …):", ...rows] : []),
    ...(scope === "inquiry" ? ["", "  문의 목록·통계·상품별 문의는 alibaba-data inquiries list|stats|by-product 로 옮김",
      "  (옛 이름 alibaba-inquiry list|stats|by-product|fields 도 그대로 동작)"] : []),
    ...(inq ? [
      "",
      "문의창(OneTalk) 조작: 답장·명함·파일·상품 카드·템플릿·태그·대화 목록·대화 읽기·읽음 처리 = OneTalk 내부 API 를 저장된 로그인 쿠키로 직접 호출",
      "  (브라우저·클릭 없음). inspect = 고객 패널 상품·메시지 HTTP 조회.",
      "  명령 실행 = 바로 보내고 대화를 API 로 다시 읽어 새 메시지를 확인한다. 미리보기만 = --dry-run(아무것도 안 보냄).",
      "  보낸 답변은 반드시 로그에 남는다: <계정 outDir>/inquiry-replies.jsonl (바이어 이름·연락처 제외). 대화 = --inquiry-id N | --trade-id N | --buyer-ali-id N(conversations 의 buyerAliId)",
      ...Object.entries(ONETALK).map(([k, v]) => `  ${k.padEnd(20)} ${v.help}`),
      `  ${"log".padEnd(20)} 보낸 기록 보기 [--days N] [--json]`,
      `  ${"flow list".padEnd(20)} 문의대응 플로우 목록(flows/*.json)`,
      `  ${"flow run".padEnd(20)} <이름> (--inquiry-id N | --unanswered [--days N]) [--dry-run] [--include-spam]: 조건 맞으면 단계 순서대로 바로 보냄, 같은 문의에 같은 플로우는 한 번만`,
    ] : []),
    "",
    "덤프·계정:",
    ...(data ? [
      "  dump                 셀러센터 페이지 raw 덤프 [--pages a,b] [--resume] [--max-pages N]",
      "  pages                덤프 가능한 페이지 키 목록",
      "  bundle list          업무 번들(ads|products|buyers|orders|store|market) 이름·언제 쓰는지·명령·제외 사유 표 [--json]",
      "  bundle <이름…|all>   --account <계정키> [--days N|--start D --end D|--max] [--fresh] [--out DIR] [--resume <폴더>]",
    ] : []),
    "  login                키체인 비밀번호로 Aside 로그인(비밀번호는 stdin 으로만)",
    "  login --browser chrome|edge|aside [--profile \"Profile 1\"] [--user-data-dir DIR] [--via-cdp]",
    "                       평소 브라우저에 로그인된 셀러센터 쿠키를 OS 비밀 저장소에 저장(값은 안 찍음)",
    "  login --browser chrome|edge --sign-in",
    "                       전용 프로필 크롬(화면 밖 창)에 키체인 비밀번호로 로그인 → 그 프로필 쿠키 저장, 만료 때 다시 로그인",
    "  session status       저장 쿠키로 읽기 요청 1번: valid|expired|wrong_account|none",
    "",
    "수집 --transport api|repl|auto (기본 api): 저장 쿠키 HTTP 직접 호출, 수집 탭 0",
    "  쿠키 만료 시: alibaba-data login --account <계정키> --browser chrome|edge",
    "  auto·repl은 선택 옵션: auto는 쿠키 오류 시 Aside REPL, repl은 처음부터 Aside REPL",
    "  login --browser chrome|edge|aside   로그인 쿠키를 가져올 브라우저 선택",
    "",
    "선택: Accio Work 연동 (기본·폴백·설치에 필요 없음)",
    "  --transport accio   Accio Work 게이트웨이(alibaba-accio login): alibaba-inquiry conversations|messages|buyer 만",
    "                      (그 밖은 사유 한 줄 + 종료 64: 광고·상품 도구 셀러 미인증(-32003), 문의 목록 도구 없음)",
  ].join("\n");
}
