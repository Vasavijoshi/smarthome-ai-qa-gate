"""Tool-call suite (report 6.5.1 to 6.5.4): 23 cases, three repeats each, checked against the device table.

The T6 judge part runs afterwards: python -m qa_gate.metrics.judge
"""

import json
import time

import pytest

from qa_gate.datasets.loader import ToolCase, load_tool_cases
from qa_gate.metrics.deterministic import MetricRow, response_valid
from qa_gate.metrics.tools import tool_checks
from qa_gate.store import add_result

pytestmark = pytest.mark.tools

CASES = [pytest.param(c, id=c.id) for c in load_tool_cases()]


def run_attempt(
    run, case: ToolCase, repeat_idx: int, seed: int
) -> tuple[list, list[str], dict, dict, bool, dict]:
    """Reset, inject the fault and send the turns; return calls, replies, devices, validity and last body."""
    run.client.reset()
    try:
        start = run.client.devices()
        if case.fault:
            run.client.set_fault(case.fault.device, case.fault.mode)
        calls, replies, valid, body = [], [], True, {}
        for message in case.turns:
            status, body = run.client.chat(f"{run.run_id}-{case.id}-{repeat_idx}", message, seed)
            ok = response_valid(status, body)
            valid = valid and ok
            calls += body["tool_calls"] if ok else []
            replies.append(body["reply"] if ok else "")
        after = run.client.devices()
    finally:
        run.client.reset()  # clear the fault and every change before the next attempt
    return calls, replies, start, after, valid, body


@pytest.mark.parametrize("case", CASES)
def test_tool_case(eval_run, case):
    run = eval_run
    outcomes = []
    for repeat_idx, seed in enumerate(run.config.seeds):
        start_time = time.perf_counter()
        calls, replies, start, after, valid, body = run_attempt(run, case, repeat_idx, seed)
        checks = tool_checks(case, calls, replies[-1], start, after)
        # Tool selection accuracy is T1 and T4 together, over the 15 non-failure cases.
        if case.group != "failure_injection":
            checks["tool_selection"] = checks["T1_right_tool"] and checks["T4_call_count"]
        rows = [MetricRow("response_validity", float(valid), None, valid)]
        rows += [MetricRow(name, float(ok), None, ok) for name, ok in checks.items()]
        passed = valid and all(checks.values())
        meta = body.get("metadata", {}) if valid else {}
        add_result(
            run.conn,
            {
                "run_id": run.run_id,
                "suite": "tool",
                "case_id": case.id,
                "repeat_idx": repeat_idx,
                "passed": passed,
                "latency_ms": meta.get("latency_ms"),
                "prompt_tokens": meta.get("prompt_tokens"),
                "completion_tokens": meta.get("completion_tokens"),
                "reply": "\n---\n".join(replies),
                "error_type": None if valid else "invalid_response",
                "retrieved_json": json.dumps(body.get("retrieved", [])) if valid else None,
                "eval_ms": int((time.perf_counter() - start_time) * 1000),
            },
            rows,
            calls,
        )
        outcomes.append(passed)
    assert sum(outcomes) * 2 > len(outcomes), (
        f"checks passed in only {sum(outcomes)} of {len(outcomes)} repeats"
    )
