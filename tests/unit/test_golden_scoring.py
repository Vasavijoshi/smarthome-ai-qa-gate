"""Unit tests for per-repeat golden scoring (layers 0 and 1) and the deterministic pass rule."""

import pytest

from qa_gate.datasets.loader import load_golden
from qa_gate.metrics.deterministic import deterministic_pass, score_golden_reply

pytestmark = pytest.mark.unit

CASES = {c.id: c for c in load_golden()}


def body(reply: str, retrieved: tuple[str, ...] = ("vayu_f1_fan#timer",)) -> dict:
    """A valid /chat response body."""
    return {
        "reply": reply,
        "tool_calls": [],
        "retrieved": [{"chunk_id": c, "score": 0.5, "text": "t"} for c in retrieved],
        "metadata": {
            "version": "v1.0",
            "model": "m",
            "latency_ms": 10,
            "prompt_tokens": 1,
            "completion_tokens": 1,
        },
    }


def passes(case_id: str, status: int, response: object) -> bool:
    """Score one reply and apply the deterministic pass rule."""
    case = CASES[case_id]
    return deterministic_pass(case.expected_behavior, score_golden_reply(case, status, response, 0.8))


@pytest.mark.parametrize(
    "case_id, reply",
    [
        ("G-NUM-002", "The longest timer is 8 hours."),
        ("G-NUM-002", "You can set up to 480 minutes."),
        ("G-UNA-001", "I don't have information about Alexa support."),
        ("G-UNA-003", "The manuals do not give a price."),
        ("G-FPR-004", "The jars are not dishwasher safe, so wash them by hand."),
        ("G-FAC-002", "It takes one CR2032 coin cell."),
    ],
)
def test_good_replies_pass(case_id, reply):
    assert passes(case_id, 200, body(reply))


@pytest.mark.parametrize(
    "case_id, status, response",
    [
        ("G-NUM-002", 200, body("The longest timer is 12 hours.")),
        ("G-NUM-002", 200, body("It has a timer.")),
        ("G-NUM-002", 502, {"detail": "invalid_response"}),
        ("G-UNA-001", 200, body("Yes, it works with Alexa.")),
        ("G-UNA-003", 200, body("It costs 12,000 rupees.")),
        ("G-FAC-002", 200, {"reply": "CR2032"}),
    ],
)
def test_bad_replies_fail(case_id, status, response):
    assert not passes(case_id, status, response)


def test_retrieval_rows_only_for_cases_with_sources():
    with_sources = {r.metric for r in score_golden_reply(CASES["G-NUM-002"], 200, body("x"), 0.8)}
    without = {r.metric for r in score_golden_reply(CASES["G-UNA-001"], 200, body("x"), 0.8)}
    assert {"retrieval_hit", "reciprocal_rank"} <= with_sources
    assert not {"retrieval_hit", "reciprocal_rank"} & without


def test_retrieval_hit_scored_against_returned_chunks():
    rows = {
        r.metric: r
        for r in score_golden_reply(CASES["G-NUM-002"], 200, body("x", ("a#b", "vayu_f1_fan#timer")), 0.8)
    }
    assert rows["retrieval_hit"].passed and rows["reciprocal_rank"].score == 0.5


def test_invalid_response_scores_every_layer_one_metric_as_failed_or_empty():
    rows = {r.metric: r for r in score_golden_reply(CASES["G-NUM-002"], 502, {"detail": "x"}, 0.8)}
    assert not rows["response_validity"].passed and rows["fact_coverage"].score == 0.0
    assert "retrieval_hit" not in rows
