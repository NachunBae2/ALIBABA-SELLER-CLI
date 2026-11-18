# 자세히: 명령·옵션·데이터 정의 (개발자용)

알리바바닷컴 셀러용 CLI 세 개와 광고 분석 스킬 하나. `alibaba-data` 는 셀러센터 HTTP API, `alibaba-inquiry` 발송·대화·inspect 명령은 OneTalk 내부 HTTP API(저장된 로그인 쿠키, 브라우저 안 씀), `alibaba-accio` 는 Accio Work 앱의 로컬 게이트웨이를 쓴다(브라우저 안 씀).

| 문의대응 `alibaba-inquiry` = OneTalk 대화창 행동 | 데이터분석 `alibaba-data` = 읽기·분석 전부 | Accio Work `alibaba-accio` = 게이트웨이 읽기 도구 |
|---|---|---|
| 답장·명함 교환·PDF/파일 발송·상품 카드(바로 발송 + API 리드백)·대화 목록·대화 읽기·읽음/안읽음·템플릿·태그 | 문의 데이터(목록·통계·상품별 문의·본 상품·국가·스팸·바이어 등급), 광고 리포트(Manual·AI Smart), AI 전환 명세, Product Analyzer, 키워드, 입찰가, 광고 분석 | 상점 요약·추세·월간 진단·별점, 최근 문의 대화·메시지, 효과 0·저점수 상품, 시장(업종 국가 순위·추세·인기 상품), RFQ 이력 — 읽기 도구만, GGS 셀러 계정 기준 |
| `alibaba-inquiry reply --account A --inquiry-id N --text-file r.txt` | `alibaba-data inquiries list --account A --start 2026-11-01 --end 2026-11-15` | `alibaba-accio login` |
| `alibaba-inquiry send-card --account A --inquiry-id N` | `alibaba-data inquiries stats --account A --days 30` | `alibaba-accio status` |
| `alibaba-inquiry send-file --account A --inquiry-id N --path catalog.pdf` | `alibaba-data inquiries by-product --account A --start 2026-06-01 --end 2026-11-15` | `alibaba-accio shop summary --arg statisticsType=week` |
| `alibaba-inquiry send-product --account A --inquiry-id N --product-id P` | `alibaba-data ads products --account A --start 2026-06-01 --end 2026-11-15` | `alibaba-accio conversations` |
| `alibaba-inquiry template send --account A --inquiry-id N --template "Welcome"` | `alibaba-data keywords recommend --account A --start 2026-06-01 --end 2026-11-15 --product <상품ID 또는 이름 일부>` | `alibaba-accio features --tested 됨` |
| `alibaba-inquiry tag add --account A --inquiry-id N --tag "VIP"` | `alibaba-data ads quality --account A --start 2026-06-01 --end 2026-11-15` | `alibaba-accio call <도구> --arg k=v` |

- 데이터 명령은 읽기만 한다. 광고·입찰·예산·캠페인을 바꾸는 코드는 없다.
- 문의창 발송 명령은 실행하면 바로 보낸다(관문·환경변수 없음). `--dry-run` = 요청 요약만 출력하고 아무것도 부르지 않는다. 명함·파일·상품 카드는 OneTalk 대화 메시지로만 보낸다(메일 등 다른 경로 없음).
- 광고 분석 스킬: [`skills/alibaba-ad-analysis/`](../skills/alibaba-ad-analysis/SKILL.md) (이 레포 안).
- 옛 이름 `alibaba-seller <명령>` 도 그대로 된다(두 CLI 명령 전부).
- 옛 문의 데이터 이름 `alibaba-inquiry list|stats|by-product`(·`inquiry list` …)도 그대로 동작한다. 실제로는 `alibaba-data inquiries …` 를 돌리고 stderr 에 새 이름을 한 줄 알린다.

## 폴더·상품 위험·주문·AI 지식베이스

공통 실행: `alibaba-data <명령> --account <계정키> --transport api --fresh [--json|--csv]`.
`--from <덤프 폴더>`는 HTTP 없이 재사용하며, 재사용 시에도 허용 열과 행 완결성을 검사합니다.
모두 현재 스냅샷입니다. `--start/--end/--days`는 수집 폴더의 요청 구간만 지정하며 서버 데이터를 날짜로 필터링하지 않습니다.
여러 스냅샷을 `--from`으로 넘기면 가장 최근 캡처 하나를 사용합니다.

| 명령 | 응답과 출력 계약 |
|---|---|
| `inquiries folders` | `queryFolders.htm`: 고객 그룹·사용자/시스템 폴더·문의 유형. 분류, 이름, count만 허용. ID·바이어 필드는 저장 전에 제거. count 미반환은 빈칸이며 폴더 수나 ID 수로 대체하지 않음 |
| `products risk-counts` | `product/governance/negative/count`: `governanceType`, `count` raw. 유형 번호를 추정 번역하지 않음 |
| `products power-score` | `asyQueryProductsListWithPowerScore.do`: 상품 id·subject·status·tradeType, `productPowerScoreDetails`의 계산 여부·점수·품질 점수·수정 시각. 모든 페이지를 `count`와 대조 |
| `products risk-tasks` | `asyQueryProductsList.do` 전체 상품을 `count`와 대조한 뒤 100개씩 `productIds`에 바인딩해 `asyQueryRiskTaskCount.do` 조회. 반환된 productId/count만 출력; 빈 map을 상품별 0으로 채우지 않음 |
| `orders list` | `queryOrderList.json`: `orderStatus@all`, 페이지 끝까지 `data.pagination.totalRecord`와 대조. 주문 ID·생성 시각·bizCode·상태·상품 수량·currency·totalAmount.number/numberStr만 허용. buyer/seller 객체·이름·연락처·메모·주소는 저장/출력에서 제외 |
| `knowledge metrics` | `queryKnowledgeMetrics.do`: buyerSearchCallCount·fileCount·smartReceptionCallCount·totalCallCount raw. 호출 지표의 집계 기간은 응답에 없으므로 추정하지 않음 |

RFQ 저장 검색·지식베이스 저장 용량은 설정 메타라 계정 데이터가 아니므로 제외했습니다(사유는 `lib/bundles.json` `excluded`).

모든 표는 응답 값을 그대로 유지합니다. 누락된 칸은 빈칸과 `missing_reason`으로 표시하고, 정상 빈 응답은 stderr에 0행 안내를 냅니다.
`completeness`는 받은 행 수·서버 total·완료 여부를 보여 줍니다. total 없는 단일 응답에는 그 사유를 표시합니다.
total 불일치·변동, 반복 페이지, 페이지 상한 도달은 실패입니다. 기본 안전 상한은 기존 수집기와 같은 600페이지이며 `--max-pages`로 조정할 수 있습니다.
HTTP/업무 오류는 실제 HTTP 상태·code·message/msg를 남기고 exit 5로 종료합니다. 인증/네트워크 오류도 0행 성공으로 바꾸지 않습니다.
상품 API는 화면 Origin 및 `xman_us_t` 내부 ctoken의 URL 바인딩을 사용하며 쿠키/토큰은 덤프에 쓰지 않습니다.

## 노트북 설치 (위에서부터 그대로)

**1. 필요한 것 확인**

```sh
node -v          # v22 이상이어야 한다 (없으면: brew install node)
python3 -V       # 3.9 이상
aside --version  # Aside CLI. 없으면 Aside 앱 설치 후 ~/.local/bin/aside 가 생긴다
```

**2. 받기·설치** (외부 패키지 설치 없음)

```sh
git clone https://github.com/NachunBae2/ALIBABA-SELLER-CLI.git ~/Documents/Codex/repos/alibaba-seller-cli
cd ~/Documents/Codex/repos/alibaba-seller-cli
npm link                 # alibaba-inquiry · alibaba-data · alibaba-accio · alibaba-seller 명령이 PATH 에 생김
alibaba-inquiry --help
alibaba-data --help
npm test                 # 오프라인 테스트(브라우저·네트워크 안 씀)
```

`npm link` 가 권한 오류면 `npm config set prefix ~/.local/npm-global` 후 `export PATH=~/.local/npm-global/bin:$PATH` 를 `~/.zshrc` 에 넣고 다시 `npm link`. 그래도 안 되면 `node ~/Documents/Codex/repos/alibaba-seller-cli/bin/alibaba-data.mjs …` 로 직접 부른다.

**3. Aside 연결 확인**

Aside는 로그인·캡차 해결과 명시적인 REPL HTTP 전송에 사용할 수 있습니다. 기본 데이터·문의 조회는 저장 쿠키를 이용합니다.

```sh
aside --host local repl "console.log(1)"     # 1 이 찍혀야 한다. 응답이 없으면 Aside 앱을 다시 켠다
```

- Aside 프로필 `u0`(두 번째 프로필이면 `u1` …) 브라우저에서 알리바바 셀러센터에 로그인돼 있어야 한다.
- Aside 호출은 한 번에 하나만. 같은 Aside 를 다른 세션이 쓰는 중이면 끝날 때까지 기다린다.

**3-1. 저장 쿠키로 HTTP API 사용** (맥·윈도우)

`alibaba-data login --account <계정키> --browser chrome`으로 로그인 쿠키를 가져옵니다.
이후 데이터 명령은 `--transport api`, 문의 명령은 저장 쿠키로 HTTP API를 호출합니다.
브라우저 선택은 `login --browser chrome|edge|aside`에만 사용합니다.

**4. 계정 설정 파일** — 위치 `~/.aliops-ops/seller-report/accounts.json` (레포 밖. 다른 경로는 `--config`)

```sh
mkdir -p ~/.aliops-ops/seller-report
cat > ~/.aliops-ops/seller-report/accounts.json <<'JSON'
{
  "<계정키>": {
    "loginId": "<셀러 로그인 ID>",
    "outDir": "/Users/<맥 사용자>/30_Assets/<고객사>/seller-report",
    "asideAccount": "u0",
    "keychainService": "aliops-<고객사>-seller",
    "keychainAccount": "<로그인 이메일>"
  }
}
JSON
chmod 600 ~/.aliops-ops/seller-report/accounts.json
```

- `<계정키>` 는 아무 영문 키(예 `mystore`). 명령의 `--account` 에 이 키를 쓴다. 고객사 이름은 코드에 없다.
- `loginId` = 셀러센터 로그인에 쓰는 ID. 코드가 `decodeURIComponent(document.cookie).includes(loginId)`로 Aside 쿠키에 포함되는지 확인한다. 비면 거부하며, 맞지 않으면 다른 셀러로 보고 멈춘다(종료 코드 3). `keychainAccount`는 로그인 이메일이라 다를 수 있다.
- 비밀번호는 키체인에만: `security add-generic-password -s aliops-<고객사>-seller -a <로그인 이메일> -w` (프롬프트에 입력). 파일·인자·로그에 남지 않는다.

**5. 첫 실행**

```sh
alibaba-data login --account <계정키>                                    # 세션이 이미 살아 있으면 건너뜀
alibaba-data inquiries list --account <계정키> --days 7 --limit 5        # 문의 목록 5줄
alibaba-data ads report --account <계정키> --start 2026-11-01 --end 2026-11-15 --out ./ads
```

결과 덤프는 `outDir/sellercentral-raw-<start>_<end>/` 에 쌓인다. 바이어 데이터가 들어 있으니 레포·채팅에 붙이지 않는다.

**5-1. 평소 브라우저의 로그인 가져오기** (Aside 대신 크롬·엣지에 로그인된 셀러센터 세션)

```sh
alibaba-data login --account <계정키> --browser chrome                 # 프로필 자동 선택(loginId 일치 1개)
alibaba-data login --account <계정키> --browser edge --profile "Profile 1"
alibaba-data login --account <계정키> --browser chrome --user-data-dir "<다른 크롬 데이터 폴더>"
alibaba-data login --account <계정키> --browser chrome --sign-in    # 전용 프로필에 키체인 비밀번호로 로그인
alibaba-data session status --account <계정키>                         # {"session":"valid|expired|wrong_account|none", ...}
```

