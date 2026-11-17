# 자세히: 명령·옵션·데이터 정의 (개발자용)

알리바바닷컴 셀러용 CLI 세 개와 광고 분석 스킬 하나. 앞의 두 개는 Aside 브라우저(셀러센터 로그인 세션), `alibaba-accio` 는 Accio Work 앱의 로컬 게이트웨이를 쓴다(브라우저 안 씀).

| 문의대응 `alibaba-inquiry` = OneTalk 대화창 행동만 | 데이터분석 `alibaba-data` = 읽기·분석 전부 | Accio Work `alibaba-accio` = 게이트웨이 읽기 도구 |
|---|---|---|
| 답장·명함 교환·PDF/파일 발송·상품 카드·템플릿·태그 (기본 dry-run, 실발송은 오너 승인 관문) | 문의 데이터(목록·통계·상품별 문의·본 상품·국가·스팸·바이어 등급), 광고 리포트(Manual·AI Smart), AI 전환 명세, Product Analyzer, 키워드, 입찰가, 광고 분석 | 상점 요약·추세·월간 진단·별점, 최근 문의 대화·메시지, 효과 0·저점수 상품, 시장(업종 국가 순위·추세·인기 상품), RFQ 이력 — 읽기 도구만, GGS 셀러 계정 기준 |
| `alibaba-inquiry reply --account A --inquiry-id N --text-file r.txt` | `alibaba-data inquiries list --account A --start 2026-11-01 --end 2026-11-15` | `alibaba-accio login` |
| `alibaba-inquiry send-card --account A --inquiry-id N` | `alibaba-data inquiries stats --account A --days 30` | `alibaba-accio status` |
| `alibaba-inquiry send-file --account A --inquiry-id N --path catalog.pdf` | `alibaba-data inquiries by-product --account A --start 2026-06-01 --end 2026-11-15` | `alibaba-accio shop summary --arg statisticsType=week` |
| `alibaba-inquiry send-product --account A --inquiry-id N --product-id P` | `alibaba-data ads products --account A --start 2026-06-01 --end 2026-11-15` | `alibaba-accio conversations` |
| `alibaba-inquiry template send --account A --inquiry-id N --template "Welcome"` | `alibaba-data keywords recommend --account A --start 2026-06-01 --end 2026-11-15 --product <상품ID 또는 이름 일부>` | `alibaba-accio features --tested 됨` |
| `alibaba-inquiry tag add --account A --inquiry-id N --tag "VIP"` | `alibaba-data ads analyze --account A --start 2026-06-01 --end 2026-11-15 --out ./ad` | `alibaba-accio call <도구> --arg k=v` |

- 데이터 명령은 읽기만 한다. 광고·입찰·예산·캠페인을 바꾸는 코드는 없다.
- 문의창 명령은 기본 dry-run(클릭·입력 0회)이다. 실발송은 `--send` 와 `ALIBABA_SELLER_ALLOW_SEND=owner-approved` 가 둘 다 있을 때만 한다. 명함·파일은 OneTalk 대화창 자체 기능으로만 보낸다(메일 등 다른 경로 없음).
- 광고 분석 스킬: [`skills/alibaba-ad-analysis/`](../skills/alibaba-ad-analysis/SKILL.md) (이 레포 안).
- 옛 이름 `alibaba-seller <명령>` 도 그대로 된다(두 CLI 명령 전부).
- 옛 문의 데이터 이름 `alibaba-inquiry list|stats|by-product|fields`(·`inquiry list` …)도 그대로 동작한다. 실제로는 `alibaba-data inquiries …` 를 돌리고 stderr 에 새 이름을 한 줄 알린다.

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

Aside는 내 로그인 상태를 그대로 쓰는 브라우저 앱이며, 이 도구는 셀러센터 화면을 이 브라우저에서 읽습니다.

```sh
aside --host local repl "console.log(1)"     # 1 이 찍혀야 한다. 응답이 없으면 Aside 앱을 다시 켠다
```

