# 변경 기록

## 0.3.0 · 2026-10-19

- CLI 실행별 native hooks·방 역할 설정 연결. 전역 Codex 설정과 기존 프로젝트의 고정 릴리스는 유지하며 훅은 native 신뢰 검토를 따른다.
- native 채팅별 `sessions/<id>/` 작업 공간과 인계 snapshot. resume·compact는 같은 폴더와 관측 누계를 유지한다.
- 최근 문맥 관측 70%에서 인계 준비 안내. 비안정 transcript를 최대 2MiB 읽고 정보가 없으면 `unknown`; 0.154.0 실제 세션 형식의 실측 검증은 미완료.
- 완료된 자동 압축 10회 이상에서 인계를 저장한 실제 작업 경계에만 한 번 새 채팅 권고. 수동 압축 제외, 식별 불가 중복 전달은 중복 집계 가능. 자동 종료·계산 중단·새 채팅 자동 생성은 하지 않는다.
- Main의 네 방 담당을 각 한 명으로 예약하고 ID 재사용. 기본 worker/explorer·작업별 추가 생성·재귀 생성을 훅에서 거부한다.
- `house sessions [--room ROOM] [--watch|--json]` 읽기 전용 상태 보기와 `house session checkpoint` 인계 검증·보관 명령 추가. 앱의 `$house` 단독 호출에는 CLI 자동 훅이 연결되지 않는다.

## 0.2.1 · 2026-10-13

- 토큰 절약 규칙: 세션 시작 때 `main/handoff.md`만 먼저 읽고, 다른 기록·보관·output 상세는 필요할 때만 연다. 보고는 짧게, 부가 산출물은 요청 시에만.
- `house check`가 20,000자를 넘는 main 기록(work·decisions·target·design)을 알리고 `main/archive/<날짜>/`로 옮기도록 권한다.

## 0.2.0 · 2026-10-12

- 문서 상태 줄을 `<!-- house:status -->`로 표시하고, 내용이 생겼는데 초기 문구가 남은 문서를 `house status`/`house check`에서 감지.
- `main/work.md`는 누적 로그, `main/handoff.md`는 8,000자 이내 현재 상태 스냅샷으로 역할 분리.
- `house offload`: 원본 데이터를 `../house-data/`로 옮기고 상대 링크·목록 해시 기록. 기록 문서 포함·최근 변경 폴더는 거부, 다른 디스크는 복사 검증 후 이동. `--suggested`로 후보 일괄 처리.
- `house track`/`house save`: house/ 텍스트 기록만 git에 커밋, 원본 규모 추가는 거부. `house/.gitignore`가 상위 ignore 규칙에서 방 폴더를 다시 포함.
- `house check`: 크기·옮길 후보·git·상태 줄·템플릿 해명 줄·handoff 길이·옮긴 원본 무결성 점검.
- `house migrate`: 이전 릴리스 프로젝트를 백업과 보고서를 남기며 새 구조로 전환.
- 새 엔진 릴리스가 스킬 연결이 없는 이전 공용 기본값을 한 번 대체.

## 0.1.0 · 2026-10-08

- CLI 메뉴와 Codex House 스킬이 동일한 Python 엔진 사용.
- 다섯 연구 방과 이유·목표·설계·문헌·결과·인계 양식 생성.
- 기존 파일 보존, 프로젝트별 릴리스 고정, 방별 Codex 실행 기록.
- GitHub/Trending 탐색과 스킬 staging·검토 증거·적용·되돌리기.
- macOS 주간 탐색 및 변화가 있는 경우에만 로컬 알림.
- Desktop 저장소와 명령·스킬 연결, 로컬 연구/런타임 데이터 Git 제외.
