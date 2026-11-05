"""SQLite result store (report 6.6): write runs, results and scores, and validate a finished run."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from qa_gate.metrics.deterministic import MetricRow

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
# Expected results per suite in a full run (report 6.6.2); scenarios run 2 repeats, the others 3.
EXPECTED_RESULTS = {"golden": 180, "attack": 75, "control": 30, "tool": 69, "scenario": 20}
JUDGE_METRICS = {
    "answer": ["faithfulness", "answer_relevancy", "correctness"],
    "correct_premise": ["faithfulness", "correctness"],
    "refuse_unknown": ["appropriate_refusal"],
}


def now() -> str:
    """Current UTC time in ISO format."""
    return datetime.now(UTC).isoformat()


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open the database, creating tables and rebuilding views."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def start_run(conn: sqlite3.Connection, meta: dict) -> str:
    """Insert a run row and return its id, e.g. 'v1.0-20261103T120000'."""
    run_id = f"{meta['assistant_version']}-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    columns = ["run_id", *meta, "started_at"]
    conn.execute(
        f"INSERT INTO runs ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
        [run_id, *meta.values(), now()],
    )
    conn.commit()
    return run_id


def add_golden_case(conn: sqlite3.Connection, run_id: str, case, judged: bool) -> None:
    """Record a golden case's category, difficulty and whether it is judged in this run."""
    conn.execute(
        "INSERT OR REPLACE INTO golden_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            case.id,
            case.category,
            case.difficulty,
            case.expected_behavior,
            bool(case.source_chunks),
            judged,
        ),
    )


def add_result(conn: sqlite3.Connection, row: dict, metrics: list[MetricRow], tool_calls: list[dict]) -> int:
    """Insert one result with its metric scores and tool calls; return the result id."""
    cursor = conn.execute(
        """INSERT INTO results (run_id, suite, case_id, repeat_idx, passed, latency_ms, prompt_tokens,
               completion_tokens, reply, error_type, retrieved_json, eval_ms)
           VALUES (:run_id, :suite, :case_id, :repeat_idx, :passed, :latency_ms, :prompt_tokens,
               :completion_tokens, :reply, :error_type, :retrieved_json, :eval_ms)""",
        row,
    )
    result_id = cursor.lastrowid
    add_metrics(conn, result_id, metrics)
    conn.executemany(
        "INSERT INTO tool_audit VALUES (?, ?, ?, ?, ?)",
        [(result_id, c["tool"], json.dumps(c["arguments"]), c["status"], now()) for c in tool_calls],
    )
    conn.commit()
    return result_id


def add_metrics(conn: sqlite3.Connection, result_id: int, metrics: list[MetricRow]) -> None:
    """Insert metric scores for one result."""
    conn.executemany(
        "INSERT OR REPLACE INTO metric_scores VALUES (?, ?, ?, ?, ?, ?)",
        [(result_id, m.metric, m.score, m.threshold, m.passed, m.reason) for m in metrics],
    )


def finish_run(conn: sqlite3.Connection, run_id: str) -> None:
    """Stamp the run's finish time."""
    conn.execute("UPDATE runs SET finished_at = ? WHERE run_id = ?", (now(), run_id))
    conn.commit()


def latest_run_id(conn: sqlite3.Connection) -> str:
    """Return the most recently started run id."""
    row = conn.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
    if row is None:
        raise ValueError("no runs in the database")
    return row["run_id"]


def check_suite_counts(conn: sqlite3.Connection, run_id: str, expected: dict[str, int]) -> list[str]:
    """Every suite present in the run has exactly its expected number of results (report 6.6.2)."""
    counts = dict(
        conn.execute("SELECT suite, COUNT(*) FROM results WHERE run_id = ? GROUP BY suite", (run_id,))
    )
    if not counts:
        return ["run has no results"]
    errors = [f"unexpected suite {s}" for s in counts if s not in expected]
    return errors + [
        f"{counts[s]} {s} results, expected {expected[s]}"
        for s in counts
        if s in expected and counts[s] != expected[s]
    ]


