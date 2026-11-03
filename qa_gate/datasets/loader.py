"""Golden-case schema and YAML loader (report section 6.2.3)."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO_ROOT / "data" / "golden_set.yaml"

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
