"""Unit tests for version configs: the five versions must match report section 6.1.8."""

import pytest

from assistant.config import kb_version, load_version

pytestmark = pytest.mark.unit

VERSIONS = ["v1.0", "v1.1", "v2.0", "v3.0", "v4.0"]


@pytest.mark.parametrize("version", VERSIONS)
def test_every_version_loads_with_its_prompt(version):
    config = load_version(version)
    assert config.version == version
    assert "SENTINEL-PRM-4417" in config.prompt_text


def test_unknown_version_raises():
    with pytest.raises(ValueError):
        load_version("v9.9")


def test_v2_removes_only_grounding_rule():
    v2 = load_version("v2.0").prompt_text
    assert "R2." not in v2
    assert all(f"R{i}." in v2 for i in (1, 3, 4, 5, 6, 7, 8))


def test_v3_changes_retrieval_only():
    v1, v3 = load_version("v1.0"), load_version("v3.0")
    assert (v3.top_k, v3.max_chunk_words) == (1, 400)
    assert v3.prompt_text == v1.prompt_text
    assert v3.validate_args and v3.require_unlock_confirmation


def test_v4_removes_guardrails_and_checks():
    v4 = load_version("v4.0")
    assert "R4." not in v4.prompt_text and "R5." not in v4.prompt_text
    assert not v4.validate_args and not v4.require_unlock_confirmation


def test_baseline_settings():
    v1 = load_version("v1.0")
    assert (v1.top_k, v1.max_chunk_words, v1.overlap_words) == (3, 200, 40)
    assert v1.validate_args and v1.require_unlock_confirmation


def test_kb_version_is_label_plus_hash():
    assert kb_version().startswith("1.0+") and len(kb_version()) == 12