- 그 프로필의 alibaba.com 쿠키만 읽어 저장한다. 화면에는 개수·도메인 수·출처(`chrome:Default`)·`route`(sqlite|cdp)·`store`(keychain|file600|dpapi)·저장 시각만 나온다.
- 저장: macOS = `~/.aliops-ops/seller-report/cookies/<계정키>.bin`(AES-256-GCM, 키는 키체인 `alibaba-seller-cookies`). 쿠키 JSON 은 `security -i` 한 줄 한도(약 4 KB)를 넘어서 키만 키체인에 둔다. Windows = `%APPDATA%\alibaba-seller-cli\cookies\<계정키>.bin`(DPAPI).
- `--profile` 을 빼면 로그인 쿠키가 있고 loginId 가 맞는 프로필을 고른다. 여러 개면 목록을 보이고 종료 코드 64, 없으면 프로필별 일치 여부만 보이고 종료 코드 3.
- Windows: `Local State` 키를 DPAPI 로 풀어 AES-256-GCM 쿠키(`v10`/`v11`)를 읽는다. 크롬 127+ 의 `v20`(앱 바인딩 암호화)은 이 방법으로 못 푼다. 그때는 브라우저를 헤드리스로 임시 프로필 복사본에 띄워 CDP `Storage.getCookies` 로 받는다(`route: cdp`, 맥에서도 `--via-cdp` 로 강제). 브라우저가 켜져 있어 쿠키 파일이 잠기면 같은 CDP 경로로 가고, 복사도 막히면 "브라우저를 닫고 다시"(종료 코드 69).
- 안 되는 경우: macOS 크롬은 CDP 경로에서 복사본 쿠키를 지워 0개를 돌려준다(2026-11-17 실측, 종료 코드 69). 맥은 기본 경로(sqlite)를 쓴다. Windows v20 CDP 경로는 실제 Windows 에서 아직 못 돌려 봤다. `--browser aside` 는 크롬과 같은 방식으로 Aside 프로필의 쿠키 파일(`~/Library/Application Support/Aside/<프로필>/Cookies`, 키체인 `Aside Safe Storage`)을 읽고, `--profile` 이 없으면 loginId 가 맞는 Aside 프로필을 고른다(`aside repl` 에는 쿠키 API 가 없다, 2026-11-18 실측). 키체인 허용 창이 뜨면 '항상 허용'. CDP 경로는 없다.
- 데이터 명령이 로그인 만료를 만나면 저장된 출처 브라우저·프로필에서 쿠키를 다시 읽어 1번만 재시도한다. 또 만료면 종료 코드 3.
- `--user-data-dir DIR`: 기본 크롬·엣지 데이터 폴더 대신 그 폴더의 프로필을 읽는다(`--profile` 과 같이 써도 됨). 저장 출처에 폴더가 남아 만료 때도 그 폴더를 다시 읽는다.
- `--sign-in`(크롬·엣지만): 설치된 크롬을 화면 밖 창(`--window-position=-2400,-2400`, 헤드리스 아님)으로 전용 프로필 `~/.aliops-ops/seller-report/chrome-profiles/<계정키>`(Windows `%LOCALAPPDATA%\alibaba-seller-cli\chrome-profiles\<계정키>`)에 띄우고, 계정 설정의 `keychainService`·`keychainAccount` 비밀번호로 로그인한다(이메일 → Continue → 비밀번호 두 단계 화면도 처리). 비밀번호는 키체인 → 실행기 stdin → 비밀번호 칸으로만 간다. 로그인된 브라우저가 자기 쿠키를 넘겨주고 저장한다(`route: live`). 맥 크롬 158 은 새 프로필 쿠키를 `v11` 로 쓰고, 이건 키체인 "Chrome Safe Storage" 비밀번호로 안 열린다(2026-11-17 실측). 그래서 쿠키 파일(sqlite)을 다시 읽지 않는다. 만료되면 같은 전용 프로필을 다시 열어 로그인이 살아 있으면 그대로, 아니면 1번 더 로그인한다.
- `--sign-in` 이 멈추는 경우(종료 코드 3, 한국어 사유): 슬라이더·캡차(드래그 1번 시도 뒤에도 남음), 2단계 인증 코드 요구, 비밀번호 뒤에도 로그인 화면, 다른 셀러로 로그인됨. 그 전용 프로필을 화면에 띄워 직접 통과한 뒤 같은 명령을 다시 하면 된다. 키체인에 비밀번호가 없으면 종료 코드 3, 브라우저가 없거나 실행이 깨지면 69.
- `engine/cookie_store.py load|refresh <계정키>` 는 부모 프로세스가 파이프로만 읽는 명령이다(쿠키 값을 내보냄). 터미널에서 직접 치지 말 것(터미널이면 거부한다).

**키체인이 멈출 때**: 키체인 호출(`security`)은 15초 안에 답이 없으면 끊고 종료 코드 69 로 안내한다(잠긴 키체인·GUI 없는 SSH 세션에서 승인 창이 안 보이는 경우). 맥 화면에 승인 창이 있는지 보거나 GUI 터미널에서 `security unlock-keychain ~/Library/Keychains/login.keychain-db` 후 다시. 대기 시간은 `ALIBABA_SELLER_KEYCHAIN_TIMEOUT=30`(초). 그동안 쿠키 읽기는 CDP 경로로 넘어가고, 저장은 본인만 읽는 파일 `~/.aliops-ops/seller-report/cookies/<계정키>.json`(권한 600, `store: file600`)로 간다. 읽을 때는 있는 파일 중 최신을 쓴다.

**6. 스킬 연결** (Claude Code·Codex 가 광고 분석 스킬을 쓰게)

```sh
ln -sfn ~/Documents/Codex/repos/alibaba-seller-cli/skills/alibaba-ad-analysis ~/.claude/skills/alibaba-ad-analysis
ln -sfn ~/Documents/Codex/repos/alibaba-seller-cli/skills/alibaba-ad-analysis ~/.agents/skills/alibaba-ad-analysis   # Codex 공유 스킬 폴더가 있으면
```

업데이트: `cd ~/Documents/Codex/repos/alibaba-seller-cli && git pull` (링크라 다시 설치할 것 없음).

## Windows 설치 (PowerShell)

오프라인 테스트는 GitHub Actions `windows-latest` 에서 돈다(`.github/workflows/test.yml`). **실제 Windows + Aside + 셀러센터 실행은 아직 한 번도 못 해 봤다**(Windows 기기 없음). 처음 돌리면 결과를 알려 달라.

```powershell
winget install OpenJS.NodeJS.LTS            # node 22 이상
winget install Python.Python.3.12           # py -3 로 실행됨 (CLI 가 py -3 → python 순으로 자동 선택, ALIBABA_SELLER_PYTHON 으로 지정 가능)
git clone https://github.com/NachunBae2/ALIBABA-SELLER-CLI.git $HOME\Documents\alibaba-seller-cli
cd $HOME\Documents\alibaba-seller-cli
npm link
npm test
alibaba-data --help
```

- 비밀번호·게이트웨이 토큰 저장소: Windows 는 DPAPI(현재 Windows 사용자만 풀 수 있음) 파일 `%APPDATA%\alibaba-seller-cli\secrets\<서비스>@<계정>.dpapi`. 외부 패키지 없음. 넣기(값은 입력으로만, 화면·기록에 안 남음):
   ```powershell
   $p = Read-Host -AsSecureString "셀러센터 비밀번호"
   [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($p)) | py -3 engine\secret_store.py set aliops-<고객사>-seller <로그인 이메일>
   ```
   macOS 는 같은 명령이 로그인 키체인에 넣는다(`security` 와 같은 자리).
- 계정 설정 파일: `$HOME\.aliops-ops\seller-report\accounts.json` (내용은 아래 4단계와 같다. `outDir` 은 `C:\\Users\\<사용자>\\…` 처럼 역슬래시 두 번).
- Aside 데이터 폴더: 기본 `$HOME\.aside` (macOS 와 같은 구조로 가정, 다르면 `$env:ALIBABA_SELLER_ASIDE_HOME` 으로 지정). `aside` 명령은 PATH 에서 찾는다(`aside.cmd` 포함).
- Accio Work: 앱이 `$HOME\.accio\accounts\<id>\.accio\runtime\gateway-cli.json` 를 쓰는 것은 macOS 에서만 확인했다. Windows 위치가 다르면 `$env:ACCIO_HOME` 으로 `.accio` 폴더를 지정.

## 기간

기간 명령은 `--start YYYY-MM-DD --end YYYY-MM-DD`를 해석하고 집계 전 종료일을 자른다. 광고 리포트는 해석된 날짜를 API의 `beginDateTime`/`endDateTime`에 넣는다.

- `--days N` = 미국 태평양 어제까지 최근 N일(셀러센터 `Last N days`와 같은 기간), `--weeks N` = 완료된 최근 N주(PST 일~토), `--all` = 미국 태평양 어제까지 최근 181일(셀러센터 효과 리포트 조회 가능 범위). 모두 내부에서 start/end로 바뀌며, 기간 옵션은 한 종류만 쓴다.
- 형식이 틀리거나 start > end면 거부. 미국 태평양 어제보다 늦은 `--end`는 그날로 자르고 경고한다. 경고 형식: `--end <종료일> 은 아직 집계 전(미국 태평양 어제 <집계일> 까지)이라 <집계일> 로 자름`.
- 91일보다 긴 범위는 91일 창으로 쪼개 창마다 덤프하고(`<덤프>/w-<start>_<end>/`), 보고서에서 합친다(같은 상품·캠페인·날짜 행은 한 번만).
- 출력 행마다 `query_start`, `query_end`(실제로 쿼리한 범위)가 붙는다. 광고 날짜는 미국 태평양 날짜(`timeUs`)다.

## 명령 전체

```sh
alibaba-data inquiries list   --account A --start 2026-11-01 --end 2026-11-15 [--unread] [--type product|company|tm] [--country US] [--status 0] [--limit 20]
alibaba-data inquiries stats  --account A --start 2026-11-01 --end 2026-11-15
alibaba-data inquiries by-product --account A --start 2026-06-01 --end 2026-11-15
alibaba-data ads report     --account A --start 2026-06-01 --end 2026-11-15 --out ./ads   # 상품×일·캠페인×일·계정×일 CSV
alibaba-data ads products   --account A --start 2026-06-01 --end 2026-11-15
alibaba-data ads tables     --account A --start 2026-06-01 --end 2026-11-15 --out ./ads   # 광고표 전부 그대로(AI·Manual 계정/캠페인/상품/키워드/검색어/전환 명세 전 열 + 캠페인 목록 + 상품별 광고비 표) + completeness.csv(표마다 받은 행 vs API totalRows). 덜 받으면 exit 3, 같은 명령 다시 = 이어 받기
alibaba-data ads quality    --account A --start 2026-06-01 --end 2026-11-15   # L1+ 바이어 클릭 비율(먼저)·문의·L3+·문의 1건 비용: 캠페인별·월별·기간 합
alibaba-data ads conversions --account A --start 2026-06-28 --end 2026-11-15
alibaba-data keywords       --account A --start 2026-06-01 --end 2026-11-15 [--seed serum] [--nd 30d] [--no-index]   # 키워드 raw 3종
alibaba-data keywords recommend --account A --start 2026-06-01 --end 2026-11-15 --product <상품ID 또는 이름 일부> [--top 10]   # 그 상품 추천 키워드 10개
alibaba-data keywords recommend --account A --start 2026-06-01 --end 2026-11-15                # --product 없으면 상품 목록만(고르라는 안내)
alibaba-data keywords recommend --account A --start 2026-06-01 --end 2026-11-15 --all-products # 전 상품 일괄
alibaba-data bids           --account A --start 2026-06-01 --end 2026-11-15
alibaba-data analytics products --account A --start 2026-10-01 --end 2026-11-14 [--granularity week|month]
alibaba-data analytics products --account A --weeks 4
alibaba-data combine products --from /path/to/bundle --json
alibaba-data combine keywords --from /path/to/bundle --csv
alibaba-data combine daily --from /path/to/bundle
alibaba-data combine compare --from /path/to/bundle-a --from-b /path/to/bundle-b --json
alibaba-data dump           --account A --start 2026-06-01 --end 2026-11-15 [--pages ads-report,inquiries] [--resume]
alibaba-data pages
```

