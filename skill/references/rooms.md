# Room conventions

Main is the project's coordinator, not a substitute for all room records. It can read every room and track requests between rooms. A room's task should name its inputs, outputs, version/run and completion criteria. Main owns the shared progress and decision records; avoid simultaneous writes to the same file.

Main reserves one reusable agent for each of **literature, work, output and discussion**, starting a room's agent when needed. Send later work to that ID (`send_input`, or `resume_agent` when needed). Pass the pinned room guide, goal, input paths, allowed output paths and completion criteria. Do not spawn an agent for every task or let room agents spawn children. The CLI supplies these four custom roles and rejects default worker/explorer roles, duplicate room agents and recursive spawning through native hooks. Reuse the registered agent even after a bounded task finishes; use `/subagents` to inspect its work. In an app chat invoked only with `$house`, follow this allocation in the conversation without claiming CLI hook enforcement is attached.

When result hooks are missing, the registry can recover an ID or a recognized failure from the pending call's matching native transcript result. Unknown results stay reserved for inspection; elapsed time alone never permits a replacement agent. This bounded transcript adapter is version-sensitive too.

Room agents write under `<room>/sessions/<main-chat-id>/agents/<agent-id>/`. They share the parent's native session ID but have their own transcripts, which must not change the parent's counters. `house sessions` lists foreground CLI sessions; inspect child activity with `/subagents` and the room agent's `SESSION.md`.

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

## Continue after interruption

Read Main's handoff (the current-state snapshot) and unresolved work, then compare referenced files with what actually exists. Use `work.md` and git history for older context rather than expecting it in the handoff. Launch records show Codex process activity; they are not evidence that a research task succeeded. Do not automatically repeat an experiment that may still be running. Report the observed state and resume the authorized unfinished work.

For a CLI-integrated chat, `SessionStart` creates `<house>/<room>/sessions/<native-chat-id>/` with `SESSION.md`, `handoff.md`, `session.json`, `events.jsonl` and `work/`. Use the actual ID supplied by the hook; do not invent one or select another chat merely because it is newest. Resume and compaction preserve that ID's directory and observed counters. A new chat receives the latest saved snapshot path from the same room and reads it alongside `main/handoff.md`. These directories hold scratch work and progress in the shared research tree, not isolated git worktrees or computation checkpoints.

Rewrite the session `handoff.md` under the four headings **목표, 확정 결정, 실행 중인 작업, 다음 행동**, with actual content in every section and at most 8,000 characters overall. Validate and retain it in `snapshots/` using `house session checkpoint <native-chat-id> --room <room> --project <project-directory>`. Main also updates `main/handoff.md`; other rooms return their handoff and output paths to Main. Add `--boundary` only at an actual work boundary. At 10 or more completed automatic compactions, that saved boundary checkpoint recommends a fresh chat once. `Stop` alone is not a boundary, and no counter automatically ends a chat, interrupts a computation or creates a new chat.

The 70% context notice uses the latest observed `token_count.info.last_token_usage.total_tokens / model_context_window`, not cumulative billed usage. CLI hooks inspect at most 2MiB of the unstable transcript format; missing data is `unknown`, and observations may lag the current context. The installed CLI 0.154.0's actual session format has not been empirically verified. Completed `PostCompact` events with `trigger=auto` count toward 10; manual compactions do not. When attached to an existing chat, counts cover only events observed since attachment. If a repeated hook delivery cannot be identified, it may count twice. The 70% and 10-event thresholds are House operating choices, not established model-quality limits.

`house sessions [--room ROOM] [--watch|--json]` reads saved metadata without modifying the session. The main CLI remains interactive; another terminal may watch it and Ctrl-C stops only the view. `house session hook --room ROOM` receives native hook JSON from the CLI launcher. Automatic integration applies to CLI-launched sessions, follows [Codex hook trust review](https://learn.chatgpt.com/docs/hooks), and does not modify global configuration. An app chat using `$house` alone does not acquire these hooks.
