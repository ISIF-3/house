# House reasoning effort and Fast mode implementation plan

**Goal:** Extend the existing Main model picker with user-requested reasoning effort and Fast mode choices. Keep running and previously queued requests unchanged.

**Design:** The session sidebar shows the observed Main settings and next-request choices. The model catalogue supplies reasoning levels and service tiers; no model-specific support table is hardcoded. Model, effort and speed are saved atomically through the existing authenticated `/api/model` route. A new instruction captures all three settings in its durable queue record and uses them when its Main turn begins. Existing room agents retain their own settings. No global configuration writes or runtime interruption.

**Ownership:** Reuse Work for `web.py` and controller/HTTP tests; Output for the three web assets; Main for browser/native integration and documentation; existing Literature/Discussion reviewer for final review. No new agents or commits.

## API and compatibility

- Catalogue rows add `supported_efforts: [{effort,description}]`, `default_effort`, `service_tiers: [{id,name,description}]`, `default_service_tier`. Missing capability metadata means unknown support, not invented choices.
- State adds `current_effort`, `selected_effort`, `current_service_tier`, `selected_fast`. The last field is null/true/false for automatic/Fast/standard. Current settings come from native thread start/resume and Main-only settings notifications.
- `/api/model` accepts `{model, effort?, fast?}` and returns the three selected fields. Unsupported effort/tier or malformed values fail before changing any selection. Old model-only requests remain accepted.
- Automatic effort keeps a compatible observed setting for the same model, otherwise uses the model's advertised default. If capability metadata is unavailable for that same model, preserve the observed setting rather than inventing a replacement. Automatic speed preserves a compatible same-model tier, otherwise uses the advertised default or standard. Explicit Fast uses the catalogue's Fast tier ID; the installed catalogue currently calls it `priority`.
- New queue records freeze `model`, `effort`, `service_tier`. Native `turn/start.effort` receives a non-null frozen effort. A present `service_tier` key is forwarded even when null so explicit standard can clear Fast; verify native null semantics before claiming it works. Older queue records without these keys retain their original behavior.
- Changing model keeps compatible explicit choices and falls back to automatic for incompatible choices. Saving or sending disables all three selectors and prevents ambiguous request ordering. Failed saves restore the complete previous selection and preserve the draft.

## Execution

- [x] Work: reproduce missing capability fields, invalid combinations, atomically saved choices, Main-only observations, queue preservation and legacy compatibility with failing tests. Implement and run focused tests.
- [x] Output: add reasoning and automatic/standard/Fast selectors with native options, current observations, unavailable/error messages and the existing in-flight guards.
- [x] Main: verify native Fast activation and explicit clearing, native reasoning changes and real short replies in an isolated temporary session; confirm global configuration hashes unchanged.
- [x] Main: verify the UI in Chrome with a mock API, including model capability changes, all-selector guards, rejected saves, restored drafts and mobile layout. Run the full unittest suite once after integration, plus JavaScript syntax and diff checks.
- [x] Reviewer: check requirements, atomicity, supported values and persistence. Main: update documentation with actual verification results and the need to restart an existing server.

Fast consumes more usage than standard; show a short note and a link to the [official speed guidance](https://learn.chatgpt.com/docs/agent-configuration/speed). No Ultrafast picker, pricing calculator or new approval flow is included.

## Observed results · 2026-10-19

207 unittests passed in 25.573 seconds, including 63 controller/HTTP tests. Chrome checks passed for supported effort/Fast options, atomic settings, incompatible-model fallback, failed-save rollback, all-selector in-flight guards, draft preservation, approvals, file preview, authentication refresh and mobile layout. JavaScript syntax and diff checks passed.

In an isolated House project, native Codex 0.163.0 completed real replies with `gpt-6-luna` at `low`/Fast (`priority`) and then `medium`/standard (`default`). The observer matched both settings. Explicit native `serviceTier: null` cleared Fast, and global config SHA-256 was unchanged. No existing research session was operated. Performance comparisons, all model/effort combinations and long-running operation were not tested.