- Aside 프로필 `u0`(두 번째 프로필이면 `u1` …) 브라우저에서 알리바바 셀러센터에 로그인돼 있어야 한다.
- Aside 호출은 한 번에 하나만. 같은 Aside 를 다른 세션이 쓰는 중이면 끝날 때까지 기다린다.

**3-1. Aside 없이: 크롬·엣지 + 저장 쿠키** (`--runner`, 맥·윈도우)

- `alibaba-data login --account <계정키> --browser chrome` 으로 평소 크롬(또는 엣지)의 알리바바 로그인 쿠키를 OS 비밀 저장소에 넣어 두면, 모든 셀러센터 명령(데이터·문의창)이 Aside 대신 설치된 크롬/엣지를 **임시 프로필**로 화면 없이 띄워 그 쿠키로 돈다. 평소 쓰는 크롬 프로필 폴더는 건드리지 않고, 임시 프로필은 끝나면 지운다. 쿠키는 stdin 파이프로만 넘기고 파일·인자·출력에 안 남긴다.
- 고르는 법: `--runner auto`(기본) = 저장 쿠키가 있으면 chrome, 없으면 aside. `--runner aside|chrome|edge` 로 고정, 계정 설정에 `"runner": "chrome"` 으로도 고정. 크롬이 없으면 엣지를 쓴다. 실행 파일 위치가 다르면 `ALIBABA_SELLER_BROWSER_EXE`.
- 로그인 만료(로그인 페이지로 튕김)면 쿠키를 출처 브라우저에서 한 번 다시 읽어 재시도하고, 그래도 만료면 종료 코드 3 + 이유 한 줄.
- 알리바바가 봇 확인(슬라이더·캡차)을 내면 숨기지 않고 `bot_blocked`(종료 코드 5)로 알린다. 첫 페이지에서 막히면 화면 밖 창(`--window-position=-2400,-2400`)으로 한 번 다시 연다. 처음부터 창으로 = `ALIBABA_SELLER_HEADED=1`.

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
alibaba-data coverage --account <계정키> --start 2026-06-01 --end 2026-11-15   # 항목별 추출됨/없음 표
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
- 안 되는 경우: macOS 크롬은 CDP 경로에서 복사본 쿠키를 지워 0개를 돌려준다(2026-11-17 실측, 종료 코드 69). 맥은 기본 경로(sqlite)를 쓴다. Windows v20 CDP 경로는 실제 Windows 에서 아직 못 돌려 봤다. `--browser aside` 는 Aside 프로필 파일 대신 `aside repl` 로 브라우저 쿠키를 받는다(Aside 키체인 항목은 매번 허용 창이 떠서).
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
alibaba-data inquiries fields --account A --days 7   # 응답 키 이름만(값 없음): 바이어 레벨 필드 찾기
alibaba-data ads report     --account A --start 2026-06-01 --end 2026-11-15 --out ./ads   # 상품×일·캠페인×일·계정×일 CSV
alibaba-data ads products   --account A --start 2026-06-01 --end 2026-11-15
alibaba-data ads tables     --account A --start 2026-06-01 --end 2026-11-15 --out ./ads   # 광고표 전부 그대로(AI·Manual 계정/캠페인/상품/키워드/검색어/전환 명세 전 열 + 캠페인 목록 + 상품별 광고비 표) + completeness.csv(표마다 받은 행 vs API totalRows). 덜 받으면 exit 3, 같은 명령 다시 = 이어 받기
alibaba-data ads quality    --account A --start 2026-06-01 --end 2026-11-15   # L1+ 바이어 클릭 비율(먼저)·문의·L3+·문의 1건 비용: 캠페인별·월별·기간 합
alibaba-data ads analyze    --account A --start 2026-06-01 --end 2026-11-15 [--out DIR]
alibaba-data ads conversions --account A --start 2026-06-28 --end 2026-11-15
alibaba-data ads conversion-fields --from <덤프 폴더>                       # 전환 명세 응답 키 이름만
alibaba-data keywords       --account A --start 2026-06-01 --end 2026-11-15 [--seed serum] [--nd 30d] [--no-index]   # 키워드 raw 3종
alibaba-data keywords recommend --account A --start 2026-06-01 --end 2026-11-15 --product <상품ID 또는 이름 일부> [--top 10]   # 그 상품 추천 키워드 10개
alibaba-data keywords recommend --account A --start 2026-06-01 --end 2026-11-15                # --product 없으면 상품 목록만(고르라는 안내)
alibaba-data keywords recommend --account A --start 2026-06-01 --end 2026-11-15 --all-products # 전 상품 일괄
alibaba-data bids           --account A --start 2026-06-01 --end 2026-11-15
alibaba-data coverage       --account A --start 2026-06-01 --end 2026-11-15
alibaba-data analytics products --account A --start 2026-10-01 --end 2026-11-14 [--granularity week|month]
alibaba-data analytics products --account A --weeks 4
alibaba-data dump           --account A --start 2026-06-01 --end 2026-11-15 [--pages ads-report,inquiries] [--resume]
alibaba-data pages
```

- 출력: 기본은 표(앞 30행), `--json`, `--csv`. `--out DIR` 이면 표마다 CSV 파일.
- 이미 받은 덤프로 다시 계산: `--account` 대신 `--from <덤프 폴더>`(창이 여럿이면 여러 번). 브라우저를 안 쓴다.
- 캐시: `--account` 로 부를 때 같은 계정·기간 덤프 폴더에 같은 페이지가 이미 있고(상태 ok, 같은 표·시드·주기) 오늘 받았거나 창 끝 +3일 뒤에 받았으면 다시 받지 않는다(stderr `캐시 사용: …`). 새로 받기 = `--fresh`.
- 기간 0 안내(stderr 한 줄): 광고 명령은 광고 계정·캠페인·상품 일별표가 전부 0 이면 `이 기간 광고비·클릭 0(광고 꺼짐)`(GGS 계정 일별표는 광고비를 쓴 날에도 0 이라 캠페인·상품표까지 본다), 문의 명령은 `기간 안 문의 0건(마지막 문의 YYYY-MM-DD)`, `analytics products` 는 `Product Analyzer 검색 노출·클릭 0`.
  `alibaba-data ads analyze --from ~/30_Assets/<고객사>/seller-report/sellercentral-raw-20261117 --out /tmp/ad`
- 라이브 실행은 필요한 페이지만 덤프한다(예: `inquiries list` = `inquiries` 페이지, `ads analyze` = 문의·광고 리포트·캠페인·상품 분석·키워드). 덤프 위치: `<outDir>/sellercentral-raw-<start>_<end>/`(`--dump-dir` 로 변경).

### analytics products (Product Analyzer)

- 셀러센터 Analytics > Product Analyzer 와 같은 API(`mydata.alibaba.com/self/.json`, `getVipEffectiveProductsAndStats`, `vip/product/getProdcutSummary`)를 기간마다 부른다. 이 API 는 날짜를 받지 않고 `statisticsType=week|month` + `selected=N`(최신 완료 기간에서 N 기간 전)만 받는다.
- 그래서 `--start/--end` 를 PST 주(일~토, 기본) 또는 달로 덮어 기간마다 부르고 상품별로 더한다. 범위에 일부만 걸린 주·달도 통째로 들어가므로 출력에 `covered_start`/`covered_end`(실제 합산 기간)를 같이 적는다. `--weeks N` = 완료된 최근 N주.
- 최신 완료 주 = 오늘(PST) 직전 토요일로 끝나는 주(화요일 갱신). 요약 응답의 `statDate` 와 계산한 기간 시작일을 `analytics_periods.statDate_matches` 에 대조한다.
- 표: `analytics_summary`(합계), `analytics_periods`(기간별 상품 수·statDate 대조), `analytics_products`(상품별 Views·Clicks·CTR·Visitors·Add to favorite·Shared·Add to compare·Inquiries·Inquiry rate·담당자·P4P/KWA), `analytics_product_keywords`(상품×유입 검색어×기간: views·clicks·p4pViews·p4pClicks·p4pCampaignStatus).
- Visitors 는 기간별 방문자 수의 합이다(여러 기간에 온 같은 방문자는 중복). Inquiry rate = Inquiries ÷ Visitors.

### 지표 정의 (오너 확정)

- **L1+ 바이어 클릭 비율**(`l1plus_click_pct`, 바이어 품질 첫 지표) = 광고표 `fiGrowthClickCnt`(L1+ Clicks) 합 ÷ `clickCnt` 합 × 100. 일별 비율 평균이 아니라 합으로 다시 계산한다. 상품별(`ads products`·`ads analyze`)·캠페인별·월별·기간 합(`ads quality`).
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
- 저장하는 필드는 이것뿐(그리고 응답 행의 키 이름 목록 `fieldNames`, 값 없음): 전환 시각(timeUs/chatTime), 광고 상품(productId·productName), 전환 상품(fbProdId·fbProductName), 국가(countryId), 유입(channelDisplay), 전환 유형(fbType), 바이어 등급(buyerLevel·senderMbrGrowthLevel), isRepeat·isLowQuality·isPunish. 바이어 이름·아바타·프로필 URL·대화 내용·회원번호·fbId·주문 URL 은 브라우저 RAM 에서 버린다(`engine/pages.json` 의 `allow`).
- 열: `conversion_time`(chatTime) · `conversion_date`(chatTime 앞 10자, 시간대 표기 없음. 이 날짜로 기간을 거르고 광고 상품의 그날 클릭 `ad_product_clicks_on_date` 를 붙인다. granularity ALL 이라 timeUs 는 조회 구간 문자열이다) · `ad_product_*` · `converted_scope`(`product` = 전환 상품 있음, `shop` = fbProdId 없음, 상점 단위 전환) · `converted_product_*` · `buyer_level`(buyerLevel) · `growth_level`(senderMbrGrowthLevel) · `channel` · `conversion_type` · `is_repeat`·`is_low_quality`·`is_punish`.
- 상품 이름이 API 에서 `"0"`·빈칸이면 상품 광고표 `subject`, 그다음 Product Analyzer 제목으로 채운다. 광고표 `subject` 가 비어 오는 계정이 있어 `ads conversions` 는 `analytics-product` 페이지도 받는다. 두 곳 모두 제목이 없는 상품은 빈칸이다.
- 바이어 등급은 두 열이다. 화면 전환 명세 카드는 `senderMbrGrowthLevel` 을 보여주므로 `ads analyze` 의 명세 L3+ 비율은 `growth_level` 기준이다.
- `conversion_summary`:
  - `campaign_table_conversions`·`campaign_table_l3plus` = 캠페인 일별표 cvr3·l3PlusCvr3 합(실측, 화면 Conversions·L3+ 와 같은 표). `spend_per_conversion_usd` = 캠페인 광고비 합 ÷ 이 cvr3 합.
  - `detail_rows` = 명세 행 수. 화면 수와 다를 수 있다(반복·처벌 행 포함).
  - `screen_estimate_conversions` = isRepeat·isPunish 가 아닌 명세 행, `screen_estimate_l3plus` = growth_level L3 이상 명세 행. 명세로 계산한 '화면 기준 추정'이며 출처 칸에 적는다.
  - `channel_source`: 응답 행에 `channelDisplay` 가 없으면 "없음". 화면 설정에만 있는 이름이다. 실제 키는 `alibaba-data ads conversion-fields --from <덤프>` 가 키 이름만(값 없음) 보여준다. 키를 찾으면 `engine/pages.json` `ai.conv_detail.allow` 에 넣는다.
- `ads analyze` 상품표의 `conv_detail_*` 열과 `inquiry_quality` 의 "AI 전환 명세(실측)" 행에 들어간다. AI 캠페인 상품별 광고비는 추정이어도, 상품별 전환 수·바이어 등급은 이 명세로 실측이다(출처 열에 구분).

### 키워드 raw·추천

- `keywords` = 키워드 raw 3종을 한 표씩:
  1. 광고 Word Report — Optional Keywords(`component_ggs_offline_keyword`)·Search Terms(`component_ggs_offline_searchword`), AI(유형 4,10)·Manual(1,2), `--start/--end` 일별.
  2. Product Analyzer 상품별 바이어 유입 검색어(`keywordEffect`, 월별) + Keyword Analyzer(`getKeywords`).
  3. Keyword Index(Analytics > Keyword Index, `vip/kwIndex/searchWords`) = `keyword_index` 표: 검색지수(`pv`)·판매자 규모 = 경쟁(`resultCnt`)·클릭률·증감·12기간 추이. 이 API 는 날짜 대신 `--nd`(기본 30d) 창만 받는다.
- Keyword Index 는 시드(머리어)마다 100행 × `--kw-pages`(기본 3)쪽을 받는다. 시드 = `--seed`(여러 번), 없으면 우리 데이터에서(최대 `--max-seeds` 5개, 근거는 stderr `시드:` 줄):
  - `--product` 있음: 그 상품 유입 검색어 상위 2개(클릭·노출 순) → 그 상품 제목 단어(그 상품 유입 검색어에 든 것 먼저, 다음은 우리 상품 제목 여러 개에 나오는 단어 = 품목·특성어 먼저, 한 상품에만 있는 이름은 뒤로). 브랜드성 단어(제목 90% 이상) 제외.
  - `--product` 없음(`keywords`·`--all-products`): 우리 상품 유입 검색어 2개 이상에 들어 있고 상품 제목에도 있는 단어. 유입 검색어가 없으면 상품 제목 2개 이상에 있는 단어.
  - `--no-index` 면 받지 않는다. `keywords recommend` 를 `--product` 없이 부르면 상품 목록만 보여 주므로 받지 않는다.
- `keywords recommend --product X` = 그 상품 하나의 추천 키워드 `--top`(기본 10)개. 상품은 상품ID 또는 제목 일부(대소문자 무시), 여러 개가 맞으면 그 목록을 보여 주고 종료 코드 64.
  - 받는 것: Product Analyzer(상품 제목·유입 검색어) + 광고 리포트 중 상품·키워드·검색어표만 + 그 상품 시드의 Keyword Index. 문의 목록·전환 명세는 안 받는다.
  - 표 위 한 줄 = 그 상품의 기간 합(상품 단위): 광고 노출·클릭·CTR·문의·L1+ %(광고 상품표), 검색 유입 노출·클릭(Product Analyzer), 광고 키워드·검색어표의 그 상품 행 수. 키워드별 광고 칸은 광고 키워드·검색어표에 그 상품·키워드 행이 있을 때만 숫자가 찬다.
  - 후보는 그 상품 특성에서만: 그 상품 유입 검색어(Product Analyzer), 그 상품 광고 Word Report 키워드, Keyword Index 검색어 중 단어 전부가 그 상품 제목에 있는 것.
  - Keyword Index 검색량이 없는 후보는 뺀다(검색량 근거 없는 키워드 추천 안 함).
  - 출력 = `키워드 | 점수 | 검색량 | 경쟁(셀러 수) | 입찰가 USD | 노출 | 클릭 | CTR % | 문의 | L1+ %`, 점수 순. 숫자 없는 칸은 빈칸(추정 안 함). 맨 아래 한 줄에 점수 공식:
    `점수 = 검색량² ÷ 경쟁 × (1 + 클릭) × (1 + 문의) × (1 + L1+% ÷ 100) ÷ (1 + 입찰가)`, 빈칸은 0으로 계산. 검색량² ÷ 경쟁 = KEI(검색 많고 셀러 적을수록 큼).
  - 숫자 출처: 검색량 = Keyword Index `pv`, 경쟁 = `resultCnt`(`--nd` 창, 기본 30d) · 노출·클릭 = 그 상품의 그 검색어 Product Analyzer `keywordEffect`(기간 합), CTR = 클릭÷노출 · 문의 = 그 상품 광고 Word Report `cvr3` · L1+ % = 그 상품 광고 Word Report `fiGrowthClickCnt ÷ clickCnt` · 입찰가 = 광고 키워드 표의 입찰가 열(없으면 빈칸, CPC 로 대신하지 않음).

### 입찰가

- AI 캠페인(GGS, 유형 51): `ads-campaigns` 응답 `properties.maxPrice` = 전환 1건 목표가(bidType 4, optimizeTarget whole_site_fb). 예측 문의 비용 = 목표가 그대로.
- 키워드: 광고 키워드 표에 입찰가 열이 있으면 그 값, 없으면 상품(또는 계정) 평균 CPC 를 입찰가 자리에 쓰고 출처에 "입찰가 없음"이라 적는다. 예측 문의 비용 = 입찰가 ÷ 전환율, 전환율은 키워드 → 상품 → 계정 순(클릭 20 이상일 때만), 어느 수준을 썼는지 `rate_level` 칸.
- Manual 캠페인(유형 1,2) 표는 `engine/pages.json` 의 `ads-report.rangeTables` 중 `manual.*` 항목이다. 상품별 cost/bid 가 나오는 계정에서 `coverage` 가 `component_ggs_offline_product`·`component_ggs_offline_keyword` 를 "없음"으로 보이면: 셀러센터 광고 리포트 Manual 탭을 Aside 로 열고 `alibaba-data dump --pages ads-report` 로 그 탭이 부른 `component/data` 요청의 `tableName`·`filters`·`extendParams.context` 를 `raw/ads-report.jsonl` 에서 읽어 `manual.*` 항목에 넣는다. 코드 수정은 필요 없다.

## Accio Work (`alibaba-accio`) — GGS 셀러 계정 기준

Accio Work 데스크톱 앱이 에이전트용으로 띄우는 로컬 게이트웨이를 그대로 부른다. Aside·셀러센터 쿠키를 쓰지 않는다.

- 게이트웨이: `http://localhost:4097` (앱이 정함). 도구 목록 `GET /mcp/proxy/tools`, 호출 `POST /mcp/proxy {name, arguments, trace:{connectId}}`, 인증 HTTP Basic `phoenix:<비밀번호>`.
- 비밀번호 출처: 앱이 켤 때마다 새로 만들어 `~/.accio/accounts/<Accio 계정>/.accio/runtime/gateway-cli.json` 에 적는다. `alibaba-accio login` 이 그 값을 OS 비밀 저장소(맥 키체인 / Windows DPAPI, 서비스 `aliops-accio-gateway`)로 옮기고, 이후 호출은 저장소에서 읽는다. 앱을 다시 켜서 401 이 나면 앱 파일에서 한 번 다시 옮기고 재시도한다. 값은 출력·로그·레포에 안 남는다.
- connectId: 앱의 `connected-accounts.json` 에 연결된 알리바바 계정(`alibaba:<connector_id>`). 여럿이면 `--connector <이름 일부>`.

