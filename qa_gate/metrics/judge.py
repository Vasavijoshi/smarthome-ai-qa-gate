"""Layer 2: DeepEval judge metrics (M6 to M9) on a local Ollama judge.

Run: python -m qa_gate.metrics.judge
"""

import os

# Must be set before deepeval is imported: no telemetry, no update checks, no .env loading.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
os.environ.setdefault("DEEPEVAL_DISABLE_DOTENV", "1")

import argparse  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
import sqlite3  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import httpx  # noqa: E402
from deepeval.metrics import AnswerRelevancyMetric, ConversationalGEval, FaithfulnessMetric, GEval  # noqa: E402
from deepeval.models import DeepEvalBaseLLM  # noqa: E402
from deepeval.test_case import (  # noqa: E402
    ConversationalTestCase,
    LLMTestCase,
    MultiTurnParams,
    SingleTurnParams,
    Turn,
)
from pydantic import BaseModel  # noqa: E402

from qa_gate.config import EvalConfig, load_eval_config  # noqa: E402
from qa_gate.datasets.loader import (  # noqa: E402
    Attack,
    GoldenCase,
    Scenario,
    load_attacks,
    load_golden,
    load_scenarios,
    load_tool_cases,
)
from qa_gate.metrics.deterministic import MetricRow  # noqa: E402
from qa_gate.metrics.safety import keyword_hints  # noqa: E402
from qa_gate.store import (  # noqa: E402
    JUDGE_METRICS,
    add_metrics,
    add_safety_result,
    clear_blocked_help,
    connect,
    finish_run,
    latest_run_id,
    mark_failed,
    validate_run,
)

RUBRIC_DIR = Path(__file__).parent
OLLAMA_URL = "http://localhost:11434"


class OllamaJudge(DeepEvalBaseLLM):
    """DeepEval model backed by a local Ollama model; nothing leaves the laptop."""

    def __init__(self, model: str, temperature: float, seed: int, url: str = OLLAMA_URL) -> None:
        """Store the Ollama settings; temperature 0 and a fixed seed keep verdicts repeatable."""
        self.model_name, self.temperature, self.seed, self.url = model, temperature, seed, url
        super().__init__(model)

    def load_model(self) -> "OllamaJudge":
        """Nothing to load: Ollama serves the model."""
        return self

    def generate(self, prompt: str, schema: type[BaseModel] | None = None, **_: object) -> str | BaseModel:
        """Ask Ollama; with a schema, use structured output and return a parsed schema instance."""
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": self.temperature, "seed": self.seed},
        }
        if schema is not None:
            payload["format"] = schema.model_json_schema()
        response = httpx.post(f"{self.url}/api/chat", json=payload, timeout=600)
        response.raise_for_status()
        content = response.json()["message"]["content"]
        return schema.model_validate_json(content) if schema is not None else content

    async def a_generate(
        self, prompt: str, schema: type[BaseModel] | None = None, **kwargs: object
    ) -> str | BaseModel:
        """Async entry point; metrics run with async_mode=False, so this just calls generate."""
        return self.generate(prompt, schema=schema, **kwargs)

    def get_model_name(self) -> str:
        """Name shown in DeepEval output."""
        return f"ollama/{self.model_name}"


def rubric_steps(name: str) -> list[str]:
    """Read a versioned rubric file; one evaluation step per line."""
    text = (RUBRIC_DIR / f"rubric_{name}.txt").read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip()]


def build_metric(name: str, judge: OllamaJudge, threshold: float):
    """Create one DeepEval metric (M6 to M9) that runs synchronously on the local judge."""
    common = {"model": judge, "threshold": threshold, "async_mode": False}
    if name == "faithfulness":
        return FaithfulnessMetric(eval_mode="llm", include_reason=True, **common)
    if name == "answer_relevancy":
        return AnswerRelevancyMetric(eval_mode="llm", include_reason=True, **common)
    if name == "correctness":
        params = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT]
        return GEval(
            name="Correctness",
            evaluation_steps=rubric_steps("correctness"),
            evaluation_params=params,
            **common,
        )
    if name == "appropriate_refusal":
        params = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT]
        return GEval(
            name="AppropriateRefusal",
            evaluation_steps=rubric_steps("refusal"),
            evaluation_params=params,
            **common,
        )
    if name == "blocked_help":
        params = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT]
        return GEval(
            name="BlockedHelpDeclined",
            evaluation_steps=rubric_steps("blocked_help"),
            evaluation_params=params,
            **common,
        )
    if name == "honest_failure":
        params = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT]
        return GEval(
            name="HonestFailure",
            evaluation_steps=rubric_steps("honest_failure"),
            evaluation_params=params,
            **common,
        )
    if name == "conversation_memory":
        return ConversationalGEval(
            name="ConversationMemory",
            evaluation_steps=rubric_steps("conversation_memory"),
            evaluation_params=[MultiTurnParams.ROLE, MultiTurnParams.CONTENT],
            **common,
        )
    raise ValueError(f"Unknown judge metric: {name}")


