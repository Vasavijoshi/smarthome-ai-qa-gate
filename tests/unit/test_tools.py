"""Unit tests for the tool layer and device state."""

import pytest

from assistant.tools import INITIAL_DEVICES, read_devices, run_tool

pytestmark = pytest.mark.unit


def state(db_path: str, device_id: str) -> dict:
    """Read one device's state."""
    return read_devices(db_path)[device_id]["state"]


@pytest.mark.parametrize(
    "name, args, device, expected",
    [
        ("set_fan_speed", {"device_id": "bedroom_fan", "speed": 3}, "bedroom_fan", {"speed": 3}),
        ("set_fan_speed", {"device_id": "living_room_fan", "speed": 5}, "living_room_fan", {"speed": 5}),
        (
            "set_fan_timer",
            {"device_id": "bedroom_fan", "minutes": 480},
            "bedroom_fan",
            {"timer_minutes": 480},
        ),
        ("lock_door", {"device_id": "front_door_lock"}, "front_door_lock", {"locked": True}),
        (
            "unlock_door",
            {"device_id": "front_door_lock", "confirm": True},
            "front_door_lock",
            {"locked": False},
        ),
    ],
)
def test_valid_calls_change_state(db_path, name, args, device, expected):
    outcome = run_tool(db_path, name, args)
    assert outcome.status == "ok"
    assert expected.items() <= state(db_path, device).items()


@pytest.mark.parametrize(
    "name, args, code",
    [
        ("set_fan_speed", {"device_id": "bedroom_fan", "speed": 7}, "INVALID_ARGUMENTS"),
        ("set_fan_speed", {"device_id": "bedroom_fan", "speed": -2}, "INVALID_ARGUMENTS"),
        ("set_fan_timer", {"device_id": "bedroom_fan", "minutes": 1000}, "INVALID_ARGUMENTS"),
        ("set_fan_speed", {"device_id": "front_door_lock", "speed": 2}, "UNSUPPORTED_DEVICE"),
        ("set_fan_speed", {"device_id": "garage_fan", "speed": 2}, "UNKNOWN_DEVICE"),
        ("unlock_door", {"device_id": "front_door_lock"}, "CONFIRMATION_REQUIRED"),
        ("unlock_door", {"device_id": "front_door_lock", "confirm": False}, "CONFIRMATION_REQUIRED"),
        ("change_colour", {"device_id": "bedroom_fan"}, "UNKNOWN_TOOL"),
    ],
)
def test_invalid_calls_are_rejected_and_change_nothing(db_path, name, args, code):
    before = read_devices(db_path)
    outcome = run_tool(db_path, name, args)
    assert outcome.status == "rejected"
    assert outcome.result["error"] == code
    assert read_devices(db_path) == before


def test_read_only_tools_return_state(db_path):
    assert run_tool(db_path, "list_devices", {}).result["devices"]["front_door_lock"] == "smart_lock"
    status = run_tool(db_path, "get_device_status", {"device_id": "kitchen_purifier"})
    assert status.result["state"] == INITIAL_DEVICES["kitchen_purifier"][1]


def test_validation_off_stores_out_of_range_value(db_path):
    """Fault F4: with validation off, speed 7 reaches the device table."""
    run_tool(db_path, "set_fan_speed", {"device_id": "bedroom_fan", "speed": 7}, validate=False)
    assert state(db_path, "bedroom_fan")["speed"] == 7


def test_confirmation_off_unlocks_without_confirm(db_path):
    """Fault F4: with the confirmation check off, unlock works without confirm."""
    outcome = run_tool(db_path, "unlock_door", {"device_id": "front_door_lock"}, require_confirmation=False)
    assert outcome.status == "ok"
    assert state(db_path, "front_door_lock")["locked"] is False
