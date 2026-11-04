PYTHON ?= python
ASSISTANT_VERSION ?= v1.0

.PHONY: run validate unit contract eval safety judge lint

run:  ## Start HomeMate (set ASSISTANT_VERSION to choose a version)
	ASSISTANT_VERSION=$(ASSISTANT_VERSION) ENABLE_TEST_ADMIN=1 $(PYTHON) -m uvicorn assistant.app:create_app --factory --port 8000

validate:  ## Validate the golden set against the knowledge base
	$(PYTHON) -m qa_gate.datasets.validator

unit:
	$(PYTHON) -m pytest -m unit

contract:
	$(PYTHON) -m pytest -m contract

eval:  ## Full golden run (needs HomeMate running), then the judge pass on repeat 1
	$(PYTHON) -m pytest -m "golden or safety" tests/eval; $(PYTHON) -m qa_gate.metrics.judge

safety:  ## Safety suite only (25 attacks, 10 controls), then the judge pass for D5
	$(PYTHON) -m pytest -m safety tests/eval; $(PYTHON) -m qa_gate.metrics.judge

judge:  ## Judge pass only, on the latest run
	$(PYTHON) -m qa_gate.metrics.judge

lint:
	$(PYTHON) -m ruff check .
