# House 설치·실행·설정

이 문서는 **0.4.2 개발 브랜치 `feat/house-0.4.2-connect`** 기준입니다.
`main`에는 앞선 변경의 PR이 남아 있을 수 있으므로, 아래 설치 명령은 이번 연결·경고 기능이 있는
브랜치를 명시합니다. 현재 검증 범위는 [검증 기록](verification.md#042-채팅-경고-전달--2026-10-22)에 있습니다.

## 준비

- Git, Python 3.9 이상, 로그인된 Codex CLI가 필요합니다. House의 Python 외부 패키지는 없습니다.
- Codex CLI 설치는 [OpenAI 공식 안내](https://learn.chatgpt.com/docs/codex/cli)를 따릅니다.
  `codex --version`, `codex login status`로 실행과 로그인을 확인하고, 필요하면 `codex login`을 실행합니다.
  이번 실제 훅 전달 검증은 Codex 0.163.0에서 수행했습니다.
- House CLI와 해당 프로젝트를 사용할 Codex가 같은 OS·사용자 환경에서 파일을 읽을 수 있어야 합니다.
  WSL에서 설치했다면 먼저 WSL의 Codex로 설정합니다. Windows 앱에서 WSL 프로젝트를 사용할 때는
  아래 [Windows와 WSL 연결](#windows와-wsl-연결)을 따릅니다.
- 저장소가 비공개라면 GitHub 접근 권한이 필요합니다.

## 설치

### macOS·Linux·WSL

아직 `$HOME/house`에 다른 폴더가 없는 새 설치 예시입니다. 이미 설치했다면 아래 업데이트 절차를 사용합니다.

```sh
git clone --branch feat/house-0.4.2-connect https://github.com/ISIF-3/house.git "$HOME/house"
cd "$HOME/house"
python3 install.py
"$HOME/.local/bin/house" --version
```

`house 0.4.2`가 출력됩니다. 설치 결과의 `command`, `skill`, `home`을 확인합니다.
명령은 `~/.local/bin/house`, 스킬은 `~/.agents/skills/house`에 연결됩니다.
이 설치는 소스 폴더를 참조하므로 설치 뒤 그 폴더를 옮기거나 지우지 않습니다.

현재 셸에서 짧은 `house` 명령을 사용하려면:

```sh
export PATH="$HOME/.local/bin:$PATH"
house --version
```

새 터미널에도 적용하려면 사용하는 셸의 설정 파일에 같은 PATH 줄을 추가합니다.
공용 데이터 기본 경로는 macOS에서 `~/Library/Application Support/House`, Linux·WSL에서
`~/.local/share/house`입니다. 연구 프로젝트마다 생기는 `house/` 기록과는 별도입니다.

macOS에서 주간 후보 탐색도 설정하려면 소스 폴더에서 `python3 install.py --weekly`를 실행합니다.
월요일 오전 9시 현지 시간에 등록하며, Linux·WSL·Windows에서는 `--weekly`를 생략합니다.

### Windows PowerShell

native Windows Python과 Codex를 사용하는 예시입니다. symlink 권한 없이 스킬을 관리된 복사로 설치하고
`house.cmd`를 만듭니다.

```powershell
git clone --branch feat/house-0.4.2-connect https://github.com/ISIF-3/house.git "$HOME\house"
Set-Location "$HOME\house"
py -3 install.py
& "$HOME\.local\bin\house.cmd" --version
$env:Path = "$HOME\.local\bin;$env:Path"
house --version
```

마지막 PATH 설정은 현재 PowerShell에 적용됩니다. 지속해서 사용하려면 사용자 PATH에
`%USERPROFILE%\.local\bin`을 추가합니다. 이 설치에는 macOS용 `--weekly`를 사용하지 않습니다.
Windows 설치·파일 락·훅 전달은 자동검사가 있으며, native Windows의 실제 경고 화면은 별도로 검증해야 합니다.

## 새 연구 프로젝트에서 시작

House 소스 폴더와 실제 연구 프로젝트 폴더를 구분합니다. 다음은 macOS·Linux·WSL에서 새 프로젝트를 만드는 예시입니다.
이미 있는 연구 폴더라면 그 경로를 지정하고 `mkdir`를 생략합니다.

```sh
HOUSE_PROJECT_DIR="$HOME/research/my-project"
mkdir -p "$HOUSE_PROJECT_DIR"
house init --project "$HOUSE_PROJECT_DIR" --goal '이 프로젝트의 연구 목표'
house check --project "$HOUSE_PROJECT_DIR"
```

`<연구 프로젝트>/house/`에 Main·Literature·Work·Output·Discussion의 기록이 생깁니다.
`init`은 기록을 준비합니다. 연구 모델을 실행하거나 기존 연구의 완료를 판정하지는 않습니다.
실제 모델·계산은 목표, 입력, 방법과 평가 기준을 합의한 뒤 진행합니다.

다음 중 사용할 방식을 선택합니다.

| 방식 | 실행 | 다음 단계 |
| --- | --- | --- |
| 터미널에서 Main 시작 | `house start --project "$HOUSE_PROJECT_DIR"` | 열린 Codex에서 훅을 검토하고 연구 요청 |
| 이미 연 Codex 앱·확장·CLI 대화 | 해당 연구 프로젝트를 열고 `$house` 요청 | 자동 알림도 원하면 아래 `connect` 설정 |
| House 브라우저 화면 | `house web --project "$HOUSE_PROJECT_DIR"` | 출력된 로컬 주소를 열고 이번 실행의 훅을 검토·허용 |

`house`는 방 선택 메뉴, `house room literature --project "$HOUSE_PROJECT_DIR"`는 문헌 방을 엽니다.
`$house`는 Codex의 스킬 호출이며 기본 `/house` 슬래시 명령을 등록하지 않습니다.
설치 직후 스킬이 보이지 않으면 Codex를 다시 열고 스킬 목록을 확인합니다.

PowerShell에서는 프로젝트 경로를 다음처럼 설정하고 같은 House 명령을 사용할 수 있습니다.

```powershell
$HOUSE_PROJECT_DIR = Join-Path $HOME 'research\my-project'
New-Item -ItemType Directory -Force -Path $HOUSE_PROJECT_DIR
house init --project "$HOUSE_PROJECT_DIR" --goal '이 프로젝트의 연구 목표'
house start --project "$HOUSE_PROJECT_DIR"
```

## 기존 Codex 대화에 알림 연결

`$house`를 불러오는 것과 native 훅을 등록·허용하는 것은 별도입니다.
먼저 실제 연구 프로젝트의 House를 초기화하고, 그 프로젝트에서 다음을 실행합니다.

```sh
house connect --project "$HOUSE_PROJECT_DIR" --dry-run --json
house connect --project "$HOUSE_PROJECT_DIR" --apply --json
house doctor --project "$HOUSE_PROJECT_DIR" --json
```

1. dry-run에서 `.codex/hooks.json` 또는 기존 inline TOML에 추가할 House 명령을 확인합니다.
   `--apply`가 실제 등록입니다. 사용자 훅, 연구 입력·결과와 프로젝트의 고정 릴리스는 보존합니다.
2. 해당 연구 프로젝트를 작업 경로로 사용하는 Codex에서 훅을 검토합니다. CLI 예시는 다음과 같습니다.

   ```sh
   cd "$HOUSE_PROJECT_DIR"
   codex
   ```

   Codex 입력창에 `/hooks`를 입력합니다. House의 `house.py connection hook` 명령을 실행하는
   `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `PostCompact`, `Stop` 다섯 훅을 검토해 허용합니다.
   프로젝트 설정 계층 자체의 신뢰 확인이 나오면 실제로 선택한 프로젝트인지 확인합니다.
   훅 신뢰는 사용자가 승인하며 House가 자동 승인하지 않습니다.
3. 이미 열린 클라이언트가 바뀐 설정을 읽지 않았다면 작업 중인 턴이 끝난 뒤 같은 프로젝트의 대화를
   재개하거나 다시 엽니다. CLI의 기존 대화는 `codex resume`에서 선택할 수 있습니다.
   설정 적용만을 위해 실행 중인 계산이나 대화를 종료하지 않습니다.
4. 해당 Codex 대화에서 메시지 또는 도구 이벤트가 발생한 뒤 상태를 확인합니다.

   ```sh
   house doctor --project "$HOUSE_PROJECT_DIR" --json
   ```

기존 대화도 첫 실제 콜백에서 붙습니다. 새 대화는 그 대화의 실제 식별자로 관측하며,
관측을 시작하기 전의 압축 횟수는 알 수 없습니다. 해당 프로젝트에서 열지 않은 대화에는
프로젝트의 hooks가 적용됐다고 가정하지 않습니다.

### 상태 읽기

| 항목 | 뜻 |
| --- | --- |
| `registration: registered` | 설정 파일에 House 등록이 있음 |
| `event_observation: observed` | 실제 콜백 수신 근거가 있음 |
| `native_trust` | native 점검이 확인한 신뢰 상태. 기본 점검에서는 `unknown`일 수 있음 |
| `screen_delivery: unknown` | 사용자가 화면 경고를 봤다는 증거가 아직 없음 |
| `conversations` | 연결된 대화, 최근 문맥 관측, 인계 파일 경로와 경고 전달 기록 |

등록·허용·콜백·사용자 화면 확인을 따로 판단합니다. 필요하면 다음 명령이 별도 소유 app-server를
초기화해 `hooks/list`만 확인합니다. 모델 턴은 시작하지 않으며, 실패하면 실제 오류를 확인합니다.

```sh
house doctor --project "$HOUSE_PROJECT_DIR" --native --json
```

### 실제로 보이는 경고

최근 문맥 관측이 70% 이상이면 인계 준비를 안내합니다. 모델의 추가 문맥을 지원하는
`SessionStart`·`UserPromptSubmit`·`PostToolUse`에서는 native 경고 이벤트와 함께 에이전트에게
다음 채팅 메시지에 아래와 같은 안내를 한 번 보여 달라고 요청합니다.

> ⚠️ House 경고: 컨텍스트 70.0% 관측. 다음 작업 전에 세션 인계를 준비하세요.

위 문장은 **출력 모양 예시**입니다. 실제 안내의 비율은 관측값을 사용합니다. native 팝업의 모양과
노출 여부는 클라이언트에 따라 확인이 필요하며, 채팅 메시지 전달 요청 자체도 화면 열람의 증거는 아닙니다.
이번 검증에서는 실제 훅의 일회성 **표시 시험**이 추가 문맥으로 도착하고, 채팅 경고를 사용자가 본 것까지
확인했습니다. 실제 문맥을 70%로 채우거나 강제 압축한 검증은 아닙니다.

같은 경고·endpoint의 채팅 요청은 반복하지 않습니다. `Stop` 등 추가 문맥을 지원하지 않는 이벤트에서
처음 발생한 경고는 다음 지원 이벤트로 전달합니다. 압축 등으로 해결된 오래된 문맥 경고는 뒤늦게 표시하지 않습니다.
표시가 안 되면 아래 문제 해결 절차에서 실제 콜백과 채팅 전달 경로를 확인합니다.

경고가 대화나 계산을 자동 종료하지는 않습니다. 인계의 목표·확정 결정·실행 중인 작업·다음 행동을
정리하고, doctor가 알려 준 실제 conversation key와 경로로 저장합니다.

```text
house connection checkpoint <conversation-key> --project <project-directory> --json
```

`--boundary`는 실제 작업 구간을 마쳤을 때만 추가합니다. 확인 가능한 자동 압축 10회 이후,
유효 인계를 저장한 작업 경계에서 새 채팅을 권하며 자동으로 새 채팅을 만들지 않습니다.

## 브라우저 실행

```sh
house web --project "$HOUSE_PROJECT_DIR" --port 0 --no-browser
```

출력된 인증 주소를 브라우저에서 엽니다. 첫 훅 신뢰 화면에서 명령과 해시를 확인하고 이번 실행에
허용한 뒤 Main에 작업을 요청합니다. 브라우저를 닫아도 작업은 계속됩니다.
서버를 다시 실행해 기존 Main을 이어가려면 해당 House에 기록된 Main ID를 `--resume`에 지정합니다.
같은 채팅을 CLI와 웹에서 동시에 조작하지 않습니다.

### 모델·추론 강도·속도 설정

House 웹의 세션 관측 영역에서 Main의 모델, 추론 강도, 자동·표준·Fast 속도 모드를 선택합니다.
Codex가 실제로 제공하는 선택지만 표시하며, 모델 변경으로 지원되지 않는 설정은 자동으로 돌아갑니다.
선택은 이후 제출하는 요청에 적용됩니다. 실행 중인 턴, 이미 대기 중인 요청과 기존 방 담당자의 설정은
바꾸지 않습니다. Fast는 사용량이 늘 수 있으며 화면의 [공식 속도 안내](https://learn.chatgpt.com/docs/agent-configuration/speed)를 확인합니다.

터미널·앱·확장에서 직접 작업한다면 그 Codex 클라이언트의 모델 설정을 사용합니다.
House 연결은 전역 Codex 모델 설정을 바꾸지 않습니다.

## Windows와 WSL 연결

Windows 앱이 WSL 소유 프로젝트를 사용할 때는 WSL에서 실제 배포판 이름을 확인한 뒤 명시적으로 전달을 설정합니다.
다음 `Ubuntu`는 예시이며 자신의 배포판 이름으로 바꿉니다.

```sh
house connect --project "$HOUSE_PROJECT_DIR" --wsl-distro Ubuntu --dry-run --json
house connect --project "$HOUSE_PROJECT_DIR" --wsl-distro Ubuntu --apply --json
```

처음 등록할 때 이 옵션을 선택합니다. 이미 다른 방식으로 등록했다면 dry-run과 doctor로 현재 binding을
확인하고 해제 후 원하는 방식으로 다시 등록합니다. Windows용 명령은 그 WSL 배포판의 Python으로
JSON을 전달하며, 잘못되거나 없는 경로 매핑을 다른 transcript로 대체하지 않습니다.
실제 Windows 앱의 훅 허용과 화면 경고는 그 클라이언트에서 확인합니다. 로컬 파일만 접근하는 클라우드 대화에
이 설정으로 로컬 훅이 생긴다고 가정하지 않습니다.

## 업데이트와 연결 해제

같은 소스 경로의 기존 설치를 업데이트하는 예시입니다. 로컬 수정이 있으면 Git이 알려 주는 상태를
먼저 확인하고 보존합니다. `--ff-only`가 실패하면 강제로 덮어쓰지 말고 브랜치 상태를 확인합니다.

```sh
cd "$HOME/house"
git status --short
git switch feat/house-0.4.2-connect
git pull --ff-only
python3 install.py
house --version
```

native Windows는 설치 단계에 `py -3 install.py`를 사용합니다.
소스 경로를 바꾼 설치는 `install.py --help`의 `--upgrade-from`으로 정확한 이전 설치만 교체합니다.
등록된 hook 명령 경로가 바뀌면 기존 binding을 해제하고 새 위치에서 등록·허용합니다.
기존 연구의 고정 릴리스는 엔진 업데이트만으로 바꾸지 않으며, 구조 전환은 `house migrate --dry-run`을
검토한 뒤 별도 합의한 범위에서 진행합니다.

```sh
house disconnect --project "$HOUSE_PROJECT_DIR" --dry-run --json
house disconnect --project "$HOUSE_PROJECT_DIR" --apply --json
```

House가 정확히 소유한 등록만 해제하고 사용자 훅과 관측 이력은 보존합니다.

## 문제 해결

| 현상 | 확인할 것 |
| --- | --- |
| `house: command not found` | 설치 결과의 `command` 경로로 실행하고 PATH 확인. Windows는 `house.cmd` 사용 |
| `House 프로젝트가 없습니다` | 실제 연구 프로젝트 경로에서 `house init --project ...` 실행 |
| 스킬이 안 보임 | 설치 결과의 `skill` 경로와 Codex 재실행 후 스킬 목록 확인 |
| 등록됐는데 `not_observed` | 프로젝트 작업 경로, 다섯 훅 허용 상태, 클라이언트의 설정 재읽기, 새 실제 이벤트 확인 |
| 기존 대화는 됐지만 새 대화는 안 됨 | 대화 ID가 고정된 로컬 시험 relay인지 확인하고, 일반 사용은 해당 프로젝트의 `house connect` 사용 |
| 콜백은 있지만 화면 경고 없음 | 실제 70% 관측 여부와 pending alert 확인. 최신 0.4.2는 채팅 전달도 요청하며 사용자 확인과 구분 |
| `doctor --native`가 읽기 전용 오류로 실패 | 소유 app-server가 사용하는 Codex 상태 폴더의 실제 접근 권한 확인. trust를 임의 변경하지 않음 |
| Windows 앱에서 WSL 경로 오류 | 처음 등록한 배포판·실행 환경·경로 매핑 확인 |

개발 중 사용한 `house-connect-this-chat`·`house-connect-current-chat` 같은 단일 대화 시험 helper는
배포 명령이 아닙니다. 특정 컴퓨터와 대화에 묶인 설정을 다른 프로젝트로 복사하지 않고, 위의 표준
프로젝트 연결 절차를 사용합니다.

화면을 설명하려면 캡처를 파일로 저장해 Codex에 경로를 알려 주거나, CLI의 `-i`로 첨부할 수 있습니다.
예: `codex -i screenshot.png '이 화면의 경고 표시를 확인해줘'`.
자세한 입력 방법은 [OpenAI 이미지 입력 안내](https://learn.chatgpt.com/docs/image-inputs)를 참고하세요.

## 개발 검증

소스 폴더에서 다음을 실행합니다. 자동검사는 합성 fixture를 포함하며 실제 클라이언트 화면 확인과 구분합니다.
웹 코드의 검사에는 Node.js도 필요합니다.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=skill/scripts python3 -m unittest discover -s tests -q
node tests/web_session_smoke.js
```

Python 검사 326개 중 324개 통과·2개 건너뜀과 실제 현재 대화의 경고 전달을 검증했습니다.
다른 OS·클라이언트의 실제 UI 검증 상태는 [검증 기록](verification.md)을 참고하세요.