def check_claims_judged(conn: sqlite3.Connection, run_id: str) -> list[str]:
    """Every failure attempt with a valid reply has a T6 judge verdict (invalid replies have none)."""
    missing = conn.execute(
        """SELECT r.case_id, r.repeat_idx FROM results r
           JOIN metric_scores m ON m.result_id = r.result_id AND m.metric = 'T6_no_success_phrase'
           WHERE r.run_id = ? AND r.suite = 'tool' AND r.error_type IS NULL
             AND NOT EXISTS (SELECT 1 FROM metric_scores j
                             WHERE j.result_id = r.result_id AND j.metric = 'T6_no_claim_judge')""",
        (run_id,),
    ).fetchall()
    return [f"{row['case_id']} repeat {row['repeat_idx']}: no T6 judge verdict" for row in missing]


def check_blocked_help_judged(conn: sqlite3.Connection, run_id: str, d5_attacks: set[str]) -> list[str]:
    """Every attempt of an attack that uses D5 has a D5 verdict (the judge pass has run)."""
    missing = conn.execute(
        f"""SELECT r.case_id, r.repeat_idx FROM results r
            WHERE r.run_id = ? AND r.suite = 'attack' AND r.case_id IN ({", ".join("?" * len(d5_attacks))})
              AND NOT EXISTS (SELECT 1 FROM safety_results s
                              WHERE s.result_id = r.result_id AND s.detector = 'D5')""",
        (run_id, *sorted(d5_attacks)),
    ).fetchall()
    return [f"{row['case_id']} repeat {row['repeat_idx']}: no D5 verdict" for row in missing]


def add_safety_result(
    conn: sqlite3.Connection, result_id: int, item: dict, detector: str, succeeded: bool, evidence: str
) -> None:
    """Insert one detector verdict for an attack or control attempt."""
    conn.execute(
        "INSERT INTO safety_results VALUES (?, ?, ?, ?, ?, ?, ?)",
        (result_id, item["id"], item["category"], item["severity"], succeeded, detector, evidence),
    )
    conn.commit()


def mark_failed(conn: sqlite3.Connection, result_id: int) -> None:
    """Set a result to not passed (for example after a late D5 verdict)."""
    conn.execute("UPDATE results SET passed = 0 WHERE result_id = ?", (result_id,))
    conn.commit()


def check_orphans(conn: sqlite3.Connection) -> list[str]:
    """No result without a run, and no metric score without a result."""
    orphan_results = conn.execute(
        "SELECT COUNT(*) FROM results WHERE run_id NOT IN (SELECT run_id FROM runs)"
    ).fetchone()[0]
    orphan_scores = conn.execute(
        "SELECT COUNT(*) FROM metric_scores WHERE result_id NOT IN (SELECT result_id FROM results)"
    ).fetchone()[0]
    return [
        f"{n} orphan {what}"
        for n, what in [(orphan_results, "results"), (orphan_scores, "metric scores")]
        if n
    ]


def check_judge_scores(conn: sqlite3.Connection, run_id: str) -> list[str]:
    """Every judged case has a non-null score for each applicable judge metric on repeat_idx 0."""
    errors = []
    rows = conn.execute(
        """SELECT g.case_id, g.expected_behavior, r.result_id FROM golden_cases g
           JOIN results r ON r.run_id = g.run_id AND r.case_id = g.case_id AND r.suite = 'golden'
           WHERE g.run_id = ? AND g.judged = 1 AND r.repeat_idx = 0""",
        (run_id,),
    ).fetchall()
    for row in rows:
        scored = {
            m["metric"]
            for m in conn.execute(
                "SELECT metric FROM metric_scores WHERE result_id = ? AND score IS NOT NULL",
                (row["result_id"],),
            )
        }
        missing = set(JUDGE_METRICS[row["expected_behavior"]]) - scored
        if missing:
            errors.append(f"{row['case_id']}: missing judge scores {sorted(missing)}")
    return errors


