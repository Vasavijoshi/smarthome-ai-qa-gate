# SmartHome AI QA Gate: rules for Claude Code

The single source of truth is `docs/PROJECT_REPORT.md`. Follow it strictly.

1. Do not change the plan. Do not add, remove or rename components, metrics, datasets, versions or folders. If something in the report looks wrong or impossible, stop and ask first.
2. Build ONE phase at a time (report section 7.4). Stop at the phase's exit check and wait for "next phase".
3. `assistant/` and `qa_gate/` never import from each other. They talk only over HTTP (CI uses the in-process mock client).
4. Python 3.11, free and local only (Ollama, SQLite). No paid APIs, no cloud.
5. Never invent results. Every number must come from a real run saved in SQLite or `runs/`. "Expected Results" in the report are hypotheses, not facts.
6. Every scorer, validator and gate function needs unit tests (at least 5 inputs that pass and 5 that fail).
7. Keep code simple and readable: short functions, type hints, one-line docstrings. Every file must be explainable in an interview.
8. After each phase, report: files created, how to run them, what to test, and 3 interview questions about the new code.
9. Commit after each phase with a clear message.

## Quality bar

At the end of every phase, before reporting, do this self-audit and show it to the author:

1. Re-read the report sections for the phase and tick off every requirement, one line each, with the file that implements it. List anything missing or different.
2. Run the full test suite, ruff and the dataset validator. Show the counts.
3. List every deviation from the report, and confirm each one is both in the README deviations log and in the report text.
4. For every number reported, name the SQLite query or file it came from. No number from memory.
5. List known weaknesses, even small ones, instead of hiding them.
6. For every scorer or detector, say what input would fool it.

Never tune prompts, rubrics, thresholds or the golden set after seeing results. If something looks wrong, investigate read-only first and report.

## Decisions taken with the author

- Phase 1: Claude drafted the six manuals; the author reviews and freezes them as kb_version 1.0 before Phase 2.
- Split chunks get ids like `vayu_f1_fan#troubleshooting/2`. Retrieval hit (M5) compares only the `document#section` part (`assistant.rag.section_of`). Only the four Troubleshooting sections are over 200 words, so v3.0's 400-word chunks keep them whole.
- Admin hooks: `POST /admin/reset` in Phase 1, `/admin/kb/documents` in Phase 4, `/admin/faults` in Phase 5.
- `POST /chat` takes an optional integer `seed` (defaults to `LLM_SEED`), so the framework can run repeats with seeds 11, 22, 33. Approved by the author and noted in report section 6.1.7.
- Deviations from the report go in the README deviations log.
- Golden dataset v1.0 is frozen at hash `5d0396e9dc0590a4db3e3bd646fe01be54ef5bc2edeaf66959e11223ceb86a24` (commit 6e5bbca). Never edit `data/golden_set.yaml` without telling the author first.
- Attacks v1.0 (`3f28599aceb7983f5df446038670b341fa5967441eaaacb2d12f23893e7da145`) and benign controls v1.0 (`11b960b4715c724856f5d6183e23423f26794c5e627fa60a04ba7cb4c0bc25da`) are frozen after Phase 4, hashes in `eval.yaml`. Never edit `data/attacks.yaml` or `data/controls.yaml` without telling the author first.
