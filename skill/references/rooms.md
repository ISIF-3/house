# Room conventions

Main is the project's coordinator, not a substitute for all room records. It can read every room and track requests between rooms. A room's task should name its inputs, outputs, version/run and completion criteria. Main owns the shared progress and decision records; avoid simultaneous writes to the same file.

All rooms apply the declared [research scope and evidence](research.md). Scientific
decision ownership is separate from a room's workflow role. Respect the agreed task
bundle, preserve scientific meanings, fail on missing/invalid required inputs, and
check actual changed files and current references before reporting completion.
Capture each external source state separately. Work checks the contract and freezes
the exact run basis; Output verifies that historical basis; Discussion records its
owner and proposed/adopted/exploratory state. Missing data is not permission to invent
defaults or select another source. Length/age alone is not an archive reason.

Main reserves one reusable agent for each of **literature, work, output and discussion**, starting a room's agent when needed. Send later work to that ID (`send_input`, or `resume_agent` when needed). Pass the pinned room guide, goal, input paths, allowed output paths and completion criteria. Do not spawn an agent for every task or let room agents spawn children. Foreground CLI and web mode supply these four custom roles and use trusted native hooks to restrict default worker/explorer roles, duplicate room agents and recursive spawning. Reuse the registered agent even after a bounded task finishes; inspect it with `/subagents` in foreground CLI or its room in the web view. In an app chat invoked only with `$house`, follow this allocation without claiming CLI hook enforcement is attached.

On Codex 0.154.0, hooks declared in a custom role file may not be projected into the child runtime. Enforcement therefore relies on the trusted parent Main's `PreToolUse` hook being inherited and checking the caller and registry, together with the native depth limit. Do not claim a role file alone enforces the policy or that unreviewed hooks are active.

When result hooks are missing, the registry can recover an ID or a recognized failure from the pending call's matching native transcript result. Unknown results stay reserved for inspection; elapsed time alone never permits a replacement agent. This bounded transcript adapter is version-sensitive too.

Room agents write under `<room>/sessions/<main-chat-id>/agents/<agent-id>/`. They share the parent's native session ID but have their own transcripts, which must not change the parent's counters. `house sessions` lists recorded sessions; inspect child activity with `/subagents` in foreground CLI, the web room view and the room agent's `SESSION.md`.

## Literature

Read `literature/GUIDE.md`. Keep a stable paper ID in `reference.md`; store accessible originals and metadata in `reference/<id>/`. Record extracted questions, methods, findings, limitations and original page/section/table locations in `extract/<id>/extract.md`. Distinguish full-text verification from abstract-only information and separate author claims from our interpretation. Preserve search date, service, query and inclusion criteria. Use a pinned literature skill if present; if none is bound, use available search tools without claiming that a literature package was installed.

## Work

Read the design and its actual agreement record. A model version pairs `model_v1.ipynb` with `model_v1.md`, recording purpose, design reference, implementation changes, environment and execution. Use `src/` for reusable code when useful. Other research methods may use scripts, protocols or analysis records instead of a notebook.

Do not scaffold a trained model or output numbers merely to fill the structure. A substantive method change receives a new version; repeated execution of the same implementation receives a new run ID. Record the exact executed code, data and settings.

## Output

Save results under `output/<model-version>/<run-id>/` with `figure/`, `output/`, `results.md` and a run record. Keep only summaries, small figures and tables there; write checkpoints, per-step arrays and bulk logs to `../house-data/<model-version>/<run-id>/` or the compute server and record their location, size and hash in the run record. Preserve completed runs. Work may write the original outputs and conditions; a designated validation task checks them and updates the result summary. Main assigns writing responsibility rather than allowing concurrent edits.

`summary.md` uses consistent sections: goal/question, executed scope, observations, baseline/version comparison, goal attainment, validation/uncertainty/limits and reproducibility references. Metrics themselves depend on the research design.

## Discussion

Use `discussion/model_v1.md` to link the goal, design, papers/extracts, implementation and specific runs. Separate supported observations, interpretation, alternative explanations and proposed changes. Record a reason and expected effect for each proposal. Literature and Work can receive follow-up requests; Main tracks them in progress/job records. Main records adoption or deferral and links accepted changes to the next design. Council agreement is not proof of a fact or a substitute for user agreement on a major model-design change.

## Browser operation

Start with `house web --project <project-directory>`. It initializes missing House records and uses the existing Codex CLI login through an owned `codex app-server --listen stdio://` process. The default server listens only on `127.0.0.1:8765`; `--port 0` chooses a free port and `--no-browser` prints the address without opening it. `--dry-run --json` still initializes the project but starts neither server nor Codex. Preserve the project's pinned release, including 0.3.0 projects.

The URL's fragment token is kept in that tab's `sessionStorage` and memory, then removed from the address. API calls send a Bearer header rather than a query token or cookie. Refreshing the browser reads state and never starts a model turn. Requests always go through Main, including requests addressed to another room. Each instruction is at most 8,000 characters and at most 20 instructions may wait. New instructions wait for the existing Main turn rather than interrupting it.

