"""Unit tests for run snapshots (report 6.6.3) and rebaselining (6.7.3), on a small synthetic store."""

import json
from types import SimpleNamespace

import pytest

from qa_gate.metrics.deterministic import MetricRow
from qa_gate.snapshot import build_snapshot, rebaseline
from qa_gate.store import add_golden_case, add_result, add_safety_result, connect, start_run

pytestmark = pytest.mark.unit

EXPECTED = {"golden": 3, "attack": 6, "control": 3, "tool": 3, "scenario": 2}
RUN_META = {
    "assistant_version": "v1.0",
    "model": "m",
    "judge_model": "j",
    "judge_scope": "full",
    "dataset_hash": "d",
    "kb_version": "k",
    "config_hash": "c",
    "settings_hash": "s",
    "git_commit": "g",
    "repeats": 3,
}


def result(
    conn, run_id: str, suite: str, case_id: str, repeat: int, metrics: dict[str, bool], **extra
) -> int:
    """Insert one result with pass/fail metric rows; passed defaults to "every metric passed"."""
    row = {
        "run_id": run_id,
        "suite": suite,
        "case_id": case_id,
        "repeat_idx": repeat,
        "passed": all(metrics.values()),
        "latency_ms": 100 + repeat,
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "reply": "r",
        "error_type": None,
        "retrieved_json": "[]",
        "eval_ms": 5,
        **extra,
    }
    return add_result(conn, row, [MetricRow(m, float(p), None, p) for m, p in metrics.items()], [])


def add_golden(conn, run_id: str, golden_valid: bool) -> None:
    """One unjudged golden case, three repeats; passed is set explicitly (a correct answer has no refusal)."""
    meta = SimpleNamespace(
        id="G-1", category="factual", difficulty="easy", expected_behavior="answer", source_chunks=["a#b"]
    )
    add_golden_case(conn, run_id, meta, judged=False)
    checks = {"forbidden_claims": True, "refusal_detected": False, "retrieval_hit": True}
    for i in range(3):
        valid = golden_valid or i > 0
        result(conn, run_id, "golden", "G-1", i, {"response_validity": valid, **checks}, passed=valid)


def add_safety(conn, run_id: str) -> None:
    """Two attacks (one critical that succeeds once) and one control refused in 1 of 3 repeats."""
    for attack_id, severity, hits in [("A-PII-001", "critical", (0, 1, 0)), ("A-DPI-001", "high", (0, 0, 0))]:
        for i, hit in enumerate(hits):
            rid = result(conn, run_id, "attack", attack_id, i, {"response_validity": True})
            item = {"id": attack_id, "category": "pii_leak", "severity": severity}
            add_safety_result(conn, rid, item, "D3", bool(hit), "")
    control = {"id": "C-001", "category": "control", "severity": "none"}
    for i, refused in enumerate((1, 0, 0)):
        rid = result(conn, run_id, "control", "C-001", i, {"response_validity": True})
        add_safety_result(conn, rid, control, "over_refusal", bool(refused), "")


def add_tools(conn, run_id: str) -> None:
    """One tool case passing 2 of 3 attempts, and one scenario passing both repeats."""
    checks = {"response_validity": True, "T2_arguments": True, "T3_state": True, "T4_call_count": True}
    for i in range(3):
        result(conn, run_id, "tool", "T-COR-001", i, {**checks, "tool_selection": i != 2})
    for i in range(2):
        result(
            conn,
            run_id,
            "scenario",
            "S-01",
            i,
            {"response_validity": True, "turn_1": True, "T5_invariants": True},
        )


def complete_run(tmp_path, golden_valid: bool = True) -> tuple[object, str]:
    """A store with one complete run: every suite present at the EXPECTED counts."""
    conn = connect(tmp_path / "qa.db")
    run_id = start_run(conn, RUN_META)
    add_golden(conn, run_id, golden_valid)
    add_safety(conn, run_id)
    add_tools(conn, run_id)
    return conn, run_id


def test_complete_valid_run_gives_a_snapshot(tmp_path):
    conn, run_id = complete_run(tmp_path)
    snap = build_snapshot(conn, run_id, expected=EXPECTED)
    assert snap["run"]["run_id"] == run_id and snap["run"]["status"] == "valid"
    assert snap["metrics"]["case_pass_rate"] == {"value": 1.0, "numerator": 1, "denominator": 1}
    assert snap["metrics"]["attack_success_rate_worst3"]["numerator"] == 1
    assert snap["metrics"]["tool_selection_accuracy"] == {"value": 1.0, "numerator": 1, "denominator": 1}
    assert snap["metrics"]["p95_latency"]["value"] == 102


def test_snapshot_lists_critical_successes_worst_of_three(tmp_path):
    conn, run_id = complete_run(tmp_path)
    assert build_snapshot(conn, run_id, expected=EXPECTED)["critical_successes"] == ["A-PII-001"]


def test_snapshot_case_outcomes_per_suite(tmp_path):
    conn, run_id = complete_run(tmp_path)
    cases = build_snapshot(conn, run_id, expected=EXPECTED)["cases"]
    assert cases == {
        "golden:G-1": True,
        "attack:A-PII-001": False,  # succeeded in one attempt
        "attack:A-DPI-001": True,
        "control:C-001": True,  # refused in 1 of 3 repeats only
        "tool:T-COR-001": True,  # 2 of 3 attempts passed
        "scenario:S-01": True,
    }


def test_snapshot_has_response_validity_per_suite(tmp_path):
    conn, run_id = complete_run(tmp_path, golden_valid=False)
    validity = build_snapshot(conn, run_id, expected=EXPECTED)["response_validity"]
    assert validity["golden"]["numerator"] == 2 and validity["tool"]["numerator"] == 3


def test_partial_run_is_refused(tmp_path):
    conn, run_id = complete_run(tmp_path)
    scenario_ids = "SELECT result_id FROM results WHERE suite = 'scenario'"
    conn.execute(f"DELETE FROM metric_scores WHERE result_id IN ({scenario_ids})")
    conn.execute("DELETE FROM results WHERE suite = 'scenario'")
    with pytest.raises(ValueError, match="not complete"):
        build_snapshot(conn, run_id, expected=EXPECTED)


def test_invalid_run_is_refused(tmp_path):
    conn, run_id = complete_run(tmp_path)
    with pytest.raises(ValueError, match="invalid"):
        build_snapshot(conn, run_id, expected={**EXPECTED, "tool": 69})


def test_snapshot_is_json_serialisable(tmp_path):
    conn, run_id = complete_run(tmp_path)
    json.dumps(build_snapshot(conn, run_id, expected=EXPECTED))


def test_rebaseline_needs_a_reason(tmp_path):
    conn, run_id = complete_run(tmp_path)
    with pytest.raises(ValueError, match="reason"):
        rebaseline(build_snapshot(conn, run_id, expected=EXPECTED), "   ", tmp_path / "baseline.json")


def test_rebaseline_writes_the_reason(tmp_path):
    conn, run_id = complete_run(tmp_path)
    path = rebaseline(
        build_snapshot(conn, run_id, expected=EXPECTED), "R1 baseline", tmp_path / "baseline.json"
    )
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["baseline"]["reason"] == "R1 baseline" and saved["run"]["run_id"] == run_id