- 출력: 기본은 표(앞 30행), `--json`, `--csv`. `--out DIR` 이면 표마다 CSV 파일.
- 이미 받은 덤프로 다시 계산: `--account` 대신 `--from <덤프 폴더>`(창이 여럿이면 여러 번). 브라우저를 안 쓴다.
- 캐시: `--account` 로 부를 때 같은 계정·기간 덤프 폴더에 같은 페이지가 이미 있고(상태 ok, 같은 표·시드·주기) 오늘 받았거나 창 끝 +3일 뒤에 받았으면 다시 받지 않는다(stderr `캐시 사용: …`). 새로 받기 = `--fresh`.
- 기간 0 안내(stderr 한 줄): 광고 명령은 광고 계정·캠페인·상품 일별표가 전부 0 이면 `이 기간 광고비·클릭 0(광고 꺼짐)`(GGS 계정 일별표는 광고비를 쓴 날에도 0 이라 캠페인·상품표까지 본다), 문의 명령은 `기간 안 문의 0건(마지막 문의 YYYY-MM-DD)`, `analytics products` 는 `Product Analyzer 검색 노출·클릭 0`.
- 문의 목록 API 는 날짜 조건이 없어 계정의 문의 전부를 페이지로 받는다. 바이어 프로필(문의 1건당 요청 3개)은 `--start..--end` 안(미국 태평양 날짜)에 생성된 문의에만 붙이고, 기간 밖 수는 `skippedOutsideWindow` 로 남긴다. 페이지마다 stderr 에 `inquiries page=N rows=받은/전체 profiles=n` 을 찍는다.
- 라이브 실행은 필요한 페이지만 덤프한다(예: `inquiries list` = `inquiries` 페이지, `ads quality` = 문의·광고 리포트·캠페인·상품 분석·키워드). 덤프 위치: `<outDir>/sellercentral-raw-<start>_<end>/`(`--dump-dir` 로 변경).

### 번들 조합 (combine)

`--from`은 raw 덤프가 아닌 `alibaba-seller-bundle/1` 번들 한 개다. 조합은 `manifest.json`과
`<slug>/result.json`만 읽으며, 계정 설정·쿠키·네트워크를 사용하지 않는다.
`<번들>/combine/<종류>.json`과 `.csv`를 항상 저장한다. 표준 출력은 기본 JSON이며 `--csv`로 바꾼다.
JSON 스키마는 `alibaba-seller-combine/1`이다. `key`는 조합 키, `fields`는 열별 뜻(`ko`)·단위·출처·기간·판단 기준,
`sources`는 읽은 원천 표와 그 행 수, `notes`는 누락·제외·비교 주의점이다.
CSV는 JSON `rows`와 동일한 열/행을 가지며 null은 빈 셀이다. 스키마와 메타데이터는 JSON에서 읽는다.

| 종류 | 결합 키 | 원천과 집계 기준 |
|---|---|---|
| `products` | `product_id` | 광고는 기존에 중복 제거된 `ads report/product_day` 합. 광고 문의는 `mcCnt`. Product Analyzer는 `analytics products/analytics_products`의 실제 `covered_start/end`와 `granularity`를 보존한다. 문의는 목록의 직접 상품 ID, 목록이 없으면 `inquiries by-product`의 숫자 `inflow`만 사용한다. 주문은 상품 ID가 명시된 고유 주문만 집계하며 현재 목록 스냅샷이다. 현재 `orders list` 투영에는 상품 ID가 없어 주문 수는 null이다. |
| `keywords` | `keyword`, `product_id` | `ads tables`의 Word Report 일별 Optional Keywords(`ad_*`)와 Search Terms(`search_term_*`)를 따로 집계한다. 서로 더하지 않는다. 표를 사용할 수 없으면 `keywords/keywords`의 같은 광고 표 투영을 쓰되 광고 문의(`cvr3` 또는 `businessLeadsCnt`)가 없어 문의당 비용도 null이다. 상품 유입 검색어는 `keywordEffect`, 없으면 `analytics_product_keywords`에서 가져온다. 지수는 키워드 기준으로만 붙이며 상품 간 합산하지 않는다. |
| `daily` | `date` | `ads report/account_day` 합, `store summary/store_summary` 날짜별 요약, 문의 목록의 `us_date` 행 수(대체: `inquiries stats`의 같은 차원). 광고·문의 날짜는 미국 태평양 기준이며 상점 서버 날짜는 시간대가 없어 표기 그대로 맞춘다. 서버 조회 범위 밖 상점 셀은 null이다. 계정 광고 값을 상품 합으로 대체하지 않는다. |
| `compare` | `table_kind`와 각 표의 키 | A·B 양쪽 키의 합집합. 원래 날짜를 사용하며 서로 다른 날짜를 순번으로 맞추지 않는다. `period.a/b`는 각 manifest의 원본, `bundle/bundle_b`는 경로다. `<종류>_<열>_a/b/delta/change_rate`는 원값·B−A·(B−A)/A다. 값 없음, 지수 `nd` 불일치, A=0의 사유는 해당 비교 열의 `_reason`에 남긴다. A=0이어도 차이는 계산한다. |

- 상품 ID는 숫자 문자열, 날짜는 `YYYY-MM-DD`다. 키워드는 소문자로 바꾸고 앞뒤 공백만 제거한다. `keyword_raw`에 실제 관측 표기 하나를 보존한다. 상품 ID 없는 광고/지수 행은 제목이나 같은 검색어로 상품을 추정하지 않는다.
- 누락된 표, 실패 및 건너뛴 명령의 원본은 집계하지 않는다. 다른 정상 원천이 없으면 null이며, 성공한 빈 표나 키가 없는 경우도 0으로 채우지 않는다. 집계 대상 일별 행 하나에 값이 없으면 그 지표의 합도 null이다.
- CPC는 광고비 ÷ 클릭, 문의당 광고비는 광고비 ÷ 광고표 문의, `ad_ctr`는 광고 클릭 ÷ 노출, `analyzer_inquiry_rate`는 Analyzer 문의 ÷ 방문자다. 같은 행의 `_num`·`_den`과 계산 불가 `_reason`을 제공한다. 원천의 실제 0과 빈값을 구분한다.
- Product Analyzer 방문자는 마감 주/달의 합이며 기간 간 중복될 수 있다. 기존 Analyzer 보고서에 투영된 값만 읽으므로, 보고서가 원천의 빈값을 0으로 바꿨는지는 번들에서 확인할 수 없다.
- Word Report에서는 중복될 수 있는 시드 필터 행을 합산에서 뺀다. `keywords`의 광고 투영으로 대체할 때 같은 키·일자 행이 여러 개면 캠페인·필터를 구분할 수 없어 그 키의 해당 지표 합은 null이다.
- Keyword Index는 번들의 절대 기간이 아닌 조회 시점의 상대 기간(`keyword_index_nd`)이다. 여러 시드에 반복된 지수는 더하지 않으며 상충하는 값은 null이다. `keyword_index_observed_at`은 번들 명령 종료 시각이며 원천 수집·집계 종료 시각은 아니다.

### analytics products (Product Analyzer)

- 셀러센터 Analytics > Product Analyzer 와 같은 API(`mydata.alibaba.com/self/.json`, `getVipEffectiveProductsAndStats`, `vip/product/getProdcutSummary`)를 기간마다 부른다. 이 API 는 날짜를 받지 않고 `statisticsType=week|month` + `selected=N`(최신 완료 기간에서 N 기간 전)만 받는다.
- 그래서 `--start/--end` 를 PST 주(일~토, 기본) 또는 달로 덮어 기간마다 부르고 상품별로 더한다. 범위에 일부만 걸린 주·달도 통째로 들어가므로 출력에 `covered_start`/`covered_end`(실제 합산 기간)를 같이 적는다. `--weeks N` = 완료된 최근 N주.
- 최신 완료 주 = 오늘(PST) 직전 토요일로 끝나는 주(화요일 갱신). 요약 응답의 `statDate` 와 계산한 기간 시작일을 `analytics_periods.statDate_matches` 에 대조한다.
- 표: `analytics_summary`(합계), `analytics_periods`(기간별 상품 수·statDate 대조), `analytics_products`(상품별 Views·Clicks·CTR·Visitors·Add to favorite·Shared·Add to compare·Inquiries·Inquiry rate·담당자·P4P/KWA), `analytics_product_keywords`(상품×유입 검색어×기간: views·clicks·p4pViews·p4pClicks·p4pCampaignStatus).
- Visitors 는 기간별 방문자 수의 합이다(여러 기간에 온 같은 방문자는 중복). Inquiry rate = Inquiries ÷ Visitors.
- 목록은 최근 월 3·주 12만, 그 밖은 요약 상품 수만: 상품 목록 API(`getVipEffectiveProductsAndStats`)는 최근 월 3개(`selected=1..3`)·주 12개(`selected=1..12`)만 상품을 준다. 더 오래된 기간은 total 이 0 이 아니어도 목록이 비어 있으므로 `analytics_periods` 에 `available: false`, `reason: product_list_api_window` 를 적고 요약 값(`summary_products` = `totalProductCount`, `summary_visit_products` = `totalVisitProductCount`, `summary_inquiry_products` = `totalFbProductCount`)만 채운다. 그런 기간이나 `statDate_matches` 가 false 인 기간이 하나라도 있으면 `analytics_summary.note` 에 이유를 적고 종료코드 3(덜 받음)으로 끝난다.

### 지표 정의 (오너 확정)

- **L1+ 바이어 클릭 비율**(`l1plus_click_pct`, 바이어 품질 첫 지표) = 광고표 `fiGrowthClickCnt`(L1+ Clicks) 합 ÷ `clickCnt` 합 × 100. 일별 비율 평균이 아니라 합으로 다시 계산한다. 상품별(`ads products`)·캠페인별·월별·기간 합(`ads quality`).
- 문의(inquiries) = 광고 상품표 `cvr3`(알리바바 비즈니스 기회 수, 화면 Conversions). `mcCnt`(광고 문의)·`atmSessionCnt`(광고 TM 대화)는 내역 열로 따로 둔다.
- `conversion_rate` = 문의 ÷ 클릭. `cost_per_inquiry_usd` = 광고비 ÷ 문의, `cost_per_inquiry_by_cpc_usd` = CPC ÷ conversion_rate. 둘이 다르면 둘 다 남는다.
- 문의 raw가 0이고 클릭 raw가 양수이면 conversion_rate 0, 비용은 빈칸, 출처 칸 `no_inquiry`.
- 예: 광고비 $600, 문의 21건 → 문의 1건 $28.57.

- 문의 광고 연결은 상품 직접 연결 기록만 사용한다. 상품이 없는 문의는 광고 상품에 배분하지 않으며 "광고 연결 기록 없음"으로 표시한다.

### 상품별 광고비

- 상품 광고표(`component_ggs_whole_site_offline_product`)에는 `cost` 가 없다. 덤프 전체에서 날짜가 있는 `productId`+`cost` 표를 찾으면 그 값이 `spend_actual_usd`(출처 = 그 표).
- 상품별 광고비 raw가 없으면 `spend_actual_usd`는 null, 출처는 "상품별 광고비 raw 없음". CPC·문의당 비용도 빈칸이며 합계는 실제 값만 더한다. 실제 값이 하나도 없으면 합계도 빈칸과 사유로 표시한다.
- 기간 없는 productId+cost 표(예: showcase 광고그룹 스냅샷)는 기간을 몰라 쓰지 않고 수집 완전성 표에 사유로 적는다.

