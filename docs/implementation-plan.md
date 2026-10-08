# House v0.1 Implementation Plan

> For agentic workers: use superpowers:subagent-driven-development and superpowers:test-driven-development. Implement and review each bounded component; no existing research project is an implementation workspace.

**Goal:** Start or resume a five-room research workspace from a terminal or Codex skill, and discover, review, pin and apply room-specific GitHub skills without changing running projects.

**Architecture:** A Python standard-library engine is shared by the terminal entrypoint and the `house` skill. Research files live in `<project>/house/`; common skills, releases and update records live at `HOUSE_HOME` (default on macOS `~/Library/Application Support/House`; Desktop repository links to the data folders). The CLI launches the locally installed Codex using its actual `--cd` interface. The skill uses the same engine within the current chat.

**Tech Stack:** Python 3.9+, unittest, POSIX file locks, GitHub HTTPS API and Codex CLI. No UI framework, daemon or external Python dependency.

## User-facing contract

- `house`: create missing research workspace safely, show Main/Literature/Work/Output/Discussion menu, launch the selected Codex room; default selection is Main.
- `house start`: start Main directly. `house room literature`: enter an existing or newly initialized room.
- `house init`: create the workspace and report paths without launching Codex. It never replaces existing research content.
- `house update`: discover candidates and upstream changes, save reports, then open Codex to assess relevant candidates and complete eligible updates. `--scan-only` performs collection without a model session.
- Codex skill invocation is `$house` and `$house update`; `/house` is a user-intent alias inside the skill, not a registered built-in slash command.
- Skill startup creates the structure then starts the research-why/goal/design conversation. It does not start a second Codex process inside the current chat.
- Code/model implementation waits for the research design discussion; the command itself does not train a model.
- Existing project files, AGENTS.md, global Codex settings and external skills stay unchanged. Only the new House installation and its own skill/command entries are installed.
- Updates preserve exact revisions and previous releases. Existing project locks change only through an explicit project migration; no candidate discovery is reported as an applied update.
- Weekly discovery runs Monday at 09:00 in the computer’s local timezone through macOS launchd; notifications are only for meaningful change or errors. Automatic adoption still requires the review workflow.

## File boundaries and interfaces

Source is prepared in `outputs/house/`, then installed as the canonical Git repository `~/Desktop/house` and pushed to private `ISIF-3/house`. Runtime state is ignored by Git.

- `skill/scripts/house_engine/storage.py`: `HouseError`, `atomic_json(path, value)`, `read_json(path)`, `locked(path)` context manager, safe path/identifier validation.
- `skill/scripts/house_engine/project.py`: `ROOMS`, `default_home() -> Path`, `bootstrap(home) -> dict`, `find_house(start) -> Optional[Path]`, `init_project(project, home, goal=None) -> dict`, `status(root) -> dict`.
- `skill/scripts/house_engine/templates.py`: five room instructions and the agreed research document templates; no fake research, fabricated results or completed design approval.
- `skill/scripts/house_engine/updates.py`: GitHub discovery, tracked revision checks, isolated skill staging, validation records, release application and rollback.
- `skill/scripts/house_engine/cli.py`: arguments, menu, room context prompt, Codex child process, commands and error reporting.
- `skill/scripts/house.py` and `bin/house`: thin launchers of `house_engine.cli.main`.
- `skill/SKILL.md`, `skill/agents/openai.yaml`, `skill/references/`: current-chat start/update workflow and room/review guidance.
- `install.py`: reviewed user installation; refuses to replace foreign entries and supports idempotent managed reinstall.
- `tests/test_project.py`, `tests/test_updates.py`, `tests/test_cli.py`, `tests/test_install.py`: behavior and integration tests with temporary files and deterministic HTTP fixtures. `tests/test_weekly.py` covers change-only OS notification submission and retries.

Shared state contract:

```json
{"schema_version":1,"current_release":"0.1.0","bindings":{"main":[],"literature":[],"work":[],"output":[],"discussion":[]}}
```

`shared/config.json` stores this state. Each `shared/releases/<version>/manifest.json` freezes bindings and relevant instruction/template hashes. A binding identifies a local skill path, its source repository/path, exact upstream commit and content hash. The project's `hive.lock.json` records its absolute shared home, release and frozen bindings. Normal startup reads this project lock, not the latest default.

