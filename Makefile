PYTHON ?= python
ASSISTANT_VERSION ?= v1.0

.PHONY: run validate unit contract eval safety judge snapshot gate rebaseline lint

run:  ## Start HomeMate (set ASSISTANT_VERSION to choose a version)
	ASSISTANT_VERSION=$(ASSISTANT_VERSION) ENABLE_TEST_ADMIN=1 $(PYTHON) -m uvicorn assistant.app:create_app --factory --port 8000

validate:  ## Validate the golden set against the knowledge base
	$(PYTHON) -m qa_gate.datasets.validator

unit:
	$(PYTHON) -m pytest -m unit

contract:
	$(PYTHON) -m pytest -m contract

eval:  ## Full golden run (needs HomeMate running), then the judge pass on repeat 1
	$(PYTHON) -m pytest -m "golden or safety or tools or multiturn" tests/eval; $(PYTHON) -m qa_gate.metrics.judge

safety:  ## Safety suite only (25 attacks, 10 controls), then the judge pass for D5
	$(PYTHON) -m pytest -m safety tests/eval; $(PYTHON) -m qa_gate.metrics.judge

judge:  ## Judge pass only, on the latest run
	$(PYTHON) -m qa_gate.metrics.judge

snapshot:  ## Export a valid, complete run: make snapshot RUN=<run_id>
	$(PYTHON) -m qa_gate.snapshot export --run $(RUN)

gate:  ## Compare a candidate snapshot with the baseline: make gate CANDIDATE=runs/<run_id>.json
	$(PYTHON) -m qa_gate.gate --candidate $(CANDIDATE)

rebaseline:  ## Replace the baseline, with a reason: make rebaseline RUN=<run_id> REASON="..."
	$(PYTHON) -m qa_gate.snapshot rebaseline --run $(RUN) --reason "$(REASON)"

lint:
	$(PYTHON) -m ruff check .
