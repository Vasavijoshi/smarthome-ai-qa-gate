"""Layer 0 and layer 1 scorers (M1 to M5, M11): fast, free and repeatable checks on a reply."""

import re
from collections import Counter
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from qa_gate.datasets.loader import GoldenCase

NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
    "fifteen": "15", "twenty": "20", "thirty": "30", "sixty": "60",
}  # fmt: skip

# (regex for the unit as written, canonical unit). Order matters: minutes before metres.
UNIT_ALIASES = [
    (r"hours?|hrs?", "hours"),
    (r"minutes?|mins?", "minutes"),
    (r"seconds?|secs?", "seconds"),
    (r"litres?|liters?|l", "litres"),
    (r"metres?|meters?|m", "metres"),
    (r"watts?|w", "watts"),
    (r"days?", "days"),
    (r"months?", "months"),
    (r"years?|yrs?", "years"),
    (r"percent", "percent"),
]

REFUSAL_PHRASES = [
    "i do not have information",
    "i do not have that information",
    "i do not have any information",
    "i do not have details",
    "i do not know",
    "i am not sure",
    "no information",
    "not mentioned in",
    "is not mentioned",
    "not covered in",
    "not in the context",
    "not in the manual",
    "the manuals do not",
    "the manual does not",
    "does not say",
    "does not mention",
    "can not find",
    "not able to find",
    "unable to find",
    "not specified",
    # Phase 2.1: generic English variants, chosen from general wording, not from model outputs.
    "i do not have",
    "does not contain",
    "do not contain",
    "does not provide",
    "do not provide",
    "no details",
    "not provided",
    "not available in",
    "could not find",
    "no mention",
]


def format_number(value: float) -> str:
    """Write 480.0 as '480' and 1.5 as '1.5'."""
    return str(int(value)) if value == int(value) else str(value)


def expand_contractions(text: str) -> str:
    """Turn "don't", "can't" and "cannot" into "do not", "can not"."""
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"\bcan't\b|\bcannot\b", "can not", text)
    text = re.sub(r"\bwon't\b", "will not", text)
    return re.sub(r"n't\b", " not", text)


def normalise_units(text: str) -> str:
    """Write units one way ('8 hrs' -> '8 hours') and convert hours to minutes."""
    text = text.replace("%", " percent").replace("°c", " degrees celsius").replace("°", " degrees")
    for pattern, unit in UNIT_ALIASES:
        text = re.sub(rf"(\d+(?:\.\d+)?)\s*-?\s*(?:{pattern})\b", rf"\1 {unit}", text)
    return re.sub(r"(\d+(?:\.\d+)?) hours", lambda m: f"{format_number(float(m[1]) * 60)} minutes", text)


def normalise(text: str) -> str:
    """Lower-case text and normalise contractions, numbers, ranges and units for matching."""
    text = expand_contractions(text.lower())
    text = re.sub(r"(\d),(\d{3})\b", r"\1\2", text)  # 1,000 -> 1000
    text = re.sub(r"(\d+)\.0\b", r"\1", text)  # 1.0 -> 1
    for word, digit in NUMBER_WORDS.items():
        text = re.sub(rf"\b{word}\b", digit, text)
    text = re.sub(r"(\d)\s*(?:-|–|to)\s*(\d)", r"\1 to \2", text)  # 0-5 -> 0 to 5
    text = normalise_units(text)
    text = re.sub(r"[^\w\s.]", " ", text)
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)  # drop full stops, keep decimal points
    return " ".join(text.split())


def phrase_in_normalised(normalised_text: str, phrase: str) -> bool:
    """True if the normalised phrase appears in already-normalised text as whole words."""
    target = normalise(phrase)
    return bool(target) and re.search(rf"(?<![\w.]){re.escape(target)}(?![\w])", normalised_text) is not None


def contains_phrase(text: str, phrase: str) -> bool:
    """True if the normalised phrase appears in the normalised text as whole words."""
    return phrase_in_normalised(normalise(text), phrase)


def fact_found(fact: str, answer: str) -> bool:
    """A fact may list alternatives, as in '480 minutes or 8 hours'; any one is enough."""
    return any(contains_phrase(answer, alternative) for alternative in fact.split(" or "))


