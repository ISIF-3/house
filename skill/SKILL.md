---
name: house
description: Use when the user starts or resumes a House research project, enters one of its five rooms, or asks House to find and update room-specific GitHub skills. Supports the house, house update, and room-selection workflows.
---

# House

House keeps reusable methods in a shared installation and research records in a project's `house/` directory. The five rooms are **main, literature, work, output, discussion**. Use the bundled Python engine for initialization and version changes; use the current conversation to conduct the research.

## Choose the operation

- `$house`, “house 시작”, or user text `/house`: initialize or resume the selected project, then begin in Main.
- `$house update`, “house update”, or user text `/house update`: follow [GitHub updates](references/update.md).
- A named room: initialize if needed, load that room's context, then work there.
- CLI startup with browser operation: use `house web --project <project-directory>` and [browser operation](references/rooms.md#browser-operation). Calling `$house` alone does not start that server or turn this chat into its Main.

`$house` is the explicit Codex skill invocation. `/house` above describes user intent; installing this skill does not register a built-in Codex slash command. The standalone terminal command is `house`.

## Start or resume in this conversation

Resolve `scripts/house.py` relative to this SKILL.md. It uses Python 3.9+ and the standard library. Use the current project directory, or the path explicitly selected by the user. If no actual project context can be determined, ask for that path before writing. Do not substitute another existing research project.

1. Run the equivalent of:

   ```text
   python3 <skill-directory>/scripts/house.py init --project <project-directory> --json
   ```

   If the user already supplied a research goal, pass it as the single `--goal` argument. Quote paths and user text correctly; do not treat goal text as shell code. The engine creates `<project>/house/` and finds an existing House when invoked from one of its rooms. A conflict with an unmanaged folder is a reason to explain the exact collision, not overwrite it.

2. Read current-chat context:

   ```text
   python3 <skill-directory>/scripts/house.py context main --project <project-directory> --json
   ```

   Substitute the selected room for `main`. Read `main/handoff.md` first, then any same-room saved snapshot path supplied by the CLI start hook, the returned room guide and the listed **pinned** skill files. Open other Main records only when the task needs them, and `main/archive/` or detailed output documents only when historical evidence is required. Keep reports short; create notebooks, HTML, diagrams or browser checks only when asked. Do not replace the project's locked release with the newest shared default. The current engine supplies CLI session operating policy; the project's pinned room guidance remains in force. If a pinned file is missing or changed, repair that specific issue before using it.

   For an existing project, also run `python3 <skill-directory>/scripts/house.py check --project <project-directory>`. If it reports an older release, explain what `migrate` changes (House-owned files and status lines only, with backups in `main/jobs/`) and run it only after the user agrees.

3. Begin the actual conversation and save the supplied research facts. In Main, connect:

   | Research section | Canonical record |
   |---|---|
   | Why the research is needed | `main/why.md` |
   | Final goal and success criteria | `main/target.md` |
   | Prior work and evidence | `literature/reference.md`, `reference/`, `extract/` |
   | Methods and detailed design | `main/design.md`, `main/designs/`, `work/` |
   | Results | `output/summary.md`, version/run folders |

   Use `main/work.md` as an append-only dated log, `decisions.md` for accepted choices and `handoff.md` as the current-state snapshot. Preserve user-written content and add or revise only what the active request justifies. Empty templates are not completed research.

   Two kinds of text are House-owned and must be rewritten rather than preserved: a line marked `<!-- house:status -->` (replace it with the document's actual state and date when you write content; never add sentences explaining old template text) and `main/handoff.md` (rewrite it completely at the end of a session, under 8,000 characters, covering the checked time, running work, next actions, open questions and files to read first). If `house/` is not tracked by git, move the previous handoff to `main/handoffs/<YYYYMMDD-HHMM>.md` before rewriting.

4. Ask for the next missing research decision at an appropriate level, rather than requesting all fields at once. For actual AI/statistical model development, explain the target, data, baseline, candidate methods, validation and resources, then establish design agreement before implementation. An approved House architecture is not approval of a particular research model.

5. Continue the authorized work using [room conventions](references/rooms.md). Main uses at most one reusable subagent for each of literature, work, output and discussion. Pass the room guide, goal, input paths, allowed output paths and completion criteria; send follow-ups to the existing ID and resume it when needed. Do not create per-task workers or recursive subagents. Keep durable progress and handoff in the project.

**Do not run interactive `house`, `house start`, or `house room` inside this chat.** Those commands launch another Codex terminal session. The `init` and `context` commands let this conversation remain the active Main or room.

## Native CLI sessions

The `house start` / `house room` and `house web` launchers inject invocation-scoped native hooks and room roles; they do not change global Codex config or bypass native hook trust review. Calling `$house` in an existing app chat does not attach those automatic hooks. Do not claim automatic session tracking without that integration. The foreground path keeps the room's `--cd`; `/subagents` shows child work. Web mode shows the same five fixed rooms in the browser. A separate terminal can run read-only `house sessions --watch` (or `--room ROOM`, `--json`). These commands do not open five terminals.

Follow the start hook's actual native chat ID and paths under `<house>/<room>/sessions/<id>/`. Resume and compaction keep that ID's workspace and observed counts. Write the session's `handoff.md` with actual content under `## 목표`, `## 확정 결정`, `## 실행 중인 작업`, `## 다음 행동`, at most 8,000 characters in total. Main also maintains `main/handoff.md`. Save a validated snapshot with:

```text
python3 <skill-directory>/scripts/house.py session checkpoint <native-chat-id> --room <room> --project <project-directory>
```

Add `--boundary` only after a real work segment is finished, not at every response. At 70% recent context observation, prepare the handoff. After at least 10 completed automatic compactions, a saved boundary checkpoint recommends a new chat once. Never end the chat, stop a running computation or create a replacement chat merely because a counter reached 10. See [session details and observation limits](references/rooms.md#continue-after-interruption).

## Browser mode

`house web --project <project-directory>` initializes missing House records and runs a loopback server at `127.0.0.1:8765` with a Codex app-server child using the existing CLI login. `--port 0` chooses a free port; `--no-browser` prints the URL without opening it. `--resume <Main-native-id>` must identify a Main session recorded in this House. `--dry-run --json` initializes records and shows the plan without starting the server or Codex. Preserve project pins.

The sidebar's model, reasoning effort and automatic/standard/Fast choices apply only to newly submitted Main requests. Use native advertised capabilities; incompatible choices return to automatic when the model changes. Each queued request keeps its captured settings, and existing child agents retain theirs. Fast can increase usage; show the linked guidance. Do not change global settings or interrupt a turn to apply a picker choice.

Wait for hook trust review before accepting work. The browser shows exact commands and hashes; only the user's approval permits trust for this invocation. Do not alter global configuration, bypass trust, or enable a required hook the user disabled. All room requests enter Main's durable queue (8,000 characters, at most 20 waiting). Preserve running work; `delivery_unknown` means inspect actual progress before deciding whether to send a follow-up, never automatically retry.

Closing the browser leaves work running. CLI Ctrl-C closes the server and its owned Codex connection; it does not send external computation termination commands. Resume by the recorded Main ID. Do not operate the same chat through foreground CLI and web simultaneously. Status may be unknown, and native turn completion is not research completion. See [browser operation and verified limits](references/rooms.md#browser-operation).

## Keep the records light

The non-interactive record commands run inside this chat with the same `scripts/house.py`:

- `check`: read-only report of size, raw-data candidates, git state, stale status lines, template-explanation lines and handoff length.
- `offload <path>` / `offload --suggested`: move a raw-data folder or file from `house/` to the project's `house-data/`, leaving a relative link and an entry in `house-data.json`. Show the `--dry-run` list and get the user's confirmation first. Never offload the output folder of a computation that may still be running; paths changed in the last 30 minutes are refused unless the user confirms with `--allow-recent`.
- `track`: start versioning `house/` text records (creates a repository in `house/`, or commits `house/` paths into an enclosing project repository). Ask before the first `track` in a user's existing repository. It refuses raw-data-sized additions.
- `save -m '<summary>'`: commit only `house/` changes at the end of a work session when the project is tracked.
- `migrate`: move a project pinned to an older release to the current structure, as described above.

Results keep only summaries, small figures and tables, and the raw location, size and hash in `run.json`. Write new raw outputs outside `house/` from the start.

## Report the actual state

State where the project lives, what was written, what was actually executed and the next unresolved decision. Update the handoff and, if tracked, `save` before ending. Keep facts, interpretations, proposals and accepted decisions distinct. Literature needs traceable sources; experiment results need an executed version and run ID. Never infer successful research from a subprocess exit or an empty output folder.

The foreground application opens one selected Codex room; web mode owns a local server and Codex app-server connection. Main coordinates its four reusable room agents. Session workspaces share the research tree; they are neither isolated git worktrees nor computation checkpoints. An always-running multi-room service and an enabled weekly scheduler are not implied.