def judge_reply(
    case: GoldenCase, reply: str, retrieved: list[str], judge: OllamaJudge, thresholds: dict
) -> list[MetricRow]:
    """Score one reply with every judge metric that applies to the case's expected behaviour."""
    test_case = LLMTestCase(
        input=case.question,
        actual_output=reply,
        expected_output=case.reference_answer,
        retrieval_context=retrieved or ["(no context retrieved)"],
    )
    rows = []
    for name in JUDGE_METRICS[case.expected_behavior]:
        metric = build_metric(name, judge, thresholds[name])
        try:
            metric.measure(test_case)
            score = float(metric.score)
            rows.append(
                MetricRow(name, score, thresholds[name], score >= thresholds[name], metric.reason or "")
            )
        except Exception as err:  # a judge failure is recorded as a missing score, never a pass
            rows.append(
                MetricRow(name, None, thresholds[name], False, f"judge error: {type(err).__name__}: {err}")
            )
    return rows


def results_to_judge(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    """Repeat-0 golden results of judged cases that have no judge scores yet."""
    return conn.execute(
        """SELECT r.result_id, r.case_id, r.reply, r.retrieved_json FROM results r
           JOIN golden_cases g ON g.run_id = r.run_id AND g.case_id = r.case_id
           WHERE r.run_id = ? AND r.suite = 'golden' AND r.repeat_idx = 0 AND g.judged = 1
             AND NOT EXISTS (SELECT 1 FROM metric_scores m WHERE m.result_id = r.result_id
                             AND m.metric IN ('faithfulness', 'answer_relevancy',
                                              'correctness', 'appropriate_refusal'))
           ORDER BY r.case_id""",
        (run_id,),
    ).fetchall()


def judge_run(conn: sqlite3.Connection, run_id: str, config: EvalConfig) -> None:
    """Judge every pending repeat-0 result of a run and add the judge time to eval_ms."""
    cases = {c.id: c for c in load_golden()}
    judge = OllamaJudge(config.judge_model, config.judge_temperature, config.judge_seed)
    pending = results_to_judge(conn, run_id)
    for i, row in enumerate(pending, start=1):
        start = time.perf_counter()
        retrieved = [r["text"] for r in json.loads(row["retrieved_json"] or "[]")]
        rows = judge_reply(cases[row["case_id"]], row["reply"] or "", retrieved, judge, config.thresholds)
        judge_ms = int((time.perf_counter() - start) * 1000)
        add_metrics(conn, row["result_id"], rows)
        conn.execute(
            "UPDATE results SET eval_ms = eval_ms + ? WHERE result_id = ?", (judge_ms, row["result_id"])
        )
        conn.commit()
        scores = ", ".join(f"{m.metric}={m.score}" for m in rows)
        print(f"[{i}/{len(pending)}] {row['case_id']} judged in {judge_ms / 1000:.1f}s: {scores}")


def judge_stability(
    conn: sqlite3.Connection, run_id: str, config: EvalConfig, n_cases: int = 10, times: int = 3
) -> None:
    """Re-judge n random judged cases several times and print the score spread (6.3.5)."""
    cases = {c.id: c for c in load_golden()}
    judge = OllamaJudge(config.judge_model, config.judge_temperature, config.judge_seed)
    rows = conn.execute(
        """SELECT r.case_id, r.reply, r.retrieved_json FROM results r
           JOIN golden_cases g ON g.run_id = r.run_id AND g.case_id = r.case_id
           WHERE r.run_id = ? AND r.suite = 'golden' AND r.repeat_idx = 0 AND g.judged = 1
           ORDER BY r.case_id""",
        (run_id,),
    ).fetchall()
    for row in random.Random(config.judge_seed).sample(rows, min(n_cases, len(rows))):
        retrieved = [r["text"] for r in json.loads(row["retrieved_json"] or "[]")]
        runs = [
            judge_reply(cases[row["case_id"]], row["reply"] or "", retrieved, judge, config.thresholds)
            for _ in range(times)
        ]
        for metric_runs in zip(*runs, strict=True):
            scores = [m.score for m in metric_runs if m.score is not None]
            spread = max(scores) - min(scores) if scores else None
            print(f"{row['case_id']} {metric_runs[0].metric}: scores={scores} spread={spread}")


def d5_attacks() -> dict[str, Attack]:
    """Attacks that use the D5 blocked-help detector, by id."""
    return {a.id: a for a in load_attacks() if "D5" in a.detectors}


def blocked_help_verdict(
    attack: Attack, reply: str, judge: OllamaJudge, config: EvalConfig
) -> tuple[bool, str]:
    """D5: the judge scores how well the reply declined; a score below the threshold means the help was given.

    Keyword hints are evidence only.
    """
    metric = build_metric("blocked_help", judge, config.blocked_decline_threshold)
    metric.measure(LLMTestCase(input="\n".join(attack.turns), actual_output=reply))
    succeeded = float(metric.score) < config.blocked_decline_threshold
    hints = keyword_hints(reply, config.blocked_topics[attack.topic])
    review = "REVIEW (judge and keywords disagree); " if succeeded != bool(hints) else ""
    return (
        succeeded,
        f"{review}decline score {float(metric.score):.2f}; keyword hints {hints}; reason: {metric.reason}",
    )


def judge_blocked_help(conn: sqlite3.Connection, run_id: str, config: EvalConfig) -> None:
    """Judge every attack attempt that needs D5 and has no D5 verdict yet; a judge error leaves it pending."""
    attacks = d5_attacks()
    rows = conn.execute(
        f"""SELECT r.result_id, r.case_id, r.repeat_idx, r.reply FROM results r
            WHERE r.run_id = ? AND r.suite = 'attack' AND r.case_id IN ({", ".join("?" * len(attacks))})
              AND NOT EXISTS (SELECT 1 FROM safety_results s
                              WHERE s.result_id = r.result_id AND s.detector = 'D5')
            ORDER BY r.case_id, r.repeat_idx""",
        (run_id, *sorted(attacks)),
    ).fetchall()
    judge = OllamaJudge(config.judge_model, config.judge_temperature, config.judge_seed)
    for i, row in enumerate(rows, start=1):
        attack, start = attacks[row["case_id"]], time.perf_counter()
        try:
            succeeded, evidence = blocked_help_verdict(attack, row["reply"] or "", judge, config)
        except Exception as err:  # never record a guess: the run stays invalid until this is judged
            print(f"[{i}/{len(rows)}] {row['case_id']} repeat {row['repeat_idx']}: judge error {err}")
            continue
        add_safety_result(conn, row["result_id"], attack.model_dump(), "D5", succeeded, evidence)
        if succeeded:
            mark_failed(conn, row["result_id"])
        judge_ms = int((time.perf_counter() - start) * 1000)
        conn.execute(
            "UPDATE results SET eval_ms = eval_ms + ? WHERE result_id = ?", (judge_ms, row["result_id"])
        )
        conn.commit()
        print(
            f"[{i}/{len(rows)}] {row['case_id']} repeat {row['repeat_idx']} "
            f"D5 succeeded={succeeded}: {evidence[:120]}"
        )


def honest_failure_verdict(question: str, reply: str, judge: OllamaJudge, config: EvalConfig) -> MetricRow:
    """T6 judge part: how honestly a reply reports an injected failure; below threshold = success claim."""
    threshold = config.honest_failure_threshold
    metric = build_metric("honest_failure", judge, threshold)
    metric.measure(LLMTestCase(input=question, actual_output=reply))
    score = float(metric.score)
    return MetricRow("T6_honest_judge", score, threshold, score >= threshold, metric.reason or "")


def judge_honest_failures(conn: sqlite3.Connection, run_id: str, config: EvalConfig) -> None:
    """Judge every injected-failure attempt without a T6 verdict; a judge error leaves it pending."""
    cases = {c.id: c for c in load_tool_cases()}
    rows = conn.execute(
        """SELECT r.result_id, r.case_id, r.repeat_idx, r.reply FROM results r
           JOIN metric_scores m ON m.result_id = r.result_id AND m.metric = 'T6_no_success_phrase'
           WHERE r.run_id = ? AND r.suite = 'tool'
             AND NOT EXISTS (SELECT 1 FROM metric_scores j
                             WHERE j.result_id = r.result_id AND j.metric = 'T6_honest_judge')
           ORDER BY r.case_id, r.repeat_idx""",
        (run_id,),
    ).fetchall()
    judge = OllamaJudge(config.judge_model, config.judge_temperature, config.judge_seed)
    for i, row in enumerate(rows, start=1):
        start = time.perf_counter()
        try:
            verdict = honest_failure_verdict(
                cases[row["case_id"]].turns[-1], row["reply"] or "", judge, config
            )
        except Exception as err:  # never record a guess: the run stays invalid until this is judged
            print(f"[{i}/{len(rows)}] {row['case_id']} repeat {row['repeat_idx']}: judge error {err}")
            continue
        add_metrics(conn, row["result_id"], [verdict])
        if not verdict.passed:
            mark_failed(conn, row["result_id"])
        judge_ms = int((time.perf_counter() - start) * 1000)
        conn.execute(
            "UPDATE results SET eval_ms = eval_ms + ? WHERE result_id = ?", (judge_ms, row["result_id"])
        )
        conn.commit()
        print(f"[{i}/{len(rows)}] {row['case_id']} repeat {row['repeat_idx']} T6 honest={verdict.passed}")


def conversation_turns(scenario: Scenario, replies: list[str]) -> list[Turn]:
    """The scripted user turns interleaved with the assistant's stored replies."""
    turns = []
    for user_turn, reply in zip(scenario.turns, replies, strict=False):
        turns += [
            Turn(role="user", content=user_turn.user),
            Turn(role="assistant", content=reply or "(no reply)"),
        ]
    return turns


def conversation_memory(
    scenario: Scenario, replies: list[str], judge: OllamaJudge, config: EvalConfig
) -> MetricRow:
    """Evidence only: does the last reply use what earlier turns established?"""
    threshold = config.conversation_memory_threshold
    metric = build_metric("conversation_memory", judge, threshold)
    metric.measure(ConversationalTestCase(turns=conversation_turns(scenario, replies)))
    score = float(metric.score)
    return MetricRow("conversation_memory", score, threshold, score >= threshold, metric.reason or "")


def judge_conversations(conn: sqlite3.Connection, run_id: str, config: EvalConfig) -> None:
    """Add the conversational evidence score to scenario results without one; it never changes a pass."""
    scenarios = {s.id: s for s in load_scenarios()}
    rows = conn.execute(
        """SELECT r.result_id, r.case_id, r.repeat_idx, r.reply FROM results r
           WHERE r.run_id = ? AND r.suite = 'scenario'
             AND NOT EXISTS (SELECT 1 FROM metric_scores m
                             WHERE m.result_id = r.result_id AND m.metric = 'conversation_memory')""",
        (run_id,),
    ).fetchall()
    judge = OllamaJudge(config.judge_model, config.judge_temperature, config.judge_seed)
    for row in rows:
        replies = (row["reply"] or "").split("\n---\n")
        try:
            add_metrics(
                conn,
                row["result_id"],
                [conversation_memory(scenarios[row["case_id"]], replies, judge, config)],
            )
            conn.commit()
        except Exception as err:  # evidence only: a judge error is reported and skipped
            print(f"{row['case_id']} repeat {row['repeat_idx']}: conversational check error {err}")


def main() -> None:
    """CLI: judge a stored run (default: the latest), then validate it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default="latest", help="run id, or 'latest'")
    parser.add_argument("--db", type=Path, help="database path (default: eval.yaml db_path)")
    parser.add_argument("--stability", action="store_true", help="re-judge 10 cases 3 times instead")
    parser.add_argument(
        "--rejudge-d5", action="store_true", help="delete the run's D5 verdicts and judge them again"
    )
    args = parser.parse_args()
    config = load_eval_config()
    conn = connect(args.db or config.db_path)
    run_id = latest_run_id(conn) if args.run == "latest" else args.run
    if args.stability:
        judge_stability(conn, run_id, config)
        return
    if args.rejudge_d5:
        print(f"removed {clear_blocked_help(conn, run_id)} D5 verdicts; re-judging the stored replies")
    judge_run(conn, run_id, config)
    judge_blocked_help(conn, run_id, config)
    judge_honest_failures(conn, run_id, config)
    judge_conversations(conn, run_id, config)
    finish_run(conn, run_id)  # the run ends after judging, not when the pytest pass ends
    # Each suite in the run must be complete (e.g. all 60 golden cases), so partial runs are invalid.
    errors = validate_run(conn, run_id, d5_attacks=set(d5_attacks()))
    print(f"run {run_id}: {'valid' if not errors else 'INVALID'}")
    for error in errors:
        print(f"  {error}")


if __name__ == "__main__":
    main()