**설치 단계** (위 노트북 설치 1·2단계 뒤)

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

## 문의창(OneTalk) 조작

```sh
alibaba-inquiry ui probe     --account A                                   # 툴바 아이콘 hover 툴팁·역할(클릭 0회)
alibaba-inquiry ui probe     --account A --allow-open                      # + 더보기 메뉴를 열어 항목 텍스트
alibaba-inquiry tags list    --account A
alibaba-inquiry template list --account A --allow-open
alibaba-inquiry draft        --account A --inquiry-id N --allow-open [--button copilot|"draft summary"|"content enhancer"]
alibaba-inquiry reply        --account A --inquiry-id N --text-file reply.txt          # dry-run
alibaba-inquiry send-card    --account A --trade-id N
alibaba-inquiry send-file    --account A --inquiry-id N --path catalog.pdf
alibaba-inquiry send-product --account A --inquiry-id N --product-id 1600000000000
alibaba-inquiry template send --account A --inquiry-id N --template "Welcome"
alibaba-inquiry tag add|remove --account A --inquiry-id N --tag "VIP"
ALIBABA_SELLER_ALLOW_SEND=owner-approved alibaba-inquiry reply --account A --inquiry-id N --text-file reply.txt --send   # 실발송
```

- 기본은 dry-run: OneTalk 탭을 열어 경고 팝업 X·대화 행·입력칸·Send·툴바 아이콘 위치만 찾고, 클릭·입력·업로드는 0회. 할 일은 결과 `plan` 에 나온다(hover 는 함).
- `--allow-open`: 팝업 닫기·대화 열기·패널/메뉴 열기만 한다(입력·전송 없음). 읽기 명령(template list, draft, ui probe)용.
- 실발송·태그 변경은 `--send` 와 `ALIBABA_SELLER_ALLOW_SEND=owner-approved` 가 둘 다 있어야 한다. 하나라도 없으면 Aside 를 부르기 전에 거부(종료 코드 77).
- 대화 지정은 `--inquiry-id`/`--trade-id`. 먼저 문의 목록 API(읽기)로 그 대화를 찾는다. dry-run 은 대화를 열지 않고(클릭 0, 읽음 처리 0) 할 일만 보여 주므로 안 읽은 대화에도 된다. 대화를 여는 `--allow-open`·`--send` 는 안 읽은 대화면 `--allow-mark-read` 없이는 거부한다(열면 읽음 처리되므로). 실제 클릭 시험은 이미 읽은 대화로 한다. 문의 목록은 임시 폴더에 받아 쓰고 지운다.
- 아이콘은 hover 툴팁·aria·title·class·svg 이름으로 찾는다(좌표 고정 없음).
- 대화 행 매칭(위에서부터 먼저 하나로 정해지는 단계):
  1. `ali-id`: 문의 목록 sender 의 계정 id(accountId·alitalkId 등)가 행 `data-ali-id` 와 같다.
  2. `cid`: 문의의 id(secTradeId·messageId·tradeId 등)가 행 `data-cid` 안에 있다.
  3. `any-attribute-id`: 문의/거래 ID 가 행 속성 값 어딘가에 있다.
  4. `modify-time(+country)`: 행 `data-modify-time` 이 문의 lastestReplyTime 등과 2분 안. 행에 국가 코드(alt/title)가 보이면 같아야 한다.
  - 한 단계에서 후보가 2개 이상이면 `conversation_ambiguous` 로 거부한다(아무것도 열지 않음).
  - 화면에 없으면 목록을 휠로 스크롤해 더 읽는다(최대 40번, 목록이 안 바뀌면 멈춤). 스크롤은 dry-run 에서도 하고 대화는 열지 않는다.
  - sender id 값은 문의 목록을 받는 Aside 스크립트가 stdout 한 줄(`LOOKUP`)로만 넘긴다. 임시 덤프·로그·출력에 쓰지 않고, OneTalk 스크립트에는 stdin 으로만 간다. 결과 `found.match` 에는 방식 이름만, `found.matchKeys` 에는 개수만 나온다.
  - 못 찾으면 `conversation_not_located` 와 행 속성 이름 목록을 낸다. sender 하위 키 이름은 `alibaba-data inquiries fields`(`sender.*`, 값 없음)로 본다.