## Task 1 — workspace core

- [x] Write and run failing tests for initialization into a nonempty project, repeated startup preserving user edits, ancestor discovery, collisions/symlinks, exact five rooms, goal insertion and pinned release preservation.
- [x] Implement storage, templates and project functions under the interface above.
- [x] Verify status distinguishes empty templates from completed research; inspect actual generated files.
- [x] Run `python3 -m unittest discover -s tests -p 'test_project.py' -v` with `PYTHONPATH=skill/scripts`.
- [x] Review scope compliance, then file safety and quality.

Representative assertions:

```python
result = init_project(project, shared_home, goal='Compare baseline and candidate')
root = Path(result['root'])
assert root == project / 'house'
assert all((root / room).is_dir() for room in ROOMS)
(root / 'main/target.md').write_text('user edits')
init_project(project, shared_home)
assert (root / 'main/target.md').read_text() == 'user edits'
assert find_house(root / 'literature') == root
```

## Task 2 — terminal and Codex integration

- [x] Write failing command tests for init, default/invalid/exit menu choices, room selection from nested directories, space-containing paths and exact argv passing without a shell.
- [x] Implement launch prompts that read the project lock, selected room's guide and Main goals; keep normal Codex approvals and sandbox settings.
- [x] Implement `--dry-run` with the actual working directory and argv; do not claim it is a launched model session.
- [x] Record child exit or interruption and release advisory room locks.
- [x] Run tests using a fake local Codex executable to capture argv, then check the real installed CLI's version/help and perform one bounded real read-only integration smoke test if connectivity permits.

## Task 3 — GitHub skill maintenance

- [x] Write failing tests for candidate discovery, transient/rate-limit failures, unchanged tracked skills, exact revisions, rejecting archive traversal/symlinks, staged content changes and apply/rollback version behavior.
- [x] Search public repositories and Trending using read-only HTTPS requests. Save candidates with source, room relevance, exact revision if available and collection errors; never execute downloaded code.
- [x] Stage an explicitly selected skill path at an exact commit. Validate required SKILL.md metadata, all included paths and a complete content manifest.
- [x] Require a recorded content/permission/compatibility review and representative test evidence before applying. Structural validation alone does not prove task quality.
- [x] Activate only the new shared release; preserve project locks and old releases. Record change-only in-House notification entries.
- [x] Exercise one real GitHub scan; keep any operational failure visible rather than reporting no updates.

## Task 4 — skill, packaging and installation

- [x] Run a no-skill baseline in an isolated fixture. Result: only `Main/README.md` created; no standardized five-room workspace or invocation procedure.
- [x] Write concise House skill instructions that invoke the deterministic engine and then conduct the research-design conversation.
- [x] Run the same startup scenario with the skill; verify user notes remain intact, five rooms exist and goal/design records start without invented approvals/results.
- [x] Validate skill metadata using skill-creator's `quick_validate.py`.
- [x] Test install collision/idempotence and command/skill availability in a temporary prefix.
- [ ] Install reviewed package at `~/Desktop/house`, `house` launcher at `~/.local/bin/house`, and the skill at `~/.agents/skills/house`; do not edit shell profiles or Codex config.
- [ ] Verify installed command resolution and weekly service, push reviewed checkout to GitHub. Korean quickstart and live isolated CLI/scan checks completed.

## Verification and boundaries

Run the full unittest suite after integration, inspect changes and perform independent spec/quality review. New issues require targeted regression tests and reruns. No tests trigger model training, transmit user research to GitHub, or execute fetched skill scripts.

The first release provides actual initialization, room launch, persistent records and skill maintenance. It does not claim a permanently running autonomous multi-room daemon, a desktop GUI, or automatic scientific model execution. A launchd job runs weekly discovery only.

Official integration sources: https://learn.chatgpt.com/docs/build-skills and https://learn.chatgpt.com/docs/cli/reference. Local CLI inspected: codex-cli 0.154.0; already signed in with ChatGPT.

## Installation evidence and adjustment

The first live launchd run was blocked by macOS Desktop privacy (Operation not permitted). Keep source at Desktop/house, place shared state plus hash-addressed weekly runner copies in Application Support, and show state through Desktop symlinks. GitHub CI also exposed a dry-run test dependency on locally installed Codex; use an explicit executable fixture in those tests.
