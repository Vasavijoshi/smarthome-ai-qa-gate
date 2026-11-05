"""Gate reports (report 6.7.1 step 6): a Markdown page for people and a JSON file for machines."""

import json
from pathlib import Path

from qa_gate.datasets.loader import REPO_ROOT
from qa_gate.gate import GateResult, as_dict

REPORTS_DIR = REPO_ROOT / "reports"


def fmt(value: float | None, unit: str = "") -> str:
    """A number for the report table; rates as percentages, latency in ms."""
    if value is None:
        return "n/a"
    return f"{value:.0f} ms" if unit == "ms" else f"{value * 100:.1f}%"


def counts(snapshot: dict, metric: str) -> str:
    """'k of n' for a rate metric, or an empty string."""
    entry = snapshot["metrics"].get(metric, {})
    return f"{entry['numerator']} of {entry['denominator']}" if "denominator" in entry else ""


def rule_rows(baseline: dict, candidate: dict, result: GateResult) -> list[str]:
    """One Markdown table row per rule."""
    rows = [
        "| Rule | Baseline | Candidate | Worse by | Margin | Result | Note |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in result.rules:
        unit = "ms" if r.rule == "p95_latency" else ""
        if r.baseline is None:  # absolute rules
            base, cand = "", str(r.candidate)
        else:
            base = f"{fmt(r.baseline, unit)} ({counts(baseline, r.rule)})"
            cand = f"{fmt(r.candidate, unit)} ({counts(candidate, r.rule)})"
        change = "" if r.change is None else f"{r.change:.1f}"
        verdict = "**TRIPPED**" if r.tripped else "ok"
        rows.append(f"| {r.rule} | {base} | {cand} | {change} | {r.margin} | {verdict} | {r.note} |")
    return rows


def markdown(baseline: dict, candidate: dict, result: GateResult) -> str:
    """The human-readable gate report."""
    lines = [
        f"# Gate: {candidate['run']['run_id']} vs baseline {baseline['run']['run_id']}",
        "",
        f"**Verdict: {result.verdict}** (exit code {result.exit_code})",
        "",
        f"- gate.yaml commit: `{result.info.get('gate_yaml_commit', 'unknown')}`",
        f"- judge margin factor: {result.info.get('judge_margin_factor', 'n/a')}",
    ]
    if result.reasons:
        lines += ["", "## Why the runs are not comparable", *[f"- {r}" for r in result.reasons]]
    if result.rules:
        lines += ["", "## Rules", *rule_rows(baseline, candidate, result)]
    if result.flips:
        f = result.flips
        lines += [
            "",
            "## Supporting statistics (information only)",
            f"- cases compared: {f['cases_compared']}; pass to fail (b): {len(f['pass_to_fail'])}; "
            f"fail to pass (c): {len(f['fail_to_pass'])}",
            f"- exact two-sided sign test: p = {f['sign_test_p']:.3f}",
            f"- pass to fail: {', '.join(f['pass_to_fail']) or 'none'}",
            f"- fail to pass: {', '.join(f['fail_to_pass']) or 'none'}",
        ]
    validity = result.info.get("response_validity_by_suite", {})
    lines += ["", "## Response validity per suite (information only)"]
    lines += [f"- {suite}: {v['numerator']} of {v['denominator']}" for suite, v in sorted(validity.items())]
    if "aa_widened_margins" in result.info:
        lines += ["", "## A/A widening this result implies (6.8.1)"]
        lines += [f"- {m}: {v:.2f}" for m, v in result.info["aa_widened_margins"].items()]
    return "\n".join(lines) + "\n"


def write_reports(
    baseline: dict, candidate: dict, result: GateResult, out_dir: Path = REPORTS_DIR
) -> list[Path]:
    """Write reports/gate_<candidate>_vs_<baseline>.md and .json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"gate_{candidate['run']['run_id']}_vs_{baseline['run']['run_id']}"
    md_path, json_path = out_dir / f"{stem}.md", out_dir / f"{stem}.json"
    md_path.write_text(markdown(baseline, candidate, result), encoding="utf-8")
    json_path.write_text(json.dumps(as_dict(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return [md_path, json_path]
