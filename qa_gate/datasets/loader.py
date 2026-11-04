"""Golden-case schema and YAML loader (report section 6.2.3)."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO_ROOT / "data" / "golden_set.yaml"
ATTACKS_PATH = REPO_ROOT / "data" / "attacks.yaml"
CONTROLS_PATH = REPO_ROOT / "data" / "controls.yaml"

Category = Literal[
    "factual", "multi_hop", "troubleshooting", "numeric_constraint", "unanswerable", "false_premise"
]
Behavior = Literal["answer", "refuse_unknown", "correct_premise"]


class GoldenCase(BaseModel):
    """One hand-written golden case."""

    model_config = ConfigDict(extra="forbid")

    id: str
    category: Category
    difficulty: Literal["easy", "medium", "hard"]
    product: str
    question: str
    reference_answer: str
    required_facts: list[str]
    forbidden_claims: list[str]
    expected_behavior: Behavior
    source_chunks: list[str]
    absent_terms: list[str] = []
    smoke: bool
    tags: list[str] = []

    @property
    def variant_of(self) -> str | None:
        """The id of the case this one rewords, from a 'variant_of:<id>' tag."""
        return next((t.split(":", 1)[1] for t in self.tags if t.startswith("variant_of:")), None)


def load_raw(path: Path = GOLDEN_PATH) -> list[dict]:
    """Read the YAML file as a list of plain dicts."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_golden(path: Path = GOLDEN_PATH) -> list[GoldenCase]:
    """Read and parse every golden case; raises if a case breaks the schema."""
    return [GoldenCase.model_validate(raw) for raw in load_raw(path)]


AttackCategory = Literal["direct_injection", "indirect_injection", "jailbreak", "pii_leak", "blocked_topic"]
Detector = Literal["D1", "D2", "D3", "D4", "D5"]


class Attack(BaseModel):
    """One safety attack (report 6.4.2): scripted user turns plus the detectors that decide success."""

    model_config = ConfigDict(extra="forbid")

    id: str
    category: AttackCategory
    severity: Literal["critical", "high", "medium"]
    goal: str
    turns: list[str]
    detectors: list[Detector]
    smoke: bool
    poisoned_doc: str | None = None  # indirect injection: markdown added as 'poisoned_faq'
    canary: str | None = None  # D1 token, or a planted value for the cross-session leak
    setup: list[str] = []  # messages sent first from a different session
    topic: str | None = None  # blocked topic for D5


class Control(BaseModel):
    """One benign control (report 6.4.3): a slightly risky request that must be answered."""

    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    why_benign: str


def load_attacks(path: Path = ATTACKS_PATH) -> list[Attack]:
    """Read and parse every attack."""
    return [Attack.model_validate(raw) for raw in load_raw(path)]


def load_controls(path: Path = CONTROLS_PATH) -> list[Control]:
    """Read and parse every benign control."""
    return [Control.model_validate(raw) for raw in load_raw(path)]
