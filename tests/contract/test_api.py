"""API checks for Phase 1: answers, tool calls change the device table, versions switch."""

import pytest

from assistant.llm import LLMReply, MockClient

pytestmark = pytest.mark.contract


@pytest.mark.parametrize(
    "question",
    [
        "What is the longest timer on the Vayu F1?",
        "How do I flush the Jal P2 purifier?",
        "What does error E5 mean?",
    ],
)
def test_questions_are_answered_with_retrieved_chunks(client, question):
    response = client.post("/chat", json={"session_id": "s1", "message": question})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"]
    assert len(body["retrieved"]) == 3
    assert body["metadata"]["version"] == "v1.0"


def test_tool_call_changes_device_table(client):
    body = client.post("/chat", json={"session_id": "s1", "message": "Set the bedroom fan to speed 3"}).json()
    assert body["tool_calls"][0]["tool"] == "set_fan_speed"
    assert body["tool_calls"][0]["status"] == "ok"
    assert client.get("/devices").json()["devices"]["bedroom_fan"]["state"]["speed"] == 3


def test_unlock_needs_confirmation_in_a_later_turn(client):
    first = client.post("/chat", json={"session_id": "s1", "message": "Unlock the front door"}).json()
    assert first["tool_calls"][0]["status"] == "rejected"
    assert client.get("/devices").json()["devices"]["front_door_lock"]["state"]["locked"] is True
    client.post("/chat", json={"session_id": "s1", "message": "yes"})
    assert client.get("/devices").json()["devices"]["front_door_lock"]["state"]["locked"] is False


@pytest.mark.parametrize(
    "body",
    [
        {"session_id": "s1", "message": ""},
        {"session_id": "s1", "message": "x" * 2001},
        {"message": "hi"},
        {"session_id": "", "message": "hi"},
        {"session_id": "s1", "message": "   "},
    ],
)
def test_bad_requests_return_422(client, body):
    assert client.post("/chat", json=body).status_code == 422


def test_admin_reset_restores_devices(client):
    client.post("/chat", json={"session_id": "s1", "message": "Set the bedroom fan to speed 4"})
    assert client.post("/admin/reset").status_code == 200
    assert client.get("/devices").json()["devices"]["bedroom_fan"]["state"]["speed"] == 0


def test_admin_routes_absent_when_disabled(make_client):
    assert make_client(admin=False).post("/admin/reset").status_code == 404


@pytest.mark.parametrize("version, top_k", [("v1.0", 3), ("v1.1", 3), ("v2.0", 3), ("v3.0", 1), ("v4.0", 3)])
def test_versions_switch_by_config(make_client, version, top_k):
    client = make_client(version)
    assert client.get("/health").json()["version"] == version
    body = client.post("/chat", json={"session_id": "s", "message": "fan timer"}).json()
    assert len(body["retrieved"]) == top_k


@pytest.mark.parametrize("seed", [11, 22, 33, 0, None])
def test_optional_seed_is_accepted(client, seed):
    body = {"session_id": "s1", "message": "What is the longest timer?", "seed": seed}
    assert client.post("/chat", json=body).status_code == 200


@pytest.mark.parametrize("seed", ["abc", 1.5, [11], {"x": 1}, "eleven"])
def test_bad_seed_returns_422(client, seed):
    body = {"session_id": "s1", "message": "What is the longest timer?", "seed": seed}
    assert client.post("/chat", json=body).status_code == 422


class RecordingClient(MockClient):
    """Mock client that remembers the seed of every call."""

    def __init__(self) -> None:
        """Start with no recorded seeds."""
        self.seeds: list[int] = []

    def complete(self, messages: list[dict], schema: dict, seed: int) -> LLMReply:
        """Record the seed, then answer like the mock."""
        self.seeds.append(seed)
        return super().complete(messages, schema, seed)


def test_seed_reaches_llm_and_defaults_to_setting(make_client):
    llm = RecordingClient()
    client = make_client(llm=llm)
    client.post("/chat", json={"session_id": "s1", "message": "What is the timer?", "seed": 22})
    client.post("/chat", json={"session_id": "s1", "message": "What is the timer?"})
    assert llm.seeds == [22, 11]
