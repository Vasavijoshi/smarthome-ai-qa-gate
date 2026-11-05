"""Unit tests for the tool-case and scenario validators: the drafts pass, broken copies fail."""

import copy

import pytest

from qa_gate.datasets.loader import SCENARIOS_PATH, TOOL_CASES_PATH, load_raw
from qa_gate.datasets.validator import validate_scenarios, validate_tool_cases

pytestmark = pytest.mark.unit

CASES = load_raw(TOOL_CASES_PATH)
SCENARIOS = load_raw(SCENARIOS_PATH)


def edited(items: list[dict], change) -> list[dict]:
    """A deep copy of the items after change(items)."""
    copied = copy.deepcopy(items)
    change(copied)
    return copied


def by_id(items: list[dict], item_id: str) -> dict:
    """Find one item by id."""
    return next(i for i in items if i["id"] == item_id)


def turn(items: list[dict], scenario_id: str, index: int) -> dict:
    """One turn of one scenario."""
    return by_id(items, scenario_id)["turns"][index]


@pytest.mark.parametrize(
    "change",
    [
        lambda c: None,
        lambda c: c.reverse(),
        lambda c: by_id(c, "T-COR-001").update(turns=["Please set the bedroom fan to speed 3"]),
        lambda c: by_id(c, "T-NTL-002").update(reply_must_mention_any=["480"]),
        lambda c: by_id(c, "T-FLT-003")["fault"].update(mode="error500"),
    ],
)
def test_valid_tool_case_sets_pass(change):
    assert validate_tool_cases(edited(CASES, change)) == []


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda c: by_id(c, "T-COR-002").update(id="T-COR-001"), "duplicate id"),
        (lambda c: c.pop(), "group counts"),
        (lambda c: by_id(c, "T-AMB-001").update(expect_tool_call="yes"), "must use expect_tool_call no"),
        (lambda c: by_id(c, "T-COR-001").update(expect_tool="set_fan_colour"), "expect_tool must be one of"),
        (
            lambda c: by_id(c, "T-COR-001").update(expect_args={"device_id": "bedroom_fan", "speed": 9}),
            "valid arguments",
        ),
        (lambda c: by_id(c, "T-COR-001").update(expect_state={"garage_fan": {"speed": 3}}), "unknown device"),
        (lambda c: by_id(c, "T-COR-001").update(expect_state={"bedroom_fan": {"speed": 8}}), "is not legal"),
        (lambda c: by_id(c, "T-FLT-001").pop("fault"), "a fault is required"),
        (lambda c: by_id(c, "T-FLT-001").update(max_tool_calls=5), "max_tool_calls 2"),
        (lambda c: by_id(c, "T-FLT-001")["fault"].update(device="bedroom_fan"), "target the faulty device"),
        (lambda c: by_id(c, "T-FLT-001")["fault"].update(mode="melted"), "fault.mode"),
        (
            lambda c: by_id(c, "T-AMB-001").update(expect_tool="set_fan_speed"),
            "only for expect_tool_call 'yes'",
        ),
    ],
)
def test_broken_tool_case_sets_fail(change, message):
    errors = validate_tool_cases(edited(CASES, change))
    assert any(message in e for e in errors), errors


@pytest.mark.parametrize(
    "change",
    [
        lambda s: None,
        lambda s: s.reverse(),
        lambda s: turn(s, "S-01", 1).update(user="It is still too slow for me."),
        lambda s: turn(s, "S-06", 0).update(must_mention_any=["480 minutes"]),
        lambda s: turn(s, "S-08", 0)["fault_before"].update(mode="timeout"),
    ],
)
def test_valid_scenario_sets_pass(change):
    assert validate_scenarios(edited(SCENARIOS, change)) == []


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda s: by_id(s, "S-01")["turns"].pop(), "turn counts"),
        (lambda s: s.pop(), "turn counts"),
        (lambda s: turn(s, "S-01", 0).update(expect_tool="set_fan_colour"), "unknown tool"),
        (lambda s: turn(s, "S-01", 0).update(expect_state={"bedroom_fan": {"speed": 6}}), "is not legal"),
        (
            lambda s: turn(s, "S-01", 0).update(expect_state={"bedroom_fan": {"colour": "blue"}}),
            "unknown field",
        ),
        (lambda s: turn(s, "S-08", 0)["fault_before"].update(device="garage_fan"), "unknown device"),
        (lambda s: turn(s, "S-02", 0).update(user="   "), "empty user turn"),
        (lambda s: turn(s, "S-02", 0).update(colour="red"), "colour"),
    ],
)
def test_broken_scenario_sets_fail(change, message):
    errors = validate_scenarios(edited(SCENARIOS, change))
    assert any(message in e for e in errors), errors
