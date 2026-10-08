# House v0.1 검증 기록

검증일: 2026-10-08. Python 3.9.6, macOS, codex-cli 0.154.0.

## 확인한 동작

- 비어 있지 않은 프로젝트 초기화, 재진입·중첩 방에서 재개, 기존 문서·AGENTS·버전 잠금 보존.
- 경로 충돌·symlink·동시 초기화 처리, 원자적 JSON 기록과 방 실행 잠금.
- 공용 방 지침과 양식의 해시 검증, 엔진에 새 양식이 생겨도 기존 릴리스 사용 가능.
- 실제 터미널 PTY의 메뉴 2번 → literature 작업 경로 → 테스트 Codex 프로세스 실행 → 종료 기록.
- 설치된 실제 Codex의 읽기 전용 임시 Main 확인: `HOUSE_SMOKE_OK`, release `0.1.0`, 입력한 연구 목표 반환.
- 실제 GitHub 공개 탐색: 문헌 검색 후보 3개와 Trending 후보 3개, 오류 없음. 검토 전 후보로 기록하며 설치하지 않음.
- GitHub 통신 fixture로 revision 고정, stage·검토·증거 스냅샷·apply·pin·rollback 전체 수명주기 검증.
- 후보/스킬/검토 내용 변경 감지, 기존 프로젝트 잠금 보존, 현재 릴리스가 손상돼도 유효한 이전 릴리스로 복구.
- 설치 충돌 보존, 반복 설치, CLI·스킬 심볼릭 링크, 주간 작업 argv와 경로 검증.
- 주간 알림: 변경이 없으면 조용함, 오류 응답을 실패 알림으로 기록, OS 제출 실패 시 재시도.
- skill-creator의 `quick_validate.py`: `Skill is valid!`.

## 스킬 사용 시나리오

격리된 동일한 연구 시작 요청을 스킬 없이 수행했을 때는 개별 `Main/README.md`만 생겼습니다. House 스킬을 적용한 시나리오에서는 다섯 방을 생성하고 목표·설계·인계를 기록했습니다. 기존 사용자 메모의 SHA-256은 동일했습니다. 구현·학습·인용·결과·사용자 동의를 만들어내지 않았습니다.

초기 시나리오에서 `rationale.md`/`why.md`의 불일치를 발견했고, 최종 코드에서는 `why.md`로 통일했습니다. 이후 핵심 테스트가 실제 파일 존재를 검증합니다.

## 검토에서 수정한 사항

- 현재 채팅 context가 프로젝트 상위 폴더에서도 House를 찾도록 수정.
- 실행 로그 디렉터리 생성과 변경된 공용 지침의 무결성 검사.
- 신규 엔진 양식이 이전 프로젝트의 버전 고정이나 사용자 경로와 충돌하지 않도록 수정.
- 설치 기본 공용 경로 일치, quoted tilde 정규화, 사용자 지정 HOME의 환경 설정 요구 명시.
- 반환된 검색 오류도 주간 알림으로 기록.
- 중단된 스킬 복사가 최종 저장소를 오염시키지 않도록 원자적 게시.
- tar 메타데이터를 포함한 전체 압축 해제 크기 제한.

## 범위

실제 연구 모델 학습이나 외부 스킬 채택은 수행하지 않았습니다. 자동 테스트의 대표 검토 증거는 테스트 fixture이며 실제 후보의 품질 평가로 사용하지 않습니다. 화면 알림은 OS에 제출한 상태와 사용자가 실제로 본 상태를 구분합니다. 운영 기록·다운로드·연구 데이터는 Git 제외 대상입니다.

재검증: `PYTHONPATH=skill/scripts python3 -m unittest discover -s tests -v`

실제 배포 확인 중 macOS launchd의 Desktop 접근 차단을 재현했습니다. 공용 데이터를 Application Support로 분리하고, 주간 실행용 Python 파일을 해시별 스냅샷으로 이 위치에 등록하도록 수정했습니다. GitHub CI의 Codex 미설치 환경도 테스트 실행 파일 fixture로 처리했습니다.

## 최종 배포 확인

- 로컬 자동 테스트 60개 통과.
- GitHub Actions의 macOS/Linux × Python 3.9/3.12 네 조합 모두 통과: https://github.com/ISIF-3/house/actions/runs/37746103062
- Desktop/house 저장소, house 명령과 Codex 스킬 연결 확인. 원격은 비공개 ISIF-3/house, 기본 브랜치는 main.
- 실제 launchd 작업에서 오류 없는 첫 탐색 완료: 후보 30개(방별 검색 25개, Trending 5개). 새 후보 25건을 하나의 macOS 알림으로 제출. 실제 배너 열람 여부는 확인하지 않음.
- 공용 데이터 경로는 Application Support, Desktop에 shared/workshop/runtime 바로가기 확인. 초기 Desktop 접근 실패 로그는 과거 기록으로 보존.
- 설치된 명령의 새 임시 프로젝트가 올바른 공용 경로를 잠그는 것까지 확인. 기존 연구 프로젝트 변경 없음.
