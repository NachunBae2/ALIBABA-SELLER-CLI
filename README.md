# ALIBABA-SELLER-CLI

내 알리바바 스토어 데이터를 뽑습니다. 문의·광고·키워드·상품을 분석합니다.
OneTalk에서 답장·명함·파일·상품 카드 작업을 미리 봅니다. 기본은 미리보기이며 클릭·입력을 하지 않습니다.
Accio Work 앱과 연결합니다. 상점 진단·시장 정보·RFQ 이력을 읽습니다.

## 무엇을 할 수 있나

| 셀러 업무 | 쓰는 명령 | 결과물 |
|---|---|---|
| 문의 확인 | `alibaba-data inquiries list`, `alibaba-data inquiries stats` | 문의 목록·국가별·답장 상태별 통계 |
| 상품별 문의 확인 | `alibaba-data inquiries by-product` | 상품별 문의·유입 경로 |
| 상품 성과 확인 | `alibaba-data analytics products` | 상품별 노출·클릭·방문·문의 |
| 광고 효율 확인 | `alibaba-data ads quality`, `alibaba-data ads analyze` | L1+ 클릭 비율·문의 1건 비용·CSV·요약 |
| 상품 키워드 고르기 | `alibaba-data keywords recommend` | 상품 하나의 추천 키워드 |
| OneTalk 응대 미리보기 | `alibaba-inquiry reply`, `alibaba-inquiry send-card`, `alibaba-inquiry send-file`, `alibaba-inquiry send-product` | 답장·명함·파일·상품 카드 작업 계획 |
| Accio Work로 상점 보기 | `alibaba-accio shop summary` | 상점 요약 |
| 시장·RFQ 이력 읽기 | `alibaba-accio market country-rank`, `alibaba-accio rfq history` | 업종 국가 순위·RFQ 견적 이력 |

## 처음 설치

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

   Aside는 내 로그인 상태를 그대로 쓰는 브라우저 앱이며, 이 도구는 셀러센터 화면을 이 브라우저에서 읽습니다.

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

   `mystore`는 내가 붙인 계정 별명입니다. `loginId`는 셀러센터 로그인에 쓰는 ID이며 Aside 쿠키에 그 값이 포함되는지 비교합니다. `keychainAccount`(로그인 이메일)는 이 값과 다를 수 있습니다.
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

   Aside는 내 로그인 상태를 그대로 쓰는 브라우저 앱이며, 이 도구는 셀러센터 화면을 이 브라우저에서 읽습니다.

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

   `mystore`는 내가 붙인 계정 별명입니다. `loginId`는 셀러센터 로그인에 쓰는 ID이며 Aside 쿠키에 그 값이 포함되는지 비교합니다. `keychainAccount`(로그인 이메일)는 이 값과 다를 수 있습니다.
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
| `keychain_item_missing` | 저장한 비밀번호를 못 찾음 | 설정의 서비스·이메일과 비밀번호 저장 명령의 서비스·이메일을 같게 맞춰 다시 저장. |
| `npm.ps1 cannot be loaded because running scripts is disabled on this system.` | PowerShell이 스크립트 실행을 막음 | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 실행 후 `npm link` 재시도. 조직 관리 PC는 관리자 정책을 따르기. |

## 이럴 때 이 명령

명령의 `mystore`는 내가 정한 계정 별명으로 바꿔 씁니다. 날짜는 원하는 기간으로 바꿉니다. 광고 날짜는 미국 태평양 기준입니다.
결과 예시는 모두 테스트용 가짜 데이터입니다. 긴 표는 일부 열·행만 보여 줍니다. 실제 출력에는 기간·출처 등 열이 더 있습니다.

| 옵션 | 뜻 |
|---|---|
| `--start YYYY-MM-DD --end YYYY-MM-DD` | 시작일·종료일을 지정합니다. 미국 태평양 어제보다 늦은 종료일은 그날로 자르고 경고합니다. |
| `--days N` | 셀러센터 `Last N days`와 같은 기간입니다(끝 날짜는 미국 태평양 어제). |
| `--weeks N` | 완료된 최근 N주(PST 일~토)입니다. |
| `--all` | 미국 태평양 어제까지 최근 181일(셀러센터 효과 리포트 조회 가능 범위)입니다. |

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

