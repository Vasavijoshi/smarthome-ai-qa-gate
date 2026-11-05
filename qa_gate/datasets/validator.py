"""Dataset validator (report 6.2.4): golden set, attacks, controls, tool cases and scenarios.

Run: python -m qa_gate.datasets.validator
"""

import hashlib
import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

from pydantic import ValidationError

from qa_gate.config import load_eval_config
from qa_gate.datasets.loader import (
    ATTACKS_PATH,
    CONTROLS_PATH,
    REPO_ROOT,
    SCENARIOS_PATH,
    TOOL_CASES_PATH,
    Attack,
    Control,
    Fault,
    GoldenCase,
    Scenario,
    ToolCase,
    load_raw,
)
from qa_gate.metrics.deterministic import normalise, phrase_in_normalised
from qa_gate.metrics.tools import ARG_MODELS, DEVICE_FIELDS, legal_value, parsed_args

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


ATTACK_COUNTS = {
    "direct_injection": 6,
    "indirect_injection": 4,
    "jailbreak": 5,
    "pii_leak": 5,
    "blocked_topic": 5,
}
ATTACK_SEVERITY = {
    "direct_injection": "high",
    "indirect_injection": "critical",
    "jailbreak": "high",
    "pii_leak": "critical",
    "blocked_topic": "medium",
}
ATTACK_SMOKE_COUNTS = {
    "direct_injection": 2,
    "indirect_injection": 1,
    "jailbreak": 2,
    "pii_leak": 2,
    "blocked_topic": 1,
}
ALLOWED_DETECTORS = {  # report 6.4.4, "Used for" column
    "direct_injection": {"D2"},
    "indirect_injection": {"D1", "D4"},
    "jailbreak": {"D2", "D4", "D5"},
    "pii_leak": {"D3"},
    "blocked_topic": {"D5"},
}
CONTROL_COUNT = 10


def parse_items(raw_items: list[dict], model: type) -> tuple[list, list[str]]:
    """Parse raw dicts with a Pydantic model, collecting schema errors."""
    items, errors = [], []
    for i, raw in enumerate(raw_items):
        try:
            items.append(model.model_validate(raw))
        except ValidationError as err:
            fields = ", ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors())
            errors.append(f"{raw.get('id', f'#{i}')}: {fields}")
    return items, errors


def check_attack_counts(attacks: list[Attack]) -> list[str]:
    """Unique ids, 25 attacks in the 6.4.2 mix, and the 8-attack smoke subset."""
    errors = [f"duplicate id {i}" for i, n in Counter(a.id for a in attacks).items() if n > 1]
    actual = dict(Counter(a.category for a in attacks))
    if actual != ATTACK_COUNTS:
        errors.append(f"attack counts {actual} differ from {ATTACK_COUNTS}")
    smoke = dict(Counter(a.category for a in attacks if a.smoke))
    if smoke != ATTACK_SMOKE_COUNTS:
        errors.append(f"attack smoke counts {smoke} differ from {ATTACK_SMOKE_COUNTS}")
    return errors


def check_attack(attack: Attack, topics: set[str], kb_text: str) -> list[str]:
    """Severity, detectors, turns, poisoned document, canary and topic rules for one attack."""
    errors = []
    if attack.severity != ATTACK_SEVERITY[attack.category]:
        errors.append(f"severity must be {ATTACK_SEVERITY[attack.category]}")
    if not attack.detectors or not set(attack.detectors) <= ALLOWED_DETECTORS[attack.category]:
        errors.append(f"detectors must be a non-empty subset of {sorted(ALLOWED_DETECTORS[attack.category])}")
    if not attack.turns or not all(t.strip() for t in attack.turns):
        errors.append("needs at least one non-empty turn")
    if (attack.category == "indirect_injection") != (attack.poisoned_doc is not None):
        errors.append("poisoned_doc is required for indirect injection and only there")
    if attack.poisoned_doc is not None and "\n## " not in attack.poisoned_doc:
        errors.append("poisoned_doc needs a '## ' section")
    if "D1" in attack.detectors and not (attack.canary and attack.canary in (attack.poisoned_doc or "")):
        errors.append("D1 needs a canary that appears in the poisoned document")
    if attack.setup and not (attack.canary and any(attack.canary in m for m in attack.setup)):
        errors.append("a cross-session setup must plant the canary")
    if attack.canary and attack.canary.lower() in kb_text:
        errors.append(f"canary {attack.canary} already appears in the knowledge base")
    if "D5" in attack.detectors and attack.topic not in topics:
        errors.append(f"D5 needs a topic from eval.yaml {sorted(topics)}")
    return [f"{attack.id}: {e}" for e in errors]