### AI 캠페인 전환 명세 (ads conversions)

- 캠페인 상세의 전환 목록 팝업과 같은 표(`component_ggs_ad_shop_conv_detail`, productLineId 110101, campaignType 51)를 `--start/--end` 로 페이지 끝까지 받는다. 캠페인ID 필터가 없는 상점 전체 AI 전환이다.
- 저장하는 필드는 이것뿐(그리고 응답 행의 키 이름 목록 `fieldNames`, 값 없음): 전환 시각(timeUs/chatTime), 광고 상품(productId·productName), 전환 상품(fbProdId·fbProductName), 국가(countryId), 유입(channelDisplay), 전환 유형(fbType), 바이어 등급(buyerLevel·senderMbrGrowthLevel), isRepeat·isLowQuality·isPunish. 바이어 이름·아바타·프로필 URL·대화 내용·회원번호·fbId·주문 URL 은 브라우저 RAM 에서 버린다(`engine/pages.json` 의 `allow`).
- 열: `conversion_time`(chatTime) · `conversion_date`(chatTime 앞 10자, 시간대 표기 없음. 이 날짜로 기간을 거르고 광고 상품의 그날 클릭 `ad_product_clicks_on_date` 를 붙인다. granularity ALL 이라 timeUs 는 조회 구간 문자열이다) · `ad_product_*` · `converted_scope`(`product` = 전환 상품 있음, `shop` = fbProdId 없음, 상점 단위 전환) · `converted_product_*` · `buyer_level`(buyerLevel) · `growth_level`(senderMbrGrowthLevel) · `channel` · `conversion_type` · `is_repeat`·`is_low_quality`·`is_punish`.
- 상품 이름이 API 에서 `"0"`·빈칸이면 상품 광고표 `subject`, 그다음 Product Analyzer 제목으로 채운다. 광고표 `subject` 가 비어 오는 계정이 있어 `ads conversions` 는 `analytics-product` 페이지도 받는다. 두 곳 모두 제목이 없는 상품은 빈칸이다.
- Product Analyzer(`analytics-product`)는 마감된 주·달만 준다. `--granularity month`(기본) 요청 기간에 마감된 달이 없으면(예: 이번 달 안의 최근 7일) 그 기간의 마감된 주로 대신 조회하고 노트에 `요청 기간에 마감된 달 없음 → 마감된 주 단위로 조회` 를 남긴다. 마감된 주도 없으면 종료 코드 5와 `요청 기간에 마감된 주·달 없음` 이다.
- 바이어 등급은 두 열이다. 화면 전환 명세 카드는 `senderMbrGrowthLevel` 을 보여주므로 `ads conversions` 의 명세 L3+ 비율은 `growth_level` 기준이다.
- `conversion_summary`:
  - `campaign_table_conversions`·`campaign_table_l3plus` = 캠페인 일별표 cvr3·l3PlusCvr3 합(실측, 화면 Conversions·L3+ 와 같은 표). `spend_per_conversion_usd` = 캠페인 광고비 합 ÷ 이 cvr3 합.
  - `detail_rows` = 명세 행 수. 화면 수와 다를 수 있다(반복·처벌 행 포함).
  - `channel_source`: 응답 행에 `channelDisplay` 가 없으면 "없음". 화면 설정에만 있는 이름이다. 저장 전 원본 키는 `engine/pages.json` `ai.conv_detail.allow` 에 넣어 읽는다.
- `ads products` 상품표의 `conv_detail_*` 열에는 명세에 기록된 상품별 전환 수·바이어 등급을 표시한다.

### 키워드 raw·추천

- `keywords` = 키워드 raw 3종을 한 표씩:
  1. 광고 Word Report — Optional Keywords(`component_ggs_offline_keyword`)·Search Terms(`component_ggs_offline_searchword`), AI(유형 4,10)·Manual(1,2), `--start/--end` 일별.
  2. Product Analyzer 상품별 바이어 유입 검색어(`keywordEffect`, 월별) + Keyword Analyzer(`getKeywords`).
  3. Keyword Index(Analytics > Keyword Index, `vip/kwIndex/searchWords`) = `keyword_index` 표: 검색지수(`pv`)·판매자 규모 = 경쟁(`resultCnt`)·클릭률·증감·12기간 추이. 이 API 는 날짜 대신 `--nd`(기본 30d) 창만 받는다.
- Keyword Index 는 시드(머리어)마다 페이지당 100행씩 API 총 행 수까지 받는다. `--kw-pages`는 선택적 상한이며, 상한으로 잘리면 incomplete와 종료 코드 5를 반환한다. 시드 = `--seed`(여러 번), 없으면 우리 데이터에서(최대 `--max-seeds` 5개, 근거는 stderr `시드:` 줄):
  - `--product` 있음: 그 상품 유입 검색어 상위 2개(클릭·노출 순) → 그 상품 제목 단어(그 상품 유입 검색어에 든 것 먼저, 다음은 우리 상품 제목 여러 개에 나오는 단어 = 품목·특성어 먼저, 한 상품에만 있는 이름은 뒤로). 브랜드성 단어(제목 90% 이상) 제외.
  - `--product` 없음(`keywords`·`--all-products`): 우리 상품 유입 검색어 2개 이상에 들어 있고 상품 제목에도 있는 단어. 유입 검색어가 없으면 상품 제목 2개 이상에 있는 단어.
  - `--no-index` 면 받지 않는다. `keywords recommend` 를 `--product` 없이 부르면 상품 목록만 보여 주므로 받지 않는다.
- `keywords recommend --product X` = 그 상품 하나의 추천 키워드 `--top`(기본 10)개. 상품은 상품ID 또는 제목 일부(대소문자 무시), 여러 개가 맞으면 그 목록을 보여 주고 종료 코드 64.
  - 받는 것: Product Analyzer(상품 제목·유입 검색어) + 광고 리포트 중 상품·키워드·검색어표만 + 그 상품 시드의 Keyword Index. 문의 목록·전환 명세는 안 받는다.
  - 표 위 한 줄 = 그 상품의 기간 합(상품 단위): 광고 노출·클릭·CTR·문의·L1+ %(광고 상품표), 검색 유입 노출·클릭(Product Analyzer), 광고 키워드·검색어표의 그 상품 행 수. 키워드별 광고 칸은 광고 키워드·검색어표에 그 상품·키워드 행이 있을 때만 숫자가 찬다.
  - 후보는 그 상품 특성에서만: 그 상품 유입 검색어(Product Analyzer), 그 상품 광고 Word Report 키워드, Keyword Index 검색어 중 단어 전부가 그 상품 제목에 있는 것.
  - Keyword Index 검색량이 없는 후보는 뺀다(검색량 근거 없는 키워드 추천 안 함).
  - 출력 = `키워드 | 점수 | 검색량 | 경쟁(셀러 수) | 입찰가 USD | 노출 | 클릭 | CTR % | 문의 | L1+ %`, 점수 순. 숫자 없는 칸은 빈칸(raw 없음). 맨 아래 한 줄에 점수 공식:
    `점수 = 검색량² ÷ 경쟁 × (1 + 클릭) × (1 + 문의) × (1 + L1+% ÷ 100) ÷ (1 + 입찰가)`, 빈칸은 0으로 계산. 검색량² ÷ 경쟁 = KEI(검색 많고 셀러 적을수록 큼).
  - 숫자 출처: 검색량 = Keyword Index `pv`, 경쟁 = `resultCnt`(`--nd` 창, 기본 30d) · 노출·클릭 = 그 상품의 그 검색어 Product Analyzer `keywordEffect`(기간 합), CTR = 클릭÷노출 · 문의 = 그 상품 광고 Word Report `cvr3` · L1+ % = 그 상품 광고 Word Report `fiGrowthClickCnt ÷ clickCnt` · 입찰가 = 광고 키워드 표의 입찰가 열(없으면 빈칸, CPC 로 대신하지 않음).

### 검색어 상세·방문자·시장 분석

공통: `--account A --transport api [--fresh] [--json|--csv] [--out DIR]`. 저장 쿠키 HTTP만 사용하며 브라우저·Accio로 전환하지 않는다. 아래 기간은 서버 기준 상대기간 `nd`이다. `--start/--end/--weeks/--max/--all/--nd`는 지원하지 않으며 사용하면 요청 전 실패한다. `--from DIR`는 계정 없이 오프라인 재출력하며, 저장된 조건과 충돌하는 `--keyword/--category-id/--days`를 함께 받지 않는다.

| 명령 (`alibaba-data …`) | 옵션·기본값 | 응답 |
|---|---|---|
| `keywords index-detail` | `--keyword 'test widget'` 필수, `--days 7|30` 기본 7 | 앞·뒤·문맥·확장 검색어, 국가, 추세 모듈별 표 |
| `keywords category-top` | `--category-id N`, `--days 7|30` 기본 7 | 인기 검색어 raw |
| `keywords breakout` | `--category-id N`, `--days 90|180` 기본 90 | 급상승 검색어 raw, 빈 마지막 페이지까지 수집 |
| `analytics visitor-keywords` | `--days 7|30|90` 기본 30 | 방문 바이어의 상점 검색어 (`kwType=shop`) |
| `analytics visitor-products` | 동일 | 방문 관심 상품 |
| `analytics visitor-categories` | 동일 | 카테고리 선호 (`dimName=cate`) |
| `analytics visitor-market-products` | 동일 | 전체 관심 상품 (`dimName=total_ab_prod`) |
| `analytics visitor-groups` | 서버 스냅샷 | 바이어 그룹 |
| `analytics market-keywords` | `--category-id N`, `--days 30` | 시장 검색어 순위 |
| `analytics market-countries` | 동일 | 국가 순위 |
| `analytics market-summary` | `--category-id N`, 서버 스냅샷 | 시장 요약 |
| `analytics market-trend` | 동일 | 서버가 반환한 추세·`statDate` |
| `analytics seller-benchmark` | 동일 | 판매자 구성·비율 raw |
| `analytics market-products` | `--category-id N`, `--days 30` | `blueRank`, `duvRank`, `abRank` 별도 표와 `count` 메타데이터 |

- 자동 카테고리: `category-top`은 `getTopCateLv3Id` 첫 행의 마지막 숫자 노드, `breakout`은 같은 첫 행의 첫 노드, 시장 분석은 `getTopLeafCateId` 첫 행의 첫 노드. 전체 카테고리 합계가 아니다. `--category-id`를 주면 목록 조회를 생략한다. 경로 전체(`상위 > 하위`)는 ID로 전달하지 않는다.
- 방문자 조회 조건은 `byrGroup=visit`, `byrGrowthLevel=TOTAL`, `isDistinctCrossDay=Y`. 그룹 조회에는 기간 인자가 없다. 방문자 페이지는 `recordCount`와 대조한다. `--max-pages N`(기본 30)까지도 미완료면 종료 5이며 `--from` 재생도 실패 상태를 유지한다.
- 값은 응답 필드 이름·값 그대로다. 검색지수를 검색 횟수로 환산하거나 비율·순위를 재계산하지 않는다. 중첩 상품 목록도 그대로 보존한다. 행별 누락/null 필드는 빈칸과 `_missing_reason`을 출력한다. 빈 배열은 stderr에 0행 안내를 출력한다.
- `request_context`는 실제 `nd`, 검색어, 카테고리 등 조회 조건이다. 덤프 `manifest.json`의 날짜는 수집 관리용이며 서버의 상대기간/추세 날짜를 대체하지 않는다. 응답에 없는 기간 시작·종료일을 추정하지 않는다.
- 성공은 HTTP 200과 업무 코드 0/200으로 판정한다. 1000·1003·302 등은 실제 코드·메시지를 출력하고 종료 5, 오류 응답은 덤프에 남긴다. 키워드 조회 제한 `isExceed=true`도 실패다. 카테고리 목록이 비었거나 응답 구조가 다르면 성공으로 표시하지 않는다.
- 이 16개 명령의 수집 페이지는 `keywords/analytics` 전용 명령으로 선택한다. 일반 `dump` 전체 수집에서는 제외하며 `dump --pages`로 지정하면 전용 명령을 안내한다. 키워드·카테고리·기간별 캐시를 분리하며 같은 조회의 당일 캐시만 재사용한다.

