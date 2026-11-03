"""Unit tests for the SQLite store: pass rules and summaries in the views, and post-run validation."""

from types import SimpleNamespace

import pytest

from qa_gate.metrics.deterministic import MetricRow
from qa_gate.store import add_golden_case, add_metrics, add_result, connect, start_run, validate_run

pytestmark = pytest.mark.unit

JUDGE_DEFAULTS = {
    "answer": {"faithfulness": 0.9, "answer_relevancy": 0.9, "correctness": 0.9},
    "correct_premise": {"faithfulness": 0.9, "correctness": 0.9},
    "refuse_unknown": {"appropriate_refusal": 0.9},
}
THRESHOLDS = {"faithfulness": 0.7, "answer_relevancy": 0.7, "correctness": 0.6, "appropriate_refusal": 0.7}


@pytest.fixture
def conn(tmp_path):
    """An empty store with one run of three repeats."""
    connection = connect(tmp_path / "qa.db")
    start_run(
        connection,
        {
            "assistant_version": "v1.0",
            "model": "m",
            "judge_model": "j",
            "judge_scope": "full",
            "dataset_hash": "d",
            "kb_version": "k",
            "config_hash": "c",
            "git_commit": "g",
            "repeats": 3,
        },
    )
    return connection


def run_of(conn) -> str:
    """The id of the single run in the test store."""
    return conn.execute("SELECT run_id FROM runs").fetchone()[0]


def add_case(
    conn,
    case_id: str,
    behavior: str = "answer",
    passes: tuple[int, ...] = (1, 1, 1),
    forbidden_ok: tuple[int, ...] = (1, 1, 1),
    refused: tuple[int, ...] = (0, 0, 0),
    judge: dict | None = None,
    judged: bool = True,
    hit: int | None = 1,
    latencies: tuple[int, ...] = (100, 100, 100),
) -> None:
    """Insert one synthetic golden case with three repeats."""
    meta = SimpleNamespace(id=case_id, category="factual", difficulty="easy", expected_behavior=behavior,
                           source_chunks=["a#b"] if hit is not None else [])  # fmt: skip
    add_golden_case(conn, run_of(conn), meta, judged)
    for i in range(3):
        rows = [
            MetricRow("response_validity", 1.0, None, True),
            MetricRow("forbidden_claims", float(forbidden_ok[i]), None, bool(forbidden_ok[i])),
            MetricRow("refusal_detected", float(refused[i]), None, bool(refused[i])),
        ]
        if hit is not None:
            rows.append(MetricRow("retrieval_hit", float(hit), None, bool(hit)))
        row = {
            "run_id": run_of(conn), "suite": "golden", "case_id": case_id,
            "repeat_idx": i, "passed": passes[i],
            "latency_ms": latencies[i], "prompt_tokens": 1, "completion_tokens": 1, "reply": "r",
            "error_type": None, "retrieved_json": "[]", "eval_ms": 5,
        }  # fmt: skip
        result_id = add_result(conn, row, rows, [])
        if i == 0 and judged:
            scores = {**JUDGE_DEFAULTS[behavior], **(judge or {})}
            add_metrics(
                conn,
                result_id,
                [MetricRow(m, s, THRESHOLDS[m], s >= THRESHOLDS[m]) for m, s in scores.items()],
            )
    conn.commit()


def case(conn, case_id: str) -> dict:
    """One row of v_golden_cases."""
    return dict(conn.execute("SELECT * FROM v_golden_cases WHERE case_id = ?", (case_id,)).fetchone())


def summary(conn) -> dict:
    """The run's row of v_run_summary."""
    return dict(conn.execute("SELECT * FROM v_run_summary").fetchone())


# ---- case pass rule (6.3.3 and 6.3.5) ----
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"passes": (1, 1, 0)},
        {"passes": (0, 1, 1)},
        {"behavior": "refuse_unknown", "hit": None, "refused": (1, 1, 1)},
        {
            "behavior": "correct_premise",
            "judge": {"faithfulness": 0.2},
        },  # faithfulness is not in its pass rule
        {"judged": False, "passes": (1, 0, 1)},
    ],
)
def test_case_passes(conn, kwargs):
    add_case(conn, "C1", **kwargs)
    assert case(conn, "C1")["passed"] == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"passes": (1, 0, 0)},
        {"passes": (0, 0, 0)},
        {"judge": {"faithfulness": 0.69}},
        {"judge": {"answer_relevancy": 0.5}},
        {"judge": {"correctness": 0.59}},
        {"behavior": "refuse_unknown", "hit": None, "judge": {"appropriate_refusal": 0.3}},
        {"behavior": "correct_premise", "judge": {"correctness": 0.1}},
    ],
)
def test_case_fails(conn, kwargs):
    add_case(conn, "C1", **kwargs)
    assert case(conn, "C1")["passed"] == 0


