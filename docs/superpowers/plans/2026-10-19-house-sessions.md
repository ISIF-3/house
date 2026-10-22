# House CLI sessions implementation plan

**Goal:** Apply the agreed 70% context preparation, review after every automatic compaction, and renewal advice after ten automatic compactions at a saved work boundary. Give each native Codex chat its own workspace, and limit delegation to one reusable agent per research room.

**Architecture:** Keep the existing foreground Codex CLI and shared research folders. Pass invocation-scoped lifecycle hooks and room-agent definitions to Codex. Store bounded session metadata, handoff snapshots and scratch work under `<house>/<room>/sessions/<chat-id>/`. Existing pinned releases and user records remain untouched.

**Tech stack:** Python 3.9 standard library, unittest, documented Codex lifecycle hooks and TOML command-line configuration. No server, browser dashboard, tmux, model change or global Codex configuration edits.

The user approved these policies and selected one CLI with a session-specific folder, rather than launching all rooms in separate terminals. Context percentages are best-effort measurements from bounded transcript token-count records: this adapter is explicitly version-sensitive, and missing data remains unknown. Native compaction limits are not changed.

## Tasks

- [x] Run the existing unittest suite as a baseline.
- [x] Add failing session tests for native chat identity, reentry, fresh workspaces, resume/compact preservation, completed automatic-compaction deduplication, 70% warnings, bounded telemetry, unknown data and saved work-boundary recommendations at ten compactions.
- [x] Implement `house_engine/sessions.py`: filesystem state, hook responses, bounded transcript usage adapter, validated handoff checkpoints and read-only session listing.
- [x] Add failing delegation tests: Main permits only literature/work/output/discussion, reserves one agent per role, reuses returned IDs, rejects recursive/default/task-specific delegation, and gives each role its own workspace.
- [x] Implement a small room-agent policy module and invocation-scoped custom role configuration. Preserve existing Codex hooks and model/permission settings.
- [x] Integrate `house session hook`, `house session checkpoint`, and `house sessions --watch` into the CLI. Keep interactive stdout/stderr attached so actual Codex activity stays visible; never equate a CLI exit with completed research.
- [x] Add CLI integration tests using a local fake Codex process, including paths with spaces, scoped hook arguments, room-role definitions, and no changes to global configuration.
- [x] Bump the engine release to 0.3.0 for new template instructions, preserving old immutable releases. Update SKILL.md, README and CHANGELOG with usage and observation limits.
- [x] Run `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=skill/scripts python3 -m unittest discover -s tests -v`, a fake-CLI smoke test, and `git diff --check`.
- [x] Review the complete change independently for policy coverage, preservation of research files, hook failure handling and session/agent isolation. Fix findings and repeat affected verification.

## Acceptance

Each real chat receives its own session folder; resume and compact reuse its counters. Only successful `PostCompact(trigger=auto)` events count; child events do not increment the parent. An ordinary turn end does not constitute a work boundary. Renewal advice requires a bounded, nonempty handoff covering goal, accepted decisions, running work and next action. Recommendations never cancel calculations, close chats or open new ones automatically. A new chat reads the previous saved handoff and verifies actual running work before continuing.

Main can use at most one agent for each of the four other rooms; it must send follow-up work to that agent ID. Worker agents cannot delegate further. Session folders are scratch and progress records inside the same research tree, not isolated Git worktrees or separate compute processes. The CLI shows work in its normal terminal, and a second terminal can inspect session state without controlling the calculation.

Official integration references: [Hooks](https://learn.chatgpt.com/docs/hooks), [CLI configuration overrides](https://learn.chatgpt.com/docs/cli/reference), [Subagents](https://learn.chatgpt.com/docs/subagents). The installed CLI is 0.154.0; configuration parsing and the hooks feature are checked locally without invoking a model. Real transcript format compatibility and live provider behavior must be reported separately from synthetic hook tests.


## Verification completed

- Baseline: 79 tests passed before implementation.
- Final suite: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=skill/scripts python3 -m unittest discover -s tests -q` — 126 tests passed (18.767s), including a foreground fake Codex process executing the generated hooks.
- `git diff --check` passed. The installed source-linked command reports `house 0.3.0`.
- Installed Codex CLI 0.154.0 accepted the Main hook/role overrides and all four worker configuration files with a temporary `CODEX_HOME`, without invoking a model.
- Independent review found and verified fixes for malformed/oversized PreToolUse inputs bypassing the guard, and pending room reservations after native spawn failures without PostToolUse. Matching-call result recovery preserves unknown or still-running reservations; no timeout creates replacement agents.
- Review confirmed native child events share the parent's session ID but use a distinct transcript and SubagentStart. Child records stay separate from foreground session counters. The installed version's child switch command is `/subagents`.
- Actual model execution and live native-hook/transcript telemetry remain unverified. Bounded transcript adapters are explicitly best effort; unknown context and unrecognized spawn results are not inferred.
