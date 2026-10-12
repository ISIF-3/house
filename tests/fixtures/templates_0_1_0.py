"""Versioned Korean research guidance; templates contain no invented evidence."""

ROOMS = ("main", "literature", "work", "output", "discussion")

COMMON = """\n## 공통 원칙
- 상위 HOUSE.md, AGENTS.md와 hive.lock.json을 먼저 읽는다. 실행 중인 프로젝트는 고정된 릴리스를 따른다.
- 외부 스킬이 설치되었다고 가정하지 않는다. bindings가 비어 있으면 기본 지침만 사용한다.
- bindings가 있으면 기록된 정확한 불변 경로와 리비전을 확인한다. 공유 기본값 변경을 현재 프로젝트에 자동 적용하지 않는다.
- 연구 이유, 목표, 성공 기준, 데이터와 설계에 대한 사용자 합의를 먼저 기록한다. 합의 전 AI·통계 모델 구현이나 학습을 시작하지 않는다.
- 기존 파일을 읽고 보존한다. 추정, 확인된 사실, 미실행 계획과 실제 실행 결과를 구분한다.
- 각 작업 후 Main의 work.md에 진행 상황과 다음 행동을 남긴다. 근거와 결정은 상대 경로로 연결한다.
"""

ROOM_INSTRUCTIONS = {
    "main": """# Main — 연구의 이유와 설계

먼저 why.md와 target.md를 읽고 왜 이 연구가 필요한지, 어떤 질문에 답할지 사용자와 대화한다.
목표, 평가 기준, 데이터 사용 범위, 비교 기준과 제약을 정리한다. reference.md는 근거 탐색의 길잡이다.
design.md와 method.md에 연구 설계와 방법을 제안하고, decisions.md에 사용자의 실제 동의와 변경 이유를 기록한다.
질문을 한 번에 과도하게 묶지 않는다. 미확정 항목은 미확정으로 남긴다.
work.md는 진행 기록이고 실제 코드·모델 작업은 Work에서 한다. results.md는 실행 근거가 생긴 뒤 갱신한다.
Literature의 근거, Work의 실행, Output의 요약을 연결하고 Discussion의 개선 요청을 검토해 결정한다.
""",
    "literature": """# Literature — 출처를 따라갈 수 있는 문헌 정리

Main의 why.md, target.md, design.md를 읽고 필요한 근거와 검색 질문을 정한다.
GUIDE.md를 따르고 reference.md에 출처 URL/DOI, 서지사항, 확인 상태와 로컬 파일을 연결한다.
원문은 reference/에, 근거 추출은 extract/에 둔다. 각 추출에는 원문 위치(쪽·표·절), 인용/요약 구분,
연구 설계, 표본, 방법, 결과, 한계와 연구 목표 관련성을 기록한다. 확인하지 않은 논문이나 수치를 만들지 않는다.
접근하지 못한 원문과 검색 범위의 한계를 밝힌다. 결론에 영향을 주는 근거는 Main으로 전달한다.
""",
    "work": """# Work — 합의된 설계의 재현 가능한 실행

시작 전에 Main의 design.md와 decisions.md에서 사용자의 설계 합의를 확인한다.
설계가 미확정이면 먼저 Main과 논의한다. 실제 데이터·통계 분석·AI 모델을 임의로 시작하지 않는다.
data/에는 사용자가 허용한 데이터와 출처를, src/에는 실제 작성한 코드를 둔다.
모델 작업이 합의되면 model_v1.ipynb와 model_v1.md를 함께 만든다. 변경은 model_v2처럼 새 버전으로 기록한다.
노트북은 실제 셀·실행 상태를 보존하고, MD는 목적·가정·입출력·평가·변경 이유를 설명한다.
실행 결과는 ../output/<version>/<run>/에 저장한다. 버전·실행 ID, 명령, 환경, 데이터 식별자,
난수 시드, 로그와 성공/실패를 연결한다. 초기화만으로 노트북이나 실행 결과를 만들지 않는다.
""",
    "output": """# Output — 확인된 실행 결과와 해석

결과는 output/<version>/<run>/ 규칙으로 구분한다. 각 실행의 모델/분석 버전과 재현 근거를 연결한다.
summary.md에는 실제 관찰한 결과와 연구 목표 대비 해석을 기록한다. 현재 없는 결과는 미기록으로 둔다.
표·그림은 원본 데이터와 실행 경로를 연결하고, 실패한 실행·불확실성·한계도 남긴다.
계획이나 예상 수치를 실행 결과로 표현하지 않는다. 요약을 Main의 results.md와 연결한다.
설계 변경이 필요하면 Discussion에 요청하고 Main의 결정을 기다린다.
""",
    "discussion": """# Discussion — 근거가 있는 개선 요청

GUIDE.md를 따라 requests/에 개선 요청을 작성한다. 요청마다 문제, 근거 경로, 제안, 기대 효과,
비용·위험, 검증 방법과 상태를 적는다. 확인되지 않은 결과나 사용자 합의를 꾸며내지 않는다.
개선 요청 → Main 검토 → 사용자와 결정 → decisions.md 기록 → 해당 방 실행 순서를 따른다.
Discussion의 제안을 곧바로 합의나 실행 지시로 취급하지 않는다. 수락·보류·거절 이유와 결정 경로를 남긴다.
""",
}
ROOM_INSTRUCTIONS = {room: guide + COMMON for room, guide in ROOM_INSTRUCTIONS.items()}

