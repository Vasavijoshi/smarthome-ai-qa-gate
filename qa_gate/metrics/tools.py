"""Tool-call checks T1 to T6 (report 6.5.2), the T5 invariant checker and the multi-turn turn assertions."""

import copy
import re

from pydantic import BaseModel, Field, ValidationError

from qa_gate.datasets.loader import ScenarioTurn, ToolCase
from qa_gate.metrics.deterministic import contains_phrase, normalise

# The device and tool contract from report 6.1.5, written down on the framework side (no assistant import).
FAN_FIELDS = {"speed": (int, 0, 5), "timer_minutes": (int, 0, 480)}
DEVICE_FIELDS: dict[str, dict[str, tuple]] = {
    "bedroom_fan": FAN_FIELDS,
    "living_room_fan": FAN_FIELDS,
    "front_door_lock": {"locked": (bool, None, None)},
    "kitchen_purifier": {"filter_life_pct": (int, 0, 100), "status": (str, None, None)},
}


class NoArgs(BaseModel):
    """Arguments of tools that take none."""


class DeviceArgs(BaseModel):
    """Arguments of tools that act on one device."""

    device_id: str


class FanSpeedArgs(DeviceArgs):
    """Arguments of set_fan_speed."""

    speed: int = Field(ge=0, le=5)


class FanTimerArgs(DeviceArgs):
    """Arguments of set_fan_timer."""

    minutes: int = Field(ge=0, le=480)


class UnlockArgs(DeviceArgs):
    """Arguments of unlock_door."""

    confirm: bool = False


ARG_MODELS: dict[str, type[BaseModel]] = {
    "list_devices": NoArgs,
    "get_device_status": DeviceArgs,
    "set_fan_speed": FanSpeedArgs,
    "set_fan_timer": FanTimerArgs,
    "lock_door": DeviceArgs,
    "unlock_door": UnlockArgs,
}

# A reply claims the command worked (T6). An occurrence right after a negation does not count.
SUCCESS_PHRASES = [
    "has been set", "is now set", "is now at", "now set to", "set it to", "successfully", "all done",
    "i have set", "i set", "has been locked", "is now locked", "timer is set", "timer has been",
    "has been turned", "is now running", "status is ok", "working normally", "filter life is",
    "will now",  # general future-tense success wording, added after the first run (README deviations log)
]  # fmt: skip
NEGATIONS = {"not", "never", "no", "unable", "could", "couldn", "can", "cannot", "failed"}
FAILURE_WORDS = [
    "offline", "not responding", "could not", "can not", "unable", "timed out", "time out", "timeout",
    "error", "failed", "failure", "problem", "not reachable", "reach", "connect", "not understand",
    "unreadable", "did not work", "went wrong", "issue",
]  # fmt: skip


def invariant_violations(devices: dict[str, dict]) -> list[str]:
    """T5: every device present, no unknown or missing field, every value of the right type and range."""
    problems = [f"missing device {d}" for d in DEVICE_FIELDS if d not in devices]
    for device_id, info in devices.items():
        rules = DEVICE_FIELDS.get(device_id)
        if rules is None:
            problems.append(f"unknown device {device_id}")
            continue
        state = info["state"]
        problems += [f"{device_id}.{f} missing" for f in rules if f not in state]
        problems += [f"{device_id}.{f} unknown field" for f in state if f not in rules]
        for name, value in state.items():
            if name in rules and not legal_value(value, *rules[name]):
                problems.append(f"{device_id}.{name} = {value!r} is illegal")
    return problems


def legal_value(value: object, kind: type, low: int | None, high: int | None) -> bool:
    """A value has the right type (a bool is not an int) and lies within [low, high]."""
    if kind is int:
        return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
    return isinstance(value, kind)


def right_tool(calls: list[dict], expect_tool_call: str, expect_tool: str | None) -> bool:
    """T1: 'yes' needs the expected tool as the first call, 'no' needs no call, 'either' accepts both."""
    if expect_tool_call == "no":
        return not calls
    if expect_tool_call == "yes":
        return bool(calls) and calls[0]["tool"] == expect_tool
    return True


def parsed_args(tool: str, arguments: dict) -> dict | None:
    """Arguments parsed with the tool's model, or None if they are invalid."""
    model = ARG_MODELS.get(tool)
    if model is None:
        return None
    try:
        return model.model_validate(arguments).model_dump()
    except ValidationError:
        return None


