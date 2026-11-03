"""Unit tests for the golden-set validator: the real set passes, broken copies fail."""

import copy

import pytest

from qa_gate.datasets.loader import load_raw
from qa_gate.datasets.validator import dataset_hash, validate

pytestmark = pytest.mark.unit

RAW = load_raw()


def edited(change) -> list[dict]:
    """Return a deep copy of the golden set after applying change(cases)."""
    cases = copy.deepcopy(RAW)
    change(cases)
    return cases


def by_id(cases: list[dict], case_id: str) -> dict:
    """Find one case by id."""
    return next(c for c in cases if c["id"] == case_id)


@pytest.mark.parametrize(
    "change",
    [
        lambda cases: None,
        lambda cases: cases.reverse(),
        lambda cases: by_id(cases, "G-FAC-004").update(question="Which motor type is used in the Vayu F1?"),
        lambda cases: (
            by_id(cases, "G-FAC-004")["tags"].append("motor")
            if "tags" in by_id(cases, "G-FAC-004")
            else by_id(cases, "G-FAC-004").update(tags=["motor"])
        ),
        lambda cases: by_id(cases, "G-NUM-002").update(
            reference_answer="Up to 480 minutes, which is 8 hours."
        ),
        lambda cases: by_id(cases, "G-UNA-002")["absent_terms"].append("Siri"),
    ],
)
def test_valid_sets_pass(change):
    assert validate(edited(change)) == []


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda cases: by_id(cases, "G-FAC-004").update(id="G-FAC-001"), "duplicate id"),
        (lambda cases: by_id(cases, "G-FAC-004").pop("question"), "question"),
        (lambda cases: by_id(cases, "G-FAC-004").update(difficulty="medium"), "difficulty counts"),
        (lambda cases: cases.pop(), "category counts"),
        (lambda cases: by_id(cases, "G-FAC-004").update(required_facts=[]), "needs required_facts"),
        (lambda cases: by_id(cases, "G-FAC-004").update(source_chunks=[]), "needs required_facts"),
        (
            lambda cases: by_id(cases, "G-FAC-004").update(source_chunks=["vayu_f1_fan#colour"]),
            "unknown source",
        ),
        (
            lambda cases: by_id(cases, "G-UNA-002").update(absent_terms=["timer"]),
            "appears in the knowledge base",
        ),
        (
            lambda cases: by_id(cases, "G-FAC-004").update(
                question="How many speed levels does the Vayu F1 fan have??"
            ),
            "near-duplicates",
        ),
        (lambda cases: by_id(cases, "G-FAC-004").update(smoke=True), "smoke counts"),
        (
            lambda cases: by_id(cases, "G-FAC-004").update(expected_behavior="refuse_unknown"),
            "expected_behavior",
        ),
        (lambda cases: by_id(cases, "G-FAC-004").update(colour="blue"), "colour"),
    ],
)
def test_broken_sets_fail(change, message):
    errors = validate(edited(change))
    assert any(message in e for e in errors), errors


def test_hash_ignores_key_order_but_not_content():
    reordered = [dict(reversed(list(c.items()))) for c in RAW]
    assert dataset_hash(reordered) == dataset_hash(RAW)
    assert dataset_hash(edited(lambda cases: by_id(cases, "G-FAC-004").update(question="x"))) != dataset_hash(
        RAW
    )
