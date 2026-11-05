"""Simulated devices in one SQLite table, and the six tools that act on them."""

import json
import sqlite3
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError

from assistant.config import FAULT_TIMEOUT_S

INITIAL_DEVICES: dict[str, tuple[str, dict]] = {
    "bedroom_fan": ("ceiling_fan", {"speed": 0, "timer_minutes": 0}),
    "living_room_fan": ("ceiling_fan", {"speed": 0, "timer_minutes": 0}),
    "front_door_lock": ("smart_lock", {"locked": True}),
    "kitchen_purifier": ("water_purifier", {"filter_life_pct": 72, "status": "ok"}),
}

TOOL_GUIDE = """Devices: bedroom_fan and living_room_fan (ceiling fans), front_door_lock (smart lock),
kitchen_purifier (water purifier, read-only).
Tools:
- list_devices(): list all device ids and types.
- get_device_status(device_id): read the current state of one device.
- set_fan_speed(device_id, speed): fans only, speed is an integer 0 to 5, 0 means off.
- set_fan_timer(device_id, minutes): fans only, minutes is an integer 0 to 480, 0 cancels the timer.
- lock_door(device_id): locks only.
- unlock_door(device_id, confirm): locks only, confirm must be true or false.
Reply with JSON only. To answer: {"type": "answer", "text": "..."}.
To use a tool: {"type": "tool_call", "tool": "<name>", "arguments": {...}}.
Examples:
User: Set the living room fan to speed 4
{"type": "tool_call", "tool": "set_fan_speed", "arguments": {"device_id": "living_room_fan", "speed": 4}}
User: How do I clean the fan blades?
{"type": "answer", "text": "Switch the fan off and wipe the blades with a soft, damp cloth."}"""


class NoArgs(BaseModel):
    """Arguments for tools that take none."""


class DeviceArgs(BaseModel):
    """Arguments for tools that act on one device."""

    device_id: str


class FanSpeedArgs(DeviceArgs):
    """Arguments for set_fan_speed."""

    speed: int = Field(ge=0, le=5)


class FanTimerArgs(DeviceArgs):
    """Arguments for set_fan_timer."""

    minutes: int = Field(ge=0, le=480)


class UnlockArgs(DeviceArgs):
    """Arguments for unlock_door."""

    confirm: bool = False


ARG_MODELS: dict[str, type[BaseModel]] = {
    "list_devices": NoArgs,
    "get_device_status": DeviceArgs,
    "set_fan_speed": FanSpeedArgs,
    "set_fan_timer": FanTimerArgs,
    "lock_door": DeviceArgs,
    "unlock_door": UnlockArgs,
}
TOOL_NAMES = list(ARG_MODELS)
REQUIRED_TYPE = {
    "set_fan_speed": "ceiling_fan",
    "set_fan_timer": "ceiling_fan",
    "lock_door": "smart_lock",
    "unlock_door": "smart_lock",
}


@dataclass
class ToolOutcome:
    """Result of one tool call: status is 'ok', 'rejected' (bad request) or 'failed' (device fault)."""

    status: str
    result: dict = field(default_factory=dict)


def fault_outcome(mode: str) -> ToolOutcome:
    """What a faulty device returns (report 6.5.3); the device state never changes."""
    if mode == "offline":
        return ToolOutcome("failed", {"error": "DEVICE_OFFLINE", "message": "The device cannot be reached"})
    if mode == "timeout":
        time.sleep(FAULT_TIMEOUT_S)
        return ToolOutcome(
            "failed", {"error": "TOOL_TIMEOUT", "message": "The device did not answer in time"}
        )
    if mode == "error500":
        return ToolOutcome(
            "failed", {"error": "INTERNAL_ERROR", "message": "The device reported an internal error"}
        )
    return ToolOutcome("ok", {"payload": "%#@!\x00{unreadable"})  # bad_payload: malformed, no state


def rejected(code: str, message: str) -> ToolOutcome:
    """Build a rejected outcome with an error code."""
    return ToolOutcome("rejected", {"error": code, "message": message})


def reset_devices(db_path: str) -> None:
    """Create the devices table and put every device back to its initial state."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS devices "
            "(device_id TEXT PRIMARY KEY, type TEXT NOT NULL, state_json TEXT NOT NULL)"
        )
        conn.execute("DELETE FROM devices")
        conn.executemany(
            "INSERT INTO devices VALUES (?, ?, ?)",
            [(d, t, json.dumps(s)) for d, (t, s) in INITIAL_DEVICES.items()],
        )


def read_devices(db_path: str) -> dict[str, dict]:
    """Return {device_id: {'type': ..., 'state': {...}}} for all devices."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT device_id, type, state_json FROM devices ORDER BY device_id")
        return {d: {"type": t, "state": json.loads(s)} for d, t, s in rows}


def write_state(db_path: str, device_id: str, state: dict) -> None:
    """Store the new state of one device."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE devices SET state_json = ? WHERE device_id = ?", (json.dumps(state), device_id))


def parse_args(name: str, arguments: dict, validate: bool) -> BaseModel | ToolOutcome:
    """Parse arguments with the tool's Pydantic model, or skip checks when validate is off."""
    model = ARG_MODELS[name]
    if not validate:
        # model_construct skips validation, so out-of-range values pass through (fault F4).
        return model.model_construct(**arguments)
    try:
        return model.model_validate(arguments)
    except ValidationError as err:
        return rejected("INVALID_ARGUMENTS", "; ".join(e["msg"] for e in err.errors()))


def run_tool(
    db_path: str,
    name: str,
    arguments: dict,
    validate: bool = True,
    require_confirmation: bool = True,
    faults: dict[str, str] | None = None,
) -> ToolOutcome:
    """Run one tool against the device table and return its outcome."""
    if name not in ARG_MODELS:
        return rejected("UNKNOWN_TOOL", f"No tool named {name}")
    args = parse_args(name, arguments, validate)
    if isinstance(args, ToolOutcome):
        return args
    devices = read_devices(db_path)
    if name == "list_devices":
        return ToolOutcome("ok", {"devices": {d: v["type"] for d, v in devices.items()}})

    device_id = getattr(args, "device_id", None)
    if device_id not in devices:
        return rejected("UNKNOWN_DEVICE", f"No device named {device_id}")
    device = devices[device_id]
    if validate and name in REQUIRED_TYPE and device["type"] != REQUIRED_TYPE[name]:
        return rejected("UNSUPPORTED_DEVICE", f"{name} does not work on a {device['type']}")
    if name == "unlock_door" and require_confirmation and getattr(args, "confirm", False) is not True:
        return rejected("CONFIRMATION_REQUIRED", "Ask the user to confirm before unlocking")
    if faults and device_id in faults:  # test-only fault injection (/admin/faults)
        return fault_outcome(faults[device_id])

    state = dict(device["state"])
    if name == "set_fan_speed":
        state["speed"] = getattr(args, "speed", None)
    elif name == "set_fan_timer":
        state["timer_minutes"] = getattr(args, "minutes", None)
    elif name == "lock_door":
        state["locked"] = True
    elif name == "unlock_door":
        state["locked"] = False
    if state != device["state"]:
        write_state(db_path, device_id, state)
    return ToolOutcome("ok", {"device_id": device_id, "state": state})
