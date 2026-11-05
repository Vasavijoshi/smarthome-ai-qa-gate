"""Evaluation settings from eval.yaml, the judged-case selection, and version config hashes."""

import hashlib
import random
from dataclasses import dataclass
from pathlib import Path

import yaml

from qa_gate.datasets.loader import REPO_ROOT, GoldenCase

EVAL_CONFIG_PATH = REPO_ROOT / "eval.yaml"
# The framework reads version files from disk; it never imports assistant code.
VERSIONS_DIR = REPO_ROOT / "assistant" / "versions"
PROMPTS_DIR = REPO_ROOT / "assistant" / "prompts"


@dataclass(frozen=True)
class EvalConfig:
    """Settings for one evaluation run."""

    golden_dataset_hash: str
    attacks_dataset_hash: str
    controls_dataset_hash: str
    homemate_url: str
    db_path: Path
    seeds: list[int]
    thresholds: dict[str, float]
    judge_model: str
    judge_temperature: float
    judge_seed: int
    judge_scope: str
    sample_seed: int
    sample_extra: int
    cost_per_1k_tokens: float
    prompt_leak_ratio: float
    prompt_leak_run_words: int
    prompt_sentinel: str
    blocked_decline_threshold: float
    blocked_topics: dict[str, list[str]]


def load_eval_config(path: Path = EVAL_CONFIG_PATH) -> EvalConfig:
    """Read eval.yaml into an EvalConfig."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    judge = raw["judge"]
    if judge["scope"] not in ("full", "sampled"):
        raise ValueError(f"judge.scope must be 'full' or 'sampled', not {judge['scope']}")
    return EvalConfig(
        golden_dataset_hash=raw["golden_dataset_hash"],
        attacks_dataset_hash=raw["attacks_dataset_hash"],
        controls_dataset_hash=raw["controls_dataset_hash"],
        homemate_url=raw["homemate_url"],
        db_path=REPO_ROOT / raw["db_path"],
        seeds=raw["seeds"],
        thresholds=raw["thresholds"],
        judge_model=judge["model"],
        judge_temperature=judge["temperature"],
        judge_seed=judge["seed"],
        judge_scope=judge["scope"],
        sample_seed=judge["sample_seed"],
        sample_extra=judge["sample_extra"],
        cost_per_1k_tokens=raw["cost_per_1k_tokens"],
        prompt_leak_ratio=raw["safety"]["prompt_leak_ratio"],
        prompt_leak_run_words=raw["safety"]["prompt_leak_run_words"],
        prompt_sentinel=raw["safety"]["prompt_sentinel"],
        blocked_decline_threshold=raw["safety"]["blocked_decline_threshold"],
        blocked_topics=raw["safety"]["blocked_topics"],
    )


def judged_case_ids(cases: list[GoldenCase], scope: str, sample_seed: int, sample_extra: int) -> set[str]:
    """All case ids for 'full'; for 'sampled', the smoke cases plus a fixed random draw of the rest."""
    if scope == "full":
        return {c.id for c in cases}
    others = sorted(c.id for c in cases if not c.smoke)
    extra = random.Random(sample_seed).sample(others, sample_extra)
    return {c.id for c in cases if c.smoke} | set(extra)


def version_config_hash(
    version: str, versions_dir: Path = VERSIONS_DIR, prompts_dir: Path = PROMPTS_DIR
) -> str:
    """SHA-256 of a version's YAML file plus its prompt file, with normalised line endings."""
    version_file = versions_dir / f"{version}.yaml"
    prompt_name = yaml.safe_load(version_file.read_text(encoding="utf-8"))["prompt_file"]
    digest = hashlib.sha256()
    for path in (version_file, prompts_dir / prompt_name):
        digest.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode())
    return digest.hexdigest()


def version_prompt_text(
    version: str, versions_dir: Path = VERSIONS_DIR, prompts_dir: Path = PROMPTS_DIR
) -> str:
    """The system prompt file of a version, read from disk (used by the prompt-leak detector)."""
    version_file = versions_dir / f"{version}.yaml"
    prompt_name = yaml.safe_load(version_file.read_text(encoding="utf-8"))["prompt_file"]
    return (prompts_dir / prompt_name).read_text(encoding="utf-8")
