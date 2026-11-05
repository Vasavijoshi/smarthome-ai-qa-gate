"""Regression gate (report 6.7): compare a candidate snapshot with the baseline; PASS, FAIL or INCOMPARABLE.

Run: python -m qa_gate.gate --candidate runs/<run_id>.json [--baseline baselines/baseline.json]
Exit codes: 0 PASS, 1 FAIL, 2 INCOMPARABLE.
"""

import argparse
import json
import math
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml
from scipy.stats import binomtest

from qa_gate.config import load_eval_config, version_config_hash
from qa_gate.datasets.loader import REPO_ROOT

GATE_PATH = REPO_ROOT / "gate.yaml"
EXIT_CODES = {"PASS": 0, "FAIL": 1, "INCOMPARABLE": 2}
TOLERANCE_PP = 1e-9  # a change exactly at the margin passes; only "more than" the margin trips


@dataclass
class RuleResult:
    """One gate rule applied to the two snapshots."""

    rule: str
    baseline: float | None
    candidate: float | None
    change: float | None
    margin: float | None
    tripped: bool
    note: str = ""


@dataclass
class GateResult:
    """The verdict and everything that led to it."""

    verdict: str
    reasons: list[str] = field(default_factory=list)
    rules: list[RuleResult] = field(default_factory=list)
    flips: dict = field(default_factory=dict)
    info: dict = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        """0 PASS, 1 FAIL, 2 INCOMPARABLE."""
        return EXIT_CODES[self.verdict]

    @property
    def tripped(self) -> list[str]:
        """Names of the rules that tripped."""
        return [r.rule for r in self.rules if r.tripped]


def load_gate_config(path: Path = GATE_PATH) -> dict:
    """Read gate.yaml."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def comparability(baseline: dict, candidate: dict, config: dict) -> list[str]:
    """Step 1: the runs must share these fields and have every gated metric, or comparing proves nothing."""
    fields = config["gate"]["comparable_on"] + config.get("extra_comparable_on", [])
    reasons = [
        f"{f} differs: baseline {baseline['run'].get(f)!r}, candidate {candidate['run'].get(f)!r}"
        for f in fields
        if baseline["run"].get(f) != candidate["run"].get(f)
    ]
    for snapshot, name in [(baseline, "baseline"), (candidate, "candidate")]:
        missing = [
            m for m in config["gate"]["relative"] if snapshot["metrics"].get(m, {}).get("value") is None
        ]
        reasons += [f"{name} has no value for {m}" for m in missing]
    return reasons


def integrity(candidate: dict, recompute=version_config_hash) -> list[str]:
    """Step 2: the candidate's version files must still hash to the snapshot's config hash."""
    run = candidate["run"]
    current = recompute(run["assistant_version"])
    if current != run["config_hash"]:
        return [f"config hash of {run['assistant_version']} changed since the run (snapshot is stale)"]
    return []


def absolute_rules(baseline: dict, candidate: dict, config: dict) -> list[RuleResult]:
    """Step 3: response validity 100% (golden M1), no invalid-state incidents, no new critical successes."""
    limits = config["gate"]["absolute"]
    validity = candidate["response_validity"][config.get("response_validity_suite", "golden")]["value"]
    incidents = candidate["metrics"]["invalid_state_incidents"]["value"]
    new_critical = sorted(set(candidate["critical_successes"]) - set(baseline["critical_successes"]))
    known = sorted(set(candidate["critical_successes"]) & set(baseline["critical_successes"]))
    return [
        RuleResult("response_validity", None, validity, None, limits["response_validity_min"],
                   validity < limits["response_validity_min"]),
        RuleResult("invalid_state_incidents", None, incidents, None, limits["invalid_state_incidents_max"],
                   incidents > limits["invalid_state_incidents_max"]),
        RuleResult("new_critical_attack_successes", None, len(new_critical), None,
                   limits["new_critical_attack_successes_max"],
                   len(new_critical) > limits["new_critical_attack_successes_max"],
                   f"new: {new_critical}; known issues in the baseline: {known}"),
    ]  # fmt: skip


def calibration_factor(config: dict, root: Path = REPO_ROOT) -> tuple[float | str, str]:
    """Judge margin factor (6.3.7): 1.0, 2.0 or 'advisory'; 1.0, provisional, before calibration."""
    path = root / config.get("calibration_result", "reports/judge_calibration.json")
    if not path.exists():
        return 1.0, "provisional (judge not calibrated yet)"
    kappa = json.loads(path.read_text(encoding="utf-8"))["cohen_kappa"]
    if kappa >= 0.6:
        return 1.0, f"calibrated (kappa {kappa:.2f})"
    if kappa >= 0.4:
        return 2.0, f"calibrated (kappa {kappa:.2f}): doubled margins"
    return "advisory", f"calibrated (kappa {kappa:.2f}): judge metrics advisory only"


def worse_by(spec: dict, base: float, cand: float) -> tuple[float, float]:
    """How much worse the candidate is, and the allowed margin, in the rule's own unit (points or percent)."""
    if "max_drop_pp" in spec:
        return (base - cand) * 100, spec["max_drop_pp"]
    if "max_rise_pp" in spec:
        return (cand - base) * 100, spec["max_rise_pp"]
    return ((cand - base) / base * 100 if base else math.inf), spec["max_rise_pct"]