PROJECT_TEMPLATES = {
    "HOUSE.md": """# House 연구 공간

이 house/ 내부만 House가 만든 연구 공간이다. 외부 프로젝트의 파일과 규칙은 보존한다.
main/에서 연구 이유와 목표를 정하고 설계에 합의한 뒤 literature/, work/, output/, discussion/으로 이동한다.
각 방의 ROOM.md와 GUIDE.md(있는 경우)를 읽는다. main/work.md에서 진행 상황을 이어 간다.
hive.lock.json은 공유 홈, 프로젝트 릴리스와 스킬 bindings를 고정한다. 빈 bindings는 외부 스킬 미연결 상태다.
공유 업데이트가 이 프로젝트를 자동 마이그레이션하지 않는다. 별도 합의 전 기존 잠금을 유지한다.
초기 문서는 빈 연구 기록이며 설계 승인, 모델 실행 또는 결과를 의미하지 않는다.
""",
    "AGENTS.md": """# House 내부 작업 지침

이 지침은 이 house/ 범위에만 적용한다. 상위 프로젝트의 사용자 지침도 따른다.
HOUSE.md, hive.lock.json, 선택한 방의 ROOM.md와 main/{why,target,design,decisions,work}.md를 읽는다.
연구 이유와 목표를 사용자와 확인하고, 설계 합의를 기록한 뒤 구현·통계 분석·AI 모델 실행을 진행한다.
승인·인용·데이터·실행·결과를 만들어내지 않는다. 기존 사용자 문서와 공유 릴리스는 덮어쓰지 않는다.
근거는 출처와 파일 경로로 연결한다. Discussion의 제안은 Main의 결정을 거친다.
작업 진행은 main/work.md에 기록한다. 모델 노트북과 설명 MD를 버전별로 함께 관리하고 결과는 output/<version>/<run>/에 둔다.
외부 스킬은 잠금의 bindings에 지정된 정확한 불변 경로를 확인한 뒤 필요할 때 읽는다.
""",
    "main/why.md": """# 연구 이유

상태: 미확정 — 사용자와 대화 후 작성

## 문제와 배경

## 왜 지금 이 연구가 필요한가

## 기존 지식의 한계와 기대 기여

## 근거와 출처
""",
    "main/target.md": """# 연구 목표

상태: 초안 — 사용자와 확인 필요

## 사용자가 제시한 목표

{goal}

## 연구 질문과 가설

## 성공 기준과 비교 기준

## 범위·제약·미확정 사항
""",
    "main/design.md": """# 연구 설계

상태: 미확정 — 사용자 합의 기록 없음

## 연구 질문과 목표 연결

## 데이터·대상·표본과 사용 범위

## 비교 설계·분석·모델 계획

## 평가 기준·검증·재현성

## 한계·위험·미확정 질문

## 사용자와의 합의

실제 합의가 이루어지면 내용과 날짜, decisions.md의 결정 항목을 연결한다.
""",
    "main/decisions.md": """# 설계와 변경 결정

아직 기록된 결정이 없다. 실제 사용자 합의가 이루어진 뒤 작성한다.

| 날짜 | 논의 내용 | 결정·합의 | 이유·근거 | 관련 문서 |
| --- | --- | --- | --- | --- |
""",
    "main/work.md": """# 연구 진행 기록

상태: 초기 문서 생성 — 연구 작업은 아직 기록되지 않음

## 현재 단계

연구 이유와 목표를 사용자와 확인하고 설계를 논의한다.

## 진행 내역

| 날짜 | 작업 | 근거·산출물 | 상태 | 다음 행동 |
| --- | --- | --- | --- | --- |

## 미해결 질문
""",
    "main/handoff.md": """# 다음 작업으로 이어가기

상태: 초기화 — 아직 수행한 연구 작업 없음

## 마지막으로 확인한 상태

## 다음 작업과 필요한 입력

## 미해결 질문·실행 중 작업

## 관련 파일과 작업 ID
""",
    "main/reference.md": """# 연구 근거 길잡이

문헌 목록은 ../literature/reference.md, 원문은 ../literature/reference/, 추출은 ../literature/extract/에 기록한다.
아직 검증한 근거가 없다.

## 목표·설계와 연결되는 근거
""",
    "main/method.md": """# 연구 방법

상태: 미확정 — 설계 합의 후 구체화

## 선택한 방법과 이유

## 데이터 준비와 분석 절차

## 검증·평가·재현 방법

## Work 문서와 실행 연결
""",
    "main/results.md": """# 연구 결과

상태: 미기록 — 실행된 연구 결과 없음

실제 실행이 생기면 ../output/summary.md와 버전/실행 경로를 연결한다.

## 확인된 결과

## 목표 대비 해석과 한계
""",
    "literature/GUIDE.md": """# 문헌 작업 안내

1. Main의 연구 질문에 맞춰 검색 질문과 범위를 기록한다.
2. reference.md에 서지사항, URL/DOI, 원문 접근 여부와 로컬 경로를 기록한다.
3. reference/에 허용된 원문을 보관하고 extract/에 출처와 위치를 연결한 추출 MD를 작성한다.
4. 추출에는 질문·설계·표본·방법·결과·한계와 Main 목표 관련성을 적는다.
5. 사실과 해석, 직접 인용과 요약을 구분하고 확인되지 않은 내용은 표시한다.
""",
    "literature/reference.md": """# 문헌 목록

아직 확인된 문헌이 없다. 실제 확인한 출처만 추가한다.

| ID | 서지사항 | URL·DOI | 확인 상태 | 원문 경로 | 추출 경로 | 목표 관련성 |
| --- | --- | --- | --- | --- | --- | --- |
""",
    "work/GUIDE.md": """# 구현·분석 작업 안내

Main의 design.md와 decisions.md에서 설계 합의를 먼저 확인한다. data/와 src/는 실제 작업이 생기기 전까지 빈 폴더다.
합의된 모델 작업을 시작할 때 model_v1.ipynb와 model_v1.md를 함께 만든다. 분석만 필요한 연구에 모델을 억지로 만들지 않는다.
설명에는 목표, 입력/출력, 가정, 사용법, 평가 기준과 변경 이유를 기록한다.
실행마다 ../output/<version>/<run>/에 실행 설정, 로그, 환경과 실제 산출물을 연결한다.
실행하지 않은 셀·예정 결과를 완료된 결과로 기록하지 않는다.
""",
    "output/summary.md": """# 결과 요약

상태: 미기록 — 실제 실행 결과 없음

## 버전과 실행

| 버전 | 실행 ID | 실제 상태 | 근거 경로 | 주요 관찰 |
| --- | --- | --- | --- | --- |

## 연구 목표 대비 해석

## 한계와 후속 검토
""",
    "discussion/GUIDE.md": """# 개선 요청 안내

requests/에 요청별 MD를 만든다. 초기에는 요청이 없다.
각 요청에 문제, 출처·실행 근거, 제안, 기대 효과, 비용·위험, 검증 방법, 상태를 기록한다.
Main에 전달해 사용자와 결정하고 ../main/decisions.md의 결정 항목을 연결한다.
결정 전 상태는 제안 또는 검토 중이며, 수락된 변경만 해당 방에서 실행한다.
""",
}

EMPTY_DIRECTORIES = ("main/designs", "main/jobs", "literature/reference", "literature/extract", "work/data", "work/src", "discussion/requests")


def project_templates(goal=None):
    files = dict(PROJECT_TEMPLATES)
    files["main/target.md"] = files["main/target.md"].format(goal=goal if goal else "아직 기록된 목표가 없다.")
    return files