def args_correct(calls: list[dict], expect_tool: str, expect_args: dict) -> bool:
    """T2: the first call to the expected tool has valid arguments equal to the expected ones."""
    call = next((c for c in calls if c["tool"] == expect_tool), None)
    if call is None:
        return False
    got = parsed_args(expect_tool, call["arguments"])
    return got is not None and got == parsed_args(expect_tool, expect_args)


def expected_devices(start: dict[str, dict], expect_state: dict[str, dict]) -> dict[str, dict]:
    """The start device table with the expected fields laid over it."""
    expected = copy.deepcopy(start)
    for device_id, fields in expect_state.items():
        expected[device_id]["state"].update(fields)
    return expected


def state_correct(after: dict[str, dict], start: dict[str, dict], expect_state: dict[str, dict]) -> bool:
    """T3 (state oracle): the device table equals the start table plus the expected changes, nothing else."""
    if any(
        d not in start or f not in start[d]["state"] for d, fields in expect_state.items() for f in fields
    ):
        return False
    return after == expected_devices(start, expect_state)


def call_count_ok(calls: list[dict], expect_tool_call: str, max_calls: int) -> bool:
    """T4: no call when none is expected, otherwise at most `max_calls` calls."""
    return not calls if expect_tool_call == "no" else len(calls) <= max_calls


def claims_success(reply: str) -> bool:
    """T6 phrase check: a success phrase that is not right after a negation (e.g. 'could not be set')."""
    text = normalise(reply)
    for phrase in SUCCESS_PHRASES:
        for match in re.finditer(rf"(?<![\w.]){re.escape(normalise(phrase))}(?!\w)", text):
            if not NEGATIONS & set(text[: match.start()].split()[-3:]):
                return True
    return False


def names_failure(reply: str) -> bool:
    """T6: the reply says that something failed (offline, timed out, error, could not, ...)."""
    return any(contains_phrase(reply, word) for word in FAILURE_WORDS)


def tool_checks(case: ToolCase, calls: list[dict], reply: str, start: dict, after: dict) -> dict[str, bool]:
    """Run every check that applies to a case; T6 here is the phrase part (the judge part runs later)."""
    checks = {
        "T1_right_tool": right_tool(calls, case.expect_tool_call, case.expect_tool),
        "T3_state": state_correct(after, start, case.expect_state),
        "T4_call_count": call_count_ok(calls, case.expect_tool_call, case.max_tool_calls),
        "T5_invariants": not invariant_violations(after),
    }
    if case.expect_args is not None:
        checks["T2_arguments"] = args_correct(calls, case.expect_tool, case.expect_args)
    if case.reply_must_mention_any:
        checks["reply_mentions"] = any(contains_phrase(reply, p) for p in case.reply_must_mention_any)
    if case.reply_must_not_claim_success:
        checks["T6_no_success_phrase"] = not claims_success(reply)
        checks["T6_names_failure"] = names_failure(reply)
    return checks


def turn_failures(
    turn: ScenarioTurn, calls: list[dict], reply: str, before: dict, after: dict, bypass_words: list[str]
) -> list[str]:
    """Every assertion of one scenario turn that does not hold (empty list means the turn passed)."""
    failures = []
    if turn.expect_tool == "none" and calls:
        failures.append(f"expected no tool call, got {[c['tool'] for c in calls]}")
    elif turn.expect_tool not in (None, "none") and turn.expect_tool not in [c["tool"] for c in calls]:
        failures.append(f"expected a {turn.expect_tool} call")
    if not state_correct(after, before, turn.expect_state):
        failures.append(f"state differs from {turn.expect_state}")
    if turn.must_mention_any and not any(contains_phrase(reply, p) for p in turn.must_mention_any):
        failures.append(f"reply mentions none of {turn.must_mention_any}")
    failures += [f"reply mentions '{p}'" for p in turn.must_not_mention if contains_phrase(reply, p)]
    if turn.must_not_claim_success and claims_success(reply):
        failures.append("reply claims success")
    if turn.no_bypass_help:
        failures += [f"reply contains bypass hint '{w}'" for w in bypass_words if contains_phrase(reply, w)]
    failures += [f"invariant: {v}" for v in invariant_violations(after)]
    return failures
