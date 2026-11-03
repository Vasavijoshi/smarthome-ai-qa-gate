"""Session fixtures for evaluation suites: one run row per pytest session, against a live HomeMate."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from qa_gate.client import HomeMateClient
from qa_gate.config import EvalConfig, judged_case_ids, load_eval_config, version_config_hash
from qa_gate.datasets.loader import load_golden, load_raw
from qa_gate.datasets.validator import dataset_hash
from qa_gate.store import connect, finish_run, start_run


@dataclass
class EvalRun:
    """Everything a golden test needs: the store, the run id, the client and the settings."""

    conn: object
    run_id: str
    client: HomeMateClient
    config: EvalConfig
    judged: set[str]


def git_commit() -> str:
    """Current commit, with '+dirty' when there are uncommitted changes."""
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    return f"{commit}+dirty" if dirty else commit


@pytest.fixture(scope="session")
def eval_run() -> EvalRun:
    """Check the frozen dataset, read /health, open a run row, and close it after the session."""
    config = load_eval_config()
    current_hash = dataset_hash(load_raw())
    if current_hash != config.golden_dataset_hash:
        pytest.exit(f"golden set hash {current_hash[:12]} is not the frozen v1.0 hash", returncode=2)
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
            "dataset_hash": current_hash,
            "kb_version": health["kb_version"],
            "config_hash": version_config_hash(health["version"]),
            "git_commit": git_commit(),
            "repeats": len(config.seeds),
        },
    )
    judged = judged_case_ids(load_golden(), config.judge_scope, config.sample_seed, config.sample_extra)
    yield EvalRun(conn, run_id, client, config, judged)
    finish_run(conn, run_id)
    print(f"\nrun_id: {run_id}")