def check_ranges(conn: sqlite3.Connection, run_id: str) -> list[str]:
    """Scores lie in [0, 1] and latencies are positive."""
    bad_scores = conn.execute(
        """SELECT COUNT(*) FROM metric_scores m JOIN results r ON r.result_id = m.result_id
           WHERE r.run_id = ? AND m.score IS NOT NULL AND (m.score < 0 OR m.score > 1)""",
        (run_id,),
    ).fetchone()[0]
    bad_latency = conn.execute(
        "SELECT COUNT(*) FROM results WHERE run_id = ? AND latency_ms IS NOT NULL AND latency_ms <= 0",
        (run_id,),
    ).fetchone()[0]
    return [
        f"{n} {what}"
        for n, what in [(bad_scores, "scores outside [0, 1]"), (bad_latency, "non-positive latencies")]
        if n
    ]


def check_run_row(conn: sqlite3.Connection, run_id: str) -> list[str]:
    """The run has one dataset hash, one config hash and one repeat count."""
    row = conn.execute(
        "SELECT dataset_hash, config_hash, repeats FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return [f"run {run_id} not found"]
    return [f"run has no {key}" for key in ("dataset_hash", "config_hash", "repeats") if not row[key]]


def validate_run(
    conn: sqlite3.Connection,
    run_id: str,
    expected: dict[str, int] | None = None,
    d5_attacks: set[str] | None = None,
) -> list[str]:
    """Post-run data validation (6.6.2) for the suites in the run; marks the run valid or invalid."""
    errors = (
        check_run_row(conn, run_id)
        + check_suite_counts(conn, run_id, expected or EXPECTED_RESULTS)
        + check_orphans(conn)
        + check_judge_scores(conn, run_id)
        + check_ranges(conn, run_id)
        + (check_blocked_help_judged(conn, run_id, d5_attacks) if d5_attacks else [])
        + check_claims_judged(conn, run_id)
    )
    conn.execute("UPDATE runs SET status = ? WHERE run_id = ?", ("invalid" if errors else "valid", run_id))
    conn.commit()
    return errors


def clear_blocked_help(conn: sqlite3.Connection, run_id: str) -> int:
    """Delete a run's D5 verdicts for re-judging; pass/fail is recomputed from the rule detectors."""
    deleted = conn.execute(
        """DELETE FROM safety_results WHERE detector = 'D5'
           AND result_id IN (SELECT result_id FROM results WHERE run_id = ? AND suite = 'attack')""",
        (run_id,),
    ).rowcount
    conn.execute(
        """UPDATE results SET passed = NOT EXISTS (
               SELECT 1 FROM safety_results s WHERE s.result_id = results.result_id AND s.succeeded = 1)
           WHERE run_id = ? AND suite = 'attack'""",
        (run_id,),
    )
    conn.commit()
    return deleted


def clear_claim_verdicts(conn: sqlite3.Connection, run_id: str) -> int:
    """Delete a run's T6 judge verdicts (old and new names) and recompute tool pass/fail."""
    deleted = conn.execute(
        """DELETE FROM metric_scores WHERE metric IN ('T6_honest_judge', 'T6_no_claim_judge')
           AND result_id IN (SELECT result_id FROM results WHERE run_id = ? AND suite = 'tool')""",
        (run_id,),
    ).rowcount
    recompute_tool_passed(conn, run_id)
    return deleted


def recompute_tool_passed(conn: sqlite3.Connection, run_id: str) -> None:
    """A tool attempt passes when none of its metric rows failed."""
    conn.execute(
        """UPDATE results SET passed = NOT EXISTS (
               SELECT 1 FROM metric_scores m WHERE m.result_id = results.result_id AND m.passed = 0)
           WHERE run_id = ? AND suite = 'tool'""",
        (run_id,),
    )
    conn.commit()
