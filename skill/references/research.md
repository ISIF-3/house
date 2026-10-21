# Research scope and evidence (0.4.1)

House keeps its existing HOUSE.md, AGENTS.md, ROOM.md and pinned-release structure.
The new `house/research.json` makes the agreed task scope, required inputs, scientific
decision ownership and preservation policy explicit. Main records actual agreement;
every room reads the relevant declaration before doing its work. No scientific
system, folder name, model, environment or decision owner is hardcoded.

## Establish the scope

New 0.4.1 projects start with `scope.status = unconfirmed`, empty input/criterion
lists and no agreement. These are unfilled declarations, not approval or successful
input validation. Main and Literature may prepare the research. Before execution,
Main records the agreed purpose, output bundle, protected scientific meanings and
the actual agreement evidence. Do not ask again for each file inside the agreed
bundle. A change to scientific definitions, selection/grouping rules, descriptor or
energy meanings, input/output scope, or effects on shared results needs review by
the appropriate owner and the user where unresolved. Native hook trust and tool
permission approvals still require their normal responses.

All paths below are relative to the project **containing** `house/`, not to the room
or the shell's current directory. Absolute paths, traversal and symlink inputs are
refused. Required inputs are explicit regular files; no alternate source or guessed
default is used when a declared input is missing or invalid.

| Section | Fields and meaning |
|---|---|
| `scope` | `status`: unconfirmed/agreed; `purpose`; `allowed_writes`: project-relative files/directories; `protected_meaning`: descriptions; `agreement`: owning evidence path; `agreement_sha256`: exact evidence hash |
| `inputs` | Each entry: unique `id`, `path`, exact `sha256`, `role: active`. Optional source `capture` requires its exact `capture_sha256`. Archive material must first be copied into active ownership with provenance. |
| `criteria` | Each entry: unique `id`, `owner`, `definition`, exact `sha256`, `status`: proposed/adopted/rejected, `use`: adopted/exploratory, `decision`, exact `decision_sha256`. Adopted use requires adopted status. An authorized exploratory trial stays explicitly exploratory. |
| `dependencies` | Each entry has `consumer` and `input` paths. They can identify files or directories. Record known retained results and shared consumers; House does not discover an entire dependency graph. |
| `preservation` | Explicit booleans `allow_compatibility_symlinks` and `reason_required`. Initial proposed values are false and true. Agree the policy before moving data. |

Use actual file hashes (`sha256sum <file>` or the capture command's returned hashes).
Changing agreement, definition or decision bytes makes the current contract fail
until the owner reviews the change and Main records the correct evidence. A contract
file and an existing decision document cannot independently authenticate consent or
scientific validity. Empty lists mean no items were declared; agents must not use
that to omit inputs or criteria actually required by the agreed design.
Hashes read the full declared files. Integrated pre-tool checks have a 60-second
hook timeout; very large inputs may exceed it and fail rather than skip validation.

```sh
house research check --project /path/to/project --json
```

This read-only command exits 0 only when the declaration is ready; errors or an
undeclared old project produce a nonzero result. `house check` also includes the
research status, while retaining its diagnostic exit-code behavior.

## Literature: preserve each source state

The source file must already have been explicitly obtained. Capture copies it; it
does not query a service, choose another source, execute it or refresh an old id.

```sh
house research capture /path/to/obtained-source.json --id query-001 \
  --source 'source URL or identifier' --purpose 'agreed evidence collection'
```

The data goes to `<project>/house-data/captures/query-001/`, with a record under
`house/literature/captures/query-001.json`. The record includes source, purpose, UTC
capture time, exact path, size and SHA-256. The command returns both the data hash
and metadata hash for the contract. The project scope must permit both locations.
Use a new id for a new query. Existing ids are never overwritten. Environment files
are refused. If reusing archived evidence, first confirm its scientific role, then
pass `--from-archive` to record the archived source path and hash alongside the active
copy. Copy only the selected artifact. Original historical dates belong in the
owning evidence; the capture time is the actual new copy time.

## Work and Output: freeze and verify a run's basis

Before a calculation:

```sh
house research check
house research snapshot output/model_v1/run-001
```

The run directory must be new or empty. The snapshot creates `basis.json` exactly once. It records inputs, scientific
criterion owners/status/use and full frozen agreement, criterion, decision and
capture-metadata text with their hashes. A later upstream criterion change does not
rewrite this result's meaning. Choose a new run id for another execution.
Never backfill an old run using today's criteria or claim an old validation as a new
check. Existing results without a basis keep their original evidence and limits;
use a new run id if a new execution is needed.

After actually running, write `run.json` with the actual nonempty `command`,
`status` (`completed` or `failed`), and the exact `basis_sha256` returned before
execution. Keep the other execution parameters, environment, outputs, logs and
limitations required by the room guide. Do not infer completion from a snapshot.

```sh
house research verify-run output/model_v1/run-001
```

Verification uses the frozen basis, not today's contract or criterion status. It
checks preserved input bytes, frozen evidence and the run record's basis hash and
execution fields. It does not rerun calculations, validate numerical correctness or
certify consent. Required input files must stay at the recorded hash-pinned paths;
immutable captures are recommended. A moved/deleted input fails clearly instead of
being silently resolved from another archive or source. Preserve failed runs too.

CLI Work launch and the integrated Main's Work spawn/follow-up/resume are gated by
the adopted contract. Reused Work agents must also run the check before each actual
analysis. Existing app chats without House's native hooks have the instruction and
CLI checks, not an automatic tool boundary. Scope checks constrain House's capture,
snapshot and move commands; they are not an OS sandbox for arbitrary external tools.

## Discussion and preservation

Record the owner of each criterion and adoption evidence. Main coordinates rooms;
it does not replace a responsibility unit's decision. A local exploratory grouping
is not adoption of a general screening rule. Keep proposals, adopted definitions
and the exact state used by a result distinct.

Long records are review candidates. Age, replacement or exceeding 20,000 characters
alone does not justify archiving. Establish a preservation purpose and check retained
results before moving; preserve original dates, scientific content and historical
paths. Deleting a discarded attempt requires explicit bundle authorization,
dependency review, retained shared evidence, and the reason/replacement (or no
replacement) recorded in its owning decision record. House does not automate that
scientific removal decision.

```sh
house offload output/model_v1/run-001/raw --no-link --reason 'agreed preservation purpose' --dry-run
```

An adopted contract checks scope, purpose, symlink policy and declared consumers.
Moving required input, scope agreement or criterion/capture evidence is refused even
with a compatibility link, because links cannot substitute for declared scientific
inputs. A no-link move also refuses ordinary declared dependents. Review with the
owner and update affected **current** runtime references
within the agreed migration; never silently rewrite historical evidence. Review
unlisted consumers manually. With contracts, destinations and their new sidecars
must be inside the declared project scope; an old undeclared pin retains its
previous move interface. Existing recent-output and Markdown guards remain active.
All-skipped batches report blocked, partial batches report partially_moved, and the
CLI returns nonzero when items were skipped.

Migration preserves edited contracts. A missing 0.4.1 contract is an error on resume,
not a reason to recreate guessed scientific state. Existing older pins remain
unchanged and explicitly undeclared until authorized migration/adoption. Native
permissions, the shared release pin and immutable external skill checks are retained.
