"""LLM clients: Ollama for real runs, and a deterministic rule-based mock for CI."""

import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, model_validator

from assistant.config import LLM_TIMEOUT_S, MAX_COMPLETION_TOKENS, TEMPERATURE
from assistant.tools import TOOL_NAMES


class Decision(BaseModel):
    """The model's structured reply: either an answer or one tool call."""

    type: Literal["answer", "tool_call"]
    text: str | None = None
    tool: str | None = None
    arguments: dict = {}

    @model_validator(mode="after")
    def check_fields(self) -> "Decision":
        """An answer needs text, and a tool call needs a tool name."""
        if self.type == "answer" and not self.text:
            raise ValueError("answer without text")
        if self.type == "tool_call" and not self.tool:
            raise ValueError("tool_call without tool")
        return self


@dataclass
class LLMReply:
    """Raw model output plus token counts."""

    content: str
    prompt_tokens: int
    completion_tokens: int


def decision_schema(allow_tools: bool) -> dict:
    """JSON schema for Ollama structured output; without tools only an answer is allowed."""
    if not allow_tools:
        return {
            "type": "object",
            "properties": {"type": {"type": "string", "enum": ["answer"]}, "text": {"type": "string"}},
            "required": ["type", "text"],
        }
    return {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["answer", "tool_call"]},
            "text": {"type": "string"},
            "tool": {"type": "string", "enum": TOOL_NAMES},
            "arguments": {"type": "object"},
        },
        "required": ["type"],
    }


class LLMClient(Protocol):
    """Anything that can complete a chat with a JSON schema."""

    model_name: str

    def complete(self, messages: list[dict], schema: dict, seed: int) -> LLMReply:
        """Return the model's reply to the messages."""
        ...


class OllamaClient:
    """Calls a local Ollama server over its REST API."""

    def __init__(self, url: str, model: str) -> None:
        """Store the server URL and model name."""
        self.url = url
        self.model_name = model

    def complete(self, messages: list[dict], schema: dict, seed: int) -> LLMReply:
        """Send one chat request with structured output and return the reply."""
        options = {"temperature": TEMPERATURE, "seed": seed, "num_predict": MAX_COMPLETION_TOKENS}
        response = httpx.post(
            f"{self.url}/api/chat",
            json={
                "model": self.model_name,
                "messages": messages,
                "stream": False,
                "format": schema,
                "options": options,
            },
            timeout=LLM_TIMEOUT_S,
        )
        response.raise_for_status()
        data = response.json()
        return LLMReply(
            data["message"]["content"], data.get("prompt_eval_count", 0), data.get("eval_count", 0)
        )


DEVICE_PATTERNS = {
    r"\bbedroom\b": "bedroom_fan",
    r"\bliving room\b": "living_room_fan",
    r"\bdoor\b": "front_door_lock",
    r"\bpurifier\b": "kitchen_purifier",
}


def find_device(text: str) -> str | None:
    """Return the first device id whose keyword appears in the text."""
    return next((d for pattern, d in DEVICE_PATTERNS.items() if re.search(pattern, text)), None)


def mock_tool_call(text: str, previous_reply: str) -> dict | None:
    """Map a user message to a tool call with simple keyword rules, or return None."""
    if re.match(r"(how|what|why|when|where|which|does|is)\b", text):
        return None  # questions are answered, not acted on
    device = find_device(text)
    is_fan = device in ("bedroom_fan", "living_room_fan")
    speed = re.search(r"speed\s*(-?\d+)", text)
    minutes = re.search(r"(-?\d+)\s*min", text)
    if re.search(r"\b(yes|confirm)\b", text) and "confirm" in previous_reply and "unlock" in previous_reply:
        return {"tool": "unlock_door", "arguments": {"device_id": "front_door_lock", "confirm": True}}
    if re.search(r"\bunlock\b", text) and device == "front_door_lock":
        return {"tool": "unlock_door", "arguments": {"device_id": device, "confirm": False}}
    if re.search(r"\block (the )?(front )?door\b", text):
        return {"tool": "lock_door", "arguments": {"device_id": "front_door_lock"}}
    if is_fan and speed:
        return {"tool": "set_fan_speed", "arguments": {"device_id": device, "speed": int(speed[1])}}
    if is_fan and re.search(r"turn off|switch off", text):
        return {"tool": "set_fan_speed", "arguments": {"device_id": device, "speed": 0}}
    if is_fan and "timer" in text and minutes:
        return {"tool": "set_fan_timer", "arguments": {"device_id": device, "minutes": int(minutes[1])}}
    if device and re.search(r"status|state", text):
        return {"tool": "get_device_status", "arguments": {"device_id": device}}
    if "list" in text and "device" in text:
        return {"tool": "list_devices", "arguments": {}}
    return None


def mock_answer_from_context(messages: list[dict]) -> str:
    """Answer with the first sentence of the top retrieved chunk, or say there is no information."""
    context = next((m["content"] for m in messages if m["content"].startswith("Context:")), "")
    lines = [line for line in context.splitlines() if line.startswith("[")]
    if not lines:
        return "I don't have that information."
    body = lines[0].split("] ", 1)[1].split(": ", 1)[-1]
    return body.split(". ")[0].rstrip(".") + "."


def mock_tool_reply(result_json: str) -> str:
    """Phrase a reply from a tool result message."""
    outcome = json.loads(result_json)
    result = outcome["result"]
    if outcome["status"] == "ok":
        return f"Done. Result: {json.dumps(result)}"
    if result.get("error") == "CONFIRMATION_REQUIRED":
        return "Do you want me to unlock the front door? Please confirm."
    return f"Sorry, that did not work: {result.get('message', 'unknown error')}."


class MockClient:
    """Deterministic keyword-based client, so CI needs no model download."""

    model_name = "mock"

    def complete(self, messages: list[dict], schema: dict, seed: int) -> LLMReply:
        """Return a decision as JSON, chosen by fixed rules (the seed is ignored)."""
        allow_tools = "tool_call" in schema["properties"]["type"]["enum"]
        last = messages[-1]
        if last["role"] == "tool":
            decision = {"type": "answer", "text": mock_tool_reply(last["content"])}
        else:
            text = last["content"].lower()
            previous = next(
                (m["content"].lower() for m in reversed(messages[:-1]) if m["role"] == "assistant"), ""
            )
            call = mock_tool_call(text, previous) if allow_tools else None
            if call:
                decision = {"type": "tool_call", **call}
            elif "fan" in text and not find_device(text) and re.search(r"turn|set|speed|timer", text):
                decision = {
                    "type": "answer",
                    "text": "Which fan do you mean: the bedroom fan or the living room fan?",
                }
            else:
                decision = {"type": "answer", "text": mock_answer_from_context(messages)}
        content = json.dumps(decision)
        prompt_words = sum(len(m["content"].split()) for m in messages)
        return LLMReply(content, prompt_words, len(content.split()))