- 답장 본문은 실발송 때만, Aside stdin 으로만 넘긴다(프로세스 인자·로그·결과에 없음). 결과에는 위치·라벨·개수만 나온다.
- 이 조작부는 셀러센터 화면 구조에 기대므로 라이브 확인 전까지 실험 단계다. 먼저 `ui probe` 로 확인한다.

## 바이어 정보

- 문의 목록은 `message.alibaba.com/message/ajax/feedback/subjectList.htm` 만 부른다. 대화를 열지 않아 읽음 처리가 바뀌지 않는다.
- 브라우저 안에서 허용 필드만 남긴다: id·tradeId·시각·출처 코드·유형·상품ID·국가·답장 상태·안읽음·스팸·기기, 그리고 `level/grade` 이름의 짧은 코드 값(예 `L3`). 이름·이메일·전화·제목·본문은 저장하지 않는다. 응답의 키 이름 목록(값 없음)은 `alibaba-data inquiries fields` 로 본다.
- 덤프 결과에는 주문·RFQ·방문자 국가 같은 바이어 데이터가 들어간다. 결과는 고객사 자산 폴더에만 두고 레포·채팅에 원문을 붙이지 않는다.

## 종료 코드

0 정상 · 1 문의창 조작 실패(결과 status) · 2 세션 만료(`login` 후 재실행) · 3 로그인 필요(다른 셀러 로그인, 저장 쿠키 없음, 다시 읽어도 만료: 이유는 stderr) · 4 토큰 형태 문자열 발견(결과 삭제됨) · 5 알리바바 봇 확인 페이지 · 64 인자 오류 · 77 안전 관문 거부