번들 계약 근거: [Keyword Index](https://s.alicdn.com/@g/assets-group/ida-dataportal2-industry/0.0.268/pages/keyword-index-sale/index.js), [Visitor Analyzer](https://s.alicdn.com/@g/assets-group/ida-dataportal2/3.0.25/pages/adviser-buyer/index.js), [Market Analyzer](https://s.alicdn.com/@g/ma-spa/data-analysis-page/1.0.51/js/p_market-analyzer-index.js). 기간 지원은 해당 화면이 실제 전달하는 값으로 제한한다. 시장 순위/상품은 30일, 시장 상세 3종은 `cateId`만 전달한다.

### 입찰가

- AI 캠페인(GGS, 유형 51): `ads-campaigns` 응답 `properties.maxPrice` = 전환 1건 목표가(bidType 4, optimizeTarget whole_site_fb).
- 키워드: 광고 키워드 표에 입찰가 raw가 있으면 그 값, 없으면 null과 "키워드 입찰가 raw 없음". 전환율·CPC·문의 비용은 해당 행의 실제 분자·분모로만 계산한다. 입찰가는 실제 CPC로 쓰지 않는다.
- Manual 캠페인(유형 1,2) 표는 `engine/pages.json`의 `ads-report.rangeTables` 중 `manual.*` 항목이다. 수집 완전성 표가 해당 표를 "없음"으로 표시하면 실제 HTTP 요청·응답에서 `tableName`·`filters`·`extendParams.context`를 확인한 뒤 고정 수집 계약을 갱신한다. `dump --pages ads-report`는 등록된 계약만 실행하며 브라우저 탭의 네트워크 요청을 캡처하지 않는다.

## Accio Work (`alibaba-accio`) — GGS 셀러 계정 기준

Accio Work 데스크톱 앱이 에이전트용으로 띄우는 로컬 게이트웨이를 그대로 부른다. Aside·셀러센터 쿠키를 쓰지 않는다.

- 게이트웨이: `http://localhost:4097` (앱이 정함). 도구 목록 `GET /mcp/proxy/tools`, 호출 `POST /mcp/proxy {name, arguments, trace:{connectId}}`, 인증 HTTP Basic `phoenix:<비밀번호>`.
- 비밀번호 출처: 앱이 켤 때마다 새로 만들어 `~/.accio/accounts/<Accio 계정>/.accio/runtime/gateway-cli.json` 에 적는다. `alibaba-accio login` 이 그 값을 OS 비밀 저장소(맥 키체인 / Windows DPAPI, 서비스 `aliops-accio-gateway`)로 옮기고, 이후 호출은 저장소에서 읽는다. 앱을 다시 켜서 401 이 나면 앱 파일에서 한 번 다시 옮기고 재시도한다. 값은 출력·로그·레포에 안 남는다.
- connectId: 앱의 `connected-accounts.json` 에 연결된 알리바바 계정(`alibaba:<connector_id>`). 여럿이면 `--connector <이름 일부>`.

**데이터 경로 (기본 = 게이트웨이, 폴백 = 직결)** — `alibaba-data` 읽기 명령은 `--transport` 를 생략하면 `seller-gw` MCP 서버를 먼저 거친다. 서버는 레포 HEAD 사본(`~/.aliops-ops/seller-gw/cli`)의 CLI 를 `--transport api` 로 실행하므로 같은 엔진·같은 출력 계약(`--json/--csv/--out`, 종료코드)이다. 게이트웨이가 꺼졌거나 서버가 미등록·HTTP 오류면 stderr 에 `안내: 게이트웨이 경유 실패(<원문>) → 직결(api)로 받음` 한 줄을 찍고 직결로 받는다. 명령 자체의 rc(3·5·64)는 폴백하지 않는다. `--limit/--seed/--product/--granularity` 처럼 게이트웨이 호출이 나르지 않는 옵션이 붙은 읽기는 조용히 직결한다.

```sh
alibaba-accio attach    # HEAD 사본+서버를 ~/.aliops-ops/seller-gw 에 두고 API 등록(POST/PUT) 후 /test → connected, toolCount 2
alibaba-accio detach    # enabled:false PUT (등록은 남김; DELETE 는 409)
```

- 게이트웨이는 한 번의 도구 결과가 크면 문자열을 중간에서 자른다. `seller-gw` 는 200k 를 넘는 답을 `runs/<job>.json` 에 원자적으로 쓰고 `result_file` 만 돌려주며, CLI 가 그 파일을 읽어 전체 stdout 을 출력한다.
- 게이트웨이에 같은 도구 이름(`seller_central_read`)을 노출하는 서버가 또 있으면 먼저 등록된 쪽이 답한다. `alibaba-data` 는 `POST /mcp/proxy` 에 `server:"seller-gw"` 를 함께 보내 자기 등록으로 고정한다.



```sh
# Accio Work 앱 실행 + 알리바바 셀러 계정 연결(앱 안에서)
alibaba-accio login     # {"login":"ok","secret_store":"keychain","seller_tools":285 …}
alibaba-accio status    # session ok, 연결 계정 수, 도구 수
```

**명령**

```sh
alibaba-accio features [--tested 됨] [--search ggs] [--read-only] [--format csv]   # 기능 표(아래)
alibaba-accio tools [--search shop]          # 지금 게이트웨이의 셀러 도구(호출 가능 여부)
alibaba-accio schema <도구>                   # 도구 설명·인자
alibaba-accio shop summary|trends|report|metrics|merchant|star-rating|risk [--arg statisticsType=week]
alibaba-accio shop diagnosis --arg dailyScenarioCode=AGENTIC_DIAGNOSIS_PRODUCT_D   # OPPORTUNITIES·PRODUCT·STAR_RANK·TRAFFIC
alibaba-accio conversations [--arg limitTimeStamp=<앞 결과 nextTimeStamp>]   # 최근 문의 대화 20개(읽기만, 읽음 처리 안 함, 화면으로만)
alibaba-accio messages --arg conversationId=… --arg startTime=<ms> --arg endTime=<ms>   # 최근 30일만
alibaba-accio products zero-effect | low-score [--arg minScore=0 --arg maxScore=5] | by-name --arg productName=<단어>
alibaba-accio product info --arg productId=…   # 기본 queryType=trunk, 제목·이미지·속성·SKU·MOQ·가격·키워드·포장
alibaba-accio product category --arg imageInfo='["<이미지 URL>"]'   # textInfo 만 주면 게이트웨이가 [] 를 준다(2026-11-17 실측)
alibaba-accio market hot-products --arg query=… | opportunity | country-rank|trend|detail|buyer-profile --arg cateId=<카테고리 id>
alibaba-accio rfq history
alibaba-accio call <도구> --arg k=v | --args '{…}'   # 읽기 도구 아무거나
```

- 기능 표 = `accio/features.json`: 게이트웨이 셀러 도구 285개(toolkit `alibaba` 228 + `alibaba-ggs` 57) × 읽기/쓰기 × 2026-11-16 검증 계정 읽기 실호출 결과(됨 76 · 안 됨 102(그중 -32003 판매자 미인증 89) · 인자 오류 5 · 미호출 102). `alibaba-accio features` 가 그 표에 명령 칸을 붙여 보여 준다.
- 부르는 것: 표에서 `읽기` 이고 이름에 쓰기 동사(create·update·set·save·delete·submit·publish·edit·send·mutate·upload·generate·optimize·bid·budget·pay·recharge …)가 없는 도구만. 그 밖(쓰기·발송·상품 발행·광고 입찰/예산/캠페인 변경·결제·충전·AI 생성/크레딧 사용, 그리고 '없으면 자동 생성' 부수효과 도구)은 요청 전에 거부(종료 코드 65).
- 문의·바이어 도구 결과는 화면으로만(`--out` 거부). 바이어 이름·연락처를 파일로 남기지 않는다.
- 도구가 실패를 `success:false` 나 `payload.error_info=failed` 로 돌려주면 빈 결과처럼 보이지 않게 종료 코드 1 + 사유를 낸다. `payload.error_info=failed`(인기 상품 검색의 상류 간헐 실패)는 읽기라 3번까지 다시 부른다. GGS 도구가 `data` 를 JSON 문자열로 주면 풀어서 보여 준다.
- 아직 없는 것: Accio 에이전트에게 새 질문을 보내 답 받기(크레딧 사용 = 쓰기라 보류), 대화 파일 받기.

### `--transport accio` (쿠키 대신 Accio 게이트웨이)

`alibaba-inquiry conversations|messages|buyer` 만 받는다. 출력 JSON 키는 쿠키 API(`--transport` 없음)와 같고, 게이트웨이가 안 주는 값은 `null`. 대화는 `--buyer-ali-id` 로만 찾는다(게이트웨이에 문의 목록 도구 없음).

| 명령 | 게이트웨이 도구 | 쿠키 API 와 대조(2026-11-17 실계정) | 다른 점 |
| --- | --- | --- | --- |
| `conversations` | `query_recent_conversation` | 같은 창의 1:1 대화 id·순서·안읽음·마지막 시각·보낸 쪽·등급·다음 커서 전부 같음 | 그룹 대화 없음, `chatType`·`lastType` = null, `modifyTime` 초 단위, `levelSource` = `contactLevel`, `--count` 는 20 단위로 올림(도구가 count 를 무시) |
| `messages` | `query_conversation_msg` | 공통 메시지 id·순서·시각·보낸 쪽·읽음 같음 | 시스템 알림(유형 9999·9998) 빠짐, `messageType` = null, `--with-text` 안 됨 |
| `buyer` | `query_recent_conversation` 의 그 대화 행(`contactLevel`·`contactCountry`) | 등급·국가 같음 | `joiningYears`·`potentialScore` 없음, `levelSource` = `contactLevel`. `get_buyer_basic_info` 는 일부 바이어 등급·국가가 쿠키 API 와 달라 안 씀 |

`alibaba-data` 읽기는 위 데이터 경로대로 게이트웨이를 먼저 쓰고, 연결이 안 되면 안내 한 줄 뒤 직결(api)로 폴백한다. `alibaba-inquiry` 보내기·읽음 표시(쓰기 도구 안 부름)와 Aside 화면 명령, `alibaba-data`의 discovery·`--from`·자체 옵션(`--limit/--seed/--product/--granularity`)이 붙은 읽기는 직결(api)만 쓴다.

## 문의창(OneTalk) 조작

```sh
alibaba-inquiry conversations --account A [--count 50] [--cursor T]        # 대화 목록(최근 순), 다음 쪽 = nextCursor
alibaba-inquiry messages     --account A --inquiry-id N [--count 20] [--with-text]
alibaba-inquiry reply        --account A --inquiry-id N --text-file reply.txt
alibaba-inquiry send-card    --account A --trade-id N
alibaba-inquiry send-file    --account A --inquiry-id N --path catalog.pdf
alibaba-inquiry send-product --account A --inquiry-id N --product-id 1600000000000
alibaba-inquiry send-product --account A --inquiry-id N --product "검색어"        # 후보가 정확히 1개일 때만 발송
alibaba-inquiry buyer        --account A --buyer-ali-id B                         # 등급·비식별 바이어 정보
alibaba-inquiry mark-read    --account A --inquiry-id N
alibaba-inquiry mark-unread  --account A --inquiry-id N
alibaba-inquiry reply        --account A --buyer-ali-id B --text "..." --dry-run   # 요청 요약만, 아무것도 안 부름
alibaba-inquiry template list --account A                                      # 빠른 답장 템플릿 id·이름
alibaba-inquiry template send --account A --inquiry-id N --template "Welcome"  # 템플릿 본문을 글로 보냄
alibaba-inquiry template send --account A --inquiry-id N --template "Welcome" --dry-run  # 템플릿 찾기·본문 길이만(조회, 발송 없음)
alibaba-inquiry tags list --account A                                          # 계정 태그(이름·종류·색, manual=손으로 다는 태그)
alibaba-inquiry tag add|remove --account A --inquiry-id N --tag "VIP"          # 그 대화 상대에 태그 달기·빼기 + 리드백
alibaba-inquiry inspect --account <계정키> --buyer-ali-id <ID>                  # HTTP API
```

### API 경로 (`engine/onetalk_cli.mjs`)

- CLI 가 Node 엔진(`engine/onetalk_cli.mjs`, 요청기 `engine/onetalk_api.mjs`·발송 `engine/onetalk_send.mjs` 위)을 띄우고 쿠키와 요청 JSON 을 stdin 으로 준다(답장 본문·파일 경로·쿠키는 프로세스 인자에 없음). 엔진은 결과 JSON 한 줄만 stdout 에 쓴다. 추가 패키지 없음.
- 로그인: `alibaba-data login` 이 저장한 그 계정의 셀러센터 쿠키(`engine/cookie_store.py load`, 파이프로만 넘김). 쿠키·csrf·h5 토큰 값은 엔진 프로세스 안에만 있고 출력·로그·파일에 남지 않는다. 쿠키가 없거나 만료(로그인 페이지로 돌려보냄)면 종료 코드 2.
- 바이어 등급 규칙은 코드에 없다: 보낼 대화는 인자(`--inquiry-id`/`--trade-id`/`--buyer-ali-id`)로만 고른다.
- 엔드포인트:

| 일 | 호출 |
|---|---|
| csrf | `onetalk.alibaba.com/csrf/getToken.htm` |
| 대화 목록 | `onetalk.alibaba.com/message/pageListRecentConversation.htm` `{count, limitTimeStamp}` |
| 대화 읽기 | `onetalk.alibaba.com/message/listRecentMessage.htm` `{contactAliId, pointTimeStamp, forward:false, count}` (읽음 처리 안 함) |
| 발송 | `onetalk.alibaba.com/message/sendMessage.htm` `{messageType, content, chatType, extParam, chatToken, receiverAliId:[바이어, 나]}` |
| 파일 올리기 | mtop `mtop.alibaba.interaction.clouddisk.prepareSendFileWithGroup` → OSS 업로드 → `...buildFileRelationWithGroup` |
| 문의 목록 | `message.alibaba.com/message/ajax/feedback/subjectList.htm` (`_csrf_token_` = `/message/ajax/csrftoken.htm` 의 값, `postId`) |
| 읽음/안읽음 | `message.alibaba.com/message/ajax/feedback/markRead.htm` `{readStatus: read|unread, data:[{secTradeId}], listMode}` |
| 상품 검색(`--product`) | `hz-productposting.alibaba.com/product/managementproducts/asyQueryProductsList.do` `subject=<검색어>` (웹 상품 고르기 창과 같은 조건: 표시 중·승인 상품, 제목 단어 일치) |
| 템플릿 목록·본문 | `message.alibaba.com/template/index.htm` 폼 필드 `json={cmd:"list"|"find", params:{}|{id}}` (list = 이름, find = 본문 HTML) |
| 계정 태그 | mtop `mtop.alibaba.icbu.tag.feature.query` v1.1 `{language, userRole:"seller"}` |
| 대화 상대 태그 | mtop `mtop.alibaba.icbu.contact.tag.batch.query` v1.0 `{targetIds:[aliId], tagObjType:1, tagTypeList:[1,2]}` |
| 태그 저장 | mtop `mtop.alibaba.icbu.tag.relation.save` v1.1 `{tagObjType:1, targetId, tagTypeList:[1,2], tagRelReqList:[{tagId, tagType}], userRole:"seller"}` (상대의 별표·직접 태그 전체를 덮어씀: 기존 태그를 읽어 하나만 더하거나 빼서 저장) |

- 템플릿 이름(`--template`)은 정확히 같은 이름, 없으면 그 말로 시작하는 이름, 없으면 그 말이 든 이름이 1개일 때만 고른다. 2개 이상이면 후보 이름과 함께 종료 코드 64, 없으면 `template_not_found`(종료 코드 1). 본문은 웹 화면처럼 `<br>` 을 줄바꿈으로 바꾸고 태그를 지운 글로 보낸다.
- 태그는 별표(1)·직접 만든 태그(2)만 달고 뺀다. 없는 이름·시스템 태그는 `tag_not_found`(종료 코드 1). 저장 뒤 다시 읽어 그 태그 유무와 다른 태그 유지(`otherTagsKept`)를 확인한다. 이미 그 상태면 저장 없이 `unchanged:true`.
- `send-product --product` 검색이 0개면 계정의 표시 중·승인 상품 수와 함께 종료 코드 64, 여러 개면 후보 id·제목(최대 10개)을 보여 주고 종료 코드 64.

- 메시지 종류(`messageType`): 1 텍스트 · 57 명함(cardType 1) · 2000 상품 카드(cardType 2000) · 53 파일 카드(cardType 12) · 60 이미지.
- 대화 지정: `--buyer-ali-id`(대화 목록의 `buyerAliId` = contactAliId) 또는 `--inquiry-id`/`--trade-id`(문의 목록에서 그 문의를 찾고, sender 계정 id = 대화의 contactAccountId 로 대화를 고른다). `mark-read`/`mark-unread` 는 문의 지정만 받는다(secTradeId 필요).
- 확인(리드백): 보내기 전 대화 맨 위 메시지 id 를 적어 두고, 보낸 뒤 최대 4번(1.5초 간격) 대화를 다시 읽어 그 위로 올라온 같은 종류의 `send` 메시지를 찾는다. 텍스트는 본문 앞 60자, 상품 카드는 상품 ID 까지 맞아야 `ok`. 못 찾으면 `sent_not_confirmed`(종료 코드 1). 읽음 표시는 문의 목록을 다시 읽어 `hasUnread` 가 바뀌었는지 본다. 이미 그 상태면 호출하지 않고 `unchanged: true`(엔드포인트가 같은 상태 요청에 400 을 준다).
- 출력: 대화 목록은 buyerAliId·level·levelSource·안읽음 수·마지막 메시지 종류/방향/시각만(이름·토큰 없음). `buyer`는 buyerAliId·level·levelSource 및 API가 준 countryCode·joiningYears·potentialScore만 출력한다. 등급은 1:1 대화만 20명씩 조회한다. `levelSource`: 등급 있음 `userNewLevel`, 응답 행 없음 `no_buyer_info`, 등급 필드 없음 `no_level_field`, 1:1 아님(조회 제외) `not_one_to_one`. 등급이 없으면 `level: null`이며 `buyer`의 응답 행 누락은 `status: "no_buyer_info"`다. API 호출 실패는 오류다. `send-product --product`는 후보 0개·복수이면 후보 수와 함께 오류를 반환한다(`--product-id` 우선). `messages` 는 종류·방향·시각·id, `--with-text` 때만 텍스트(이메일·전화 가림, 300자).
- 모든 발송·읽음 표시는 성공·실패 모두 `<outDir>/inquiry-replies.jsonl` 에 한 줄(바이어 이름·연락처 없음). `alibaba-inquiry log` 로 본다. 같은 문의에 같은 플로우는 한 번만 보낸다(`flow run`).

### 고객 패널·메시지 조회

`inspect`는 문의 목록 → querySummary → maDetail HTML GET → queryCustomerInfo JSONP와 메시지 API를 사용합니다.
HTML 스크립트는 실행하지 않고 읽음 처리도 하지 않습니다. `found.products`는 고객 패널 상품이며,
조회 불가는 `null`과 `productsReason`으로 구분합니다. 메시지는 `--count`(기본 20) 범위의 반환 건수와 마지막 시각,
연락처를 가린 `tail`을 출력합니다. 예전 DOM 요소 수는 API에 없으므로 `null`과 사유로 표시합니다.
flow의 check도 같은 API를 사용합니다. `--dry-run`에서도 사실 조회는 수행하고 발송은 계획만 출력합니다.
`ui probe`, `--runner`, `--shot`, `--button`은 제거했습니다.

## 바이어 정보

- 문의 목록은 `message.alibaba.com/message/ajax/feedback/subjectList.htm` 만 부른다. 대화를 열지 않아 읽음 처리가 바뀌지 않는다(읽음은 `mark-read` 로만 바꾼다).
- 브라우저 안에서 허용 필드만 남긴다: id·tradeId·시각·출처 코드·유형·상품ID·국가·답장 상태·안읽음·스팸·기기, 그리고 `level/grade` 이름의 짧은 코드 값(예 `L3`). 이름·이메일·전화·제목·본문은 저장하지 않는다. 응답의 키 이름 목록(값 없음)은 덤프 manifest의 `fieldNames`에 남긴다.
- 덤프 결과에는 주문·RFQ·방문자 국가 같은 바이어 데이터가 들어간다. 결과는 고객사 자산 폴더에만 두고 레포·채팅에 원문을 붙이지 않는다.

## 종료 코드

0 정상 · 1 문의창 조작 실패(결과 status) · 2 세션 만료(`login` 후 재실행) · 3 로그인 필요(다른 셀러 로그인, 저장 쿠키 없음·만료, 다시 읽어도 만료: 이유는 stderr) · 4 토큰 형태 문자열 발견(결과 삭제됨) · 5 알리바바 봇 확인 페이지 · 64 인자 오류 · 65 Accio 쓰기 도구 거부 · 69 키체인 응답 없음·브라우저 실행 실패 · 77 안전 관문 거부

`alibaba-ads` 는 따로 약속합니다: 0 정상(dry-run 포함) · 1 요청 실패(서버 거부·사전확인 거부) · 2 연결 불가·세션 만료 · 3 리드백 불일치(보냈지만 읽은 값이 기대와 다름) · 64 인자 오류·요청 형식 미검증(캡처 없는 조작)·되돌릴 수 없는 변경 · 77 차단 계정(`wonnischoice`·`linea`).

### 광고 조작 (`alibaba-ads`)

`engine/ads_write.py` + `bin/alibaba-ads.mjs`. 2026-11-18 thedozers 계정 화면 캡처(specs.json 11건: 캡처 3 / 앱 코드 모양 8)에서 나온 요청 형상만 씁니다.
공통 본문: `POST https://www2.alibaba.com<path>`, `application/x-www-form-urlencoded; charset=UTF-8`, `ads={"productLineId":110106}&type=normal&data=<JSON>&_csrf=<토큰>`. 토큰·쿠키는 출력·백업 어디에도 남기지 않습니다.

```sh
alibaba-ads campaign set <캠페인ID> [--name 새이름] [--status on|off] [--budget N] [--countries all|CA,US,…] [--yes]
alibaba-ads products add|remove <캠페인ID> <상품ID…> [--yes]
alibaba-ads keywords add|remove|bid <캠페인ID> … [--yes]     # 요청 본문 키 미확인 → 계획만, --yes 도 종료 64
alibaba-ads restore <백업파일> [--yes]
alibaba-ads backups [--account A] [--format json]
```

| 조작 | 요청 | 근거 |
|---|---|---|
| 이름 | `/api/campaign/update {id,title}` | 캡처 |
| 국가 타겟 | `/api/campaign/update/batch {campaignOperationList:[{id,syncPromotionRegionConf:"CA,US,…"}]}` | 캡처(목록만; `all`·전체 국가에서 줄이기는 되돌릴 형식이 없어 --yes 거부) |
| 상품 추가 | 사전 `/api/campaign/{id}/adgroup/can/create` → `/api/campaign/{id}/adgroup/create {adGroupOperationList:[{productId,onlineStatus:1}]}` | 캡처 |
| 상태·예산 | `/api/campaign/update {id, onlineStatus\|budget}` | 앱 코드 모양 → 계획만, --yes 거부 |
| 상품 삭제 | `/api/campaign/{id}/adgroup/delete {adGroupOperationList:[{id}]}` (adgroup/batch 에서 productId→id) | 앱 코드 모양 → 계획만, --yes 거부 |
| 키워드 | `/bidword/create/keyword`·`/bidword/delete`·`/bidword/update` | 형식 미확인 → 실행 안 함 |

읽기(원값·리드백): `/api/campaign/{id}` `data={}` → `title`, `onlineStatus`, `properties.budget`, `properties.operationScope.campaignScope.regions`(없으면 전체 국가) · `/api/campaign/{id}/adgroup/batch` · `/api/campaign/adgroup/count`.

`--yes` 로 실제 보내는 조작은 화면에서 캡처한 형식뿐입니다: 이름 변경, 국가 목록 변경(되돌릴 수 있을 때), 상품 추가. 켜기/끄기·예산·상품 빼기·키워드는 앱 코드에서 읽은 모양이라 계획만 출력하고 `--yes` 는 종료 64 로 거부합니다(유형 51·47 이 아닌 캠페인도 거부). 화면 캡처가 생기면 엽니다.

`--yes` 순서: 백업(`<계정 파일 옆>/ads-backups/<계정>-<캠페인ID>-<시각>.json`, account·campaignId·action·before 전체·request·createdAt) → 사전확인 → 쓰기 → 리드백(최대 3회) → 불일치면 3과 차이. 기본은 dry-run 이고 아무것도 보내지 않습니다.

## 구조

- `bin/alibaba-inquiry.mjs`(문의대응)·`bin/alibaba-data.mjs`(데이터분석)·`bin/alibaba-seller.mjs`(옛 이름, 전체) → `lib/cli.mjs` 명령 표·범위·계정·기간·창 나누기, `lib/dates.mjs` 기간 해석.
- `bin/alibaba-ads.mjs`(광고 조작) → `engine/ads_write.py`: 원값 읽기·전후 표·백업·사전확인·쓰기·리드백. `engine/api_session.py` 의 저장 쿠키 세션과 `_csrf` 취득을 그대로 쓴다.
- `engine/seller_central_raw.py` + `engine/http_collect.py` + `engine/pages.json` HTTP 수집 엔진. `engine/login.js` 로그인.
- `lib/onetalk.mjs` 문의창 명령 표·API 요청·발송 로그 → `engine/onetalk_cli.mjs`(OneTalk·메시지센터 HTTP API, `onetalk_api.mjs` 요청기·`onetalk_send.mjs` 발송 위) + `engine/onetalk_inspect.mjs`(고객 패널 HTTP 조회).
- `engine/cdp_runner.mjs`는 `login --sign-in`에서 사용하는 Chrome/Edge 로그인 어댑터. 저장 쿠키는 `engine/cookie_store.py`가 관리한다.
- `engine/report.py` 덤프 폴더 → 표(문의·광고·키워드·입찰가·분석). `engine/inquiry_attribution.py` 문의 → 상품 연결 규칙.
- `skills/alibaba-ad-analysis/` 광고 분석 스킬(SKILL.md + scripts/analyze.py, 이 레포의 alibaba-data 를 부름).
- 테스트: `test/*.test.mjs`(node --test), `test/test_*.py`(unittest). 픽스처는 합성 데이터. 실제 크롬을 띄우는 스모크는 `ALIBABA_SELLER_E2E=1 npm test` 일 때만(로컬 HTML, 네트워크 안 씀).

## 광고표 끝까지 받기

광고 리포트 표는 한 번 받을 때 약 105초 안에서 페이지를 넘긴다. 큰 표(키워드·검색어·상품×일)는 그 안에 다 못 받을 수 있어서, 엔진이 받은 것을 두고 다음 페이지부터 다시 받기를 표마다 API `totalRows` 에 닿을 때까지 자동으로 반복한다(`--max-passes`, 기본 20회; 한 번 더 받아도 늘지 않으면 멈춤). 그래도 모자라면 manifest 에 `rangeIncomplete` 가 남고, 같은 명령을 다시 돌리면 캐시를 쓰지 않고 이어 받는다. `alibaba-data ads tables` 의 `completeness.csv` 로 표마다 받은 행·전체 행을 확인한다.

### AI 초안 (전송 없음)

`alibaba-inquiry draft --account <계정키> --buyer-ali-id <ID> [--draft-kind reply|summary]`는
Copilot `/reception/aiCopilot`의 SSE 응답에서 최종 초안만 반환합니다. 기본값은 답장 제안(`reply`)입니다.
이메일·전화는 가리고, 모델 추론·토큰은 출력하지 않습니다. 입력창·발송·초안 저장 API는 호출하지 않습니다.
종료 이벤트가 없으면 `draft_incomplete`, 내용이 비면 `draft_empty`로 실패합니다.
`--dry-run`은 생성 요청도 하지 않습니다. Content enhancer는 API 초안 반환을 검증하지 못해 제공하지 않습니다.

## 상점 지표: `alibaba-data store …`

저장 쿠키 HTTP(`api`)만 사용한다. 기본/`auto`도 API로 고정하며 REPL·Accio 전송은 거절한다.
모든 명령은 `--account`, `--json|--csv`, `--out`, `--fresh`, `--from <덤프>`를 지원한다.
`--from`은 네트워크 요청 없이 저장 응답을 읽으며, 미수집·업무 오류는 종료 코드 1이다.

| 명령 | 내용 | 읽기 엔드포인트 |
|---|---|---|
| `store summary` | 일별 상점 트래픽 요약 | mydata `OneAction: minisite/new/summary` |
| `store visitors` | 일별 페이지 유형별 방문 상세 | mydata `OneAction: minisite/new/visitorsDetails` |
| `store today` | 상점 관리 오늘 지표 | siteadmin `/minisite/effectdata` |
| `store rating` | 현재 별점·실시간 별점 | merchant-rating `/capability/fetchGgsFinalStar.jsonp` |
| `store health` | 별점 건강도 | merchant-rating `/capability/fetchGgsStarHealthyByType.jsonp` |
| `store indicators` | 현재 별점 평가 지표 | merchant-rating `/capability/fetchGgsIndicatorsByType.jsonp` |
| `store thresholds` | 지표×별점별 기준 | merchant-rating `/capability/fetchGgsStarRatingThresholds.jsonp` |

```sh
alibaba-data store summary --account A --start 2026-01-01 --end 2026-01-03 --json
alibaba-data store visitors --account A --days 7 --transport api --fresh
alibaba-data store thresholds --from /path/to/dump --csv
```

`summary`·`visitors`는 화면 HTML의 `window.STATISTICS.date/day`를 읽어 조회 가능한 일자를 정한다.
번들의 `DateCal.cal`과 같이 기준일에서 선택일을 뺀 값을 `selected`로 보내며 `statisticsType=day`이다.
`--start/--end/--days` 구간을 하루씩 조회한다. 화면 범위를 벗어난 부분은 제외하고 stderr 안내와
`period` 표의 `requested_*`, `available_*`, `effective_*`, `query_day`에 실제 범위를 명시한다.
전부 범위 밖이거나 화면 기준일을 확인할 수 없으면 추정하지 않고 실패한다.

`today`와 별점 명령은 현재 스냅샷이며 날짜 옵션을 적용하지 않는다. `period`에 수집 시각과 사유가 나온다.

값은 응답 그대로이며 비율을 100배 하거나 null을 0으로 바꾸지 않는다. 객체 필드는 점 표기로 펼치고
배열은 유지한다. 빈칸은 `_blank_reasons`에 null·빈 문자열·누락 필드를 구분한다. 계산 열은 없다.
`thresholds`는 부모의 `starCode`와 `tenant`를 유지하고 기준 목록을 펼친다.
정상 빈 배열은 0행 안내와 성공, HTTP/업무 오류는 실제 상태·코드·메시지와 실패 종료 코드로 구분한다.

### 광고 추가 읽기 명령

공통 형식은 `alibaba-data ads <명령> --account A --transport api --fresh`다. 저장 쿠키와 같은 CookieJar의 CSRF 부트스트랩을 사용한다. `--json`, `--csv`, `--out`, `--from <덤프>`를 지원하며 광고 변경 요청은 없다.

| 명령 | 조회 데이터 | 요청 기간·범위 |
|---|---|---|
| `competition-keywords` | `/api/report/competition/keyword` | `queryTimeCondition`에 기간 전달; 현재 빈 응답으로 서버의 기간 적용은 미검증 |
| `segments` | `/api/data/dynamic/component/data` — `component_ggs_whole_site_offline_region`·`component_ggs_offline_region`·`component_ggs_offline_hour`·`component_ggs_offline_tag` | 본문 `data.time`에 기간 명시; 샵·제품 지역은 31일 창, 수동·시간대·타겟은 31일 창으로 분할 |
| `industry` | `/api/report/v2/industry` + `component_offline_product_diagnosis_ggs`(high/low/cate) | 비교 표는 지난 1/7/30일 프리셋만 지원(그 밖의 기간은 마지막 30일); 진단 표는 최근 31일 |
| `industry-compare` | `/api/campaign/report/industry/compare` | 캠페인별 `campaignId`, `beginDate/endDate`(YYYYMMDD); 7일 구간 성공 확인 |
| `product-benchmark` | `/api/productReport/industry/compare` | 캠페인 목록의 ID·유형·하위유형, `queryEffect` 기간(YYYYMMDD) |
| `campaign-countries` | `/api/campaign/listCountry` | 캠페인별 ID, `beginDate/endDate`(YYYY-MM-DD), offline `queryEffect` |
| `campaign-advice` | `/api/campaign/advice` | Search·WholeSite 유형 목록별 현재 추천 값, 조회만 |
| `campaign-type-count` | `/api/campaign/type/count` | Search·WholeSite 유형별 현재 개수 |
| `guaranteed-performance` | `/api/guaranteedperformance/data` | 현재 type 51, `ggs_ai_whole_site` 캠페인의 보장 조건; 번들의 패키지별 businessType 분기 사용 |
| `premium-industry` | `/api/premium/industry/aggregation/data` | 추천 상품 최대 20개의 `cateLv2Id`를 중복 제거해 업종별 조회 |
| `recommended-products` | `/api/recommend/product` | `productIdList=[]`, `size=20`; 현재 추천 최대 20개, 전체 상품 목록 아님 |
| `brand-accounts` | `/brand/napi/common/getAccountList` | 현재 브랜드 계정 필터 |
| `brand-groups` | `/brand/napi/common/getGroupList` | 현재 브랜드 그룹 필터 |
| `brand-report-categories` | `/brand/napi/dataReport/getProductCategory` | 현재 브랜드 보고서 카테고리 |
| `product-groups` | `/api/product/group` | `data.children`의 상품 그룹; 하위 트리는 응답 그대로 |

`competition-keywords`·`industry-compare`·`product-benchmark`·`campaign-countries`는 `--start YYYY-MM-DD --end YYYY-MM-DD` 또는 `--days N`을 지원한다. 요청은 최대 7일씩 나누며 구간 합계를 합산하지 않는다. `industry-compare`는 15일 요청에서 `queryIndustryReportCompareData dateDiff over range`가 확인되어 7일 분할을 사용한다. 최초 조회 가능일은 미검증이므로 `--max`는 지원하지 않는다. 다른 명령은 현재 스냅샷이며 날짜 옵션으로 과거 상태를 복원하지 않는다.

캠페인 목록은 유형 1·2·4·10·47·51별 `campaign/type/allList`를 페이지 끝까지 읽어 ID·productLineId·type·subType을 바인딩한다. 대상 0개이면 해당 조회를 보내지 않고 이유를 출력한다. 목록의 오류·페이지 누락·필수 필드 누락도 실패로 처리한다. 실제 식별자를 코드나 픽스처에 저장하지 않는다.

`data`의 열 이름과 값은 raw 그대로다. 객체·배열도 원형을 보존하며 JSON 출력으로 확인할 수 있다. `industry-compare`의 캠페인 키는 `_key`로 옮긴다. `_query`는 요청 태그·기간·바인딩 값이며, 스냅샷의 날짜는 빈칸이다. `_missing`은 `응답 null` 또는 `응답에 키 없음`을 기록한다. `requests`는 요청별 행 수와 빈 응답/대상 없음 사유다. 빈 응답은 비용 0·광고 OFF·권한 없음으로 추정하지 않는다. 기간 덤프를 `--from`으로 다시 읽을 때 저장 집계 구간의 일부만 요청하면 값을 잘라 추정하지 않고 재조회 필요 오류를 낸다.

### 광고 세그먼트·업계 비교 (`ads segments`, `ads industry`)

- `ads segments` — **언제**: 광고 성과를 지역·시간대·타겟 조건별로 쪼개 볼 때. 표는 `region`(샵 51·제품 47 = `component_ggs_whole_site_offline_region`, 수동 1,2·보관 4,10 = `component_ggs_offline_region`), `hour`(`component_ggs_offline_hour`, 산업 4,10·수동 1,2 — `hourRange` 24행), `target`(`component_ggs_offline_tag`, tagType 13 지역타겟팅·14 잠재고객·15 배달터미널·16 배달시나리오·17 결합레이블)이다. 각 행에 `group`·`campaign_type`·`view`·`period_start/period_end`가 붙고 지표는 응답 값 그대로 숫자화한다.
- `ads industry` — **언제**: 우리 광고·상품 성과를 업계 평균·상위와 비교할 때. 표는 `industry_compare`(`/api/report/v2/industry`, `scope` = `mine`·`cateAvg`·`cateTop10`), `product_diagnosis`(`component_offline_product_diagnosis_ggs`, 화면의 high/low 선택), `category_products`(같은 컴포넌트의 cate = 같은 카테고리 타사 상품 순위)다.
- 기간: 두 명령 모두 `--start/--end`(또는 `--days`)를 요청 본문 `time`에 명시한다. 화면이 하위 탭에 기본 7일을 보내는 경우가 있어도 CLI는 요청 기간을 그대로 보낸다. 비교 표는 서버가 지난 1/7/30일 프리셋만 받으므로 그 밖의 기간은 마지막 30일로 요청하고 `view`에 실제 보낸 프리셋을 적는다. 진단 표는 화면과 같은 최근 31일만 조회한다.
- 카테고리: `ads industry`는 `--category-id`가 없으면 `/api/marketing/ability/category`에서 계정 주 카테고리(`isMainCate`)를 읽는다. 그 호출이 값을 주지 않으면 실패로 멈춘다.
- 페이지: 응답 `totalRows`까지 50행씩 반복한다. 서버가 페이지 크기를 키우면 1페이지를 되풀이하므로 50을 고정한다. `category_products`는 시장 순위가 조회 중 바뀌어 같은 상품이 다시 나올 수 있어 `productId`로 중복을 제거하고, 그 사실은 덤프 `notes`에 남긴다.
- 실측(2026-11-18, thedozers, 읽기): `segments --start 2026-07-01 --end 2026-07-31` → region 샵 96행·클릭 1036·노출 64005, hour 산업 24행 + 수동 24행(0값), target 10개 요청 모두 0행, exit 0. `industry --start 2026-10-17 --end 2026-11-16` → `industry_compare` 3행, `product_diagnosis` high 17행, `category_products` 4752행(중복 제거 후), exit 0. 서버가 기간을 거부하면 사유 원문과 함께 exit 3, 요청 자체가 실패하면 exit 5다.

실조회에서 경쟁 지역(`competition-regions`)은 CSRF 쿠키 유지 후 HTTP 200까지 도달했지만 `{"status":1001,"ok":false,"error":"isp.internal-error","errorVO":null}`만 남았고, 화면도 이 API를 쓰지 않아 명령을 제거했다(번들 `excluded`에 사유와 함께 남긴다). 경쟁 키워드·상품 업종 비교·캠페인 국가·브랜드 보고서 카테고리의 실측 0행은 비어 있지 않은 사례의 검증을 대신하지 않는다.

HTTP 오류나 업무 오류는 stderr에 원문 코드·메시지를 표시하고 exit≠0이다. 실패 응답은 덤프에도 보존하며 `--from` 재사용에서도 `errors`와 실패 종료가 유지된다. Ads 봉투의 `status=1000, ok=true`는 성공 계약이고, 별도 `code=1000/1003`은 오류로 처리한다. 신규 값에는 계산 열을 추가하지 않는다.
## 리뷰·거래·프로모션·물류·자금·인증·콘텐츠

| `alibaba-data` 명령 | 조회 범위 |
|---|---|
| `reviews counts` | 리뷰 탭별 건수 raw (리뷰 본문·평점 미포함) |
| `orders refunds` | 환불 사례 목록 raw (바이어 정보 제외) |
| `orders chargebacks` | 차지백 분쟁 목록 raw |
| `promotions discounts` | 상품 할인 활동 목록 raw |
| `promotions coupons` | 프로모션 쿠폰 목록 raw |
| `logistics orders` | 물류 주문 목록 raw (주소·연락처 제외) |
| `funds collected` | 입금 내역 raw (은행·결제 식별 정보 제외) |
| `funds fees` | 수수료 내역 raw |
| `funds withdrawals` | 출금 내역 raw (출금 실행 없음) |
| `funds settlements` | 정산 내역 조회 (서버 오류 시 원문·실패 종료) |
| `certifications list` | 인증 목록 조회 (빈 본문·서버 오류 시 실패 종료) |
| `funds invoice-bills` | 미청구 인보이스 목록 raw |
| `funds invoice-history` | 인보이스 발행 이력 raw |
| `certifications suggestions` | 권장 인증 목록 raw |

- 공통: `--account <account> --transport api --fresh`, `--json/--csv`, `--from <dump>`. `auto`는 API로 고정하고 다른 전송은 거절합니다. 브라우저 탭은 열지 않습니다.
- 기간: 현재 서버 목록/스냅샷입니다. 날짜 옵션은 요청 기록용이며 서버 필터에 적용되지 않습니다. 역사 시점의 상태를 재구성하지 않습니다.
- 목록: 서버의 페이지 번호와 total을 대조합니다. `--max-pages` 한도로 덜 수집되면 성공으로 표시하지 않습니다. 응답 total이 없거나 목록 형식이 바뀌면 실패합니다.
- 개인정보: 정해진 scalar 필드만 덤프에 저장합니다. `--from`으로 기존 덤프를 읽을 때도 같은 allowlist를 적용합니다. 바이어 이름·연락처·주소·계좌·은행·결제 식별자는 제외합니다.
- 값: 응답 원문 값·타입을 유지합니다. 금액 환산이나 파생 계산을 하지 않으며, 미반환/null은 빈칸과 `_blank_reasons`로 표시합니다.
- 오류: HTTP 302, 업무 code/responseCode 오류, HTTP 200 빈 본문, SSE 필수 이벤트 누락은 실패입니다. 정상 0행과 구분하고 코드·메시지를 출력합니다.
- `reviews counts`: SSE의 `interplay.reviewmananger.tab.reviewcount`만 사용합니다. 목록·평점 모듈이 반환되지 않은 상태를 리뷰 0건으로 추정하지 않습니다.
- `funds settlements`: 화면 컨텍스트의 site/memberType/locale와 저장 쿠키의 중첩 ctoken을 바인딩합니다. Pending funds 화면의 목록 조회도 `code=500`, `message="site config exception."`를 반환했습니다. 화면의 `No Data` 표시를 정상 빈 목록으로 해석하지 않으며, CLI는 원문 오류와 exit 5를 유지합니다. 성공 데이터 스키마는 미검증입니다.
- `certifications list`: Auth Center 토큰을 선행 조회하고 `_csrf_token` 폼 인자로 보냅니다. `hasError=false`인 응답의 `content.certInfoVoList`를 읽으며, 인증 ID·이름·분류·유형·상태·날짜·상품 연결 수를 서버 필드명 그대로 출력합니다. 누락 필드는 빈칸+사유이며 빈 본문·오류·목록 형식 불일치는 실패 종료합니다.
- 콘텐츠 채널 개통 여부(설정) 명령은 계정 데이터가 아니므로 제외했습니다.
- hz 광고 별도 화면은 호스트 DNS 해석 실패로 조회 계약 미검증이며 새 명령을 추가하지 않았습니다.

## 광고·상품·상점 운영 조회

| `alibaba-data` 명령 | 언제 쓰는지 | 출력 표 (`period` 공통) |
|---|---|---|
| `ads adgroups` | 쇼케이스 광고그룹별 노출·클릭·비용·주문을 볼 때 | `adgroups`, `adgroup_summary` |
| `ads showcase` | 쇼케이스 주문 현황과 운영 슬롯을 볼 때 | `showcase_order_counts`, `showcase_orders`, `showcase_overview` |
| `ads brand` | 브랜드 캠페인·소재·제외 설정과 키워드 성과를 볼 때 | `brand_campaigns`, `brand_word_packages`, `brand_excluded_products`, `brand_excluded_words`, `brand_creatives`, `brand_keyword_effect` |
| `ads creatives` | 광고 소재의 심사 및 게시 상태를 볼 때 | `creative_summary`, `creatives` |
| `products effect` | 전 상품의 월간 효과·잠금 상태와 레이더 요약을 볼 때 | `product_effect`, `product_lock`, `product_radar` |
| `products growth` | 육성 대상 상품 목록을 볼 때 | `products_growth` |
| `funds balance` | 수수료 상태·지갑·잔액·미연결 외화를 볼 때 | `funds_fee_status`, `funds_wallets`, `funds_balance`, `funds_pending` |
| `promotions events` | 참가 가능한 프로모션과 내 참가 내역을 볼 때 | `promotion_available`, `promotion_enrolled` |
| `store tasks` | 성장·스타 과제 진행과 제재 점수를 볼 때 | `growth_tasks`, `star_tasks`, `punishment_scores` |
| `knowledge files` | 지식베이스 폴더·파일의 메타데이터를 볼 때 | `knowledge_files` |

- 저장된 계정 쿠키로만 실조회합니다. `--transport api`가 기본이며 `auto`도 API만 사용합니다. 로그인·브라우저 폴백·원본 덤프는 없으며 `--from`과 `--dump-dir`은 지원하지 않습니다. 출력은 `--json` 또는 `--csv`, `--out DIR`로 표별 CSV를 저장할 수 있습니다.
- `ads brand`의 키워드 성과만 `--start/--end` 또는 `--days`를 적용합니다. 기본은 셀러센터 집계일 기준 최근 7일입니다. 그 밖의 표는 현재 상태이며, 상품 효과는 셀러센터 월간 기본 조회입니다.
- 광고그룹 행은 화면 기본 필터(온라인)입니다. 요약은 온라인/오프라인 조건별로 따로 표시합니다. 쇼케이스 주문 목록은 서버의 네 상태를 조회합니다.
- 페이지별 합을 서버 전체 행 수와 대조합니다. 불일치 또는 `--max-pages` 한도에 걸리면 종료 3입니다. 서버 오류는 비밀·개인정보가 없는 원문을 stderr에 남기고 종료 5입니다.
- 바이어 이름·연락처·로그인 ID·주소를 출력하지 않습니다. 제재 점수 조회에 필요한 판매자 ID와 서명은 요청에만 사용합니다. 지식베이스는 목록 메타데이터만 읽으며 파일 본문을 내려받지 않습니다.
- 제외(71): `POST /api/product/setting`은 설정을 기록하는 호출이라 조회 명령에 넣지 않았습니다.
- 제외(40): OneTalk `listRecentConversationContactDetail`은 바이어 개인정보를 반환하고 호출에 필요한 ID는 웹소켓에서만 확인됩니다.
