# House 0.4.2 연결 구현·검증

검증일: 2026-10-22 KST. 환경: WSL2 Linux, 전체 시험 Python 3.14.4, codex-cli 0.163.0.
상태: 로컬 개발본. 원격 릴리스·GitHub Actions 실행 결과를 뜻하지 않는다.

## 사용자에게 달라지는 점

지원되는 로컬 Codex 클라이언트가 프로젝트의 메시지·도구·압축 이벤트를 House에 전달하는 연결을 추가했다.
VS Code에서 이미 사용하는 Codex 대화에도 이 경로로 연결할 수 있도록 구현했다. 별도 터미널에서
House를 통해 대화를 다시 시작하는 것을 필수로 두지 않는다. 실제 확장·앱이 훅을 읽고 실행하며
경고를 표시하는지는 해당 클라이언트에서 확인해야 한다.

    house connect --project /path/to/project --dry-run --json
    house connect --project /path/to/project --apply --json
    house doctor --project /path/to/project --native --json

프로젝트는 먼저 House로 초기화되어 있어야 한다. connect는 새 연구의 입력·설계에 동의하는 명령이 아니다.
연결 명령은 프로젝트 설정에 House 훅만 병합하며, doctor는 등록·실제 관측·native 검색·신뢰 상태를 나누어 보여준다.
설정 변경을 발견하기 위해 클라이언트를 다시 열거나 재개해야 할 수 있다.

최근 문맥 관측이 70%에 이르면 인계 준비를 알린다. 식별 가능한 자동 압축 10회 이후에도 바로 대화를
종료하지 않으며, 실제 인계 내용을 저장한 작업 경계에서 새 대화를 권한다. 수동·불명 압축, 과거 관측
미확인 구간, 경고 출력 시도와 실제 화면 열람을 구분한다.

사용자 훅과 기존 프로젝트 pin·입력·결과를 보존한다. 기존 세션 관측은 덮어쓰지 않고 v2 관측을 별도로
기록한다. House가 실행한 CLI·웹은 기존 invocation 경로를 유지하며 project 경로가 중복 기록하지 않는다.
일반 클라이언트는 project 경로로 같은 경고 엔진을 사용한다.

## 이번에 확인한 범위

| 검사 | 결과 | 의미와 한계 |
|---|---|---|
| Python 전체 자동 시험 | 315개 실행, 313개 통과, 2개 생략; 33.056초 | Windows native 전용 두 검사는 WSL에서 생략 |
| 설치한 실행기·등록 명령 | 24개 검사 통과 | 실제 subprocess 경계를 사용했으며 lifecycle JSON과 transcript는 합성 fixture |
| 실제 Codex 훅 검색 | 프로젝트 신뢰 전 0개, 전용 fixture 프로젝트 신뢰 후 House 훅 5개 발견 | initialize와 hooks/list만 호출. 모델 턴·실제 훅 실행·화면 표시 시험과 구분 |
| native 훅 신뢰 | 다섯 훅 모두 untrusted / requires_review | trusted hash와 우회 설정을 쓰지 않음 |
| 웹 경고 표시 코드 | 실제 renderer 검사 8개와 JavaScript 구문 검사 통과 | V8에서 Node 모듈 shim·DOM stub 사용. 실제 브라우저 검사가 아님 |
| 설치 스킬 형식 | skill-creator quick_validate 통과 | 새 프로젝트의 프로젝트 전용 스킬 설치 경로 검사 |
| 기존 0.4.1 자료 | run-001 파일 20개·입력 CSV의 SHA-256 일치, pin 0.4.1 유지 | 이전 검증을 새 모델 실행 결과로 바꾸지 않음 |
| 전역 Codex 설정 | native 검사 전후 SHA-256 일치 | 별도 CODEX_HOME에서 자체 생성 fixture의 프로젝트만 신뢰 |
| 설치 소스 | manifest 파일 55개 해시 일치 | 설치본은 별도 소스 스냅샷으로 보존 |

자동 시험은 등록 계획의 무변경, JSON/TOML 병합·해제, 사용자 변경 보존, 쓰기 실패 복구,
실제 OS 프로세스 간 파일 락, 연결한 CLI의 방 에이전트 권한 유지, fork·child 분리, 문맥 값의
신선도·중복 경고, 압축 식별·중복·분류, 인계 저장과 웹 관측의 정확한 대화 선택을 포함한다.

첫 설치 fixture에는 Codex가 허용하지 않는 최상위 메타데이터가 있어 native 검색이 실패했다.
해당 실패 증거를 보존하고 유효한 description 필드로 새 fixture를 만들었다. doctor가 native parser의
warnings도 반환하도록 보강했으며, 이를 별도 회귀 시험으로 확인했다.

## 설치와 근거 위치

- 실행기: /home/kjm/.local/bin/house → /home/kjm/.local/share/house-source/0.4.2-dev-20261022-r2/bin/house
- 프로젝트 전용 스킬: /tmp/house-connect-0.4.2-validation-r2/project/.agents/skills/house
- 최종 검증 요약: /tmp/house-connect-0.4.2-validation-r2/validation-evidence/summary.json
- 전체 시험 출력: 같은 evidence 폴더의 full-suite.txt
- 설치 동작·native 검색·웹 코드 검사: installed-smoke-report.json, native-summary.json, frontend.json
- 설치 파일 해시: /home/kjm/.local/share/house-source/0.4.2-dev-20261022-r2-provenance.json
- 첫 fixture 실패·복구 증거: /tmp/house-connect-0.4.2-validation/validation-evidence/

기존 0.4.1 설치 소스는 보존했다. 공용 기본 릴리스는 개발본 설치로 0.4.2가 되었으며, 기존 프로젝트의
고정 릴리스는 그대로 유지된다. 새 개발본 스킬은 검증 프로젝트에 설치했고 보호된 전역 스킬 폴더는 변경하지 않았다.

## 남은 실제 환경 확인

VS Code 확장·내장 터미널·독립 CLI·데스크톱 앱의 실제 이벤트 실행과 화면 경고는 각각 확인해야 한다.
이번 native 검색 성공은 그 화면 시험의 통과를 뜻하지 않는다. Windows 실행은 이 환경의 WSL Windows
연결에서 UtilBindVsockAnyPort socket failed 1 오류로 사용할 수 없었다. Windows CI 작업은 추가했으나
원격으로 실행하지 않았다. 클라우드가 세션을 운영하는 클라이언트의 원격 adapter는 포함하지 않는다.

Windows hooks는 기본 cmd.exe 셸을 대상으로 모든 인자를 인용하며, %, !, 따옴표·개행이 포함된 인자는
명확한 오류로 거부한다. 설치용 house.cmd의 batch 인용과 hook 문자열의 인용을 구분한다. Codex에서
별도 environment.shell을 지정한 경우 같은 문자열의 실행은 보장하지 않는다. 해당 버전의 기본 셸과
재정의 동작은 [공식 실행 코드](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/hooks/src/engine/command_runner.rs#L384-L408)와
[공식 셸 선택 코드](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/core/src/session/mod.rs#L5445-L5469)로 확인했다.

사용법·제약은 [연결 지침](../skill/references/connections.md), 초기 목표와 후속 조정은
[연결 설계](superpowers/plans/2026-10-22-house-cross-client-session-alerts.md)에 있다.
