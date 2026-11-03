"""Golden-set validator (report section 6.2.4). Run: python -m qa_gate.datasets.validator"""

import hashlib
import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

from pydantic import ValidationError

from qa_gate.datasets.loader import REPO_ROOT, GoldenCase, load_raw
from qa_gate.metrics.deterministic import normalise, phrase_in_normalised

# The framework reads the manuals as files; it never imports assistant code.
KB_DIR = REPO_ROOT / "assistant" / "knowledge_base"

CATEGORY_COUNTS = {
    "factual": 20,
    "multi_hop": 8,
    "troubleshooting": 8,
    "numeric_constraint": 8,
    "unanswerable": 12,
    "false_premise": 4,
}
DIFFICULTY_COUNTS = {"easy": 24, "medium": 24, "hard": 12}
SMOKE_COUNTS = {
    "factual": 8,
    "multi_hop": 3,
    "troubleshooting": 3,
    "numeric_constraint": 2,
    "unanswerable": 3,
    "false_premise": 1,
}
EXPECTED_BEHAVIOR = {
    "unanswerable": "refuse_unknown",
    "false_premise": "correct_premise",
}  # every other category expects "answer"
MIN_VARIANTS = 10
NEAR_DUPLICATE_RATIO = 0.9


def read_kb(kb_dir: Path = KB_DIR) -> tuple[set[str], str]:
    """Return the 'document#section' ids and the lower-case full text of the manuals."""
    sections, texts = set(), []
    for path in sorted(kb_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        texts.append(text.lower())
        for heading in re.findall(r"^## (.+)$", text, flags=re.MULTILINE):
            sections.add(f"{path.stem}#{re.sub(r'[^a-z0-9]+', '_', heading.lower()).strip('_')}")
    return sections, "\n".join(texts)


def parse_cases(raw_cases: list[dict]) -> tuple[list[GoldenCase], list[str]]:
    """Parse raw dicts into cases, collecting schema errors such as missing fields."""
    cases, errors = [], []
    for i, raw in enumerate(raw_cases):
        try:
            cases.append(GoldenCase.model_validate(raw))
        except ValidationError as err:
            fields = ", ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors())
            errors.append(f"case {raw.get('id', f'#{i}')}: {fields}")
    return cases, errors


def check_counts(cases: list[GoldenCase]) -> list[str]:
    """Duplicate ids, category and difficulty counts, and the number of variants."""
    errors = [f"duplicate id {i}" for i, n in Counter(c.id for c in cases).items() if n > 1]
    for name, expected, actual in [
        ("category", CATEGORY_COUNTS, Counter(c.category for c in cases)),
        ("difficulty", DIFFICULTY_COUNTS, Counter(c.difficulty for c in cases)),
    ]:
        if dict(actual) != expected:
            errors.append(f"{name} counts {dict(actual)} differ from {expected}")
    variants = sum(c.variant_of is not None for c in cases)
    if variants < MIN_VARIANTS:
        errors.append(f"only {variants} style variants, need at least {MIN_VARIANTS}")
    return errors


def check_behavior_and_facts(cases: list[GoldenCase]) -> list[str]:
    """Each category has its expected behaviour; answerable cases need facts and source chunks."""
    errors = []
    for c in cases:
        expected = EXPECTED_BEHAVIOR.get(c.category, "answer")
        if c.expected_behavior != expected:
            errors.append(f"{c.id}: {c.category} must have expected_behavior {expected}")
        if c.expected_behavior != "refuse_unknown" and not (c.required_facts and c.source_chunks):
            errors.append(f"{c.id}: answerable case needs required_facts and source_chunks")
        if c.expected_behavior == "refuse_unknown" and not c.absent_terms:
            errors.append(f"{c.id}: unanswerable case needs absent_terms")
    return errors


def check_knowledge_base(cases: list[GoldenCase], sections: set[str], kb_text: str) -> list[str]:
    """Source chunks must exist; absent terms must really be absent from the manuals."""
    errors = []
    for c in cases:
        errors += [f"{c.id}: unknown source chunk {s}" for s in c.source_chunks if s not in sections]
        for term in c.absent_terms:
            if re.search(rf"\b{re.escape(term.lower())}\b", kb_text):
                errors.append(f"{c.id}: absent term '{term}' appears in the knowledge base")
    return errors


def are_variants(a: GoldenCase, b: GoldenCase) -> bool:
    """True if one case is marked as a variant of the other, or both reword the same case."""
    return (
        a.variant_of == b.id
        or b.variant_of == a.id
        or (a.variant_of is not None and a.variant_of == b.variant_of)
    )


def check_near_duplicates(cases: list[GoldenCase]) -> list[str]:
    """Questions with similarity above 0.9 must be marked as variants."""
    errors = []
    for a, b in combinations(cases, 2):
        ratio = SequenceMatcher(None, a.question.lower(), b.question.lower()).ratio()
        if ratio > NEAR_DUPLICATE_RATIO and not are_variants(a, b):
            errors.append(f"{a.id} and {b.id} are near-duplicates ({ratio:.2f}) but not marked as variants")
    return errors


def check_smoke(cases: list[GoldenCase]) -> list[str]:
    """The smoke subset must be exactly 20 cases with the fixed category mix."""
    actual = dict(Counter(c.category for c in cases if c.smoke))
    return [] if actual == SMOKE_COUNTS else [f"smoke counts {actual} differ from {SMOKE_COUNTS}"]


def validate(raw_cases: list[dict], kb_dir: Path = KB_DIR) -> list[str]:
    """Run every check and return all error messages (empty means valid)."""
    cases, errors = parse_cases(raw_cases)
    if errors:
        return errors
    sections, kb_text = read_kb(kb_dir)
    return (
        check_counts(cases)
        + check_behavior_and_facts(cases)
        + check_knowledge_base(cases, sections, kb_text)
        + check_near_duplicates(cases)
        + check_smoke(cases)
    )


def forbidden_claim_warnings(cases: list[GoldenCase], kb_text: str) -> list[str]:
    """Forbidden claims that appear in the manuals (matched like M3), for the author to review."""
    normalised_kb = normalise(kb_text)
    return [
        f"{c.id}: forbidden claim '{claim}' appears in the knowledge base"
        for c in cases
        for claim in c.forbidden_claims
        if phrase_in_normalised(normalised_kb, claim)
    ]


def warnings(raw_cases: list[dict], kb_dir: Path = KB_DIR) -> list[str]:
    """Return review warnings; these never fail validation."""
    cases, errors = parse_cases(raw_cases)
    if errors:
        return []
    return forbidden_claim_warnings(cases, read_kb(kb_dir)[1])


def dataset_hash(raw_cases: list[dict]) -> str:
    """SHA-256 of the cases in canonical JSON, so formatting and comments do not change it."""
    canonical = json.dumps(raw_cases, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> int:
    """Validate data/golden_set.yaml, print the result and return an exit code."""
    raw = load_raw()
    errors = validate(raw)
    review = warnings(raw)
    for error in errors:
        print(f"ERROR {error}")
    for warning in review:
        print(f"WARNING {warning}")
    print(
        f"{len(raw)} golden cases, {len(errors)} errors, {len(review)} warnings, "
        f"dataset hash {dataset_hash(raw)[:12]}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