### 문의 답장·명함·카탈로그(PDF) 미리보기

**아래 명령은 기본 미리보기(dry-run)만 합니다. 클릭·입력·업로드는 하지 않습니다.**
문의 목록의 ID로 `<문의ID>`를 바꿉니다. 텍스트 편집기에서 답장 내용을 일반 텍스트로 `reply.txt`에 저장해 받은 폴더에 둡니다. `catalog.pdf`도 준비해 받은 폴더에 둡니다.
명함·파일은 OneTalk 대화창 자체 기능만 사용합니다. 미리보기는 대화를 열지 않아 읽음 처리도 하지 않습니다.

```sh
alibaba-inquiry reply --account mystore --inquiry-id '<문의ID>' --text-file reply.txt
alibaba-inquiry send-card --account mystore --inquiry-id '<문의ID>'
alibaba-inquiry send-file --account mystore --inquiry-id '<문의ID>' --path catalog.pdf
```

```text
# 예시 (테스트용 가짜 데이터)
# reply 결과 JSON 일부
{
 "action": "reply",
 "mode": "dry-run",
 "status": "ok",
 "plan": [
  {"kind": "open", "what": "open conversation"},
  {"kind": "write", "what": "focus message input"},
  {"kind": "write", "what": "type reply text (12 chars)"},
  {"kind": "write", "what": "press Send"}
 ],
 "done": []
}

# send-card 결과 JSON 일부
{"action": "send-card", "mode": "dry-run", "status": "ok"}

# send-file 결과 JSON 일부
{"action": "send-file", "mode": "dry-run", "status": "ok"}
```

`plan`은 할 일 목록입니다. 보냈다는 뜻이 아닙니다. 화면을 찾지 못하면 `status`가 달라질 수 있습니다.
이 예시는 실제 발송 안내가 아닙니다.

### Accio Work로 상점 진단

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
- 문의창 명령은 기본 미리보기입니다. 클릭·입력·업로드를 하지 않습니다.
- 결과 파일에 바이어 정보가 있을 수 있습니다. 원본을 공개 레포·채팅에 공유하지 마세요.

## 자세히 (개발자용)

설치 세부사항·기간·지표 정의·전체 명령·종료 코드·구조는 [명령·옵션·데이터 정의](docs/REFERENCE.md)에 있습니다.
전체 명령은 도움말에서도 확인할 수 있습니다. 테스트는 브라우저·네트워크 없이 실행합니다.

```sh
alibaba-data --help
alibaba-inquiry --help
alibaba-accio help
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
- `ads campaigns`는 조회 시점 설정과 원본 응답. 과거 기간 상태로 해석하지 않는다. 해당 일자 상태 raw가 없으면 `ads daily` 운영 열은 빈칸이다.
- `ads history`는 수집된 변경 이력 raw만 출력하며 미수집 시 0행 안내를 낸다.
- `ads product-inquiries`는 실제 문의의 상품 ID로만 연결한다. 상품의 기간 지표가 문의 행마다 반복되므로 그 표의 광고비를 다시 합산하지 않는다. 이 연결만으로 광고 기여 문의라고 판정하지 않는다.
- 일자·상품·품질·전환·분석 명령도 `ads tables`의 완전성 검사를 공유한다. API `totalRows`보다 덜 받았으면 `completeness` 표와 안내를 내고 exit 3을 반환한다.
- 계산 열 바로 옆의 두 열은 원본 분자·분모다. 누락 값이나 0인 분모로 비율을 만들지 않는다.
- 실제 문의와 광고 상품의 날짜 일치만으로 광고 기여를 판정하지 않는다. 문의 응답에 확인된 광고 유입 필드가 없으면 `ad`는 빈칸이다.
- 계정과 캠페인은 겹치는 집계 수준이다. `ads quality`의 `scope`를 확인하고 서로 합산하지 않는다.

