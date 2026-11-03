"""Unit tests for the deterministic scorers: at least 5 passing and 5 failing inputs each."""

import pytest

from qa_gate.datasets.loader import load_golden
from qa_gate.metrics.deterministic import (
    fact_coverage,
    forbidden_claim_check,
    normalise,
    refusal_detected,
    response_valid,
    retrieval_hit,
    stability,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("The longest timer is 8 hours.", "the longest timer is 480 minutes"),
        ("an 8-hour timer", "an 480 minutes timer"),
        ("1.5 hrs", "90 minutes"),
        ("speeds 0-5", "speeds 0 to 5"),
        ("four AA batteries", "4 aa batteries"),
        ("I don't know", "i do not know"),
        ("It cannot charge", "it can not charge"),
        ("2,000 mg", "2000 mg"),
        ("fill 1.0 litre", "fill 1 litres"),
        ("20% left", "20 percent left"),
    ],
)
def test_normalise(raw, expected):
    assert normalise(raw) == expected


# ---- M2 fact coverage ----
@pytest.mark.parametrize(
    "facts, answer",
    [
        (["480 minutes or 8 hours"], "You can set it for up to eight hours."),
        (["480 minutes or 8 hours"], "The maximum is 480 mins."),
        (["0 to 5"], "It has speeds 0-5."),
        (["7 litres"], "The tank holds 7 L."),
        (["not charge"], "The power bank doesn't charge the batteries."),
        (["CR2032", "30 seconds"], "Use a CR2032 cell and wait 30 secs."),
        ([], "Anything at all."),
    ],
)
def test_fact_coverage_full(facts, answer):
    assert fact_coverage(facts, answer) == 1.0


@pytest.mark.parametrize(
    "facts, answer",
    [
        (["480 minutes or 8 hours"], "The longest timer is 12 hours."),
        (["4 litres"], "The chutney jar holds 0.4 litres."),
        (["20"], "It stores 200 codes."),
        (["CR2032"], "It uses two AAA batteries."),
        (["0.4 litres"], "The jar holds 4 litres."),
        (["dimmer"], "Check the dimmers."),
    ],
)
def test_fact_coverage_missing(facts, answer):
    assert fact_coverage(facts, answer) == 0.0


def test_fact_coverage_is_a_share():
    assert fact_coverage(["overload", "10 minutes"], "The overload protector tripped.") == 0.5


# ---- M3 forbidden claims ----
@pytest.mark.parametrize(
    "claims, answer",
    [
        (["12 hours"], "The longest timer is 8 hours."),
        (["no limit"], "The limit is 480 minutes."),
        (["AAA"], "It uses one CR2032 cell."),
        (["works with Alexa"], "I don't have that information."),
        (["4 digits"], "A PIN has 6 to 8 digits."),
        ([], "Anything."),
    ],
)
def test_forbidden_claims_absent(claims, answer):
    assert forbidden_claim_check(claims, answer) == 1


@pytest.mark.parametrize(
    "claims, answer",
    [
        (["12 hours"], "The longest timer is 12 hours."),
        (["12 hours"], "You can set up to 720 minutes."),
        (["works with Alexa"], "Yes, it WORKS with Alexa."),
        (["no limit"], "There is no limit."),
        (["rupees", "Rs"], "It costs 12,000 rupees."),
    ],
)
def test_forbidden_claims_present(claims, answer):
    assert forbidden_claim_check(claims, answer) == 0


def test_forbidden_claim_needs_whole_words():
    assert forbidden_claim_check(["Rs"], "Check the filters first.") == 1


def test_known_limit_refusal_that_echoes_a_claim_fails_m3():
    """Documented weakness: phrase matching cannot see negation, so an echoing refusal fails M3."""
    answer = "I don't have information on whether the Kavach L1 is compatible with Alexa."
    assert forbidden_claim_check(["compatible with Alexa"], answer) == 0


# ---- M4 refusal detection ----
@pytest.mark.parametrize(
    "answer",
    [
        "I don't have that information.",
        "Sorry, I do not know.",
        "That is not mentioned in the manuals.",
        "The manuals don't say whether it supports that.",
        "I can't find anything about NFC in the context.",
        "That detail is not specified.",
    ],
)
def test_refusal_detected(answer):
    assert refusal_detected(answer)


@pytest.mark.parametrize(
    "answer",
    [
        "The longest timer is 480 minutes.",
        "Yes, it works with Alexa.",
        "It costs about 12,000 rupees.",
        "Use speed 3 for a medium bedroom.",
        "Knowledge is power.",
    ],
)
def test_refusal_not_detected(answer):
    assert not refusal_detected(answer)


# ---- M5 retrieval hit and reciprocal rank ----
@pytest.mark.parametrize(
    "sources, retrieved, rr",
    [
        (["vayu_f1_fan#timer"], ["vayu_f1_fan#timer", "a#b", "c#d"], 1.0),
        (["vayu_f1_fan#timer"], ["a#b", "vayu_f1_fan#timer", "c#d"], 0.5),
        (["vayu_f1_fan#timer"], ["a#b", "c#d", "vayu_f1_fan#timer"], 1 / 3),
        (["vayu_f1_fan#troubleshooting"], ["vayu_f1_fan#troubleshooting/2"], 1.0),
        (["a#x", "vayu_f1_fan#modes"], ["b#y", "vayu_f1_fan#modes"], 0.5),
    ],
)
def test_retrieval_hit(sources, retrieved, rr):
    assert retrieval_hit(sources, retrieved) == (True, pytest.approx(rr))


