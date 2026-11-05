"""Session fixtures for evaluation suites: one run row per pytest session, against a live HomeMate."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from qa_gate.client import HomeMateClient
from qa_gate.config import EvalConfig, judged_case_ids, load_eval_config, settings_hash, version_config_hash
from qa_gate.datasets.loader import (
    ATTACKS_PATH,
    CONTROLS_PATH,
    SCENARIOS_PATH,
    TOOL_CASES_PATH,
    load_golden,
    load_raw,
)
from qa_gate.datasets.validator import dataset_hash, run_dataset_hash
from qa_gate.store import connect, finish_run, start_run


@dataclass
class EvalRun:
    """Everything a golden test needs: the store, the run id, the client and the settings."""

    conn: object
    run_id: str
    client: HomeMateClient
    config: EvalConfig
    judged: set[str]
    version: str


def git_commit() -> str:
    """Current commit, with '+dirty' when there are uncommitted changes."""
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    return f"{commit}+dirty" if dirty else commit


@pytest.fixture(scope="session")
def eval_run() -> EvalRun:
    """Check the frozen dataset, read /health, open a run row, and close it after the session."""
    config = load_eval_config()
    golden_hash = dataset_hash(load_raw())
    if golden_hash != config.golden_dataset_hash:
        pytest.exit(f"golden set hash {golden_hash[:12]} is not the frozen v1.0 hash", returncode=2)
    for name, path, frozen in [
        ("attacks", ATTACKS_PATH, config.attacks_dataset_hash),
        ("controls", CONTROLS_PATH, config.controls_dataset_hash),
        ("tool_cases", TOOL_CASES_PATH, config.tool_cases_dataset_hash),
        ("scenarios", SCENARIOS_PATH, config.scenarios_dataset_hash),
    ]:
        if dataset_hash(load_raw(path)) != frozen:
            pytest.exit(f"{name} hash is not the frozen v1.0 hash", returncode=2)
    # runs.dataset_hash covers every dataset, so the gate only compares runs on identical data.
    combined_hash = run_dataset_hash(
        {
            "golden": load_raw(),
            "attacks": load_raw(ATTACKS_PATH),
            "controls": load_raw(CONTROLS_PATH),
            "tool_cases": load_raw(TOOL_CASES_PATH),
            "scenarios": load_raw(SCENARIOS_PATH),
        }
    )
    client = HomeMateClient(os.getenv("HOMEMATE_URL", config.homemate_url))
    health = client.health()
    conn = connect(Path(os.getenv("QA_DB", config.db_path)))
    run_id = start_run(
        conn,
        {
            "assistant_version": health["version"],
            "model": health["model"],
            "judge_model": config.judge_model,
            "judge_scope": config.judge_scope,
            "dataset_hash": combined_hash,
            "kb_version": health["kb_version"],
            "config_hash": version_config_hash(health["version"]),
            "settings_hash": settings_hash(),  # eval.yaml + rubrics: the gate refuses runs that differ
            "git_commit": git_commit(),
            "repeats": len(config.seeds),
        },
    )
    judged = judged_case_ids(load_golden(), config.judge_scope, config.sample_seed, config.sample_extra)
    yield EvalRun(conn, run_id, client, config, judged, health["version"])
    finish_run(conn, run_id)
    print(f"\nrun_id: {run_id}")
