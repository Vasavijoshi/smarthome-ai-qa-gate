"""Golden suite: every case three times (seeds 11, 22, 33), layers 0 and 1 scored and stored in SQLite.

The judge layer runs afterwards on repeat_idx 0: python -m qa_gate.metrics.judge
"""

import json
import time

import pytest

from qa_gate.datasets.loader import load_golden
from qa_gate.metrics.deterministic import deterministic_pass, score_golden_reply
from qa_gate.store import add_golden_case, add_result

pytestmark = pytest.mark.golden

CASES = [pytest.param(c, id=c.id, marks=[pytest.mark.smoke] if c.smoke else []) for c in load_golden()]


def error_type(status_code: int, body: object) -> str:
    """Short error label for an invalid response, e.g. 'invalid_response' or 'http_500'."""
    if isinstance(body, dict) and isinstance(body.get("detail"), str):
        return body["detail"]
    return f"http_{status_code}"


@pytest.mark.parametrize("case", CASES)
def test_golden_case(eval_run, case):
    run = eval_run
    add_golden_case(run.conn, run.run_id, case, judged=case.id in run.judged)
    outcomes = []
    for repeat_idx, seed in enumerate(run.config.seeds):
        start = time.perf_counter()
        status, body = run.client.chat(f"{run.run_id}-{case.id}-{repeat_idx}", case.question, seed)
        rows = score_golden_reply(case, status, body, run.config.thresholds["fact_coverage"])
        passed = deterministic_pass(case.expected_behavior, rows)
        valid = rows[0].passed
        add_result(
            run.conn,
            {
                "run_id": run.run_id,
                "suite": "golden",
                "case_id": case.id,
                "repeat_idx": repeat_idx,
                "passed": passed,
                "latency_ms": body["metadata"]["latency_ms"] if valid else None,
                "prompt_tokens": body["metadata"]["prompt_tokens"] if valid else None,
                "completion_tokens": body["metadata"]["completion_tokens"] if valid else None,
                "reply": body["reply"] if valid else None,
                "error_type": None if valid else error_type(status, body),
                "retrieved_json": json.dumps(body["retrieved"]) if valid else None,
                "eval_ms": int((time.perf_counter() - start) * 1000),
            },
            rows,
            body["tool_calls"] if valid else [],
        )
        outcomes.append(passed)
    assert sum(outcomes) * 2 > len(outcomes), (
        f"layers 0-1 passed in only {sum(outcomes)} of {len(outcomes)} repeats"
    )
