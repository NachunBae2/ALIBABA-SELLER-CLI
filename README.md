# ALIBABA-SELLER-CLI

내 알리바바 스토어 데이터를 뽑습니다. 문의·광고·키워드·상품을 분석합니다.
OneTalk에서 답장·명함·파일·상품 카드를 보내고, 대화 목록·대화 내용·읽음 상태를 다룹니다. 명령을 실행하면 바로 보냅니다(미리보기만 = `--dry-run`).

## 무엇을 할 수 있나

| 셀러 업무 | 쓰는 명령 | 결과물 |
|---|---|---|
| 문의 확인 | `alibaba-data inquiries list`, `alibaba-data inquiries stats` | 문의 목록·국가별·답장 상태별 통계 |
| 상품별 문의 확인 | `alibaba-data inquiries by-product` | 상품별 문의·유입 경로 |
| 상점 지표 확인 | `alibaba-data store summary`, `store trends`, `store visitors`, `store today` | 일별 요약·방문 상세, 서버 기본 추세, 오늘 지표 |
| 상점 별점 확인 | `alibaba-data store rating`, `store health`, `store indicators`, `store thresholds`, `store country-rules`, `store benefits` | 현재 별점·건강도·평가 지표·기준·국가 규칙·혜택 |
| 상품 성과 확인 | `alibaba-data analytics products` | 상품별 노출·클릭·방문·문의 |
| 문의 폴더 확인 | `alibaba-data inquiries folders` | 고객 그룹·폴더·유형 이름과 응답 개수(미반환은 빈칸) |
| 상품 위험·성장 점수 | `alibaba-data products risk-counts`, `products power-score`, `products risk-tasks` | 위반/개선 집계·전체 상품 점수·위험 작업 집계 |
| 저장 검색·주문 | `alibaba-data rfq saved-searches`, `alibaba-data orders list` | RFQ 저장 검색·전체 주문 목록(바이어 이름·연락처 제외) |
| AI 지식베이스 | `alibaba-data knowledge metrics`, `knowledge storage` | 호출·파일 지표와 저장 용량 raw |
| 광고 효율 확인 | `alibaba-data ads quality`, `alibaba-data ads analyze` | L1+ 클릭 비율·문의 1건 비용·CSV·요약 |
| 상품 키워드 고르기 | `alibaba-data keywords recommend` | 상품 하나의 추천 키워드 |
| OneTalk 응대 | `alibaba-inquiry reply`, `alibaba-inquiry send-card`, `alibaba-inquiry send-file`, `alibaba-inquiry send-product` | 답장·명함·파일·상품 카드 발송 + 대화에서 다시 읽어 확인. 상품은 ID 또는 검색어로 지정 |
| OneTalk 대화 보기·읽음 | `alibaba-inquiry conversations`, `alibaba-inquiry buyer`, `alibaba-inquiry messages`, `alibaba-inquiry mark-read` | 대화 목록·바이어 등급·대화 내용(이메일·전화 가림)·읽음/안읽음 표시 |

