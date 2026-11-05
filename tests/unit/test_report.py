"""Unit tests for the gate reports and the gate command line (exit codes 0, 1 and 2)."""

import json
import subprocess
import sys

import pytest

from qa_gate.config import version_config_hash
from qa_gate.datasets.loader import REPO_ROOT
from qa_gate.gate import evaluate, load_gate_config
from qa_gate.report import markdown, write_reports
from tests.unit.test_gate import snapshot, with_metric

pytestmark = pytest.mark.unit

CONFIG = load_gate_config()


def evaluated(candidate: dict, tmp_path):
    """Gate result for a candidate against the synthetic baseline."""
    return evaluate(snapshot(), candidate, CONFIG, recompute=lambda v: "c", root=tmp_path)


def test_pass_report_shows_verdict_rules_and_validity(tmp_path):
    text = markdown(snapshot(), snapshot("R2"), evaluated(snapshot("R2"), tmp_path))
    assert "**Verdict: PASS** (exit code 0)" in text and "| case_pass_rate |" in text
    assert "- golden: 180 of 180" in text and "40 of 60" in text


def test_fail_report_marks_the_tripped_rule(tmp_path):
    candidate = with_metric(snapshot("R2"), "retrieval_hit_rate", 40)
    text = markdown(snapshot(), candidate, evaluated(candidate, tmp_path))
    assert "**Verdict: FAIL**" in text and "| retrieval_hit_rate |" in text and "**TRIPPED**" in text


def test_incomparable_report_lists_reasons(tmp_path):
    candidate = snapshot("R2", judge_model="other")
    text = markdown(snapshot(), candidate, evaluated(candidate, tmp_path))
    assert "INCOMPARABLE" in text and "judge_model differs" in text


def test_write_reports_creates_markdown_and_json(tmp_path):
    md, js = write_reports(snapshot(), snapshot("R2"), evaluated(snapshot("R2"), tmp_path), tmp_path)
    assert md.name == "gate_R2_vs_R1.md" and json.loads(js.read_text())["verdict"] == "PASS"


def run_cli(tmp_path, candidate: dict) -> subprocess.CompletedProcess:
    """Run the gate CLI on a real-hash synthetic baseline and the given candidate, writing reports to tmp."""
    real_hash = version_config_hash("v1.0")
    baseline = snapshot(config_hash=real_hash)
    candidate["run"]["config_hash"] = candidate["run"].get("config_hash_override", real_hash)
    (tmp_path / "b.json").write_text(json.dumps(baseline))
    (tmp_path / "c.json").write_text(json.dumps(candidate))
    files = ["--baseline", str(tmp_path / "b.json"), "--candidate", str(tmp_path / "c.json")]
    command = [
        sys.executable,
        "-m",
        "qa_gate.gate",
        *files,
        "--no-db",
        "--reports-dir",
        str(tmp_path / "out"),
    ]
    return subprocess.run(command, capture_output=True, text=True, cwd=REPO_ROOT)


@pytest.mark.parametrize(
    "candidate, code",
    [
        (snapshot("R2"), 0),
        (with_metric(snapshot("R2"), "case_pass_rate", 30), 1),
        (snapshot("R2", dataset_hash="other"), 2),
        (snapshot("R2", config_hash_override="stale"), 2),
    ],
)
def test_cli_exit_codes(tmp_path, candidate, code):
    completed = run_cli(tmp_path, candidate)
    assert completed.returncode == code, completed.stderr
    assert (tmp_path / "out" / "gate_R2_vs_R1.md").exists()
