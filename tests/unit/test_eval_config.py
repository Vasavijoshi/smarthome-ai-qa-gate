"""Unit tests for eval.yaml loading, judged-case sampling, the frozen hash and version config hashes."""

import shutil

import pytest

from qa_gate.config import (
    EVAL_CONFIG_PATH,
    PROMPTS_DIR,
    VERSIONS_DIR,
    judged_case_ids,
    load_eval_config,
    version_config_hash,
)
from qa_gate.datasets.loader import ATTACKS_PATH, CONTROLS_PATH, load_golden, load_raw
from qa_gate.datasets.validator import dataset_hash

pytestmark = pytest.mark.unit

CASES = load_golden()


def test_golden_set_matches_frozen_hash():
    """Golden v1.0 is frozen: any edit to data/golden_set.yaml must fail here until the author re-freezes."""
    assert dataset_hash(load_raw()) == load_eval_config().golden_dataset_hash


@pytest.mark.parametrize(
    "path, field",
    [(ATTACKS_PATH, "attacks_dataset_hash"), (CONTROLS_PATH, "controls_dataset_hash")],
)
def test_safety_datasets_match_frozen_hashes(path, field):
    """Attacks and controls v1.0 are frozen: any edit must fail here until the author re-freezes."""
    assert dataset_hash(load_raw(path)) == getattr(load_eval_config(), field)


def test_thresholds_match_report():
    assert load_eval_config().thresholds == {
        "fact_coverage": 0.8,
        "faithfulness": 0.7,
        "answer_relevancy": 0.7,
        "correctness": 0.6,
        "appropriate_refusal": 0.7,
    }


def test_judge_settings():
    config = load_eval_config()
    assert (config.judge_model, config.judge_temperature, config.seeds) == ("qwen2.5:7b", 0, [11, 22, 33])


def test_full_scope_judges_every_case():
    assert judged_case_ids(CASES, "full", 2026, 10) == {c.id for c in CASES}


@pytest.mark.parametrize("seed", [1, 7, 2026, 42, 99])
def test_sampled_scope_is_smoke_plus_ten(seed):
    judged = judged_case_ids(CASES, "sampled", seed, 10)
    smoke = {c.id for c in CASES if c.smoke}
    assert len(judged) == 30 and smoke <= judged


def test_sampled_scope_is_repeatable_and_seed_dependent():
    assert judged_case_ids(CASES, "sampled", 2026, 10) == judged_case_ids(CASES, "sampled", 2026, 10)
    assert judged_case_ids(CASES, "sampled", 2026, 10) != judged_case_ids(CASES, "sampled", 1, 10)


def test_invalid_scope_is_rejected(tmp_path):
    text = EVAL_CONFIG_PATH.read_text(encoding="utf-8")
    bad = tmp_path / "eval.yaml"
    bad.write_text(text.replace("scope: full", "scope: half"), encoding="utf-8")
    with pytest.raises(ValueError):
        load_eval_config(bad)


@pytest.fixture
def version_copy(tmp_path):
    """A writable copy of the versions and prompts folders."""
    shutil.copytree(VERSIONS_DIR, tmp_path / "versions")
    shutil.copytree(PROMPTS_DIR, tmp_path / "prompts")
    return tmp_path


def test_config_hash_is_stable(version_copy):
    assert version_config_hash(
        "v1.0", version_copy / "versions", version_copy / "prompts"
    ) == version_config_hash("v1.0")


@pytest.mark.parametrize("version", ["v1.1", "v2.0", "v3.0", "v4.0"])
def test_every_version_has_its_own_hash(version):
    assert version_config_hash(version) != version_config_hash("v1.0")


def test_config_hash_changes_when_prompt_changes(version_copy):
    prompt = version_copy / "prompts" / "v1.0.txt"
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\nR9. Be brief.", encoding="utf-8")
    assert version_config_hash(
        "v1.0", version_copy / "versions", version_copy / "prompts"
    ) != version_config_hash("v1.0")


def test_config_hash_ignores_line_endings(version_copy):
    path = version_copy / "versions" / "v1.0.yaml"
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert version_config_hash(
        "v1.0", version_copy / "versions", version_copy / "prompts"
    ) == version_config_hash("v1.0")
