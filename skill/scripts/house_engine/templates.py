"""Versioned Korean research guidance; templates contain no invented evidence."""

ROOMS = ("main", "literature", "work", "output", "discussion")

# A line carrying this marker is House-owned document state, not preserved user text.
STATUS_MARK = "<!-- house:status -->"
# Written by `house migrate` where an older initial line remained in a written record.
MIGRATED_STATUS = "상태: 기록 있음 — 다음 작업에서 현재 상태로 갱신 필요 " + STATUS_MARK
HANDOFF_LIMIT = 8000
# Records read on demand stay short; older content moves to main/archive/<date>/.
RECORD_LIMIT = 20000

COMMON = """\n## 공통 원칙
- 상위 HOUSE.md, AGENTS.md와 hive.lock.json을 먼저 읽는다. 실행 중인 프로젝트는 고정된 릴리스를 따른다.
- 외부 스킬이 설치되었다고 가정하지 않는다. bindings가 비어 있으면 기본 지침만 사용한다.
- bindings가 있으면 기록된 정확한 불변 경로와 리비전을 확인한다. 공유 기본값 변경을 현재 프로젝트에 자동 적용하지 않는다.
- 연구 이유, 목표, 성공 기준, 데이터와 설계에 대한 사용자 합의를 먼저 기록한다. 합의 전 AI·통계 모델 구현이나 학습을 시작하지 않는다.
- 기존 파일을 읽고 보존한다. 추정, 확인된 사실, 미실행 계획과 실제 실행 결과를 구분한다.
- `<!-- house:status -->`가 붙은 줄은 House가 관리하는 문서 상태 줄이다. 문서에 실제 내용을 쓰면 이 줄을
  `상태: <현재 상태> — <날짜> <!-- house:status -->`로 바꿔 쓴다. 보존 대상이 아니며 템플릿 문구를 해명하는 문장을 덧붙이지 않는다.
- main/work.md는 날짜별 누적 로그다. 기존 항목은 고치지 않고 아래에 추가한다.
- main/handoff.md는 현재 상태의 스냅샷이다. 작업을 마칠 때 전체를 다시 쓰고 8,000자 이내로 유지한다.
  지난 내용은 work.md와 git 이력이 보존한다. house/가 git으로 추적되지 않으면 다시 쓰기 전에 이전 내용을 main/handoffs/<YYYYMMDD-HHMM>.md로 옮긴다.
- 원본·대용량 데이터(체크포인트, 단계별 배열, 대량 로그)는 house/ 밖에 둔다. house/ 안에 생긴 원본 폴더는 `house offload <경로>`로
  ../house-data/에 옮기고 결과 문서에는 요약·작은 그림·원본 위치를 남긴다. 실행 중인 계산의 출력 폴더는 옮기지 않는다.
- 작업을 마치면 handoff를 갱신하고, house/가 git으로 추적되면 `house save -m '<요약>'`으로 기록을 커밋한다.
- 세션 시작 때는 main/handoff.md만 먼저 읽는다. 다른 main 문서는 필요할 때만, main/archive/와 output 상세 문서는 과거 근거가 꼭 필요할 때만 연다.
- 보고는 짧게 한다. 노트북·HTML·흐름도·브라우저 검사 같은 부가 산출물은 요청이 있을 때만 만든다.
- main의 work·decisions·target·design이 20,000자를 넘으면 지난 내용을 main/archive/<날짜>/로 옮기고 현재 요약만 남긴다.
"""

