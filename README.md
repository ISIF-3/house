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
```

Codex 앱·CLI·확장에서 프로젝트를 열고 **`$house 연구 목표를 정리하며 시작해줘`**라고 요청할 수도 있습니다. `/house`는 기본 슬래시 명령으로 등록되지 않습니다. 새 스킬이 보이지 않으면 Codex를 다시 열고 스킬 목록을 확인합니다. [Codex 스킬 문서](https://learn.chatgpt.com/docs/build-skills)

Main은 연구의 이유·최종 목표를 정리하고 데이터, 방법, 평가 기준과 자원을 설명합니다. 실제 AI·통계 모델 구현은 설계에 합의한 뒤 시작합니다. 초기화 시 가짜 노트북·논문·결과·승인을 만들지 않습니다.

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
├── shared/                      로컬 공용 상태, Git 제외
│   ├── config.json              새 프로젝트의 기본 릴리스
│   ├── templates/               초기 양식
│   ├── skills/store/            검토를 통과한 스킬의 고정 복사본
│   └── releases/<version>/      방 지침·양식·스킬 연결·해시
├── workshop/                    로컬 업데이트 작업실, Git 제외
│   ├── scans/                   GitHub 탐색 결과와 오류
│   ├── candidates/              탐색 후보 기록·정확한 revision의 staged 스킬
│   ├── validation/              내용 검토·대표 작업 검증 증거
│   ├── notifications/           변화·적용·실패 기록
│   └── schedule.json
└── runtime/                     로컬 실행 기록·동시 실행 잠금

/path/to/research-project/       사용자 프로젝트: 원래 파일 보존
└── house/
    ├── HOUSE.md / AGENTS.md / hive.lock.json
    ├── main/
    │   ├── why.md / target.md / design.md
    │   ├── method.md / reference.md / results.md
    │   ├── decisions.md / work.md / handoff.md
    │   └── designs/ / jobs/
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

모든 방에는 `ROOM.md`가 있습니다. 모델 작업을 실제로 시작하면 `work/model_v1.ipynb`와 `model_v1.md`를 만들고, 실행별 결과는 `output/model_v1/run-001/`의 `figure/`, `output/`, `results.md`, `run.json`으로 연결합니다. Discussion은 `council-model_v1.md` 등에 문헌·구현·결과를 근거로 개선안을 기록하며, Main이 결정한 후 다음 버전에 반영합니다.

Main은 필요한 방에 입력 파일·목표·산출물 경로를 지정해 작업을 맡깁니다. Codex에서 subagent가 가능하면 한정된 일을 위임하고, 진행 상태와 인계는 MD에 남깁니다. 터미널 메뉴는 선택한 방의 Codex를 여는 진입점입니다. 항상 작동하는 자율 에이전트 군집이나 별도 채팅 생성 서비스는 아닙니다.

Finder나 VS Code에서 해당 프로젝트의 `house` 폴더를 열어 두면 실제 생성·수정되는 파일을 볼 수 있습니다. 기존 연구를 완료 상태로 임의 분류하거나 이동하는 기능은 수행하지 않습니다.

## 두 종류의 업데이트

**공장 프로그램 자체**를 다른 컴퓨터나 세션에서 수정한 뒤 가져오기:

```sh
cd ~/Desktop/house
git pull --ff-only
PYTHONPATH=skill/scripts python3 -m unittest discover -s tests -v
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

기본 공용 위치는 `~/Desktop/house`입니다. 별도 위치는 매 세션의 `HOUSE_HOME` 환경 변수 또는 `--home`으로 지정합니다. `install.py --home`만으로 셸·Codex의 환경을 바꾸지는 않습니다. 프로젝트를 만든 뒤에는 해당 프로젝트가 기록한 공용 경로를 우선 사용합니다.

`house room main --dry-run --json`은 폴더를 초기화하고 실제 Codex 실행 인자를 표시하지만 Codex를 실행하지 않습니다. `house context main --json`은 현재 채팅에서 사용할 지침을 출력합니다. Codex 실행 종료는 연구 완료와 구별해 기록합니다.

동일 방의 중복 터미널 실행은 잠금으로 제한합니다. 문서 편집기와 임의의 에이전트까지 자동 잠금하는 것은 아니므로, 같은 파일을 병렬 작업에 중복 배정하지 않습니다. 외부 검색은 네트워크·GitHub rate limit의 영향을 받으며, 실패는 오류로 기록합니다. `HOUSE_GITHUB_TOKEN` 또는 `GITHUB_TOKEN`을 설정하면 GitHub API 요청에만 사용합니다. 토큰을 이 저장소에 저장하지 않습니다.

검증 명령: `PYTHONPATH=skill/scripts python3 -m unittest discover -s tests -v`

통합 근거: [Codex CLI](https://learn.chatgpt.com/docs/cli/reference), [GitHub 검색 API](https://docs.github.com/en/rest/search/search), [GitHub 커밋 API](https://docs.github.com/en/rest/commits/commits).
