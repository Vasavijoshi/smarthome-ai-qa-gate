"""Unit tests for the decision model and the deterministic mock client."""

import json

import pytest
from pydantic import ValidationError

from assistant.llm import Decision, MockClient, decision_schema

pytestmark = pytest.mark.unit


def ask(message: str, allow_tools: bool = True, history: list[dict] | None = None) -> dict:
    """Send one message to the mock client and return its decision."""
    messages = [
        {"role": "system", "content": "rules"},
        {
            "role": "system",
            "content": "Context:\n[vayu_f1_fan#timer] Timer: The longest timer is 480 minutes. More.",
        },
        *(history or []),
        {"role": "user", "content": message},
    ]
    return json.loads(MockClient().complete(messages, decision_schema(allow_tools), seed=11).content)


@pytest.mark.parametrize(
    "raw",
    [
        '{"type": "answer", "text": "Hi"}',
        '{"type": "tool_call", "tool": "list_devices"}',
        '{"type": "tool_call", "tool": "set_fan_speed", "arguments": {"device_id": "bedroom_fan", "speed": 2}}',  # noqa: E501
    ],
)
def test_valid_decisions_parse(raw):
    Decision.model_validate_json(raw)


@pytest.mark.parametrize(
    "raw",
    ['{"type": "answer"}', '{"type": "tool_call"}', '{"type": "shout", "text": "x"}', "not json", "{}"],
)
def test_invalid_decisions_fail(raw):
    with pytest.raises(ValidationError):
        Decision.model_validate_json(raw)


@pytest.mark.parametrize(
    "message, tool, args",
    [
        ("Set the bedroom fan to speed 3", "set_fan_speed", {"device_id": "bedroom_fan", "speed": 3}),
        ("turn off the living room fan", "set_fan_speed", {"device_id": "living_room_fan", "speed": 0}),
        (
            "set a timer of 60 minutes on the bedroom fan",
            "set_fan_timer",
            {"device_id": "bedroom_fan", "minutes": 60},
        ),
        ("Lock the front door", "lock_door", {"device_id": "front_door_lock"}),
        ("Please unlock the front door", "unlock_door", {"device_id": "front_door_lock", "confirm": False}),
    ],
)
def test_mock_maps_commands_to_tools(message, tool, args):
    decision = ask(message)
    assert decision["type"] == "tool_call"
    assert (decision["tool"], decision["arguments"]) == (tool, args)


def test_mock_confirms_unlock_after_yes():
    history = [{"role": "assistant", "content": "Do you want me to unlock the front door? Please confirm."}]
    decision = ask("yes", history=history)
    assert decision["arguments"]["confirm"] is True


def test_mock_asks_which_fan_when_ambiguous():
    assert "Which fan" in ask("Turn off the fan")["text"]


def test_mock_answers_questions_from_context():
    assert ask("How do I unlock the door?")["type"] == "answer"
    assert ask("What is the longest timer?")["text"] == "The longest timer is 480 minutes."


def test_mock_never_calls_tool_when_not_allowed():
    assert ask("Set the bedroom fan to speed 3", allow_tools=False)["type"] == "answer"