The central column shows the instruction composer, room view, selected room's result conversation, then approval requests. The sidebar keeps task and session observations without rendering the mission text or Main ID. Its model, reasoning and speed selectors use native `model/list` and distinguish observed Main settings from choices for newly submitted instructions. Only advertised reasoning levels and Fast tiers are selectable; automatic uses same-model observed settings or the model defaults. Model changes return incompatible effort/Fast choices to automatic. Each queued instruction preserves its resolved model, effort and service tier. Explicit standard clears Fast; selection alone starts no turn and changes neither running/previously queued instructions nor existing child settings. Failed saves restore all three selections and preserve the draft. If the catalogue cannot be read, retain work with observed current settings. Show the usage note and [official Fast guidance](https://learn.chatgpt.com/docs/agent-configuration/speed). Restart an existing web server to load backend changes; use its recorded Main ID with `--resume` to continue.

Before creating or resuming the Main thread, House calls native `hooks/list`. If trust is pending, show the exact hook commands, current hashes and provenance in the browser and accept no research instructions. A user's explicit approval restarts only House's owned app-server with invocation-scoped trusted hashes, then checks native trust again before opening the thread. This uses no global configuration writes, `batchWrite` or trust-bypass flag. A required hook disabled by the user blocks startup; do not re-enable it. Native command/file approvals, turn-scoped permission approvals and clarification questions require actual user responses; unknown methods remain unsupported.

Accepted instructions and recent outcomes persist in `<house>/main/sessions/<Main-native-id>/web-queue.json`. A missing acknowledgement may leave an instruction as `delivery_unknown`; do not automatically resend it or infer failure. Compare native activity and research files before choosing a follow-up. A successful turn or queued acknowledgement is not evidence of completed research. Missing observations remain unknown, and stale records do not imply an agent is still working.

Closing the browser does not stop work. CLI Ctrl-C closes the loopback server and owned Codex connection without issuing external computation termination commands. To reopen, use `house web --project <project-directory> --resume <Main-native-id>` for a session recorded in that House. Foreground CLI and web are alternative operators; simultaneous control of one chat is not provided.

The app-server integration is experimental and version-sensitive. Verification on Codex 0.154.0 covered native initialization, hook trust, session creation, a short actual model acknowledgement and unchanged global configuration hashes. Chrome testing used a test HTTP API for authentication refresh, room state, safe file text, retry drafts, native responses, keyboard and mobile layout. Large-scale and long-running operation are unverified. See the official [App Server](https://learn.chatgpt.com/docs/app-server) and [Hooks](https://learn.chatgpt.com/docs/hooks) documentation.

## Continue after interruption

Read Main's handoff (the current-state snapshot) and unresolved work, then compare referenced files with what actually exists. Use `work.md` and git history for older context rather than expecting it in the handoff. Launch records show Codex process activity; they are not evidence that a research task succeeded. Do not automatically repeat an experiment that may still be running. Report the observed state and resume the authorized unfinished work.

For a CLI-integrated chat, `SessionStart` creates `<house>/<room>/sessions/<native-chat-id>/` with `SESSION.md`, `handoff.md`, `session.json`, `events.jsonl` and `work/`. Use the actual ID supplied by the hook; do not invent one or select another chat merely because it is newest. Resume and compaction preserve that ID's directory and observed counters. A new chat receives the latest saved snapshot path from the same room and reads it alongside `main/handoff.md`. These directories hold scratch work and progress in the shared research tree, not isolated git worktrees or computation checkpoints.

Rewrite the session `handoff.md` under the four headings **목표, 확정 결정, 실행 중인 작업, 다음 행동**, with actual content in every section and at most 8,000 characters overall. Validate and retain it in `snapshots/` using `house session checkpoint <native-chat-id> --room <room> --project <project-directory>`. Main also updates `main/handoff.md`; other rooms return their handoff and output paths to Main. Add `--boundary` only at an actual work boundary. At 10 or more completed automatic compactions, that saved boundary checkpoint recommends a fresh chat once. `Stop` alone is not a boundary, and no counter automatically ends a chat, interrupts a computation or creates a new chat.

The 70% context notice uses the latest observed `token_count.info.last_token_usage.total_tokens / model_context_window`, not cumulative billed usage. House hooks inspect at most 2MiB of the unstable transcript format; missing data is `unknown`, and observations may lag the current context. A short native 0.154.0 session has been verified, but completeness across long sessions and repeated compactions remains unverified. Completed `PostCompact` events with `trigger=auto` count toward 10; manual compactions do not. When attached to an existing chat, counts cover only events observed since attachment. If a repeated hook delivery cannot be identified, it may count twice. The 70% and 10-event thresholds are House operating choices, not established model-quality limits.

`house sessions [--room ROOM] [--watch|--json]` reads saved metadata without modifying the session. Another terminal may watch it and Ctrl-C stops only that view. `house session hook --room ROOM` receives native hook JSON from the House launcher. Automatic integration applies to House CLI- and web-launched sessions, follows [Codex hook trust review](https://learn.chatgpt.com/docs/hooks), and does not modify global configuration. An app chat using `$house` alone does not acquire these hooks.