def validate_attacks(raw_attacks: list[dict], topics: set[str], kb_dir: Path = KB_DIR) -> list[str]:
    """Run every attack check and return all error messages (empty means valid)."""
    attacks, errors = parse_items(raw_attacks, Attack)
    if errors:
        return errors
    canaries = [a.canary for a in attacks if a.canary]
    errors += [f"canary {c} is used twice" for c, n in Counter(canaries).items() if n > 1]
    kb_text = read_kb(kb_dir)[1]
    errors += check_attack_counts(attacks)
    for attack in attacks:
        errors += check_attack(attack, topics, kb_text)
    return errors


def validate_controls(raw_controls: list[dict]) -> list[str]:
    """Exactly 10 benign controls with unique ids and non-empty questions."""
    controls, errors = parse_items(raw_controls, Control)
    if errors:
        return errors
    errors = [f"duplicate id {i}" for i, n in Counter(c.id for c in controls).items() if n > 1]
    if len(controls) != CONTROL_COUNT:
        errors.append(f"{len(controls)} controls, expected {CONTROL_COUNT}")
    return errors + [f"{c.id}: empty question" for c in controls if not c.question.strip()]


def run_dataset_hash(datasets: dict[str, list[dict]]) -> str:
    """One hash over every dataset a run uses (golden, attacks, controls); stored as runs.dataset_hash."""
    return dataset_hash([{name: datasets[name]} for name in sorted(datasets)])


TOOL_GROUP_COUNTS = {
    "correct_call": 5,
    "ambiguity": 2,
    "invalid_argument": 3,
    "unsupported": 2,
    "no_tool_needed": 2,
    "missing_capability": 1,
    "failure_injection": 8,
}
GROUP_CALL = {  # what each group expects (report 6.5.1)
    "correct_call": "yes",
    "ambiguity": "no",
    "invalid_argument": "either",
    "unsupported": "either",
    "no_tool_needed": "no",
    "missing_capability": "no",
    "failure_injection": "yes",
}
SCENARIO_TURNS = {  # report 6.5.5, in order
    "S-01": 3, "S-02": 3, "S-03": 3, "S-04": 4, "S-05": 3,
    "S-06": 5, "S-07": 3, "S-08": 4, "S-09": 7, "S-10": 4,
}  # fmt: skip


def check_state_spec(expect_state: dict[str, dict]) -> list[str]:
    """An expected state names known devices and fields, with legal values."""
    errors = []
    for device_id, fields in expect_state.items():
        rules = DEVICE_FIELDS.get(device_id)
        if rules is None:
            errors.append(f"unknown device {device_id}")
            continue
        for name, value in fields.items():
            if name not in rules:
                errors.append(f"unknown field {device_id}.{name}")
            elif not legal_value(value, *rules[name]):
                errors.append(f"expected value {device_id}.{name} = {value!r} is not legal")
    return errors


def check_fault(fault: Fault | None) -> list[str]:
    """A fault names a known device."""
    return [f"fault on unknown device {fault.device}"] if fault and fault.device not in DEVICE_FIELDS else []


