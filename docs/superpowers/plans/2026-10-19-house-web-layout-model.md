# House web layout and model selection implementation plan

**Goal:** Apply the user's approved layout: remove the left mission and Main ID, put the composer above the room view, show the selected room's conversation directly below the room view, and place approvals after it. Add Main model selection to session observations.

**Architecture:** Keep the existing local server, queue and five fixed rooms. Obtain selectable models from native `model/list`; never hardcode a model catalogue or modify global settings. Freeze the selected model when a new instruction is queued, then pass it to `turn/start` when Main can accept that instruction. Changing the selection sends no turn and never interrupts a running one. Existing child threads keep their own settings.

**Files and ownership:** Work owns `skill/scripts/house_engine/web.py`, `tests/test_web.py` and model-route HTTP tests. Output owns `skill/web/index.html`, `style.css`, `app.js`. Main owns integration checks and documentation. Reuse the existing room agents; no new or nested agents.

## API contract

Add `models: [{model, display_name}]`, `current_model: string|null`, `selected_model: string|null`, `model_error: string|null` to `/api/state`. `current_model` is the last observed Main setting, not a guarantee about each child's execution. `selected_model: null` means retain the observed Main setting. `POST /api/model` accepts `{model: string|null}` and returns `{selected_model}`; only names in the native catalogue are accepted. It uses the same local authentication and JSON validation as other mutations. Refuse selection before readiness or after disconnect. A catalogue failure leaves existing default work available and displays an explanatory model error.

Queued instructions store `model = selected_model or current_model`; previously stored records without a model remain compatible. Non-null models are passed explicitly to the next `turn/start`, preserving the choice of each queued request. Native thread start/resume and Main settings notifications provide the observed current setting; child settings never overwrite it.

## Steps

- [x] Work: write failing tests for native catalogue pagination, observed current model, valid/invalid selection, no immediate native model turn, preservation of already queued choices and non-disruptive application after the current turn. Verify failure, implement, and run controller/HTTP tests.
- [x] Output: remove mission/thread DOM and JavaScript references; move composer and messages using the approved DOM order, preserving selected-room routing, draft and approvals. Add the native catalogue selector, observed current setting and explicit next-request scope. Disable send while a selection is being saved; handle selection errors without losing the draft. Also disable model changes while an instruction is being sent.
- [x] Main: review changes against the four requested changes and queue/permission boundaries. Work ran the full unittest suite; Main inspected integration changes and checked JavaScript syntax.
- [x] Main: validate desktop/mobile layout and browser model selection with a mock native API, then inspect the installed native model list. No test instruction was sent to a live research session.
- [x] Main: update usage and validation documentation, record actual results and explain that an existing server needs restarting for backend changes. Do not commit or push unless requested.

## Validation commands

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=skill/scripts python3 -m unittest discover -s tests -q
node --check skill/web/app.js
git diff --check
```

The browser check must cover central DOM order, missing mission/ID, room-dependent messages, preserved approvals, native model labels, rejected model selection, disabled submission during a save, and a 390px viewport without horizontal overflow.

## Observed results

186 unittests passed, including 42 controller/HTTP tests. Chrome checks passed for the approved layout, model selection, both model/instruction in-flight guards, safe file text, approvals, questions, refresh authentication and mobile layout. The root separately verified native Codex 0.163.0 catalogue loading and an actual Main response after switching models; global config SHA-256 was unchanged. Existing research sessions were not controlled by these checks.
