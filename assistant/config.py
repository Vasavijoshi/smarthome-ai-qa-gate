"""Settings from environment variables and version configs from YAML files."""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ASSISTANT_DIR = Path(__file__).parent
VERSIONS_DIR = ASSISTANT_DIR / "versions"
PROMPTS_DIR = ASSISTANT_DIR / "prompts"
KB_DIR = ASSISTANT_DIR / "knowledge_base"
KB_LABEL = "1.0"

TEMPERATURE = 0.3
# A small model in JSON mode can generate forever; the cap turns that into invalid JSON (M1).
MAX_COMPLETION_TOKENS = 400
LLM_TIMEOUT_S = 180
MAX_TOOL_CALLS = 2
HISTORY_TURNS = 6
MAX_MESSAGE_CHARS = 2000


@dataclass(frozen=True)
class Settings:
    """Runtime settings read once from environment variables."""

    version: str
    llm_client: str
    retriever: str
    ollama_url: str
    ollama_model: str
    seed: int
    db_path: str
    enable_test_admin: bool

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment variables, with local defaults."""
        return cls(
            version=os.getenv("ASSISTANT_VERSION", "v1.0"),
            llm_client=os.getenv("LLM_CLIENT", "ollama"),
            retriever=os.getenv("RETRIEVER", "embedding"),
            ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2:3b"),
            seed=int(os.getenv("LLM_SEED", "11")),
            db_path=os.getenv("HOMEMATE_DB", str(ASSISTANT_DIR / "homemate.db")),
            enable_test_admin=os.getenv("ENABLE_TEST_ADMIN", "0") == "1",
        )


@dataclass(frozen=True)
class VersionConfig:
    """One assistant version: prompt file plus retrieval and validation options."""

    version: str
    prompt_file: str
    top_k: int
    max_chunk_words: int
    overlap_words: int
    validate_args: bool
    require_unlock_confirmation: bool

    @property
    def prompt_text(self) -> str:
        """Return the system prompt text for this version."""
        return (PROMPTS_DIR / self.prompt_file).read_text(encoding="utf-8")


def load_version(version: str) -> VersionConfig:
    """Load a version YAML file such as versions/v1.0.yaml."""
    path = VERSIONS_DIR / f"{version}.yaml"
    if not path.exists():
        raise ValueError(f"Unknown assistant version: {version}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return VersionConfig(
        version=raw["version"],
        prompt_file=raw["prompt_file"],
        top_k=raw["retrieval"]["top_k"],
        max_chunk_words=raw["retrieval"]["max_chunk_words"],
        overlap_words=raw["retrieval"]["overlap_words"],
        validate_args=raw["validation"]["server_side_args"],
        require_unlock_confirmation=raw["validation"]["unlock_confirmation"],
    )


def kb_version() -> str:
    """Return the frozen KB label plus a short hash of the documents, e.g. '1.0+3fa2b1c0'."""
    digest = hashlib.sha256()
    for path in sorted(KB_DIR.glob("*.md")):
        digest.update(path.name.encode())
        # Normalise line endings so Windows and Linux checkouts give the same hash.
        digest.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode())
    return f"{KB_LABEL}+{digest.hexdigest()[:8]}"
