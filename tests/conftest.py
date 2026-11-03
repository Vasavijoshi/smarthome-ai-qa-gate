"""Shared pytest fixtures."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from assistant.app import create_app
from assistant.config import Settings
from assistant.llm import LLMClient
from assistant.tools import reset_devices


@pytest.fixture
def db_path(tmp_path) -> str:
    """A fresh SQLite file with all devices in their initial state."""
    path = str(tmp_path / "homemate_test.db")
    reset_devices(path)
    return path


@pytest.fixture
def make_client(tmp_path) -> Callable[..., TestClient]:
    """Factory for in-process HomeMate clients using the mock LLM and TF-IDF retriever."""

    def build(version: str = "v1.0", admin: bool = True, llm: LLMClient | None = None) -> TestClient:
        settings = Settings(
            version=version,
            llm_client="mock",
            retriever="tfidf",
            ollama_url="",
            ollama_model="",
            seed=11,
            db_path=str(tmp_path / f"homemate_{version}_{admin}.db"),
            enable_test_admin=admin,
        )
        return TestClient(create_app(settings, llm))

    return build


@pytest.fixture
def client(make_client) -> TestClient:
    """An in-process v1.0 HomeMate API client with admin routes on."""
    return make_client()