def check_tool_case(case: ToolCase) -> list[str]:
    """Group rules, tool and arguments, state, fault and failure-reporting rules for one tool case."""
    errors = check_state_spec(case.expect_state) + check_fault(case.fault)
    if case.expect_tool_call != GROUP_CALL[case.group]:
        errors.append(f"{case.group} cases must use expect_tool_call {GROUP_CALL[case.group]}")
    if not case.turns or not all(t.strip() for t in case.turns):
        errors.append("needs at least one non-empty turn")
    if case.expect_tool_call == "yes":
        if case.expect_tool not in ARG_MODELS:
            errors.append(f"expect_tool must be one of {sorted(ARG_MODELS)}")
        elif case.expect_args is None or parsed_args(case.expect_tool, case.expect_args) is None:
            errors.append("expect_args must be valid arguments for expect_tool")
    elif case.expect_tool is not None or case.expect_args is not None:
        errors.append("expect_tool and expect_args are only for expect_tool_call 'yes'")
    failure = case.group == "failure_injection"
    if failure != (case.fault is not None):
        errors.append("a fault is required for failure injection and only there")
    if failure and not (case.reply_must_not_claim_success and case.max_tool_calls == 2):
        errors.append("failure cases need reply_must_not_claim_success and max_tool_calls 2")
    if failure and case.fault and (case.expect_args or {}).get("device_id") != case.fault.device:
        errors.append("the expected call must target the faulty device")
    return [f"{case.id}: {e}" for e in errors]


def validate_tool_cases(raw_cases: list[dict]) -> list[str]:
    """23 tool cases in the 6.5.1 mix, each passing its own rules."""
    cases, errors = parse_items(raw_cases, ToolCase)
    if errors:
        return errors
    errors = [f"duplicate id {i}" for i, n in Counter(c.id for c in cases).items() if n > 1]
    actual = dict(Counter(c.group for c in cases))
    if actual != TOOL_GROUP_COUNTS:
        errors.append(f"tool group counts {actual} differ from {TOOL_GROUP_COUNTS}")
    for case in cases:
        errors += check_tool_case(case)
    return errors


def validate_scenarios(raw_scenarios: list[dict]) -> list[str]:
    """The 10 scenarios of 6.5.5 with their turn counts, known tools, devices and legal expected states."""
    scenarios, errors = parse_items(raw_scenarios, Scenario)
    if errors:
        return errors
    counts = {s.id: len(s.turns) for s in scenarios}
    if counts != SCENARIO_TURNS:
        errors.append(f"scenario turn counts {counts} differ from {SCENARIO_TURNS}")
    for scenario in scenarios:
        for i, turn in enumerate(scenario.turns, start=1):
            problems = check_state_spec(turn.expect_state) + check_fault(turn.fault_before)
            if turn.expect_tool not in (None, "none") and turn.expect_tool not in ARG_MODELS:
                problems.append(f"unknown tool {turn.expect_tool}")
            if not turn.user.strip():
                problems.append("empty user turn")
            errors += [f"{scenario.id} turn {i}: {p}" for p in problems]
    return errors


def main() -> int:
    """Validate every dataset in data/, print the result and return an exit code."""
    raw = load_raw()
    attacks, controls = load_raw(ATTACKS_PATH), load_raw(CONTROLS_PATH)
    tool_cases, scenarios = load_raw(TOOL_CASES_PATH), load_raw(SCENARIOS_PATH)
    errors = validate(raw)
    review = warnings(raw)
    safety_errors = validate_attacks(attacks, set(load_eval_config().blocked_topics)) + validate_controls(
        controls
    )
    tool_errors = validate_tool_cases(tool_cases) + validate_scenarios(scenarios)
    for error in errors + safety_errors + tool_errors:
        print(f"ERROR {error}")
    for warning in review:
        print(f"WARNING {warning}")
    print(
        f"{len(raw)} golden cases, {len(errors)} errors, {len(review)} warnings, "
        f"dataset hash {dataset_hash(raw)[:12]}"
    )
    print(f"{len(attacks)} attacks and {len(controls)} controls, {len(safety_errors)} errors")
    print(f"{len(tool_cases)} tool cases and {len(scenarios)} scenarios, {len(tool_errors)} errors")
    combined = run_dataset_hash(
        {
            "golden": raw,
            "attacks": attacks,
            "controls": controls,
            "tool_cases": tool_cases,
            "scenarios": scenarios,
        }
    )
    print(f"run dataset hash {combined[:12]}")
    return 1 if errors or safety_errors or tool_errors else 0


if __name__ == "__main__":
    sys.exit(main())
