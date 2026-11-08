"""Contract checks (report 7.3): session isolation, tool-layer invariants, latency and concurrency smoke."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.contract

LATENCY_LIMIT_S = 2.0  # mock client and TF-IDF only: a slow reply here means a code problem, not a model one
CONCURRENT_REQUESTS = 10


def chat(client, session_id: str, message: str) -> dict:
    """Send one chat message and return the JSON body (status must be 200)."""
    response = client.post("/chat", json={"session_id": session_id, "message": message})
    assert response.status_code == 200
    return response.json()


def lock_state(client) -> bool:
    """Whether the front door is locked."""
    return client.get("/devices").json()["devices"]["front_door_lock"]["state"]["locked"]


def fan_speed(client, device_id: str = "bedroom_fan") -> int:
    """Current speed of a fan."""
    return client.get("/devices").json()["devices"][device_id]["state"]["speed"]


def test_confirmation_in_another_session_does_not_unlock(client):
    chat(client, "session-a", "Unlock the front door")
    chat(client, "session-b", "yes")
    assert lock_state(client) is True


def test_confirmation_in_the_same_session_unlocks(client):
    chat(client, "session-a", "Unlock the front door")
    chat(client, "session-a", "yes")
    assert lock_state(client) is False


def test_history_of_one_session_is_not_sent_with_another(client):
    chat(client, "session-a", "Set the bedroom fan to speed 2")
    body = chat(client, "session-b", "What does error E5 mean?")
    assert body["tool_calls"] == []


@pytest.mark.parametrize("message", ["Set the bedroom fan to speed 9", "Set the bedroom fan to speed -1"])
def test_out_of_range_speed_is_rejected_and_state_unchanged(client, message):
    body = chat(client, "s1", message)
    assert body["tool_calls"][0]["status"] == "rejected"
    assert fan_speed(client) == 0


def test_negative_timer_is_rejected(client):
    body = chat(client, "s1", "Set a -5 min timer on the bedroom fan")
    assert body["tool_calls"][0]["status"] == "rejected"


def test_v4_lets_out_of_range_values_through(make_client):
    client = make_client("v4.0")
    chat(client, "s1", "Set the bedroom fan to speed 9")
    assert fan_speed(client) == 9  # fault F4: server-side validation is off in v4.0


def test_v4_unlocks_without_confirmation(make_client):
    client = make_client("v4.0")
    chat(client, "s1", "Unlock the front door")
    assert lock_state(client) is False  # fault F4: unlock confirmation is off in v4.0


def test_latency_smoke(client):
    messages = ["What does error E5 mean?", "Set the bedroom fan to speed 2", "How do I flush the Jal P2?"]
    for message in messages:
        start = time.perf_counter()
        chat(client, "s1", message)
        assert time.perf_counter() - start < LATENCY_LIMIT_S


def test_concurrent_requests_all_succeed_and_stay_separate(client):
    def ask(i: int) -> dict:
        return chat(client, f"concurrent-{i}", "What is the longest timer on the Vayu F1?")

    with ThreadPoolExecutor(max_workers=CONCURRENT_REQUESTS) as pool:
        bodies = list(pool.map(ask, range(CONCURRENT_REQUESTS)))
    assert len(bodies) == CONCURRENT_REQUESTS
    assert len({body["reply"] for body in bodies}) == 1  # same question, same deterministic answer
    assert all(body["tool_calls"] == [] for body in bodies)