상점 명령은 저장 쿠키 API 전용입니다. `store summary|visitors`는 `--start/--end/--days`로 일별 조회합니다.
`trends`는 서버 기본 기간, `today`와 별점 명령은 현재 스냅샷이며 출력에 기간 제한을 표시합니다.
`--json`, `--csv`, `--from`도 지원합니다. [상점 명령과 기간 정의](docs/REFERENCE.md#상점-지표-alibaba-data-store-)를 참고하세요.

## 처음 설치

위 폴더·상품 위험·저장 검색·주문·지식베이스 명령은 현재 스냅샷을 읽습니다.
`--account <계정키> --transport api --fresh --json`으로 조회하며 `--csv`, `--from <덤프 폴더>`도 지원합니다.
`--start/--end/--days`는 이 8개 명령의 서버 조회 기간을 바꾸지 않습니다. 주문은 전체 상태 목록입니다.
목록은 서버 total과 받은 행 수를 대조하고, 반복 페이지·업무 오류·미완료 수집은 실패 종료합니다.
자세한 열·단위·빈 응답 해석은 [명령 참조](docs/REFERENCE.md#폴더상품-위험rfq-저장-검색주문ai-지식베이스)를 참고하세요.

명령은 터미널에 한 줄씩 붙여 넣고 Enter를 누릅니다.
`<...>`는 내 정보로 바꿀 자리입니다. 괄호까지 바꾸세요. 비밀번호는 명령이나 파일에 적지 않습니다.
성공 예시의 버전·경로·데이터는 내 화면과 다를 수 있습니다.

### 맥

1. **Node.js와 Python 확인**

   Spotlight에서 **터미널**을 찾아 엽니다. [Node.js 다운로드](https://nodejs.org/en/download)에서 Node.js 22 이상을 설치합니다.
   Homebrew를 쓴다면 `brew install node`로 설치해도 됩니다. 설치 뒤 터미널을 다시 엽니다.
   Python은 3.9 이상이 필요합니다. 맥의 `python3`를 확인하세요. 없거나 너무 낮으면 [Python 다운로드](https://www.python.org/downloads/)에서 설치합니다.
   npm은 Node.js와 함께 설치되며, 이 레포는 `package.json`에 dependencies가 없어 `npm install`이 필요 없습니다.

   ```sh
   node -v
   python3 -V
   ```

   이렇게 나오면 성공: Node는 `v22.x.x` 이상, Python은 `3.9` 이상입니다.

   ```text
   # 예시 (버전은 설치 시점에 따라 다름)
   v22.0.0
   Python 3.12.0
   ```

2. **Aside 브라우저 설치**

   Aside는 로그인·캡차 해결과 REPL HTTP 전송에 사용할 수 있습니다. 데이터·문의 조회는 저장 쿠키로 HTTP API를 호출합니다.

   [Aside 다운로드](https://aside.com/download)에서 설치하고 앱을 켭니다.
   Homebrew 대안은 `brew install --cask aside`입니다.

   ```sh
   aside --version
   aside --host local repl "console.log(1)"
   ```

   이렇게 나오면 성공: 버전 번호 뒤에 연결 확인 결과 `1`과 `[ok | 6ms]`가 두 줄로 보입니다. 버전과 ms 값은 다를 수 있습니다.

   ```text
   # 예시 (버전 형식은 설치된 Aside --version으로 확인)
   1.26.1115.1556
   1
   [ok | 6ms]
   ```

3. **레포 받기: A 또는 B 중 하나**

   A. Git이 있다면 아래 명령을 씁니다. 레포는 프로그램 파일이 들어 있는 폴더입니다.

   ```sh
   cd ~/Downloads
   git clone https://github.com/NachunBae2/ALIBABA-SELLER-CLI.git
   ```

   B. Git을 모르거나 설치하지 않았다면 [GitHub 레포](https://github.com/NachunBae2/ALIBABA-SELLER-CLI)의 초록 **Code → Download ZIP**을 누릅니다.
   다운로드 폴더에서 압축을 풉니다. 폴더 이름은 `ALIBABA-SELLER-CLI-main`입니다.

   이렇게 나오면 성공: A는 아래 메시지가 시작되고 오류 없이 끝납니다. B는 다운로드 폴더에 압축 푼 폴더가 보이면 됩니다.

   ```text
   # 예시 (Git 출력 첫 줄)
   Cloning into 'ALIBABA-SELLER-CLI'...
   ```

4. **받은 폴더로 이동**

   터미널은 Spotlight에서 엽니다. `cd`는 작업할 폴더로 이동하는 명령입니다.

   Git으로 받았다면:

   ```sh
   cd ~/Downloads/ALIBABA-SELLER-CLI
   pwd
   ```

   ZIP으로 받았다면:

   ```sh
   cd ~/Downloads/ALIBABA-SELLER-CLI-main
   pwd
   ```

   이렇게 나오면 성공: 경로 끝이 받은 폴더 이름입니다. ZIP 폴더 안에 같은 이름의 폴더가 있으면 안쪽 폴더로 이동합니다.
   다른 곳에 풀었다면 그 경로로 이동하세요.

   ```text
   # 예시 (경로는 내 컴퓨터에 따라 다름)
   /Users/example/Downloads/ALIBABA-SELLER-CLI
   ```

5. **명령 설치**

   받은 폴더 안에서 실행합니다. `npm link`는 이 폴더의 명령을 컴퓨터에 연결합니다.

   ```sh
   npm link
   alibaba-data --help
   ```

   이렇게 나오면 성공: 도움말 첫 줄입니다.

   ```text
   alibaba-data (데이터분석) <명령> --account <계정키> --start YYYY-MM-DD --end YYYY-MM-DD [--json|--csv] [--out DIR]
   ```

6. **셀러센터 로그인과 계정 설정**

   Aside 첫 프로필(`u0`)에서 [셀러센터 로그인](https://login.alibaba.com)을 엽니다. 내 셀러 계정으로 로그인합니다.
   두 번째 프로필을 쓴다면 아래 `asideAccount`를 `u1`로 바꿉니다. Aside는 한 번에 한 작업만 실행하세요.

   아래는 계정 설정 예시입니다. 붙여 넣기 전에 메모 앱 등에서 `<...>` 자리를 내 값으로 바꾼 뒤 통째로 붙여 넣으세요.

   ```sh
   mkdir -p ~/.aliops-ops/seller-report
   cat > ~/.aliops-ops/seller-report/accounts.json <<'JSON'
   {
     "mystore": {
       "loginId": "<셀러센터 로그인 ID>",
       "outDir": "/Users/<맥 사용자>/Downloads/seller-report",
       "asideAccount": "u0",
       "keychainService": "aliops-mystore-seller",
       "keychainAccount": "<로그인 이메일>"
     }
   }
   JSON
   ```

   아래 명령으로 JSON 문법을 확인합니다. 성공하면 JSON이 다시 출력되고, 오류가 있으면 `Expecting ...` 같은 문구가 나옵니다.

   ```sh
   python3 -m json.tool ~/.aliops-ops/seller-report/accounts.json
   ```

   `mystore`는 내가 붙인 계정 별명입니다. `loginId`는 셀러센터 로그인에 쓰는 ID이며 로그인 흐름에서 현재 브라우저 계정과 비교합니다. `keychainAccount`(로그인 이메일)는 이 값과 다를 수 있습니다.
   `alibaba-data login`은 Aside에 해당 셀러로 이미 로그인돼 있으면 `already_signed_in`으로 끝내고, 세션이 끊겼을 때 키체인에 저장한 비밀번호로 다시 로그인합니다.
   계정 별명 `mystore`를 바꾸면 `keychainService` 이름과 이후 모든 명령의 `--account`도 함께 바꿉니다.
   `outDir`는 결과를 둘 폴더입니다. `/Users/<맥 사용자>`를 내 홈 경로로 바꾸세요.
   비밀번호는 맥 키체인에만 저장합니다. 아래 이메일도 내 것으로 바꾸고, 비밀번호는 물어볼 때만 입력하세요.
   `security` 명령은 `password data for new item:`으로 비밀번호를, `retype password for new item:`으로 한 번 더 확인합니다. 입력해도 글자가 보이지 않는 것이 정상입니다.

   위 설정과 같은 서비스·이메일을 사용합니다.

   ```sh
   chmod 600 ~/.aliops-ops/seller-report/accounts.json
   security add-generic-password -s aliops-mystore-seller -a '<로그인 이메일>' -w
   alibaba-data login --account mystore
   ```

   이렇게 나오면 성공: `signed_in` 또는 `already_signed_in`입니다. 인증 화면이 남으면 Aside에서 직접 마친 뒤 다시 실행하세요.

   ```text
   # 예시 (테스트용 가짜 데이터)
   {"account": "mystore", "login": "already_signed_in"}
   ```

   **Aside 없이 평소 크롬·엣지로**: 크롬(또는 엣지)에 셀러센터가 로그인돼 있으면 그 쿠키를 가져와 씁니다. 이후 데이터 명령은 저장 쿠키로 Python HTTP를 직접 호출하며, 유효한 세션에서는 브라우저를 띄우지 않습니다.

   ```sh
   alibaba-data login --account mystore --browser chrome
   alibaba-data session status --account mystore
   ```

   이렇게 나오면 성공: `"session": "valid"`입니다. 쿠키 값은 화면에 나오지 않습니다. 크롬에 로그인된 프로필이 없으면 `--sign-in`으로 전용 프로필에 키체인 비밀번호로 로그인합니다. 엣지·윈도우·안 되는 경우는 [자세히](docs/REFERENCE.md)의 5-1.

7. **첫 문의 목록 보기**

   ```sh
   alibaba-data inquiries list --account mystore --days 7 --limit 5
   ```

   이렇게 나오면 성공: 수집 안내 뒤 `== inquiries` 표가 나옵니다. 문의가 없으면 `== inquiries (0행)`도 정상입니다.
   아래는 표의 일부입니다. 열 이름은 실제 출력과 같습니다.

   ```text
   # 예시 (테스트용 가짜 데이터)
   # 표 일부
   == inquiries (2행)
   inquiry_id  buyer_country  reply_status  unread  spam
   1           US             1             False   False
   ```

### 윈도우 (PowerShell)

오프라인 테스트는 Windows CI에서 실행합니다. **실제 Windows에서 Aside와 셀러센터를 연결해 실행하는 과정은 아직 확인하지 못했습니다.**

1. **Node.js와 Python 설치**

   시작 메뉴에서 **PowerShell**을 엽니다. [Node.js 다운로드](https://nodejs.org/en/download)에서 Node.js 22 이상을 설치합니다.
   아래 첫 줄로 설치해도 됩니다. Python은 3.9 이상이 필요합니다.
   npm은 Node.js와 함께 설치되며, 이 레포는 `package.json`에 dependencies가 없어 `npm install`이 필요 없습니다.

   ```powershell
   winget install OpenJS.NodeJS.LTS
   winget install Python.Python.3.12
   ```

   PowerShell을 닫았다 다시 열고 확인합니다. 이 프로그램은 Windows에서 `py -3`, 그다음 `python` 순으로 찾습니다.

   ```powershell
   node -v
   py -3 -V
   ```

   이렇게 나오면 성공: Node는 `v22.x.x` 이상, Python은 `3.9` 이상입니다.

   ```text
   # 예시 (버전은 설치 시점에 따라 다름)
   v22.0.0
   Python 3.12.0
   ```

2. **Aside 브라우저 설치**

   Aside는 로그인·캡차 해결과 REPL HTTP 전송에 사용할 수 있습니다. 데이터·문의 조회는 저장 쿠키로 HTTP API를 호출합니다.

   [Aside 다운로드](https://aside.com/download)에서 설치하고 앱을 켭니다. PowerShell을 다시 열고 확인합니다.

   ```powershell
   aside --version
   aside --host local repl "console.log(1)"
   ```

   이렇게 나오면 성공: 버전 번호 뒤에 연결 확인 결과 `1`과 `[ok | 6ms]`가 두 줄로 보입니다. 버전과 ms 값은 다를 수 있습니다.

   ```text
   # 예시 (버전 형식은 맥의 Aside --version으로 확인)
   1.26.1115.1556
   1
   [ok | 6ms]
   ```

3. **레포 받기: A 또는 B 중 하나**

   A. Git이 있다면 다음 명령을 씁니다.

   ```powershell
   cd $HOME\Downloads
   git clone https://github.com/NachunBae2/ALIBABA-SELLER-CLI.git
   ```

   B. Git이 없다면 [GitHub 레포](https://github.com/NachunBae2/ALIBABA-SELLER-CLI)의 초록 **Code → Download ZIP**을 누릅니다.
   다운로드 폴더에서 ZIP을 우클릭해 **모두 추출**합니다. 폴더 이름은 `ALIBABA-SELLER-CLI-main`입니다.

   이렇게 나오면 성공: A는 아래 메시지가 시작되고 오류 없이 끝납니다. B는 압축 푼 폴더가 보이면 됩니다.

   ```text
   # 예시 (Git 출력 첫 줄)
   Cloning into 'ALIBABA-SELLER-CLI'...
   ```

4. **받은 폴더로 이동**

   시작 메뉴에서 PowerShell을 엽니다. 아래는 다운로드 폴더에 받은 경우입니다.

   ```powershell
   # A: Git으로 받은 경우
   cd $HOME\Downloads\ALIBABA-SELLER-CLI
   # B: ZIP으로 받은 경우에는 위 줄 대신 아래 줄
   # cd $HOME\Downloads\ALIBABA-SELLER-CLI-main
   (Get-Location).Path
   ```

   이렇게 나오면 성공: 경로 끝이 받은 폴더 이름입니다. ZIP 폴더 안에 같은 이름의 폴더가 있으면 안쪽 폴더로 이동합니다.

   ```text
   # 예시 (경로는 내 컴퓨터에 따라 다름)
   C:\Users\example\Downloads\ALIBABA-SELLER-CLI
   ```

5. **명령 설치**

   ```powershell
   npm link
   alibaba-data --help
   ```

   이렇게 나오면 성공: 도움말 첫 줄입니다. `npm.ps1` 오류가 나면 아래 오류 해결 표를 보세요.

   ```text
   alibaba-data (데이터분석) <명령> --account <계정키> --start YYYY-MM-DD --end YYYY-MM-DD [--json|--csv] [--out DIR]
   ```

6. **셀러센터 로그인과 계정 설정**

   Aside 첫 프로필(`u0`)에서 [셀러센터 로그인](https://login.alibaba.com)을 열고 로그인합니다.
   설정 파일은 레포 밖에 둡니다. 아래 명령 뒤 메모장이 파일을 만들지 물으면 만듭니다.

   ```powershell
   New-Item -ItemType Directory -Force "$HOME\.aliops-ops\seller-report"
   notepad "$HOME\.aliops-ops\seller-report\accounts.json"
   ```

   아래는 계정 설정 예시입니다. `<Windows 사용자>`와 로그인 정보를 내 것으로 바꾸고 저장하세요.
   `outDir`의 역슬래시는 JSON 안에서 두 번 씁니다.

   ```json
   {
     "mystore": {
       "loginId": "<셀러센터 로그인 ID>",
       "outDir": "C:\\Users\\<Windows 사용자>\\Downloads\\seller-report",
       "asideAccount": "u0",
       "keychainService": "aliops-mystore-seller",
       "keychainAccount": "<로그인 이메일>"
     }
   }
   ```

   ```powershell
   py -3 -m json.tool "$HOME\.aliops-ops\seller-report\accounts.json"
   ```

   `mystore`는 내가 붙인 계정 별명입니다. `loginId`는 셀러센터 로그인에 쓰는 ID이며 로그인 흐름에서 현재 브라우저 계정과 비교합니다. `keychainAccount`(로그인 이메일)는 이 값과 다를 수 있습니다.
   `alibaba-data login`은 Aside에 해당 셀러로 이미 로그인돼 있으면 `already_signed_in`으로 끝내고, 세션이 끊겼을 때 저장한 비밀번호로 다시 로그인합니다.
   계정 별명 `mystore`를 바꾸면 `keychainService` 이름과 이후 모든 명령의 `--account`도 함께 바꿉니다.
   비밀번호는 DPAPI로 암호화해 저장합니다. 같은 Windows 사용자만 열 수 있습니다.
   받은 레포 폴더에서 아래 명령을 실행합니다. 이메일은 내 것으로 바꾸고, 비밀번호는 물어볼 때만 입력합니다.

   ```powershell
   # 예시 (위 설정과 같은 서비스·이메일 사용)
   $p = Read-Host -AsSecureString "셀러센터 비밀번호"
   [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($p)) | py -3 engine\secret_store.py set aliops-mystore-seller '<로그인 이메일>'
   alibaba-data login --account mystore
   ```

   저장 위치는 `%APPDATA%\alibaba-seller-cli\secrets\<서비스>@<계정>.dpapi`입니다.
   이렇게 나오면 성공: `signed_in` 또는 `already_signed_in`입니다. 인증 화면은 Aside에서 직접 마칩니다.

   ```text
   # 예시 (테스트용 가짜 데이터)
   # Windows 실기기 미확인
   {"account": "mystore", "login": "already_signed_in"}
   ```

7. **첫 문의 목록 보기**

   ```powershell
   # 예시 (mystore는 내가 정한 계정 별명)
   alibaba-data inquiries list --account mystore --days 7 --limit 5
   ```

   이렇게 나오면 성공: 수집 안내 뒤 문의 표가 보입니다. `== inquiries (0행)`이면 해당 기간 문의가 없는 것입니다.

   ```text
   # 예시 (테스트용 가짜 데이터)
   # 표 일부
   == inquiries (2행)
   inquiry_id  buyer_country  reply_status  unread  spam
   1           US             1             False   False
   ```

### 흔한 오류 해결

프로그램 오류 문구는 코드의 원문입니다. npm·셸·PowerShell 오류는 프로그램 밖에서 나옵니다. 설치 경로와 언어에 따라 나머지 문장이 달라집니다.

| 증상 (오류 문구) | 원인 | 해결 |
|---|---|---|
| `npm error code EACCES` | npm 전역 폴더에 쓸 권한이 없음 | 맥: `npm config set prefix ~/.local/npm-global` 실행 후 `echo 'export PATH="$HOME/.local/npm-global/bin:$PATH"' >> ~/.zshrc`를 실행하고 터미널을 다시 연 뒤 `npm link`. |
| `command not found: alibaba-data` 또는 `alibaba-data : The term 'alibaba-data' is not recognized as the name of a cmdlet` | 명령 연결 또는 PATH가 빠짐 | 받은 폴더에서 `npm link`. `npm prefix -g`로 위치 확인. 맥은 그 위치의 `bin` 폴더, Windows는 그 위치 자체를 PATH에 추가한 뒤 터미널을 다시 열기. |
| `npm WARN EBADENGINE Unsupported engine` 또는 `npm warn EBADENGINE Unsupported engine` | Node 버전이 낮음. 이 레포는 Node 22 이상 필요 | `node -v` 확인. Node.js 22 이상을 설치하고 터미널을 다시 열기. npm 버전에 따라 대소문자가 다름. |
| `aside_failed` 또는 Aside 명령에 응답이 없음 | Aside 앱이나 연결 확인 필요 | 앱을 다시 켜고 `aside --host local repl "console.log(1)"` 확인. 다른 Aside 작업이 끝날 때까지 기다리기. |
| `Aside is signed in to a different seller than loginId -> sign out in Aside, then --login` (종료 코드 3) | 설정한 계정과 브라우저 계정이 다름 | `accounts.json`의 `loginId` 확인. Aside에서 로그아웃하고 해당 셀러로 다시 로그인. `alibaba-data login --account mystore` 후 재실행. |
| `loginId 가 비어 있다` | 설정 파일의 로그인 ID가 비어 있음 | `accounts.json`에 내 셀러센터 로그인 ID 입력. |
| `키체인이 15초 동안 응답하지 않음(잠겨 있거나 GUI 세션이 아님)` (종료 코드 69) | 키체인이 잠겼거나 승인 창이 화면에 안 보임(SSH 등) | 맥 화면의 승인 창 확인 또는 GUI 터미널에서 `security unlock-keychain ~/Library/Keychains/login.keychain-db` 후 다시. 대기 시간은 `ALIBABA_SELLER_KEYCHAIN_TIMEOUT=30`. |
| `keychain_item_missing` | 저장한 비밀번호를 못 찾음 | 설정의 서비스·이메일과 비밀번호 저장 명령의 서비스·이메일을 같게 맞춰 다시 저장. |
| `저장된 로그인 쿠키 없음: alibaba-data login --account <계정키>` 또는 `로그인 필요(쿠키 만료): alibaba-data login` | OneTalk 명령이 쓸 로그인이 없거나 만료됨 | `alibaba-data login --account <계정키>` 후 다시 실행. |
| `npm.ps1 cannot be loaded because running scripts is disabled on this system.` | PowerShell이 스크립트 실행을 막음 | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 실행 후 `npm link` 재시도. 조직 관리 PC는 관리자 정책을 따르기. |

## 이럴 때 이 명령

명령의 `mystore`는 내가 정한 계정 별명으로 바꿔 씁니다. 날짜는 원하는 기간으로 바꿉니다. 광고 날짜는 미국 태평양 기준입니다.
결과 예시는 모두 테스트용 가짜 데이터입니다. 긴 표는 일부 열·행만 보여 줍니다. 실제 출력에는 기간·출처 등 열이 더 있습니다.

| 옵션 | 뜻 |
|---|---|
| `--start YYYY-MM-DD --end YYYY-MM-DD` | 시작일·종료일을 지정합니다. 미국 태평양 어제보다 늦은 종료일은 그날로 자르고 경고합니다. |
| `--days N` | 셀러센터 `Last N days`와 같은 기간입니다(끝 날짜는 미국 태평양 어제). |
| `--weeks N` | 완료된 최근 N주(PST 일~토)입니다. |
| `--max`, `--all` | 서버 날짜 필터의 최초일~최신 집계일. 최초일 미확인 표는 명시 기간이 필요합니다. |

기간 옵션은 한 종류만 씁니다. N은 1 이상의 정수입니다.

### 이번 달 문의 보기

시작일·종료일로 문의 목록과 통계를 봅니다. `--days 7`은 셀러센터 `Last 7 days`와 같은 기간입니다.

최근 기간으로 보려면 `--days` 명령을 사용합니다.

```sh
alibaba-data inquiries list --account mystore --start 2026-11-01 --end 2026-11-17
alibaba-data inquiries stats --account mystore --start 2026-11-01 --end 2026-11-17
alibaba-data inquiries list --account mystore --days 7 --limit 5
```

```text
# 예시 (테스트용 가짜 데이터)
# 출력 일부
== inquiries (2행)
inquiry_id  buyer_country  reply_status  unread  spam
1           US             1             False   False

== inquiry_stats (15행)
dimension  value      inquiries
lead_type  상품 문의    1
lead_type  TM/IM 대화  1
```

### 상품별 문의·상품 성과 보기

상품별 문의와 Product Analyzer의 노출·클릭·방문·문의 성과를 나란히 봅니다. 광고 효율은 다음 레시피에서 봅니다.

```sh
alibaba-data inquiries by-product --account mystore --days 30
alibaba-data analytics products --account mystore --weeks 4
```

```text
# 예시 (테스트용 가짜 데이터)
# 출력 일부
== inquiry_by_product_counts (2행)
inflow   product_title  inquiries
100                     1
TM 유입                  1

== analytics_products (2행)
product_id  product_title  views  clicks  visitors  inquiries
1           fixture 1      150    15      12        1
2           fixture 2      10     0       0         0
```

Product Analyzer는 완료된 주·달 단위입니다. 실제 합산 기간은 `covered_start`·`covered_end`를 확인하세요.

`inflow`는 상품 문의면 상품 ID(위 예시의 `100`), TM 유입이면 `TM 유입`입니다.

### 광고 분석: 문의 1건 비용·L1+ 바이어

L1+ 클릭 비율과 문의 1건 비용을 보고, 분석 파일을 `ad` 폴더에 저장합니다.
`--out ./ad`는 지금 터미널이 있는 폴더 아래 `ad` 폴더를 뜻합니다.

```sh
alibaba-data ads quality --account mystore --days 30
alibaba-data ads analyze --account mystore --days 30 --out ./ad
```

```text
# 예시 (테스트용 가짜 데이터)
# 출력 일부
== buyer_quality (3행)
level     campaign_kind  l1plus_click_pct  clicks  inquiries_cvr3  spend_usd  cost_per_inquiry_usd
campaign  AI Smart       0.0               40      2               80.0       40.0
month     AI Smart       0.0               40      2               80.0       40.0
total     AI Smart       0.0               40      2               80.0       40.0
written: ./ad
```

`ad/summary.md`는 요약, `ad/buyer_quality.csv`는 바이어 품질 표입니다.
`product_ad_performance.csv`·`bid_analysis.csv`·`inquiry_quality.csv`·`top_products.csv`도 함께 저장됩니다. CSV는 Excel에서 열 수 있습니다.
L1+ 클릭 비율은 전체 클릭 중 L1+ 바이어 클릭 비율입니다. 문의 1건 비용은 광고비를 문의 수로 나눈 값입니다. 문의가 없으면 비용 칸은 비워 둡니다.

### 상품 하나의 키워드 추천 받기

`<상품ID 또는 이름 일부>`를 내 상품으로 바꿉니다. 기본으로 최대 10개를 추천합니다. 검색량 근거가 있는 후보만 나옵니다.

```sh
alibaba-data keywords recommend --account mystore --days 30 --product '<상품ID 또는 이름 일부>'
```

```text
# 예시 (테스트용 가짜 데이터)
# 출력 일부
== keyword_recommendations (3행)
키워드              점수    검색량  경쟁(셀러 수)  입찰가 USD  노출  클릭  CTR %  문의  L1+ %
vitamin c serum     270000  900     30             -           40    3     7.5    1     25.0
face serum vitamin  25000   500     10             -           -     -     -      -     -
face serum          300     300     300            -           20    0     0.0    -     -
```

예시의 `-`는 실제 출력의 빈칸입니다. 점수는 검색량·경쟁·클릭·문의·L1+·입찰가로 계산하며, 자세한 공식은 [REFERENCE](docs/REFERENCE.md#키워드-raw추천)를 참조합니다.

### 검색어 상세·방문자·시장 보기

```bash
alibaba-data keywords index-detail --account mystore --keyword 'test widget' --days 7 --json
alibaba-data keywords category-top --account mystore --days 7
alibaba-data keywords breakout --account mystore --days 90
alibaba-data analytics visitor-keywords --account mystore --days 30
alibaba-data analytics market-categories --account mystore
alibaba-data analytics market-summary --account mystore --category-id '<카테고리 노드 ID>'
```

이 명령들은 저장 쿠키를 쓰는 `api` 수집 전용이다. `--fresh`는 새 조회, `--from <덤프 폴더>`는 저장된 조회 조건 그대로 재출력하며 `--json`, `--csv`, `--out`을 지원한다. 검색어·카테고리·기간별로 캐시를 구분한다.

`index-detail`·`category-top`은 7·30일(기본 7), `breakout`은 90·180일(기본 90), 방문자 키워드·상품·카테고리는 7·30·90일(기본 30)을 지원한다. 기간은 서버 기준 최근 N일이며 `--start/--end`로 과거 날짜를 조회할 수 없다. 그룹·시장 요약·시장 추세·판매자 비교는 서버 스냅샷이다.

카테고리를 생략하면 목록 API 첫 경로에서 인기어는 마지막 노드, 급상승어와 시장 분석은 첫 노드를 선택한다. `market-categories`의 `idDisplay`에서 필요한 **숫자 노드 하나**를 `--category-id`로 지정할 수 있다. `request_context` 표에서 실제 조회 조건을 확인한다. [전체 명령·지원 기간](docs/REFERENCE.md#검색어-상세방문자시장-분석)을 참고한다.

### 문의 답장·명함·카탈로그(PDF)·상품 카드 보내기

**아래 명령은 실행하면 바로 보냅니다.** 먼저 확인만 하려면 끝에 `--dry-run`을 붙입니다(아무것도 보내지 않음).
OneTalk 내부 API를 로그인 정보로 직접 부릅니다. 브라우저 클릭은 없습니다. 보낸 뒤 대화를 다시 읽어 새 메시지가 올라왔는지 확인합니다.
문의 목록의 ID로 `<문의ID>`를 바꿉니다. 답장 내용은 일반 텍스트로 `reply.txt`에 저장해 받은 폴더에 둡니다. `catalog.pdf`도 준비합니다.
상품 카드의 `<상품ID>`는 내 상품 ID입니다. 이미지 파일(jpg·png)은 사진으로, 그 밖의 파일은 파일 카드로 갑니다.

```sh
alibaba-inquiry reply --account mystore --inquiry-id '<문의ID>' --text-file reply.txt --dry-run
alibaba-inquiry reply --account mystore --inquiry-id '<문의ID>' --text-file reply.txt
alibaba-inquiry send-card --account mystore --inquiry-id '<문의ID>'
alibaba-inquiry send-product --account mystore --inquiry-id '<문의ID>' --product-id '<상품ID>'
alibaba-inquiry send-product --account mystore --inquiry-id '<문의ID>' --product '<검색어>'
alibaba-inquiry send-file --account mystore --inquiry-id '<문의ID>' --path catalog.pdf
alibaba-inquiry template send --account mystore --inquiry-id '<문의ID>' --template '<템플릿 이름 앞부분>'
alibaba-inquiry log --account mystore --days 1
```

```text
# 예시 (테스트용 가짜 데이터)
# --dry-run 결과: 보낼 내용의 요약만
{"status": "dry_run", "op": "send", "inquiryId": "1000000000077", "kind": "text", "textLength": 182}

# 보낸 결과: readback.seen = 대화를 다시 읽어 새 메시지를 찾음
{"status": "ok", "sentMessage": true, "readback": {"messageType": 1, "seen": true, "textSeen": true}}
{"status": "ok", "sentMessage": true, "readback": {"messageType": 57, "seen": true}}
```

`status`가 `ok`면 보내고 대화에서 확인까지 된 것입니다. `sent_not_confirmed`면 보냈지만 대화에서 아직 못 찾은 것이니 `messages`로 다시 봅니다.
보낸 기록은 모두 `<계정 outDir>/inquiry-replies.jsonl`에 남고 `log`로 봅니다(바이어 이름·연락처 제외).

대화 목록·대화 내용·읽음 표시:

```sh
alibaba-inquiry conversations --account mystore --count 20
alibaba-inquiry buyer --account mystore --buyer-ali-id '<바이어ID>'
alibaba-inquiry messages --account mystore --inquiry-id '<문의ID>' --count 10 --with-text
alibaba-inquiry mark-read --account mystore --inquiry-id '<문의ID>'
alibaba-inquiry mark-unread --account mystore --inquiry-id '<문의ID>'
```

빠른 답장 템플릿과 태그:

```sh
alibaba-inquiry template list --account mystore
alibaba-inquiry template send --account mystore --inquiry-id '<문의ID>' --template '<템플릿 이름 앞부분>' --dry-run
alibaba-inquiry tags list --account mystore
alibaba-inquiry tag add --account mystore --inquiry-id '<문의ID>' --tag '<태그 이름>'
alibaba-inquiry tag remove --account mystore --inquiry-id '<문의ID>' --tag '<태그 이름>'
```

`template send --dry-run`은 템플릿을 찾아 본문 길이만 보여 주고 보내지 않습니다. 이름이 여러 템플릿에 맞으면 후보 이름과 함께 멈춥니다. `tag add`·`tag remove`는 저장 뒤 다시 읽어 확인하며, 없는 태그면 `tag_not_found`로 실패(종료 코드 1)합니다.

`conversations`의 `buyerAliId`는 `--inquiry-id` 대신 `--buyer-ali-id`로 대화를 고를 때 씁니다. 각 행의 `level`은 L0~L4 또는 `null`입니다. `levelSource`는 등급 있음 `userNewLevel`, 응답 행 없음 `no_buyer_info`, 등급 필드 없음 `no_level_field`, 1:1 대화 아님(조회 제외) `not_one_to_one`입니다. `buyer`도 같은 대화 지정 방식으로 등급·출처·국가코드·가입연수·잠재점수를 조회하며 이름·연락처는 출력하지 않습니다. 응답 행이 없으면 `status: "no_buyer_info"`이며 오류가 아닙니다. 등급 조회는 20명씩 처리하고 API 호출 실패는 오류를 반환합니다. `send-product --product '검색어'`는 표시 중·승인 상품의 제목에서 찾고 후보가 정확히 1개일 때만 발송합니다. 0개면 계정 상품 수를, 복수면 후보 ID·제목을 함께 보여 주고 멈춥니다(`--product-id`가 있으면 검색하지 않음). `messages`는 읽음 처리를 하지 않습니다. `--with-text`일 때만 글을 보여 주며 이메일·전화번호는 가립니다.

### 선택: Accio Work 연동

기본 명령·폴백·설치에는 Accio가 필요 없습니다. 아래 기능만 앱을 명시적으로 사용합니다.
`--transport accio`는 Accio Work 게이트웨이(`alibaba-accio login`)로 `alibaba-inquiry conversations|messages|buyer`만 읽는다(`--buyer-ali-id`). 다른 명령은 사유 한 줄과 종료 64: 광고·상품 분석 도구가 셀러 미인증(-32003)이고 문의 목록 도구가 없다. 차이는 [REFERENCE](docs/REFERENCE.md)의 `--transport accio` 절.
`accio/features.json`은 이 선택 기능의 도구 표이며 기본 API 명령에서 읽지 않습니다.
`ACCIO_HOME`으로 앱 설정 폴더, `ACCIO_GATEWAY_URL`로 게이트웨이 주소를 바꿀 수 있습니다(앱 설정 URL보다 우선).
`npm test`는 빈 임시 `ACCIO_HOME`과 가짜 게이트웨이를 사용하며 실제 앱에 연결하지 않습니다.

Accio Work 앱을 켜고 앱 안에서 알리바바 셀러 계정을 연결한 뒤, 연결 확인과 상점 요약을 읽습니다.
이 레시피는 [기능 표](accio/features.json)에서 결과가 **됨**인 `query_ggs_shop_summary`만 씁니다. GGS 셀러 계정 기준이며 계정에 따라 결과가 다를 수 있습니다.
`statisticsType`은 `day|week|month` 중에서 고릅니다.

```sh
alibaba-accio login
alibaba-accio status
alibaba-accio shop summary --arg statisticsType=week
```

```text
# 예시 (테스트용 가짜 데이터)
# shop summary 출력 형식
{
 "success": true,
 "data": {
  "pv": 5
 }
}
```

`login` 결과의 `login: ok`, `status` 결과의 `session: ok`를 확인합니다. Accio Work는 Aside 대신 앱의 연결을 씁니다.

## AI 앱에게 맡기기

### Claude Code·Codex

[광고 분석 스킬](skills/alibaba-ad-analysis/SKILL.md)을 설치하면 AI가 이 명령을 실행하고 결과를 읽습니다. 먼저 위 설치·로그인을 끝내세요.
채팅에 아래 문장을 붙여 넣을 수 있습니다.

```text
https://github.com/NachunBae2/ALIBABA-SELLER-CLI 레포의 skills/alibaba-ad-analysis 스킬을 설치해 줘
```

직접 연결하려면 받은 레포 폴더 안에서 실행합니다. 맥 터미널 또는 Windows의 Git Bash 기준입니다.
같은 이름의 스킬이 이미 있으면 먼저 확인하세요. 아래 명령은 그 연결을 바꿉니다.

Claude Code:

```sh
mkdir -p ~/.claude/skills && ln -sfn "$PWD/skills/alibaba-ad-analysis" ~/.claude/skills/alibaba-ad-analysis
```

Codex(공유 스킬 폴더):

```sh
mkdir -p ~/.agents/skills && ln -sfn "$PWD/skills/alibaba-ad-analysis" ~/.agents/skills/alibaba-ad-analysis
```

설치 후 새 대화에서 이렇게 요청합니다. 스킬의 실제 요청 문구인 **광고 분석·문의 1건 비용·L1+ 클릭·바이어 품질**을 쓰면 됩니다.

```text
# 예시 (mystore는 내가 정한 계정 별명)
내 광고 분석해 줘. 계정은 mystore. 최근 기간의 L1+ 클릭과 문의 1건 비용을 보여 줘.
```

### ChatGPT (웹·앱)

일반 ChatGPT 채팅은 내 컴퓨터의 명령을 직접 실행하지 못합니다. 터미널에서 먼저 분석 파일을 만드세요.

```sh
alibaba-data ads analyze --account mystore --days 30 --out ./ad
```

공유해도 되는 정보만 남긴 `summary.md`와 CSV를 올리고, [SKILL.md](skills/alibaba-ad-analysis/SKILL.md) 내용을 채팅에 붙여 넣습니다.
바이어 정보·계정 정보가 있는 원본 파일은 올리지 마세요.

```text
붙여 넣은 SKILL.md의 분석 순서대로 첨부 파일을 분석해 줘.
L1+ 클릭, 문의 1건 비용, 바이어 품질부터 보여 줘. raw 없는 값은 빈칸과 사유로 보여 줘.
```

## 업데이트 방법

Git으로 받았다면 받은 폴더 안에서 실행합니다.

```sh
git pull
```

ZIP으로 받았다면 GitHub에서 **Code → Download ZIP**으로 다시 받습니다. 새 폴더를 연 뒤 `npm link`를 다시 실행합니다.
스킬도 연결했다면 새 폴더 안에서 위 연결 명령을 다시 실행합니다. 계정 파일과 비밀번호는 레포 밖에 있어 그대로 남습니다.

## 안전 규칙

- 데이터 명령은 읽기만 합니다. 광고·입찰·예산·캠페인 설정을 바꾸지 않습니다.
- 문의창 발송 명령은 실행하면 바로 보냅니다. 먼저 보려면 `--dry-run`(아무것도 안 보냄). 보낸 기록은 `alibaba-inquiry log`로 봅니다.
- 결과 파일에 바이어 정보가 있을 수 있습니다. 원본을 공개 레포·채팅에 공유하지 마세요.

## 자세히 (개발자용)

설치 세부사항·기간·지표 정의·전체 명령·종료 코드·구조는 [명령·옵션·데이터 정의](docs/REFERENCE.md)에 있습니다.
전체 명령은 도움말에서도 확인할 수 있습니다. 테스트는 브라우저·네트워크 없이 실행합니다.

```sh
alibaba-data --help
alibaba-inquiry --help
npm test
```
### 광고 일별 raw와 운영 기록

```bash
alibaba-data ads daily --account A --start 2026-06-01 --end 2026-11-15 --level campaign
alibaba-data ads daily --from <덤프> --level product --json
alibaba-data ads daily --from <덤프> --level keyword --csv
alibaba-data ads campaigns --from <덤프> --json
alibaba-data ads history --from <덤프> --start 2026-06-01 --end 2026-11-15
alibaba-data ads product-inquiries --from <덤프> --json
```

- `ads tables`는 요청 필터가 다른 동일 날짜 행을 별도로 보존하며 `_query_filters`·`_query_tag`로 출처를 표시한다. 일자 집계는 명시적으로 수집한 `rangeTables`를 우선 사용해 기본 화면의 다른 캠페인 유형과 섞지 않는다.
- `ads daily`는 `ads tables`의 공통 표 파서를 재사용한다. `daily_coverage`에 전체·비용·문의·L1+·운영 상태가 채워진 행 수를 출력한다. 기본 수준은 campaign. 날짜·캠페인·상품·키워드 식별자는 응답에 있는 값만 출력한다.
- 계정·캠페인·상품·키워드·월·기간 합은 일자 raw 합이다. 날짜 없는 기간 합계나 관리 화면 누적 비용을 합산하지 않는다.
- `spend` / `spend_actual_usd`는 같은 수준 일별 `cost` 또는 `costStr`의 합. 일부 날짜라도 비용이 누락되면 기간 비용은 빈칸이고 `cost_filled_rows`로 수집 범위를 표시한다.
- 상품 raw에 비용이 없으면 빈칸과 사유를 출력한다. 상품에 캠페인 비용을 나누거나 CPC로 비용을 만들지 않는다.
- `ads campaigns`는 조회 시점 설정과 원본 응답. `budget_usd`와 `budget_cycle_days`는 캠페인 예산과 주기이며 `dailyBudget` 원본이 없으면 일 예산은 빈칸이다. 과거 기간 상태로 해석하지 않는다.
- `campaign_targets`는 Shop 캠페인 상세에서 확인한 광고 그룹 목록·상품 선택 설정 요청의 원본이다. 캠페인 목록의 ID로 조회하며, 조회 시각과 성공 여부를 함께 출력한다. 키워드/상품 목록 필드가 없는 응답에서 임의 목록을 만들지 않는다.
- `ads history`는 Operation Log의 실제 `/api/log` 요청으로 Shop·KWA의 캠페인·상품·키워드 변경 기록을 읽는다. 셀러센터가 보존하는 최근 2개월까지만 조회할 수 있다. `history_coverage`에 조회 범위·받은 행·전체 행·완전성을 표시하며 실패·누락 응답은 exit 3을 반환한다. 미수집과 실제 0행은 구분한다.
- `ads daily`는 이력 raw에 캠페인 ID가 명시된 변경만 같은 날짜의 `changes`에 연결한다. 변경 기록만으로 하루 전체 설정을 복원하지 않는다. 일자별 상태 스냅샷이 없으면 상태·일 예산·입찰가는 빈칸이다.
- Shop 상품 기간 리포트 요청도 `ads tables`로 보존한다. 이 표에 비용 열이 없으면 상품 광고비는 빈칸이며, 기간표를 일별 합계 대신 사용하지 않는다.
- `ads product-inquiries`는 실제 문의의 상품 ID로만 연결한다. 상품의 기간 지표가 문의 행마다 반복되므로 그 표의 광고비를 다시 합산하지 않는다. 이 연결만으로 광고 기여 문의라고 판정하지 않는다.
- 일자·상품·품질·전환·분석 명령도 `ads tables`의 완전성 검사를 공유한다. API `totalRows`보다 덜 받았으면 `completeness` 표와 안내를 내고 exit 3을 반환한다.
- 계산 열 바로 옆의 두 열은 원본 분자·분모다. 누락 값이나 0인 분모로 비율을 만들지 않는다.
- 실제 문의와 광고 상품의 날짜 일치만으로 광고 기여를 판정하지 않는다. 문의 응답에 확인된 광고 유입 필드가 없으면 `ad`는 `광고 연결 기록 없음`이다.
- 계정과 캠페인은 겹치는 집계 수준이다. `ads quality`의 `scope`를 확인하고 서로 합산하지 않는다.


### 광고·문의 빈칸 확인

```sh
alibaba-data ads tables --account A --start 2026-07-01 --end 2026-07-31 --fresh --json
alibaba-data ads products --from <덤프> --json
alibaba-data ads conversions --from <덤프> --json
alibaba-data ads conversion-fields --from <덤프> --json
alibaba-data inquiries list --from <덤프> --json
```

- `manual.account_daily`, `manual.product`, `manual.keyword`, `manual.searchword`는 Manual 유형 1·2의 별도 요청이다. `ads tables`의 `completeness.range_table_key`로 각 요청의 실제 응답 행 수와 `totalRows`를 확인한다. 계정 일별표의 날짜 행이 있어도 상품 행은 없을 수 있다.
- 사용자 지정 기간은 화면 요청처럼 시작일 `00:00:00`부터 종료일 `23:59:59`까지 보낸다. `manual.product_period`는 화면의 `ALL` 기간 표를 별도로 보존한다. 일별 값으로 분배하지 않으며 `ads tables`에서 `_query_tag`로 구분한다.
- HTTP·API 오류, `detailList`/`totalRows` 누락, manifest의 미수집 표는 정상 0행으로 처리하지 않는다. `completeness.reason`과 종료 코드 3으로 알리며 `--fresh`로 다시 조회한다. 실제 0행은 성공 응답의 `detailList=[]`, `totalRows=0`으로 확인한다.
- `ads products`는 날짜가 있는 상품 raw만 합산한다. 같은 상품·날짜가 whole_site 표와 Manual 표에 모두 있으면 whole_site 행을 한 번만 센다. whole_site는 계정 전체이며 AI 전용 표가 아니다. `ads quality`는 캠페인의 실제 `campaignType`으로 Manual/AI를 나누고, `scope`가 다른 행을 합산하지 않는다.
- 문의 비용은 실제 `cost` 합 ÷ `mcCnt` 합, 전환율은 실제 `cvr3` 합 ÷ 클릭 합이다. 계산 열 다음 두 열은 분자·분모이며 원본 누락 또는 분모 0이면 계산값도 빈칸이다.
- 전환 유입 채널은 `channelDisplay`, 전환 상품은 `fbProdId`만 읽는다. 빈칸은 각각 `channel_reason`, `converted_product_id_reason`에 이유를 적는다. `fbProdId`가 없으면 `converted_scope`도 빈칸이며 광고 상품 ID로 대체하지 않는다. `ads conversion-fields`는 저장 전 원본 키 이름만 보여준다.
- 문의의 `product_id_reason`은 TM·회사 문의의 상품 연결 부재, 상품 문의의 `targetIds` 키 누락, 빈 값을 구분한다. 방문 이력은 별도 고객 패널 응답만 읽으며 문의 대상 상품으로 대체하지 않는다.


### 탭 없는 API 수집과 기간

`alibaba-data`는 `engine/pages.json`의 읽기 요청을 `--transport api|repl|auto`로 실행한다(기본 `api`).
`api`는 저장 쿠키를 `cookie_store.py load`의 파이프로 받아 Python HTTP로 직접 요청한다. 쿠키·CSRF는 RAM에만 두고 로그나 덤프에 쓰지 않는다.
유효한 저장 쿠키로 실행할 때 Aside 호출과 수집 탭은 0이다. 만료 응답이면 `cookie_store.py refresh --read-only`로 출처 브라우저의 쿠키를 한 번 다시 읽고 재시도한다.
이 단계는 브라우저 실행·자동 로그인을 하지 않는다. 저장 쿠키가 없거나 재읽기 후에도 만료되면 `auto`는 Aside REPL fetch로 넘어간다.
기본 `api`는 REPL로 넘어가지 않고 실패 사유와 `alibaba-data login` 안내를 반환한다. `repl`은 처음부터 Aside fetch를 사용한다.
REPL에서도 수집 탭은 열지 않으며 로그인·캡차 응답일 때만 기존 login 흐름을 실행한다.
문의 고객 패널은 querySummary → 상세 HTML GET → queryCustomerInfo JSONP 순서로 조회한다. HTML 스크립트를 실행하거나 문의 읽음 처리를 호출하지 않는다.
바이어 등급·국가·90일 활동·스팸·블랙리스트는 실제 응답의 허용 필드만 보존한다.

공유 Aside를 사용하는 경우 `ALIBABA_SELLER_ASIDE_WRAPPER`에 잠금 래퍼 경로를 지정한다.
이미 래퍼 안에서 실행하면 `ALIBABA_SELLER_ASIDE_LOCK_HELD=1`로 중첩 잠금을 피한다. 유효한 `api` 세션에는 래퍼가 필요 없다.
`--runner`, `--shot`, `--button`, `ui probe`는 제거했다. 데이터 수집 전송은 `--transport`로 선택한다. 브라우저 선택은 `login --browser chrome|edge|aside`에 사용한다.

기본 기간은 **미국 태평양 어제까지 30일**이다. `--days N`, `--weeks N`, `--start/--end`를 사용할 수 있다.
`ads report`와 `ads tables`의 `--max`(`--all` 별칭)는 현재 서버 날짜 필터가 반환한 최초일~최신 집계일까지 받는다.
최초일을 확인할 수 없는 표는 `--max`를 거부한다. 표별 요청 기간을 나누고 페이지를 끝까지 조회한다.
`--fresh`는 캐시를 재사용하지 않는다. `--from DIR`은 네트워크 없이 저장한 실제 응답만 읽는다.

캠페인·상품 식별자는 목록 API에서 받아 `{{campaignIds}}`, `{{productId}}` 템플릿에 넣는다.
목록에 대상이 없으면 조회하지 않고 사유를 남긴다. API 계약이 없거나 응답 구조가 다르면 명시적으로 실패한다.
광고 상품표는 날짜별 요청을 묶어서 받아 페이지 경계의 불안정한 정렬에 의한 중복을 막는다.
일시적인 전송 오류는 완료한 응답을 보존한 채 제한 횟수 재시도한다.
각 manifest는 `transport`, `asideCalls`, `collectionTabOpens`, `httpRequests`, `elapsedSeconds`, 표별 `rowsGot/totalRows`, 중복 검사를 기록한다.
`totalRows`가 반환되지 않는 목록은 빈 마지막 페이지로 종료하며 total 검증과 구분한다.

`dump --max --pages inquiries,analytics-product,analytics-keyword,showcase`는 서버 광고 날짜 필터의 최초일~최신일을 공통 보고 기간으로 사용한다.
문의는 전체 목록을 받고 보고서에서 기간을 걸러낸다. 상품 분석은 선택 주기 응답을 받으며, 이 공통 기간이 각 보조 API의 최초 보존일이라는 뜻은 아니다.
상품·키워드·검색어 일별표는 날짜별로 조회해 불안정한 페이지 순서의 중복을 피한다. 중복 페이지는 저장하지 않고 실패로 표시한다.
main의 기간표(`ALL`)와 일별표(`DAY`)는 캐시·이어받기·완결성 검사를 별도로 유지한다. 기간표를 일별 값으로 분배하지 않는다.

`alibaba-inquiry inspect --account mystore --buyer-ali-id <ID>`는 고객 패널 상품과 최근 메시지를 HTTP로 읽는다.
`--count`는 조회 메시지 수(기본 20)이며 전체 대화 수가 아니다. API에 없는 DOM 요소 수와 상품 이력은 `null`과 사유로 표시한다.
flow의 check는 dry-run에서도 같은 API로 상품을 확인하며, 조회 불가 시 발송을 중단한다.

`alibaba-inquiry draft --account mystore --buyer-ali-id <ID> [--draft-kind reply|summary]`는
Copilot HTTP API로 답장 제안 또는 요약을 생성한다. 결과만 출력하며 입력창·발송·저장 작업은 하지 않는다.
`--dry-run`은 생성 요청도 하지 않는다. 이전 `--button` 대신 검증된 두 종류만 제공한다.

### 광고 화면의 추가 조회

```bash
alibaba-data ads industry-compare --account mystore --days 7 --transport api --fresh --json
alibaba-data ads recommended-products --account mystore --json
alibaba-data ads brand-groups --from <덤프> --csv
```

- 기간 조회: `competition-keywords`, `competition-regions`, `industry-compare`, `product-benchmark`, `campaign-countries`. `--start/--end` 또는 `--days`를 사용하며, 긴 기간은 7일씩 요청한다. 각 구간의 원본 응답을 따로 출력한다.
- 현재 상태 조회: `campaign-advice`, `campaign-type-count`, `guaranteed-performance`, `premium-industry`, `recommended-products`, `brand-accounts`, `brand-groups`, `brand-report-categories`, `product-groups`.
- 모두 `alibaba-data ads <명령>` 형식이다. 캠페인·업종 식별자는 실행 시 목록 API에서 읽는다. 추천 상품은 최대 20개이며 `premium-industry`는 이 추천 상품들의 업종 범위다.
- `data`는 응답의 원래 값, `_query`는 요청 범위, `_missing`은 빈칸 사유, `requests`는 요청별 행 수·0행 사유다. 비율·금액은 재계산하거나 환산하지 않는다. 성과 보장 조건은 응답의 중첩 객체 그대로 보존한다.
- 업무 오류는 코드·메시지를 그대로 표시하고 실패로 종료한다. 경쟁 지역은 현재 `status=1001`, `isp.internal-error`가 확인되어 사용 가능으로 보장하지 않는다. 성공 응답의 0행도 광고가 꺼졌다는 뜻으로 해석하지 않는다.
- 상세 범위와 제한은 [명령 레퍼런스](docs/REFERENCE.md#광고-추가-읽기-명령)를 참고한다.
### 추가 셀러 메뉴 읽기

저장 쿠키 HTTP(`--transport api`)로 조회합니다. 각 명령은 `alibaba-data` 뒤에 붙입니다.

| 명령 | 데이터 |
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
| `content status` | 콘텐츠 역할별 개통 상태 raw |

`--account <account> --fresh --json` 또는 `--csv`, `--from <dump>`를 지원합니다. 현재 목록/상태 API라 `--start/--end/--days`는 서버 필터에 적용되지 않으며 안내를 출력합니다. 정상 빈 목록은 0행 안내, HTTP/업무 오류는 실제 코드·메시지와 비정상 종료로 구분합니다.

바이어 이름·연락처·주소와 은행·결제 식별 정보는 저장·출력 allowlist에서 제외합니다. 금액은 응답 원값을 유지하고 없는 필드는 빈칸과 사유를 남깁니다. 인증 목록은 HTTP 200 빈 본문, 정산은 `500 / site config exception.`이 관측되어 현재 성공 데이터 계약은 미검증입니다.