# ---- M11 stability and flaky cases ----
@pytest.mark.parametrize(
    "passes, stability, flaky",
    [
        ((1, 1, 1), 1.0, 0),
        ((0, 0, 0), 1.0, 0),
        ((1, 1, 0), 2 / 3, 1),
        ((0, 0, 1), 2 / 3, 1),
        ((1, 0, 1), 2 / 3, 1),
    ],
)
def test_stability_and_flaky(conn, passes, stability, flaky):
    add_case(conn, "C1", passes=passes)
    row = case(conn, "C1")
    assert row["stability"] == pytest.approx(stability) and row["flaky"] == flaky


def test_flaky_view_lists_only_split_cases(conn):
    add_case(conn, "STABLE")
    add_case(conn, "SPLIT", passes=(1, 0, 1))
    assert [r["case_id"] for r in conn.execute("SELECT case_id FROM v_flaky_cases")] == ["SPLIT"]


# ---- hallucination parts (6.3.4) ----
def test_hallucination_parts(conn):
    add_case(conn, "ANS-OK")
    add_case(conn, "ANS-UNFAITHFUL", judge={"faithfulness": 0.5})
    add_case(conn, "ANS-FORBIDDEN", forbidden_ok=(0, 0, 1))
    add_case(conn, "UNA-OK", behavior="refuse_unknown", hit=None, refused=(1, 1, 1))
    add_case(conn, "UNA-NO-REFUSAL", behavior="refuse_unknown", hit=None, refused=(0, 0, 1))
    add_case(
        conn,
        "UNA-INVENTS",
        behavior="refuse_unknown",
        hit=None,
        refused=(1, 1, 1),
        judge={"appropriate_refusal": 0.2},
    )
    s = summary(conn)
    assert (s["unsupported_cases"], s["fabricated_cases"]) == (2, 2)
    assert s["hallucination_rate"] == pytest.approx(4 / 6)
    assert s["failure_to_abstain_rate"] == pytest.approx(2 / 3)


def test_false_refusal_needs_a_majority(conn):
    add_case(conn, "A1", refused=(1, 1, 0))
    add_case(conn, "A2", refused=(1, 0, 0))
    s = summary(conn)
    assert (s["false_refusals"], s["false_refusal_rate"]) == (1, 0.5)


def test_retrieval_rate_ignores_cases_without_sources(conn):
    add_case(conn, "A1", hit=1)
    add_case(conn, "A2", hit=0)
    add_case(conn, "U1", behavior="refuse_unknown", hit=None, refused=(1, 1, 1))
    s = summary(conn)
    assert (s["retrieval_hits"], s["retrieval_cases"], s["retrieval_hit_rate"]) == (1, 2, 0.5)


def test_latency_percentiles_use_nearest_rank(conn):
    for i in range(1, 8):  # 21 latencies: 10, 20, ..., 210
        add_case(conn, f"C{i}", latencies=tuple(10 * (3 * (i - 1) + k) for k in (1, 2, 3)))
    s = summary(conn)
    assert (s["p50_latency_ms"], s["p95_latency_ms"]) == (110, 200)


def test_category_summary_has_both_dimensions(conn):
    add_case(conn, "C1")
    add_case(conn, "C2", passes=(0, 0, 0))
    rows = {(r["dimension"], r["value"]): r for r in conn.execute("SELECT * FROM v_category_summary")}
    assert rows[("category", "factual")]["cases_passed"] == 1 and rows[("difficulty", "easy")]["cases"] == 2


# ---- post-run validation (6.6.2) ----
def test_complete_run_is_valid(conn):
    for i in range(4):
        add_case(conn, f"C{i}")
    assert validate_run(conn, run_of(conn), expected_cases=4) == []
    assert conn.execute("SELECT status FROM runs").fetchone()[0] == "valid"


def test_unjudged_cases_need_no_judge_scores(conn):
    add_case(conn, "C1", judged=False)
    assert validate_run(conn, run_of(conn), expected_cases=1) == []


@pytest.mark.parametrize(
    "break_it, message",
    [
        (
            lambda c: c.execute("DELETE FROM results WHERE case_id = 'C0' AND repeat_idx = 2"),
            "golden results",
        ),
        (
            lambda c: c.execute("DELETE FROM metric_scores WHERE metric = 'faithfulness'"),
            "missing judge scores",
        ),
        (
            lambda c: c.execute("UPDATE metric_scores SET score = NULL WHERE metric = 'correctness'"),
            "missing judge",
        ),
        (
            lambda c: c.execute("UPDATE metric_scores SET score = 1.5 WHERE metric = 'answer_relevancy'"),
            "outside",
        ),
        (lambda c: c.execute("UPDATE results SET latency_ms = 0 WHERE case_id = 'C1'"), "non-positive"),
        (lambda c: c.execute("INSERT INTO metric_scores VALUES (9999, 'x', 0.5, NULL, 1, '')"), "orphan"),
        (lambda c: c.execute("UPDATE runs SET config_hash = ''"), "config_hash"),
    ],
)
def test_broken_runs_are_invalid(conn, break_it, message):
    for i in range(2):
        add_case(conn, f"C{i}")
    conn.execute("PRAGMA foreign_keys = OFF")
    break_it(conn)
    errors = validate_run(conn, run_of(conn), expected_cases=2)
    assert any(message in e for e in errors), errors
    assert conn.execute("SELECT status FROM runs").fetchone()[0] == "invalid"
