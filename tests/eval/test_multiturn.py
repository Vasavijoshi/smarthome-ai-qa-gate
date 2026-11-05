"""Multi-turn suite (report 6.5.5): 10 scripted scenarios, two repeats each (seeds 11 and 22).

State and fact assertions decide every turn; the conversational judge check runs later as evidence only.
"""

import time

import pytest

from qa_gate.datasets.loader import Scenario, load_scenarios
from qa_gate.metrics.deterministic import MetricRow, response_valid
from qa_gate.metrics.tools import invariant_violations, turn_failures
from qa_gate.store import add_result

pytestmark = pytest.mark.multiturn

SCENARIOS = [pytest.param(s, id=s.id) for s in load_scenarios()]
SCENARIO_REPEATS = 2  # report 6.5.5: 10 scenarios x 2 repeats


def run_scenario(
    run, scenario: Scenario, repeat_idx: int, seed: int
) -> tuple[list[MetricRow], list[str], dict]:
    """Play every turn in one session; return per-turn rows, the replies and token totals."""
    run.client.reset()
    rows: list[MetricRow] = []
    replies: list[str] = []
    totals = {"latency_ms": 0, "prompt_tokens": 0, "completion_tokens": 0}
    faulty: set[str] = set()
    legal, valid_all = True, True
    try:
        before = run.client.devices()
        for i, turn in enumerate(scenario.turns, start=1):
            if turn.clear_faults_before:
                for device_id in faulty:
                    run.client.set_fault(device_id, "none")
                faulty.clear()
            if turn.fault_before:
                run.client.set_fault(turn.fault_before.device, turn.fault_before.mode)
                faulty.add(turn.fault_before.device)
            status, body = run.client.chat(f"{run.run_id}-{scenario.id}-{repeat_idx}", turn.user, seed)
            valid = response_valid(status, body)
            calls, reply = (body["tool_calls"], body["reply"]) if valid else ([], "")
            after = run.client.devices()
            bypass = run.config.blocked_topics["lock_bypass"]
            failures = turn_failures(turn, calls, reply, before, after, bypass) + (
                [] if valid else ["invalid response"]
            )
            rows.append(MetricRow(f"turn_{i}", float(not failures), None, not failures, "; ".join(failures)))
            replies.append(reply)
            legal = legal and not invariant_violations(after)
            valid_all = valid_all and valid
            for key in totals:
                totals[key] += body["metadata"][key] if valid else 0
            before = after
    finally:
        run.client.reset()
    rows += [MetricRow("response_validity", float(valid_all), None, valid_all)]
    rows += [MetricRow("T5_invariants", float(legal), None, legal)]
    return rows, replies, totals


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_scenario(eval_run, scenario):
    run = eval_run
    outcomes = []
    for repeat_idx, seed in enumerate(run.config.seeds[:SCENARIO_REPEATS]):
        start = time.perf_counter()
        rows, replies, totals = run_scenario(run, scenario, repeat_idx, seed)
        passed = all(row.passed for row in rows)
        validity = next(row for row in rows if row.metric == "response_validity")
        add_result(
            run.conn,
            {
                "run_id": run.run_id,
                "suite": "scenario",
                "case_id": scenario.id,
                "repeat_idx": repeat_idx,
                "passed": passed,
                "latency_ms": totals["latency_ms"] or None,
                "prompt_tokens": totals["prompt_tokens"],
                "completion_tokens": totals["completion_tokens"],
                "reply": "\n---\n".join(replies),
                "error_type": None if validity.passed else "invalid_response",
                "retrieved_json": None,
                "eval_ms": int((time.perf_counter() - start) * 1000),
            },
            rows,
            [],
        )
        outcomes.append(passed)
    failed = [r.reason for r in rows if not r.passed and r.reason]
    assert all(outcomes), f"passed {sum(outcomes)} of {len(outcomes)} repeats; last failures: {failed}"
