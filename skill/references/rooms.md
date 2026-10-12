# Room conventions

Main is the project's coordinator, not a substitute for all room records. It can read every room and track requests between rooms. A room's task should name its inputs, outputs, version/run and completion criteria. Main owns the shared progress and decision records; avoid simultaneous writes to the same file.

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