@pytest.mark.parametrize(
    "sources, retrieved",
    [
        (["vayu_f1_fan#timer"], ["vayu_f1_fan#modes", "a#b", "c#d"]),
        (["vayu_f1_fan#timer"], []),
        (["vayu_f1_fan#timer"], ["vayu_f1_fan#timer_extra"]),
        (["kavach_l1_lock#battery"], ["jal_p2_purifier#battery"]),
        (["vayu_f1_fan#troubleshooting"], ["mixo_m3_grinder#troubleshooting/1"]),
    ],
)
def test_retrieval_miss(sources, retrieved):
    assert retrieval_hit(sources, retrieved) == (False, 0.0)


def test_retrieval_hit_needs_sources():
    with pytest.raises(ValueError):
        retrieval_hit([], ["a#b"])


# ---- M1 response validity ----
VALID_BODY = {
    "reply": "Hi",
    "tool_calls": [{"tool": "list_devices", "arguments": {}, "result": {}, "status": "ok"}],
    "retrieved": [{"chunk_id": "a#b", "score": 0.5, "text": "t"}],
    "metadata": {
        "version": "v1.0",
        "model": "m",
        "latency_ms": 5,
        "prompt_tokens": 1,
        "completion_tokens": 1,
    },
}


@pytest.mark.parametrize(
    "body",
    [
        VALID_BODY,
        {**VALID_BODY, "tool_calls": []},
        {**VALID_BODY, "retrieved": []},
        {**VALID_BODY, "reply": ""},
        {**VALID_BODY, "extra_field": 1},
    ],
)
def test_response_valid(body):
    assert response_valid(200, body)


@pytest.mark.parametrize(
    "status, body",
    [
        (502, {"detail": "invalid_response"}),
        (422, VALID_BODY),
        (200, {k: v for k, v in VALID_BODY.items() if k != "metadata"}),
        (200, {**VALID_BODY, "reply": None}),
        (200, {**VALID_BODY, "retrieved": [{"chunk_id": "a#b"}]}),
        (200, "not json"),
    ],
)
def test_response_invalid(status, body):
    assert not response_valid(status, body)


# ---- M11 stability ----
@pytest.mark.parametrize(
    "outcomes, expected",
    [
        ([True, True, True], 1.0),
        ([False, False, False], 1.0),
        ([True, True, False], 2 / 3),
        ([False, True, False], 2 / 3),
        ([True], 1.0),
    ],
)
def test_stability(outcomes, expected):
    assert stability(outcomes) == pytest.approx(expected)


def test_stability_needs_outcomes():
    with pytest.raises(ValueError):
        stability([])


# ---- The dataset itself ----
def test_every_reference_answer_passes_its_own_checks():
    """A reference answer that fails its own facts or forbidden claims means a broken case."""
    for case in load_golden():
        assert fact_coverage(case.required_facts, case.reference_answer) == 1.0, case.id
        assert forbidden_claim_check(case.forbidden_claims, case.reference_answer) == 1, case.id
        if case.expected_behavior == "refuse_unknown":
            assert refusal_detected(case.reference_answer), case.id


ECHO_REFUSALS = [
    "I don't have information on whether this is true: {q}",
    "Sorry, the manuals do not say. You asked: {q}",
    "That is not mentioned in the manual, so I can't answer '{q}'",
]


@pytest.mark.parametrize("template", ECHO_REFUSALS)
def test_refusals_that_echo_the_question_pass_m3(template):
    """Forbidden claims for unanswerable cases must be affirmative, so an echoing refusal passes."""
    for case in load_golden():
        if case.expected_behavior == "refuse_unknown":
            answer = template.format(q=case.question)
            assert refusal_detected(answer), case.id
            assert forbidden_claim_check(case.forbidden_claims, answer) == 1, case.id


# ---- M4 Phase 2.1 refusal variants ----
@pytest.mark.parametrize(
    "answer",
    [
        "I do not have anything on that topic.",
        "The context doesn't contain that detail.",
        "The manuals don't provide a figure for this.",
        "There are no details about that in the documents.",
        "That information is not provided.",
        "It is not available in the documentation I have.",
        "I couldn't find that anywhere.",
        "There is no mention of that feature.",
    ],
)
def test_refusal_variants_detected(answer):
    assert refusal_detected(answer)


@pytest.mark.parametrize(
    "answer",
    [
        "Set the timer in steps of 30 minutes, up to 480 minutes.",
        "The lock has a 1-year warranty.",
        "Wipe the blades with a soft, damp cloth.",
        "The tank holds 7 litres and refills in about 30 minutes.",
        "Yes, the mechanical key always works, even with empty batteries.",
        "Hold the power button for 5 seconds until the fan beeps twice.",
    ],
)
def test_refusal_variants_not_flagged_on_normal_answers(answer):
    assert not refusal_detected(answer)


def test_does_not_include_is_not_a_refusal_phrase():
    """Removed in Phase 2.1b: 'does not include' appears in legitimate answers about exclusions."""
    assert not refusal_detected("The warranty does not include consumable parts such as filters.")
