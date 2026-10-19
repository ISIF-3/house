# House · 연구 공장

하나의 공용 공장에서 여러 연구 프로젝트를 운영합니다. 터미널의 `house`와 Codex의 `$house`는 같은 엔진과 파일을 사용합니다. 기존 프로젝트의 파일을 옮기거나 덮어쓰지 않습니다.

## 시작

macOS 또는 Linux, Python 3.9 이상, 로그인된 Codex CLI가 필요합니다. Python 외부 패키지는 필요하지 않습니다.

```sh
git clone https://github.com/ISIF-3/house.git ~/Desktop/house
cd ~/Desktop/house
python3 install.py --weekly
```

`--weekly`는 macOS에서 월요일 오전 9시(컴퓨터의 현지 시간)에 후보 탐색을 등록합니다. Linux에서는 이 옵션을 생략합니다. 명령은 `~/.local/bin/house`, 스킬은 `~/.agents/skills/house`에서 이 저장소로 연결됩니다. `~/.local/bin`이 PATH에 없다면 해당 경로의 명령을 직접 사용하거나 자신의 셸 설정에 추가합니다.

연구할 프로젝트 폴더에서 실행합니다.

```sh
cd /path/to/research-project
house                         # 방 선택 메뉴, Enter는 Main
house start                   # Main으로 바로 시작
house room literature         # 문헌 방에서 Codex 실행
house init --goal '연구 목표'  # 폴더만 생성
house status                  # 기록된 상태 확인
house check                   # 크기·git·상태 줄·handoff 점검 (변경 없음)
house web                     # 로컬 웹 연구실을 열고 브라우저에서 작업
```

