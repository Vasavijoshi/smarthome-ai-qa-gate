# SmartHome AI QA Gate

A QA gate that tests, scores and gates **HomeMate**, a small local LLM smart-home assistant, before each release.
The full design is in [docs/PROJECT_REPORT.md](docs/PROJECT_REPORT.md).

> Status: Phase 3 done. Exit-check run `v1.0-20261103T152402` (golden only, full judging) is stored in `runs/qa_results.db` and valid; it took 2 h 27 min. Results tables are added only after real runs.

## Setup (Windows, Python 3.11)

```bash
py -3.11 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
ollama pull llama3.2:3b
```

## Run HomeMate

```bash
make run ASSISTANT_VERSION=v1.0
```

Without `make` (PowerShell):

```powershell
$env:ASSISTANT_VERSION="v1.0"; $env:ENABLE_TEST_ADMIN="1"; .venv\Scripts\python -m uvicorn assistant.app:create_app --factory --port 8000
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `ASSISTANT_VERSION` | `v1.0` | `v1.0`, `v1.1`, `v2.0`, `v3.0` or `v4.0` (files in `assistant/versions/`) |
| `LLM_CLIENT` | `ollama` | `ollama` or `mock` (deterministic, used in CI) |
| `RETRIEVER` | `embedding` | `embedding` (all-MiniLM-L6-v2) or `tfidf` (used in CI) |
| `OLLAMA_MODEL` | `llama3.2:3b` | Assistant model |
| `LLM_SEED` | `11` | Sampling seed |
| `HOMEMATE_DB` | `assistant/homemate.db` | Device table and interaction log |
| `ENABLE_TEST_ADMIN` | `0` | `1` turns on `POST /admin/reset` |

API docs: http://localhost:8000/docs

## Dataset validation

```bash
.venv/Scripts/python -m qa_gate.datasets.validator
```

Checks `data/golden_set.yaml` (report 6.2.4) and prints the dataset hash. Variants carry a `variant_of:<id>` tag.

**Golden dataset v1.0 is frozen** (commit `6e5bbca`) at hash
`5d0396e9dc0590a4db3e3bd646fe01be54ef5bc2edeaf66959e11223ceb86a24`.
The same hash is in `eval.yaml`; a unit test and the evaluation both refuse to run if the file changes.

## Tests

```bash
.venv/Scripts/python -m pytest -m unit
.venv/Scripts/python -m pytest -m contract
```

## Deviations log

| Date | Change | Reason |
| --- | --- | --- |
| 2026-11-03 | `POST /chat` accepts an optional integer `seed` (report 6.1.7). If it is missing, the server uses `LLM_SEED` (default 11). | Report 6.3.5 runs every case with seeds 11, 22 and 33, and the original contract gave the framework no way to send them. Approved by the author. |
| 2026-11-03 | Ollama calls are capped at 400 completion tokens (`num_predict`). | In JSON mode `llama3.2:3b` once generated more than 2,600 tokens without stopping. The cap turns this into invalid JSON, which is retried once and then counted as `invalid_response` (6.1.4, M1). |
| 2026-11-03 | The shared tool-format text in the prompt has one tool-call example and one answer example. | Without them the model sometimes wrote a tool call inside an answer's text, so no tool ran. The R1–R8 rules are unchanged, and the text is the same for every version. |
| 2026-11-03 | Forbidden claims for unanswerable cases are affirmative or value-only, for example `"yes, it works with Alexa"` instead of the report example's `"works with Alexa"` (G-UNA-001, 6.2.3). | M3 matches phrases and cannot see negation, so a correct refusal that repeats the question ("I don't know if it is compatible with Alexa") scored M3 = 0. A unit test now wraps every unanswerable question in a refusal and requires M3 = 1. Approved by the author. |
| 2026-11-03 | Report section 6.2.3 updated: the G-UNA-001 example now uses the affirmative forbidden claims, and a short note explains the rule for unanswerable cases. | Keeps the report, the source of truth, consistent with `data/golden_set.yaml`. Requested by the author. |
| 2026-11-03 | Phase 2.1 golden-set fixes from an independent review: forbidden claims for G-FAC-003, 009, 010, 012, 018, 020 and G-MHP-002; required facts for G-FAC-003, 007, 017 and G-MHP-006; reference answer for G-MHP-006. | Some forbidden claims were things a correct answer could legitimately say (for example "15 litres", which is the purifier's hourly output). Some required facts were too narrow. Requested by the author. |
| 2026-11-03 | M4 refusal list widened with generic English variants ("i do not have", "does not contain", "does not provide", "does not include", "no details", "not provided", "not available in", "could not find", "no mention" and others). | Increases refusal recall. The phrases were chosen from general wording, not from model outputs. Known limit: "does not include" also matches legitimate answers, such as one about warranty exclusions; a unit test documents this. Requested by the author. |
| 2026-11-03 | The validator prints a WARNING (not an error) for every forbidden claim that appears in the knowledge base, matched the same way as M3. | Lets the author review forbidden claims a correct answer might legitimately repeat. Requested by the author. |
| 2026-11-03 | Phase 2.1b: "does not include" removed from the M4 refusal list. | It also matched legitimate answers, such as "the warranty does not include consumable parts", which would inflate the false-refusal rate. Requested by the author. |
| 2026-11-03 | Phase 2.1b golden-set fixes: forbidden claims for G-FAC-003, 007, 011, 012, 017, 020 and G-TRB-006 rewritten so none appears in the knowledge base. | The Phase 2.1 validator warnings showed these phrases could be said by a correct answer. The validator now reports 0 warnings. Requested by the author. |
| 2026-11-03 | M11: a case is flaky when its three repeats do not all agree (report 6.3.5 updated). | With 3 repeats, 2 of 3 agreeing gives 0.667, just below the 0.67 cut-off, so the rule was ambiguous. Decided by the author. |
| 2026-11-03 | Result store additions: `runs.judge_scope` and `runs.status`, `results.eval_ms` and `results.retrieved_json`, a `golden_cases` table and a `v_golden_cases` helper view. | `judge_scope` lets the gate refuse to compare full and sampled runs. `status` records the 6.6.2 valid/invalid verdict. `eval_ms` is the per-case time asked for by the author. `retrieved_json` lets the judge score faithfulness after the run. `golden_cases` stores category, difficulty and whether a case was judged, so views need no YAML. Approved by the author. |
| 2026-11-03 | `eval.yaml` holds the thresholds, judge settings and `judge.scope: full \| sampled`. Sampled judging covers the 20 smoke cases plus 10 non-smoke cases drawn with `sample_seed`. | Risk table fallback (10.2) for runs over 4 hours. Approved by the author. |
| 2026-11-03 | Evaluation runs in two passes: pytest (`-m golden`) runs all 180 assistant calls and layers 0-1, then `python -m qa_gate.metrics.judge` judges repeat 1 (`repeat_idx` 0, seed 11). Case pass/fail is computed in SQL views. | On a CPU, Ollama swaps the 3B and 7B models every time it switches between them; batching avoids about 180 swaps. Approved by the author. |
| 2026-11-03 | In 6.3.4, "a forbidden claim is present" and "no refusal is detected" are decided by a majority of the three repeats. The false-refusal rate counts answerable cases where M4 detects a refusal in at least 2 of 3 repeats. | Matches the 2-of-3 rule for deterministic conditions in 6.3.5. Approved by the author. |
| 2026-11-03 | `deepeval==4.3.2` requires `click<8.4`, so `huggingface-hub` is pinned to 1.16.1 and `transformers` to 5.17.0. | `huggingface-hub` 2.4 needs `click>=8.4.2`. Embedding scores were checked and are unchanged. |
| 2026-11-03 | Judge scope rule, fixed before any full run: use `full`; if a full golden run takes more than 4 hours end to end, switch every later run to `sampled` and compare earlier full runs on the same 30 sampled cases only. | Timed estimate from 3 cases: about 3.6 h per full run (287 s per answer case, 69 s per unanswerable case), close to the 4 h limit in risk table 10.2. Decided by the author. |
| 2026-11-03 | The judge command now re-stamps `runs.finished_at` after judging. | The exit-check run's `finished_at` (21:18 IST) marks the end of the assistant pass only; judging ended at 23:20 IST (run log). The stored value was not edited by hand; `total_eval_ms` holds the full time. |
