# SmartHome AI QA Gate

A QA gate that tests, scores and gates **HomeMate**, a small local LLM smart-home assistant, before each release.
The full design is in [docs/PROJECT_REPORT.md](docs/PROJECT_REPORT.md).

> Status: Phase 1 (HomeMate) built. Results tables are added only after real runs.

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