Codex 앱·CLI·확장에서 프로젝트를 열고 **`$house 연구 목표를 정리하며 시작해줘`**라고 요청할 수도 있습니다. `/house`는 기본 슬래시 명령으로 등록되지 않습니다. 새 스킬이 보이지 않으면 Codex를 다시 열고 스킬 목록을 확인합니다. [Codex 스킬 문서](https://learn.chatgpt.com/docs/build-skills)

Main은 연구의 이유·최종 목표를 정리하고 데이터, 방법, 평가 기준과 자원을 설명합니다. 실제 AI·통계 모델 구현은 설계에 합의한 뒤 시작합니다. 초기화 시 가짜 노트북·논문·결과·승인을 만들지 않습니다.

## 브라우저에서 운영하기 (0.4.0)

CLI에서 실행하고 브라우저에서 Main과 네 방의 작업·대화·파일을 확인합니다. 없는 프로젝트 구조는 자동 초기화하며 기존 House의 고정 릴리스는 유지합니다. 별도 API 키를 요구하지 않고 기존 Codex CLI의 ChatGPT 로그인을 사용합니다.

중앙 화면은 **작업 요청 → 연구실 둘러보기 → 선택한 방의 작업 결과 대화 → 확인과 승인** 순서입니다. 왼쪽에는 작업 보드와 세션 관측을 두며 연구 목표 본문과 Main ID는 표시하지 않습니다. 목표 기록은 프로젝트 파일에 보존됩니다.

```sh
house web --project /path/to/research
house web --project /path/to/research --port 0 --no-browser
house web --project /path/to/research --resume MAIN_CHAT_ID
house web --project /path/to/research --dry-run --json
```

기본 주소는 `127.0.0.1:8765`이며 `--port 0`은 빈 포트를 고릅니다. `--no-browser`는 주소만 출력합니다. 출력된 인증 주소를 열면 토큰을 해당 탭의 `sessionStorage`에 보관하고 주소의 fragment를 지워 새로고침에도 인증을 유지합니다. API에는 Bearer 헤더로 보내며 query나 cookie에 넣지 않습니다. `--dry-run --json`은 프로젝트 초기화 후 실행 계획을 보여 주며 서버와 Codex는 실행하지 않습니다.

첫 연결에 훅 신뢰 검토가 필요하면 아직 작업을 접수하지 않습니다. 웹에서 훅의 명령·해시를 확인하고 **이번 실행에 허용**하면, House가 소유한 Codex 프로세스를 채팅 생성 전에 다시 시작해 해당 실행의 신뢰를 확인합니다. 전역 설정을 바꾸거나 신뢰 검토를 우회하지 않습니다. 사용자가 비활성화한 필수 훅은 실행을 막으며 임의로 켜지 않습니다.

어느 방에서 요청해도 Main을 거쳐 기존 담당자에게 전달합니다. 지시는 8,000자 이내, 전달 대기는 최대 20개입니다. 실행 중인 작업은 자동 중단하지 않고 다음 Main 턴에서 처리합니다. 대기열은 세션의 `web-queue.json`에 보관하며, 전달 확인이 끊긴 `delivery_unknown` 항목은 자동 재전송하지 않습니다. 기록과 실제 실행 상태를 확인한 뒤 후속 요청을 결정합니다.

세션 관측에서 **Main 모델·추론 강도·속도 모드**를 선택합니다. 추론 강도는 Codex가 해당 모델에 제공하는 단계만 표시하고, 속도는 자동·표준·Fast 중 고릅니다. Fast는 모델이 지원할 때 선택할 수 있으며 사용량이 늘 수 있습니다. [공식 속도 안내](https://learn.chatgpt.com/docs/agent-configuration/speed)

현재 관측된 Main 설정과 다음 요청에 사용할 선택을 구분하며, 요청을 보낼 때 세 설정을 대기열에 함께 저장합니다. 자동은 같은 모델의 관측 설정을 유지하거나 모델 기본값을 사용합니다. 모델을 바꾸면 지원되지 않는 추론 강도와 Fast 선택은 자동으로 돌아갑니다. 선택을 바꿔도 실행 중이거나 이미 대기 중인 요청과 기존 방 담당자의 설정은 유지됩니다. 모델 목록을 가져오지 못하면 새 선택은 사용할 수 없지만 관측된 기존 설정으로 작업할 수 있습니다. 이미 실행 중인 웹 서버에 이 변경을 적용하려면 작업 상태를 확인한 뒤 서버를 재시작하고, 이어갈 Main ID를 `--resume`으로 지정합니다.

브라우저를 닫아도 작업은 계속됩니다. CLI의 **Ctrl-C**는 웹 서버와 House가 소유한 Codex 연결을 종료하며, 외부 계산을 종료하는 명령은 보내지 않습니다. 이어가려면 이 House에 기록된 Main의 native ID를 `--resume`에 지정합니다. 같은 채팅을 CLI와 웹에서 동시에 조작하는 기능은 제공하지 않습니다.

웹은 Codex `app-server --listen stdio://`의 실제 이벤트를 표시합니다. 정보가 없거나 연결이 끊기면 `unknown`으로 다루고, 요청 접수·모델 응답·연구 완료를 구별합니다. 실험적 app-server를 사용하며 대규모·장기 운영은 검증하지 않았습니다. 자동 테스트 207개를 통과했습니다. Codex 0.154.0에서는 Work 담당 생성·동일 ID 재사용과 Chrome의 실제 연결·승인·응답·파일 열람·새로고침을, 0.163.0에서는 모델 목록과 Main 모델·추론 강도·Fast 변경 후 실제 응답 및 표준 모드 복귀를 확인했습니다. 새 배치와 모델 선택 UI는 Chrome의 테스트 HTTP API로 검증했고 전역 설정 파일의 해시는 동일했습니다. [검증 기록](docs/verification.md), [App Server 문서](https://learn.chatgpt.com/docs/app-server)

## 공용 공장과 연구 방

```text
~/Desktop/house/                 Git으로 관리하는 공장 코드
├── bin/house                    터미널 진입점
├── skill/                       Codex 스킬과 공통 Python 엔진
│   ├── SKILL.md
│   ├── references/              방별 작업법·업데이트 검토 절차
│   └── scripts/house_engine/
├── install.py
├── scripts/weekly_scan.py
├── tests/                       동작·보존·업데이트 검증
├── docs/
├── shared/ → 공용 상태           아래 공용 데이터 위치의 바로가기, Git 제외
│   ├── config.json              새 프로젝트의 기본 릴리스
│   ├── templates/               초기 양식
│   ├── skills/store/            검토를 통과한 스킬의 고정 복사본
│   └── releases/<version>/      방 지침·양식·스킬 연결·해시
├── workshop/ → 업데이트 작업실   공용 데이터 위치의 바로가기, Git 제외
│   ├── scans/                   GitHub 탐색 결과와 오류
│   ├── candidates/              탐색 후보 기록·정확한 revision의 staged 스킬
│   ├── validation/              내용 검토·대표 작업 검증 증거
│   ├── notifications/           변화·적용·실패 기록
│   └── schedule.json
└── runtime/ → 실행 기록          공용 데이터 위치의 바로가기, Git 제외

/path/to/research-project/       사용자 프로젝트: 원래 파일 보존
├── house-data/                  house offload로 옮긴 원본 데이터 (자체 .gitignore로 git 제외)
└── house/                       텍스트 기록 (house track으로 git 추적)
    ├── HOUSE.md / AGENTS.md / hive.lock.json / .gitignore
    ├── house-data.json          옮긴 원본의 위치·크기·목록 해시
    ├── main/
    │   ├── why.md / target.md / design.md
    │   ├── method.md / reference.md / results.md
    │   ├── decisions.md / work.md / handoff.md
    │   ├── designs/ / jobs/
    │   └── sessions/<native-chat-id>/  채팅별 인계·작업 기록 (모든 방 공통)
    ├── literature/
    │   ├── GUIDE.md / reference.md
    │   └── reference/ / extract/
    ├── work/
    │   └── GUIDE.md / data/ / src/
    ├── output/
    │   └── summary.md
    └── discussion/
        └── GUIDE.md / requests/
```

공용 데이터는 macOS에서 `~/Library/Application Support/House`에 저장합니다. Desktop의 세 바로가기로 같은 파일을 열 수 있습니다. 주간 작업도 이 위치에 저장한 실행 파일을 사용하므로 백그라운드에서 Desktop 접근 권한을 요구하지 않습니다. Linux 기본 데이터 위치는 `~/.local/share/house`입니다.

모든 방에는 `ROOM.md`가 있습니다. 모델 작업을 실제로 시작하면 `work/model_v1.ipynb`와 `model_v1.md`를 만들고, 실행별 결과는 `output/model_v1/run-001/`의 `figure/`, `output/`, `results.md`, `run.json`으로 연결합니다. Discussion은 `council-model_v1.md` 등에 문헌·구현·결과를 근거로 개선안을 기록하며, Main이 결정한 후 다음 버전에 반영합니다.

Main은 literature·work·output·discussion에 각 한 명의 담당 에이전트를 두고, 필요한 방부터 작업을 맡깁니다. 같은 방의 후속 작업은 기존 ID로 보내고 필요하면 재개합니다. 신뢰 검토를 통과한 생성 훅과 깊이 제한으로 기본 worker/explorer, 작업마다 새 에이전트 생성, 하위 에이전트의 재귀 생성을 제한합니다. 적용 방식은 [방 지침](skill/references/rooms.md)에 설명합니다. 목표·입력 파일·허용 산출물·완료 기준을 지정하고 공유 기록의 작성자를 한 명으로 정합니다.

생성 결과 훅이 누락되면 같은 native 호출의 저장된 결과에서 ID나 확인된 실패를 복구합니다. 식별할 수 없는 결과는 예약을 유지하므로 `/subagents`에서 확인합니다. 시간이 지났다는 이유로 새 에이전트를 만들지는 않습니다.

foreground CLI에서는 대화와 `/subagents`로, 웹에서는 고정된 다섯 자리를 눌러 작업을 봅니다. 방 에이전트의 기록과 작업은 `<room>/sessions/<main-chat-id>/agents/<agent-id>/`에 둡니다. 별도 터미널의 `house sessions --watch`는 기록된 세션 상태를 읽어 보여 줍니다. 터미널 다섯 개를 자동으로 열거나 항상 실행되는 방별 서비스를 만들지는 않습니다.

Finder나 VS Code에서 해당 프로젝트의 `house` 폴더를 열어 두면 실제 생성·수정되는 파일을 볼 수 있습니다. 기존 연구를 완료 상태로 임의 분류하거나 이동하는 기능은 수행하지 않습니다.

## 채팅별 작업과 인계 (0.3.0)

`house start`·`house room`은 기존처럼 해당 방을 `--cd`로 지정해 Codex를 foreground에서 실행합니다. 웹 모드도 실행별 `-c` 설정으로 native hooks와 방 역할을 연결하며 전역 Codex 설정 파일은 바꾸지 않습니다. 훅은 Codex의 신뢰 검토를 따릅니다. 자동 기록은 House CLI·웹 연동으로 시작한 채팅에 적용되며, 앱에서 `$house`만 호출한 채팅에 훅이 설치됐다고 간주하지 않습니다. [공식 Hooks 문서](https://learn.chatgpt.com/docs/hooks)

`SessionStart`는 `house/<room>/sessions/<native-chat-id>/`에 `SESSION.md`, `handoff.md`, `session.json`, `events.jsonl`, `work/`를 만듭니다. 같은 native ID의 resume·compact는 같은 폴더와 관측 누계를 이어 씁니다. 폴더는 공유 연구 트리 안의 메모·작업 공간이며, 격리된 git worktree나 계산 체크포인트가 아닙니다.

문맥 **70% 이상**을 최근 관측하면 인계 준비를 안내합니다. 비율은 마지막 `token_count`의 `last_token_usage.total_tokens / model_context_window`이며 누적 청구 토큰을 쓰지 않습니다. transcript는 비안정 형식의 보조 인터페이스라 최대 2MiB만 읽고, 정보가 없으면 `unknown`으로 표시합니다. 이 값은 실시간 점유율이나 Codex UI와의 일치를 보장하지 않습니다. 0.154.0에서 짧은 세션은 확인했지만 장기 세션과 반복 압축의 관측 완전성은 검증하지 않았습니다.

자동 압축은 완료된 `PostCompact`의 `trigger=auto`만 셉니다. 수동 압축은 10회 기준에서 제외하며, 중간에 연동한 기존 채팅은 관측 시작 이후 횟수만 압니다. 이벤트를 식별할 수 없는 중복 전달은 중복 집계될 수 있습니다. 70%와 10회는 House 운영 기준이며 모델 품질의 공식 임계치가 아닙니다.

인계 파일은 **목표·확정 결정·실행 중인 작업·다음 행동** 네 제목 아래 실제 내용을 적고 전체 8,000자 이내로 유지합니다. 작성 후 checkpoint 명령으로 검증한 복사본을 `snapshots/`에 보관합니다. Main은 `main/handoff.md`도 갱신하고, 새 채팅은 이 파일과 같은 방의 최근 저장 snapshot을 읽습니다.

```sh
house sessions --room main          # 읽기 전용; --json으로 구조화 출력
house sessions --watch              # 읽기 전용; Ctrl-C는 상태 보기만 종료
house session checkpoint CHAT_ID --room main --project /path/to/research-project
house session checkpoint CHAT_ID --room main --boundary --project /path/to/research-project
```

`CHAT_ID`는 시작 훅이 알려 준 실제 native ID를 사용합니다. `--boundary`는 실제 작업 구간을 마쳤을 때만 붙입니다. 자동 압축 10회 이상이며 인계를 저장한 작업 경계에서 한 번 새 채팅을 권합니다. 단순 응답 종료나 `Stop`으로 자동 종료하지 않고, 실행 중인 계산을 중단하거나 새 채팅을 자동 생성하지 않습니다. `house session hook --room ROOM`은 CLI가 연결하는 native hook JSON 입력용입니다.

## 기록을 가볍게 유지하기 (0.2.0)

- **상태 줄**: 문서의 `상태: … <!-- house:status -->` 줄은 House가 관리합니다. 내용을 쓰면 이 줄을 현재 상태로 바꿉니다. 초기 문구를 남겨 두고 해명 문장을 덧붙이지 않습니다. `house check`는 내용이 생겼는데 상태 줄이 그대로인 문서를 알려 줍니다.
- **work와 handoff**: `main/work.md`는 날짜별 누적 로그, `main/handoff.md`는 매 작업 종료 때 전체를 다시 쓰는 현재 상태(8,000자 이내)입니다. 지난 내용은 work.md와 git 이력에 남습니다.
- **원본 데이터 분리**: 체크포인트·단계별 배열·대량 로그는 house/ 밖에 둡니다. 이미 house/ 안에 생긴 폴더는 옮기고 원래 자리에 상대 링크를 남겨 기존 코드 경로가 그대로 작동합니다. `.md` 기록이 든 폴더와 최근 30분 안에 바뀐 폴더(실행 중일 수 있음)는 거부합니다.
- **git 추적**: house/의 텍스트 기록만 커밋합니다. 프로젝트가 git 저장소가 아니면 house/에 저장소를 만들고, 저장소 안이면 house/ 경로만 커밋해 다른 staged 변경은 건드리지 않습니다. 10MB 넘는 파일, 합계 200MB, 5,000개를 넘는 추가는 거부합니다. house/.gitignore는 상위 프로젝트의 ignore 규칙이 방 폴더를 가리지 않게 다시 포함합니다.

```sh
house check                                   # 옮길 후보·git·상태 줄·handoff 길이
house offload output/model_v1/run-001/cases   # 원본 폴더를 ../house-data/로 옮기고 링크 남김
house offload --suggested --dry-run           # check 후보 전체의 이동 계획
house track                                   # git 추적 시작 (첫 커밋)
house save -m 'model_v1 run-002 결과 정리'      # house/ 기록만 커밋
```

## 이전 버전 프로젝트 업그레이드

현재 엔진을 처음 실행하면 외부 스킬 연결이 없는 이전 공용 기본값을 한 번 갱신합니다. 기본값에 검토된 외부 스킬이 연결되어 있거나 이후 `rollback`한 경우에는 바꾸지 않습니다. 기존 0.3.0 프로젝트를 포함해 프로젝트의 고정 버전은 강제로 바꾸거나 migrate하지 않습니다. CLI·웹 세션 운영 정책은 실행 중인 현재 엔진이 적용하고, 방의 연구 지침은 프로젝트가 고정한 릴리스를 유지합니다. 프로젝트별 구조 전환은 아래 순서를 따릅니다.

```sh
cd /path/to/research-project
house check                        # 현재 상태 확인
house migrate --dry-run            # 바뀔 파일 목록 (변경 없음)
house migrate                      # 적용: house/main/jobs/house-migrate-*/REPORT.md 생성
house offload --suggested --dry-run
house offload --suggested          # 실행 중인 계산이 없는지 확인한 뒤
house track
```

`migrate`가 바꾸는 것은 House 소유 부분뿐입니다. 사용자가 손대지 않은 House 파일(ROOM.md, HOUSE.md, AGENTS.md, GUIDE.md, 빈 템플릿)은 새 버전으로 교체하고, 내용이 생긴 문서는 0.1.0 초기 상태 문구 한 줄만 `상태: 기록 있음 — 다음 작업에서 현재 상태로 갱신 필요 <!-- house:status -->`로 바꿉니다. 바꾼 파일의 원본은 `REPORT.md` 옆 `previous/`에, 고정 버전 변경은 `hive.lock.json`의 history에 남습니다. 상태 줄의 실제 내용, 템플릿 해명 문장 정리, 긴 handoff 다시 쓰기는 연구 내용을 읽어야 하므로 REPORT.md의 요청 예시로 Codex에 맡깁니다.

## 두 종류의 업데이트

**공장 프로그램 자체**를 다른 컴퓨터나 세션에서 수정한 뒤 가져오기:

```sh
cd ~/Desktop/house
git pull --ff-only
PYTHONPATH=skill/scripts python3 -m unittest discover -s tests -v
python3 install.py --weekly    # 주간 실행 파일도 최신 코드로 등록 (macOS)
```

명령과 스킬이 저장소를 가리키므로 새 실행부터 변경된 코드를 사용합니다. 실행 중인 Codex 작업은 종료 후 다시 시작합니다. 로컬 수정이 있으면 먼저 검토해 커밋합니다. `shared/`, `workshop/`, `runtime/`, 연구 데이터는 Git 동기화 대상이 아니므로 다른 컴퓨터에 복제되지 않습니다.

**방에서 사용하는 외부 스킬**을 탐색·검토·적용하기:

```sh
house update                   # GitHub 탐색 후 Codex에서 검토·적용 작업
house update --scan-only       # 네트워크 탐색과 기록만 수행
house update --room literature # 특정 방에 집중
```

현재 채팅에서는 `$house update`를 사용합니다. 일반적인 방별 키워드로 GitHub 저장소와 주간 Trending을 찾고, 연결된 스킬의 upstream revision도 확인합니다. 인기와 새 커밋은 검토 후보를 뜻합니다. 자동 설치나 품질 보증을 뜻하지 않습니다.

실제 적용은 `stage → review → apply` 순서입니다. 정확한 커밋의 스킬을 가져와 내용을 검토하고 대표 작업을 실행한 증거를 보관한 다음 새 공용 릴리스를 만듭니다. 기존 프로젝트의 `hive.lock.json`은 유지됩니다. 상세 명령과 검토 형식은 [스킬 업데이트 지침](skill/references/update.md)에 있습니다.

```sh
house skills rollback 0.1.0
house pin VERSION --reason '이 프로젝트를 새 스킬 구성으로 전환하는 이유'
```

`rollback`은 새 프로젝트의 공용 기본값을 되돌립니다. `pin`은 현재 프로젝트에만 명시적으로 적용하며 연구 기록을 덮어쓰지 않습니다. 프로젝트에 복사된 `ROOM.md`의 사용자 편집도 보존하고, 실행은 잠금 버전의 공용 지침과 함께 읽습니다.

초기 설치는 다섯 방의 기본 연구 지침을 제공합니다. 외부 literature/council/superpowers 스킬은 자동으로 복제되지 않으며, 검토 후 해당 방의 bindings에 추가합니다. 기록 파일의 해시 검사는 변조를 감지하는 기능이고 검토자의 판단 자체를 인증하는 시스템은 아닙니다.

## 주간 탐색과 알림

설치 시 `--weekly`를 사용하면 macOS의 `research.house.weekly` 작업을 등록합니다. 주간 작업은 모델을 호출하거나 스킬을 자동 적용하지 않습니다. 새 후보·upstream 변경·오류를 탐색해 `workshop/`에 기록하고, 새 알림만 macOS에 제출합니다. 변화가 없으면 알림을 보내지 않습니다.

적용 기록은 `house v<version> #literature skill update` 같은 형식을 사용합니다. 화면 배너 표시는 macOS 알림 설정에 따르며, 로그는 항상 로컬에 남습니다. 컴퓨터가 꺼져 있거나 로그아웃된 동안 정시 실행은 보장되지 않습니다.

```sh
launchctl print gui/$(id -u)/research.house.weekly
launchctl bootout gui/$(id -u)/research.house.weekly  # 현재 로그인 세션에서 중지
```

영구 중지는 위 명령 후 `~/Library/LaunchAgents/research.house.weekly.plist`를 다른 곳으로 옮깁니다. 다시 켜려면 `python3 install.py --weekly`를 실행합니다.

## 다른 위치와 진단

코드 저장소는 `~/Desktop/house`, 기본 공용 데이터는 macOS에서 `~/Library/Application Support/House`입니다. 주간 작업의 사용자 지정 데이터 경로는 Desktop·Documents·Downloads 같은 macOS 보호 폴더를 피합니다. 별도 위치는 매 세션의 `HOUSE_HOME` 환경 변수 또는 `--home`으로 지정합니다. `install.py --home`만으로 셸·Codex의 환경을 바꾸지는 않습니다. 프로젝트를 만든 뒤에는 해당 프로젝트가 기록한 공용 경로를 우선 사용합니다.

`house room main --dry-run --json`은 폴더를 초기화하고 실제 Codex 실행 인자를 표시하지만 Codex를 실행하지 않습니다. `house context main --json`은 현재 채팅에서 사용할 지침을 출력합니다. Codex 실행 종료는 연구 완료와 구별해 기록합니다.

동일 방의 중복 터미널 실행은 잠금으로 제한합니다. 문서 편집기와 임의의 에이전트까지 자동 잠금하는 것은 아니므로, 같은 파일을 병렬 작업에 중복 배정하지 않습니다. 외부 검색은 네트워크·GitHub rate limit의 영향을 받으며, 실패는 오류로 기록합니다. `HOUSE_GITHUB_TOKEN` 또는 `GITHUB_TOKEN`을 설정하면 GitHub API 요청에만 사용합니다. 토큰을 이 저장소에 저장하지 않습니다.

검증 명령: `PYTHONPATH=skill/scripts python3 -m unittest discover -s tests -v`

통합 근거: [Codex CLI](https://learn.chatgpt.com/docs/cli/reference), [GitHub 검색 API](https://docs.github.com/en/rest/search/search), [GitHub 커밋 API](https://docs.github.com/en/rest/commits/commits).