ROOM_INSTRUCTIONS = {
    "main": """# Main — 연구의 이유와 설계

먼저 handoff.md를 읽고, 필요하면 why.md와 target.md를 열어 왜 이 연구가 필요한지, 어떤 질문에 답할지 사용자와 대화한다.
목표, 평가 기준, 데이터 사용 범위, 비교 기준과 제약을 정리한다. reference.md는 근거 탐색의 길잡이다.
design.md와 method.md에 연구 설계와 방법을 제안하고, decisions.md에 사용자의 실제 동의와 변경 이유를 기록한다.
질문을 한 번에 과도하게 묶지 않는다. 미확정 항목은 미확정으로 남긴다.
work.md는 누적 진행 로그이고 실제 코드·모델 작업은 Work에서 한다. results.md는 실행 근거가 생긴 뒤 갱신한다.
handoff.md는 다음 세션이 처음 읽는 현재 상태다. 확인 시각, 실행 중인 작업, 다음 행동, 미해결 질문과 먼저 읽을 파일만 남긴다.
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
data/에는 사용자가 허용한 작은 데이터와 출처를, src/에는 실제 작성한 코드를 둔다. 큰 원본 데이터는 house/ 밖에 두고 경로와 해시를 기록한다.
모델 작업이 합의되면 model_v1.ipynb와 model_v1.md를 함께 만든다. 변경은 model_v2처럼 새 버전으로 기록한다.
노트북은 실제 셀·실행 상태를 보존하고, MD는 목적·가정·입출력·평가·변경 이유를 설명한다.
실행 결과는 ../output/<version>/<run>/에 저장한다. 버전·실행 ID, 명령, 환경, 데이터 식별자,
난수 시드, 로그와 성공/실패를 연결한다. 체크포인트·단계별 배열 같은 원본 출력은 ../../house-data/나 계산 서버에 쓴다.
초기화만으로 노트북이나 실행 결과를 만들지 않는다.
""",
    "output": """# Output — 확인된 실행 결과와 해석

결과는 output/<version>/<run>/ 규칙으로 구분한다. 각 실행의 모델/분석 버전과 재현 근거를 연결한다.
run 폴더에는 results.md, run.json, figure/(작은 그림), output/(작은 표·요약 수치)만 둔다.
원본 출력은 house/ 밖(../house-data/ 또는 계산 서버)에 두고 run.json의 raw 항목에 위치·크기·해시를 기록한다.
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
각 방의 ROOM.md와 GUIDE.md(있는 경우)를 읽는다. main/handoff.md에서 현재 상태를, main/work.md에서 누적 진행을 확인한다.
hive.lock.json은 공유 홈, 프로젝트 릴리스와 스킬 bindings를 고정한다. 빈 bindings는 외부 스킬 미연결 상태다.
공유 업데이트가 이 프로젝트를 자동 마이그레이션하지 않는다. 별도 합의 전 기존 잠금을 유지한다.
초기 문서는 빈 연구 기록이며 설계 승인, 모델 실행 또는 결과를 의미하지 않는다.
`<!-- house:status -->`가 붙은 줄은 문서의 현재 상태이며 내용을 쓸 때 함께 바꾼다.
텍스트 기록은 git으로 추적하고(`house track`, `house save`), 원본 데이터는 ../house-data/에 둔다(`house offload`).
house-data.json은 house/ 밖으로 옮긴 원본의 위치·크기·목록 해시를 기록한다.
""",
    "AGENTS.md": """# House 내부 작업 지침

이 지침은 이 house/ 범위에만 적용한다. 상위 프로젝트의 사용자 지침도 따른다.
HOUSE.md, hive.lock.json, 선택한 방의 ROOM.md와 main/handoff.md만 먼저 읽는다. 나머지 main 문서는 필요할 때만, main/archive/와 output 상세 문서는 과거 근거가 꼭 필요할 때만 연다.
보고는 짧게, 노트북·HTML·흐름도·브라우저 검사는 요청이 있을 때만 만든다.
연구 이유와 목표를 사용자와 확인하고, 설계 합의를 기록한 뒤 구현·통계 분석·AI 모델 실행을 진행한다.
승인·인용·데이터·실행·결과를 만들어내지 않는다. 기존 사용자 문서와 공유 릴리스는 덮어쓰지 않는다.
예외: `<!-- house:status -->` 줄은 현재 상태로 바꿔 쓰고, main/handoff.md는 작업 종료 때 현재 상태 스냅샷으로 다시 쓴다.
근거는 출처와 파일 경로로 연결한다. Discussion의 제안은 Main의 결정을 거친다.
작업 진행은 main/work.md 아래에 날짜별로 추가한다. 모델 노트북과 설명 MD를 버전별로 함께 관리하고 결과는 output/<version>/<run>/에 둔다.
원본·대용량 출력은 house/ 밖에 두고 위치·크기·해시만 기록한다. 실행 중인 계산의 출력 폴더는 옮기지 않는다.
외부 스킬은 잠금의 bindings에 지정된 정확한 불변 경로를 확인한 뒤 필요할 때 읽는다.
""",
    ".gitignore": """# House 기록 저장소 규칙: 텍스트 기록은 추적하고 원본·대용량 데이터는 제외한다.
# 원본 데이터는 `house offload <경로>`로 house/ 밖(../house-data/)에 옮긴다.
# 상위 프로젝트의 ignore 규칙이 방 폴더 자체를 가리지 않도록 다시 포함한다.
!/main/
!/literature/
!/work/
!/output/
!/discussion/
# 운영체제·편집기·실행 캐시
.DS_Store
__pycache__/
*.py[cod]
.ipynb_checkpoints/
# 대용량 수치·체크포인트 형식 (꼭 필요한 작은 파일은 git add -f로 추가)
*.npy
*.npz
*.h5
*.hdf5
*.nc
*.pt
*.pth
*.ckpt
*.safetensors
*.pkl
*.pickle
""",
    "main/why.md": """# 연구 이유

상태: 미확정 — 사용자와 대화 후 작성 <!-- house:status -->

## 문제와 배경

## 왜 지금 이 연구가 필요한가

## 기존 지식의 한계와 기대 기여

## 근거와 출처
""",
    "main/target.md": """# 연구 목표

상태: 초안 — 사용자와 확인 필요 <!-- house:status -->

## 사용자가 제시한 목표

{goal}

## 연구 질문과 가설

## 성공 기준과 비교 기준

## 범위·제약·미확정 사항
""",
    "main/design.md": """# 연구 설계

상태: 미확정 — 사용자 합의 기록 없음 <!-- house:status -->

## 연구 질문과 목표 연결

## 데이터·대상·표본과 사용 범위

## 비교 설계·분석·모델 계획

## 평가 기준·검증·재현성

## 한계·위험·미확정 질문

## 사용자와의 합의

실제 합의가 이루어지면 내용과 날짜, decisions.md의 결정 항목을 연결한다.
""",
    "main/decisions.md": """# 설계와 변경 결정

상태: 기록된 결정 없음 — 실제 사용자 합의 후 작성 <!-- house:status -->

| 날짜 | 논의 내용 | 결정·합의 | 이유·근거 | 관련 문서 |
| --- | --- | --- | --- | --- |
""",
    "main/work.md": """# 연구 진행 기록

상태: 기록 없음 — 첫 작업 후 갱신 <!-- house:status -->

날짜별 누적 로그다. 기존 항목은 고치지 않고 아래에 새 항목을 추가한다.
현재 상태·다음 행동·미해결 질문은 handoff.md에 쓴다.

| 날짜 | 방 | 작업 | 근거·산출물 | 결과·상태 |
| --- | --- | --- | --- | --- |
""",
    "main/handoff.md": """# 다음 작업으로 이어가기

상태: 초기화 — 아직 수행한 연구 작업 없음 <!-- house:status -->

이 문서는 누적 기록이 아니라 현재 상태의 스냅샷이다. 작업을 마칠 때마다 전체를 다시 쓰고 8,000자 이내로 유지한다.
지난 진행은 work.md와 git 이력에 남는다.

## 지금 상태 (확인 시각과 근거)

## 실행 중이거나 대기 중인 작업

## 다음 작업과 필요한 입력

## 미해결 질문

## 먼저 읽을 파일
""",
    "main/reference.md": """# 연구 근거 길잡이

상태: 검증한 근거 없음 <!-- house:status -->

문헌 목록은 ../literature/reference.md, 원문은 ../literature/reference/, 추출은 ../literature/extract/에 기록한다.

## 목표·설계와 연결되는 근거
""",
    "main/method.md": """# 연구 방법

상태: 미확정 — 설계 합의 후 구체화 <!-- house:status -->

## 선택한 방법과 이유

## 데이터 준비와 분석 절차

## 검증·평가·재현 방법

## Work 문서와 실행 연결
""",
    "main/results.md": """# 연구 결과

상태: 미기록 — 실행된 연구 결과 없음 <!-- house:status -->

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

상태: 확인된 문헌 없음 — 실제 확인한 출처만 추가 <!-- house:status -->

| ID | 서지사항 | URL·DOI | 확인 상태 | 원문 경로 | 추출 경로 | 목표 관련성 |
| --- | --- | --- | --- | --- | --- | --- |
""",
    "work/GUIDE.md": """# 구현·분석 작업 안내

Main의 design.md와 decisions.md에서 설계 합의를 먼저 확인한다. data/와 src/는 실제 작업이 생기기 전까지 빈 폴더다.
합의된 모델 작업을 시작할 때 model_v1.ipynb와 model_v1.md를 함께 만든다. 분석만 필요한 연구에 모델을 억지로 만들지 않는다.
설명에는 목표, 입력/출력, 가정, 사용법, 평가 기준과 변경 이유를 기록한다.
실행마다 ../output/<version>/<run>/에 실행 설정, 로그 요약, 환경과 작은 산출물을 연결한다.
체크포인트·단계별 배열·대량 로그 같은 원본은 ../../house-data/<version>/<run>/ 또는 계산 서버에 쓰고 run.json에 위치를 남긴다.
실행하지 않은 셀·예정 결과를 완료된 결과로 기록하지 않는다.
""",
    "output/summary.md": """# 결과 요약

상태: 미기록 — 실제 실행 결과 없음 <!-- house:status -->

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

# Exact placeholder sentences of release 0.1.0 that later releases express as status lines.
LEGACY_PLACEHOLDERS = (
    "아직 기록된 결정이 없다. 실제 사용자 합의가 이루어진 뒤 작성한다.",
    "아직 확인된 문헌이 없다. 실제 확인한 출처만 추가한다.",
    "아직 검증한 근거가 없다.",
)


def project_templates(goal=None):
    files = dict(PROJECT_TEMPLATES)
    files["main/target.md"] = files["main/target.md"].format(goal=goal if goal else "아직 기록된 목표가 없다.")
    return files


def status_line(text):
    """Return the House-owned status line of a template, including 0.1.0 ``상태:`` lines."""
    lines = text.splitlines()
    for line in lines:
        if STATUS_MARK in line:
            return line
    for line in lines:
        if line.startswith("상태:"):
            return line
    return None
