"""Unit tests for the snapshot schema check that CI runs (report 6.9.1, data-validation job)."""

import copy
import json

import pytest

from qa_gate.snapshot import BASELINE_PATH, RUNS_DIR, check_files, check_snapshot

pytestmark = pytest.mark.unit

GOOD = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def changed(edit) -> dict:
    """A deep copy of the good snapshot with one edit applied."""
    data = copy.deepcopy(GOOD)
    edit(data)
    return data


def set_rate(data: dict, name: str, num: int, den: int) -> None:
    """Set a count metric to a consistent rate."""
    data["metrics"][name] = {"numerator": num, "denominator": den, "value": num / den}


PASSING = {
    "the committed baseline": GOOD,
    "no baseline block (a plain run snapshot)": changed(lambda d: d.pop("baseline")),
    "no critical successes": changed(lambda d: d.update(critical_successes=[])),
    "a rate of zero": changed(lambda d: set_rate(d, "false_refusal_rate", 0, 10)),
    "a rate of one": changed(lambda d: set_rate(d, "retrieval_hit_rate", 48, 48)),
    "integer p95 and incidents": changed(
        lambda d: d["metrics"].update(invalid_state_incidents={"value": 19})
    ),
}

FAILING = {
    "wrong schema version": (changed(lambda d: d.update(schema_version=2)), "schema_version"),
    "missing settings hash": (changed(lambda d: d["run"].pop("settings_hash")), "settings_hash"),
    "run not valid": (changed(lambda d: d["run"].update(status="invalid")), "status"),
    "missing gated metric": (changed(lambda d: d["metrics"].pop("hallucination_rate")), "hallucination_rate"),
    "numerator above denominator": (
        changed(
            lambda d: d["metrics"].update(case_pass_rate={"numerator": 61, "denominator": 60, "value": 1.0})
        ),
        "impossible counts",
    ),
    "value does not match counts": (
        changed(lambda d: d["metrics"]["case_pass_rate"].update(value=0.9)),
        "does not equal",
    ),
    "missing suite validity": (
        changed(lambda d: d["response_validity"].pop("tool")),
        "response_validity.tool",
    ),
    "negative incidents": (
        changed(lambda d: d["metrics"].update(invalid_state_incidents={"value": -1})),
        "invalid_state_incidents",
    ),
    "case outcome not a boolean": (
        changed(lambda d: d["cases"].update({"golden:G-FAC-001": 1})),
        "malformed",
    ),
    "unknown suite in case key": (changed(lambda d: d["cases"].update({"other:X": True})), "malformed"),
    "not an object": ([], "not a JSON object"),
}


@pytest.mark.parametrize("data", PASSING.values(), ids=PASSING.keys())
def test_good_snapshots_pass(data):
    assert check_snapshot(data) == []


@pytest.mark.parametrize(("data", "expected"), FAILING.values(), ids=FAILING.keys())
def test_bad_snapshots_fail_with_a_named_reason(data, expected):
    errors = check_snapshot(data)
    assert errors and any(expected in e for e in errors), errors


def test_every_committed_snapshot_passes():
    paths = [BASELINE_PATH, *sorted(RUNS_DIR.glob("*.json"))]
    assert all(check_snapshot(json.loads(p.read_text(encoding="utf-8"))) == [] for p in paths)


def test_check_files_exit_code(tmp_path):
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(GOOD), encoding="utf-8")
    bad.write_text(json.dumps(FAILING["run not valid"][0]), encoding="utf-8")
    assert check_files([good]) == 0
    assert check_files([good, bad]) == 1
