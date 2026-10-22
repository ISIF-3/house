# Project connection

Use this workflow when the user wants session alerts in an existing Codex IDE,
CLI or local desktop conversation. Loading the skill alone does not register hooks.
The same project registration can be discovered by supported local clients.

## Register and inspect

Run the bundled engine with the explicitly selected project:

    python3 <skill-directory>/scripts/house.py connect --project <project> --dry-run --json
    python3 <skill-directory>/scripts/house.py connect --project <project> --apply --json
    python3 <skill-directory>/scripts/house.py doctor --project <project> --json

Within a user-authorized connection task, inspect the plan and apply the artifact
bundle without asking again for each file. This writes only House-owned entries
in the project's .codex/hooks.json, or a bounded block in its existing inline
hook config, plus local receipts and ignored observation state. It does not
change model settings, project pins, research inputs, global config or native trust.
The registration stores machine-specific paths; do not commit it as a portable
team installation. Inspect the plan for those paths before sharing configuration.
Existing tracked hook files also receive local absolute paths when explicitly
merged; a portable team binding is not implemented. A failed registration restores
the hook configuration if its receipt cannot be saved. Prepared ignored runtime
identity and ignore entries can remain; they do not imply an active connection.

Project trust and native hook review remain the host's responsibility. Do not
persist trusted hashes or bypass review on behalf of the user. A disabled
required hook stays disabled. Existing clients may need a reload or resume before
they discover a changed project layer; check actual callbacks before claiming success.

Optional model-free native inspection:

    python3 <skill-directory>/scripts/house.py doctor --project <project> --native --json

This starts an owned app-server solely for initialization and hooks/list, closes
it afterward, and starts no model turn. Its discovery/trust result does not prove
that a particular IDE/app displayed a warning.

For a Windows app using a WSL-owned project, the owner may explicitly register:

    python3 <skill-directory>/scripts/house.py connect --project <project> --wsl-distro <distribution> --apply

The Windows override forwards native JSON to that distribution via wsl.exe.
Windows transcript paths are translated only in this configured mode; missing
or invalid mappings remain errors rather than selecting another transcript.
Native Windows installations use the Windows installer/launcher and lock backend.
Windows hook commands target Codex's default cmd.exe shell. Paths containing %, !,
quotes or newlines fail explicitly instead of shell expansion. A custom Windows
hook shell needs its own adapter and is not verified by this implementation.
Native Windows, WSL forwarding and IDE/app UI require their own actual validation.
Cloud-orchestrated clients do not acquire local hooks from this registration.

## Observation and handoff

The first real callback can attach an already open conversation even if its start
hook was missed. The local conversation key is not a fabricated native thread ID.
Unknown native thread IDs and previous compactions remain unknown. Forks and
child transcripts stay separate.

Use doctor or sessions to find the exact observation workspace and handoff path.
Write actual content under 목표, 확정 결정, 실행 중인 작업, 다음 행동; keep it
within 8,000 characters. Save it with:

    python3 <skill-directory>/scripts/house.py connection checkpoint <conversation-key> --project <project> --json

Add --boundary only at an actual work boundary. Only identifiable completed
automatic compactions count toward the v2 ten-compaction recommendation;
unidentifiable deliveries and unknown causes are shown separately. This tightens
the old best-effort count without rewriting old counters.

Recent valid context observations at 70% prepare a handoff; compaction invalidates
old usage. A saved work boundary at ten confirmed automatic compactions recommends
a new chat once. No observation ends a chat or cancels a calculation. Legacy
session workspaces and snapshots remain intact.

Native warning output is a delivery attempt, not proof that a person saw the UI.
Report registration, observed callbacks, native trust, and screen verification
separately. House also requests a concise, ordinary chat message through
`hookSpecificOutput.additionalContext` on `SessionStart`, `UserPromptSubmit` and
`PostToolUse`. Show the actual supplied notice once; keep working and prepare the
handoff rather than ending a conversation or calculation. Never present an
explicitly labelled display test as a real threshold crossing or claim that a
native popup appeared.

A notice first observed on another event waits for the next supported context
event. The v2 delivery record's `additional_context_prepared_at` means that the
model-context output was prepared, not that the model or user acknowledged it;
`chat_display_confirmation` remains unknown without evidence. Native retries do
not repeat the chat request for the same alert and endpoint. Resolved notices
are not forwarded later. Missing observations are not healthy-session evidence.

This is a local integration trust boundary: the host supplies callback JSON and
readable transcripts. A local process able to edit those files can forge them;
the binding ID prevents accidental routing mistakes, not local-user impersonation.

## Remove

    python3 <skill-directory>/scripts/house.py disconnect --project <project> --dry-run --json
    python3 <skill-directory>/scripts/house.py disconnect --project <project> --apply --json

Only exact managed entries are removed. User-added hooks are retained and edited
House entries cause a clear conflict. Local observation history is preserved.
