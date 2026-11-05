"""Run snapshots (report 6.6.3): a compact JSON of one complete, valid run that the gate (and CI) reads.

Run: python -m qa_gate.snapshot export --run <run_id>
     python -m qa_gate.snapshot rebaseline --run <run_id> --reason "..."
"""

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from qa_gate.config import load_eval_config
from qa_gate.datasets.loader import REPO_ROOT, load_attacks
from qa_gate.store import EXPECTED_RESULTS, connect, validate_run

RUNS_DIR = REPO_ROOT / "runs"
BASELINE_PATH = REPO_ROOT / "baselines" / "baseline.json"
SCHEMA_VERSION = 1
RUN_FIELDS = [
    "run_id", "assistant_version", "model", "judge_model", "judge_scope", "dataset_hash", "kb_version",
    "config_hash", "settings_hash", "git_commit", "repeats", "status", "started_at", "finished_at",
]  # fmt: skip


def rate(numerator: int | None, denominator: int | None) -> dict:
    """A rate with its counts, so small samples are shown as 'k of n' (6.11.2)."""
    value = numerator / denominator if denominator else None
    return {"value": value, "numerator": numerator, "denominator": denominator}


def one_row(conn: sqlite3.Connection, query: str, run_id: str) -> sqlite3.Row:
    """Exactly one view row for the run, or an error naming the missing part."""
    row = conn.execute(query, (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"no row for run {run_id}: {query.split('FROM')[1].split()[0]}")
    return row


def gated_metrics(conn: sqlite3.Connection, run_id: str) -> dict:
    """Every metric the gate reads (6.7.2), each with its counts, straight from the SQL views."""
    golden = one_row(conn, "SELECT * FROM v_run_summary WHERE run_id = ?", run_id)
    safety = one_row(conn, "SELECT * FROM v_safety_summary WHERE run_id = ? AND category = 'all'", run_id)
    tools = one_row(conn, "SELECT * FROM v_tool_summary WHERE run_id = ?", run_id)
    return {
        "case_pass_rate": rate(golden["cases_passed"], golden["cases"]),
        "hallucination_rate": rate(golden["unsupported_cases"] + golden["fabricated_cases"], golden["cases"]),
        "failure_to_abstain_rate": rate(golden["fabricated_cases"], golden["unanswerable_cases"]),
        "retrieval_hit_rate": rate(golden["retrieval_hits"], golden["retrieval_cases"]),
        "attack_success_rate_worst3": rate(safety["attacks_succeeded"], safety["attacks"]),
        "false_refusal_rate": rate(safety["controls_refused"], safety["controls"]),
        "tool_selection_accuracy": rate(tools["tool_selection_passed"], tools["tool_selection_cases"]),
        "scenario_success_rate": rate(tools["scenario_passed"], tools["scenario_results"]),
        "false_success_claim_rate": rate(tools["success_claims"], tools["failure_attempts"]),
        "p95_latency": {"value": golden["p95_latency_ms"], "unit": "ms"},
        "invalid_state_incidents": {"value": tools["invalid_state_incidents"]},
    }


def response_validity(conn: sqlite3.Connection, run_id: str) -> dict:
    """Share of valid responses (M1) per suite."""
    rows = conn.execute(
        """SELECT r.suite, SUM(m.passed) AS valid, COUNT(*) AS n FROM results r
           JOIN metric_scores m ON m.result_id = r.result_id AND m.metric = 'response_validity'
           WHERE r.run_id = ? GROUP BY r.suite""",
        (run_id,),
    ).fetchall()
    return {row["suite"]: rate(row["valid"], row["n"]) for row in rows}


def critical_successes(conn: sqlite3.Connection, run_id: str) -> list[str]:
    """Critical attacks that succeeded in at least one attempt (worst of three)."""
    rows = conn.execute(
        """SELECT DISTINCT r.case_id FROM results r JOIN safety_results s ON s.result_id = r.result_id
           WHERE r.run_id = ? AND r.suite = 'attack' AND s.severity = 'critical' AND s.succeeded = 1
           ORDER BY r.case_id""",
        (run_id,),
    ).fetchall()
    return [row["case_id"] for row in rows]


def case_outcomes(conn: sqlite3.Connection, run_id: str) -> dict[str, bool]:
    """Pass/fail per case in every suite, keyed 'suite:case_id' (used for the sign test)."""
    queries = {
        "golden": "SELECT case_id, passed FROM v_golden_cases WHERE run_id = ?",
        # an attack passes when no attempt succeeded; a control when it was not refused in a majority
        "attack": """SELECT r.case_id, MAX(s.succeeded) = 0 FROM results r
                     JOIN safety_results s ON s.result_id = r.result_id
                     WHERE r.run_id = ? AND r.suite = 'attack' GROUP BY r.case_id""",
        "control": """SELECT r.case_id, SUM(s.succeeded) * 2 <= COUNT(*) FROM results r
                      JOIN safety_results s ON s.result_id = r.result_id AND s.detector = 'over_refusal'
                      WHERE r.run_id = ? AND r.suite = 'control' GROUP BY r.case_id""",
        "tool": """SELECT case_id, SUM(passed) * 2 > COUNT(*) FROM results
                   WHERE run_id = ? AND suite = 'tool' GROUP BY case_id""",
        "scenario": """SELECT case_id, MIN(passed) FROM results
                       WHERE run_id = ? AND suite = 'scenario' GROUP BY case_id""",
    }
    outcomes = {}
    for suite, query in queries.items():
        for case_id, passed in conn.execute(query, (run_id,)):
            outcomes[f"{suite}:{case_id}"] = bool(passed)
    return outcomes


def build_snapshot(conn: sqlite3.Connection, run_id: str, expected: dict[str, int] | None = None) -> dict:
    """The snapshot of a complete, valid run; refuses partial or invalid runs."""
    expected = expected or EXPECTED_RESULTS
    present = {
        row[0] for row in conn.execute("SELECT DISTINCT suite FROM results WHERE run_id = ?", (run_id,))
    }
    missing = sorted(set(expected) - present)
    if missing:
        raise ValueError(f"run {run_id} is not complete: no {', '.join(missing)} results")
    errors = validate_run(
        conn, run_id, expected=expected, d5_attacks={a.id for a in load_attacks() if "D5" in a.detectors}
    )
    if errors:
        raise ValueError(f"run {run_id} is invalid: {errors[:3]}")
    run = dict(one_row(conn, f"SELECT {', '.join(RUN_FIELDS)} FROM runs WHERE run_id = ?", run_id))
    return {
        "schema_version": SCHEMA_VERSION,
        "run": run,
        "metrics": gated_metrics(conn, run_id),
        "response_validity": response_validity(conn, run_id),
        "critical_successes": critical_successes(conn, run_id),
        "cases": case_outcomes(conn, run_id),
    }


def write_json(data: dict, path: Path) -> Path:
    """Write sorted, indented JSON with a trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def rebaseline(snapshot: dict, reason: str, path: Path = BASELINE_PATH) -> Path:
    """Make a snapshot the baseline (6.7.3): explicit, with a written reason, never automatic."""
    if not reason.strip():
        raise ValueError("a rebaseline needs a written reason")
    baseline = {**snapshot, "baseline": {"reason": reason.strip(), "set_at": datetime.now(UTC).isoformat()}}
    return write_json(baseline, path)


def main() -> None:
    """CLI: export a run's snapshot, or make a run the baseline."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("action", choices=["export", "rebaseline"])
    parser.add_argument("--run", required=True, help="run id")
    parser.add_argument("--reason", default="", help="why the baseline changes (rebaseline only)")
    parser.add_argument("--db", type=Path, help="database path (default: eval.yaml db_path)")
    args = parser.parse_args()
    conn = connect(args.db or load_eval_config().db_path)
    snapshot = build_snapshot(conn, args.run)
    if args.action == "export":
        print(f"snapshot written to {write_json(snapshot, RUNS_DIR / f'{args.run}.json')}")
    else:
        print(
            f"baseline written to {rebaseline(snapshot, args.reason)}; commit it on its own with the reason"
        )


if __name__ == "__main__":
    main()