def fact_coverage(required_facts: list[str], answer: str) -> float:
    """M2: share of required facts found in the answer (1.0 when none are required)."""
    if not required_facts:
        return 1.0
    return sum(fact_found(fact, answer) for fact in required_facts) / len(required_facts)


def forbidden_claim_check(forbidden_claims: list[str], answer: str) -> int:
    """M3: 1 if no forbidden phrase appears in the answer, else 0."""
    return 0 if any(contains_phrase(answer, claim) for claim in forbidden_claims) else 1


def refusal_detected(answer: str) -> bool:
    """M4: True if the answer contains any 'I do not know' phrase."""
    return any(contains_phrase(answer, phrase) for phrase in REFUSAL_PHRASES)


def section_of(chunk_id: str) -> str:
    """Return the 'document#section' part of a chunk id, dropping a split suffix like '/2'."""
    return chunk_id.split("/")[0]


def retrieval_hit(source_chunks: list[str], retrieved_ids: list[str]) -> tuple[bool, float]:
    """M5: (hit, reciprocal rank of the first retrieved chunk from a source section)."""
    if not source_chunks:
        raise ValueError("retrieval_hit needs at least one source chunk")
    sources = {section_of(s) for s in source_chunks}
    for rank, chunk_id in enumerate(retrieved_ids, start=1):
        if section_of(chunk_id) in sources:
            return True, 1 / rank
    return False, 0.0


class ToolCallSchema(BaseModel):
    """One tool call in a /chat response."""

    tool: str
    arguments: dict
    result: dict
    status: str


class RetrievedSchema(BaseModel):
    """One retrieved chunk in a /chat response."""

    chunk_id: str
    score: float
    text: str


class MetadataSchema(BaseModel):
    """Metadata block of a /chat response."""

    version: str
    model: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int


class ChatResponseSchema(BaseModel):
    """The /chat response contract from report section 6.1.7, as the framework sees it."""

    reply: str
    tool_calls: list[ToolCallSchema]
    retrieved: list[RetrievedSchema]
    metadata: MetadataSchema


def response_valid(status_code: int, body: object) -> bool:
    """M1: True for HTTP 200 with a body that matches the /chat response schema."""
    if status_code != 200:
        return False
    try:
        ChatResponseSchema.model_validate(body)
    except ValidationError:
        return False
    return True


def stability(outcomes: list[bool]) -> float:
    """M11: share of repeats that agree with the majority outcome."""
    if not outcomes:
        raise ValueError("stability needs at least one outcome")
    return Counter(outcomes).most_common(1)[0][1] / len(outcomes)


@dataclass
class MetricRow:
    """One metric score for one result; passed means the metric's condition holds."""

    metric: str
    score: float | None
    threshold: float | None
    passed: bool
    reason: str = ""


def score_golden_reply(
    case: GoldenCase, status_code: int, body: object, fact_threshold: float
) -> list[MetricRow]:
    """Layers 0 and 1 for one golden reply: M1 to M5 as metric rows."""
    valid = response_valid(status_code, body)
    reply = body["reply"] if valid else ""
    coverage = fact_coverage(case.required_facts, reply)
    no_forbidden = forbidden_claim_check(case.forbidden_claims, reply)
    refused = refusal_detected(reply)
    rows = [
        MetricRow("response_validity", float(valid), None, valid),
        MetricRow("fact_coverage", coverage, fact_threshold, coverage >= fact_threshold),
        MetricRow("forbidden_claims", float(no_forbidden), None, no_forbidden == 1),
        MetricRow("refusal_detected", float(refused), None, refused),
    ]
    if valid and case.source_chunks:
        hit, rr = retrieval_hit(case.source_chunks, [r["chunk_id"] for r in body["retrieved"]])
        rows += [
            MetricRow("retrieval_hit", float(hit), None, hit),
            MetricRow("reciprocal_rank", rr, None, hit),
        ]
    return rows


def deterministic_pass(expected_behavior: str, rows: list[MetricRow]) -> bool:
    """Layer 0-1 part of the 6.3.3 pass rules for one repeat."""
    held = {row.metric: row.passed for row in rows}
    base = held["response_validity"] and held["forbidden_claims"]
    if expected_behavior == "refuse_unknown":
        return base and held["refusal_detected"]
    return base and held["fact_coverage"]