def relative_rules(
    baseline: dict, candidate: dict, config: dict, factor: float | str, status: str
) -> list[RuleResult]:
    """Step 4: the candidate must not be worse than the baseline by more than each margin."""
    results = []
    for metric, spec in config["gate"]["relative"].items():
        base, cand = baseline["metrics"][metric]["value"], candidate["metrics"][metric]["value"]
        change, margin = worse_by(spec, base, cand)
        note = ""
        if metric in config.get("judge_based", []):
            note = f"judge-based, {status}"
            if factor == "advisory":
                results.append(RuleResult(metric, base, cand, change, margin, False, note + "; not enforced"))
                continue
            margin *= factor
        results.append(RuleResult(metric, base, cand, change, margin, change > margin + TOLERANCE_PP, note))
    return results


def flip_statistics(baseline: dict, candidate: dict) -> dict:
    """Step 5, information only: pass->fail (b), fail->pass (c), and the exact two-sided sign test."""
    shared = sorted(set(baseline["cases"]) & set(candidate["cases"]))
    b = [c for c in shared if baseline["cases"][c] and not candidate["cases"][c]]
    c = [c for c in shared if not baseline["cases"][c] and candidate["cases"][c]]
    p_value = binomtest(len(b), len(b) + len(c), 0.5).pvalue if b or c else 1.0
    return {"cases_compared": len(shared), "pass_to_fail": b, "fail_to_pass": c, "sign_test_p": p_value}


def evaluate(
    baseline: dict, candidate: dict, config: dict, recompute=version_config_hash, root: Path = REPO_ROOT
) -> GateResult:
    """Steps 1 to 6 of report 6.7.1 on two snapshots."""
    info = {"response_validity_by_suite": candidate.get("response_validity", {})}
    reasons = comparability(baseline, candidate, config) + integrity(candidate, recompute)
    if reasons:
        return GateResult("INCOMPARABLE", reasons=reasons, info=info)
    factor, status = calibration_factor(config, root)
    info["judge_margin_factor"] = {"factor": factor, "status": status}
    rules = absolute_rules(baseline, candidate, config) + relative_rules(
        baseline, candidate, config, factor, status
    )
    verdict = "FAIL" if any(r.tripped for r in rules) else "PASS"
    return GateResult(verdict, rules=rules, flips=flip_statistics(baseline, candidate), info=info)


def aa_widened_margins(result: GateResult, baseline: dict, config: dict) -> dict[str, float]:
    """A/A policy (6.8.1): widen each tripped relative margin to the A/A change, up to a whole case."""
    relative = config["gate"]["relative"]
    widened = {}
    for rule in result.rules:
        if not rule.tripped or rule.rule not in relative:
            continue
        if "max_rise_pct" in relative[rule.rule]:
            widened[rule.rule] = float(math.ceil(rule.change - TOLERANCE_PP))
            continue
        points_per_case = 100 / baseline["metrics"][rule.rule]["denominator"]
        widened[rule.rule] = math.ceil(rule.change / points_per_case - TOLERANCE_PP) * points_per_case
    return widened


def git_output(*args: str) -> str:
    """Run a git command in the repository and return its trimmed output."""
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=REPO_ROOT).stdout.strip()


def gate_file_commit(path: Path = GATE_PATH) -> str:
    """The git commit that last changed gate.yaml, plus '+modified' if it has uncommitted edits."""
    commit = git_output("log", "-1", "--format=%H", "--", str(path))
    dirty = git_output("status", "--porcelain", "--", str(path))
    return f"{commit or 'uncommitted'}{'+modified' if dirty else ''}"


def record_decision(conn: sqlite3.Connection, baseline: dict, candidate: dict, result: GateResult) -> None:
    """Store the verdict in gate_decisions."""
    conn.execute(
        "INSERT INTO gate_decisions VALUES (?, ?, ?, ?, ?)",
        (baseline["run"]["run_id"], candidate["run"]["run_id"], result.verdict,
         json.dumps(result.tripped or result.reasons), datetime.now(UTC).isoformat()),
    )  # fmt: skip
    conn.commit()


def main() -> int:
    """CLI: run the gate, write the reports and the gate_decisions row, and exit 0, 1 or 2."""
    from qa_gate.report import write_reports  # local import keeps the gate logic free of report formatting
    from qa_gate.snapshot import BASELINE_PATH
    from qa_gate.store import connect

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument(
        "--no-db", action="store_true", help="do not write gate_decisions (CI has no database)"
    )
    parser.add_argument(
        "--aa", action="store_true", help="A/A check: also print the widened margins it implies"
    )
    parser.add_argument("--reports-dir", type=Path, help="where to write the reports (default: reports/)")
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    config = load_gate_config()
    result = evaluate(baseline, candidate, config)
    result.info["gate_yaml_commit"] = gate_file_commit()
    if args.aa and result.verdict == "FAIL":
        result.info["aa_widened_margins"] = aa_widened_margins(result, baseline, config)
    paths = write_reports(baseline, candidate, result, *([args.reports_dir] if args.reports_dir else []))
    if not args.no_db:
        record_decision(connect(load_eval_config().db_path), baseline, candidate, result)
    print(f"{result.verdict}: tripped {result.tripped or result.reasons}; reports {[str(p) for p in paths]}")
    return result.exit_code


def as_dict(result: GateResult) -> dict:
    """The result as plain data for the JSON report."""
    return {**asdict(result), "exit_code": result.exit_code, "tripped": result.tripped}


if __name__ == "__main__":
    sys.exit(main())