## 구조

- `bin/alibaba-inquiry.mjs`(문의대응)·`bin/alibaba-data.mjs`(데이터분석)·`bin/alibaba-seller.mjs`(옛 이름, 전체) → `lib/cli.mjs` 명령 표·범위·계정·기간·창 나누기, `lib/dates.mjs` 기간 해석.
- `engine/seller_central_raw.py` + `engine/capture.js` + `engine/pages.json` 덤프 엔진(Aside repl). `engine/login.js` 로그인.
- `engine/cdp_runner.mjs` 같은 repl 스크립트를 임시 프로필 크롬/엣지에서 CDP 로 돌리는 러너(Aside 전역 openTab·evaluate·snapshot·locator·mouse·keyboard·fs 호환층). `lib/runner.mjs` 러너 고르기(aside|chrome|edge|auto), `lib/cookies.mjs` 저장 쿠키 읽기.
- `engine/report.py` 덤프 폴더 → 표(문의·광고·키워드·입찰가·분석·coverage). `engine/inquiry_attribution.py` 문의 → 상품 연결 규칙.
- `skills/alibaba-ad-analysis/` 광고 분석 스킬(SKILL.md + scripts/analyze.py, 이 레포의 alibaba-data 를 부름).
- 테스트: `test/*.test.mjs`(node --test), `test/test_*.py`(unittest). 픽스처는 합성 데이터. 실제 크롬을 띄우는 스모크는 `ALIBABA_SELLER_E2E=1 npm test` 일 때만(로컬 HTML, 네트워크 안 씀).

## 광고표 끝까지 받기

광고 리포트 표는 한 번 받을 때 약 105초 안에서 페이지를 넘긴다. 큰 표(키워드·검색어·상품×일)는 그 안에 다 못 받을 수 있어서, 엔진이 받은 것을 두고 다음 페이지부터 다시 받기를 표마다 API `totalRows` 에 닿을 때까지 자동으로 반복한다(`--max-passes`, 기본 20회; 한 번 더 받아도 늘지 않으면 멈춤). 그래도 모자라면 manifest 에 `rangeIncomplete` 가 남고, 같은 명령을 다시 돌리면 캐시를 쓰지 않고 이어 받는다. `alibaba-data ads tables` 의 `completeness.csv` 로 표마다 받은 행·전체 행을 확인한다.

