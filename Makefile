PYTHON ?= python
ASSISTANT_VERSION ?= v1.0

.PHONY: run unit contract lint

run:  ## Start HomeMate (set ASSISTANT_VERSION to choose a version)
	ASSISTANT_VERSION=$(ASSISTANT_VERSION) ENABLE_TEST_ADMIN=1 $(PYTHON) -m uvicorn assistant.app:create_app --factory --port 8000

unit:
	$(PYTHON) -m pytest -m unit

contract:
	$(PYTHON) -m pytest -m contract

lint:
	$(PYTHON) -m ruff check .
