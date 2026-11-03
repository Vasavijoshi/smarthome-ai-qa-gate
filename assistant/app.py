"""HomeMate API: the chat turn loop and the FastAPI routes."""

import json
import time
from collections import defaultdict

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from assistant.config import (
    HISTORY_TURNS,
    KB_DIR,
    MAX_MESSAGE_CHARS,
    MAX_TOOL_CALLS,
    Settings,
    kb_version,
    load_version,
)
from assistant.llm import Decision, LLMClient, MockClient, OllamaClient, decision_schema
from assistant.logging_db import init_log, log_interaction
from assistant.rag import Retrieved, Retriever, load_chunks
from assistant.tools import TOOL_GUIDE, read_devices, reset_devices, run_tool


class ChatRequest(BaseModel):
    """Body of POST /chat."""

    model_config = ConfigDict(str_strip_whitespace=True)
    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    # Optional sampling seed so the QA framework can run repeats 11, 22, 33 (report 6.3.5).
    seed: int | None = None


class ToolCallRecord(BaseModel):
    """One tool call made during a turn."""

    tool: str
    arguments: dict
    result: dict
    status: str


class RetrievedChunk(BaseModel):
    """One retrieved chunk returned to the caller."""

    chunk_id: str
    score: float
    text: str


class Metadata(BaseModel):
    """Version, model, latency and token counts for one turn."""

    version: str
    model: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int


class ChatResponse(BaseModel):
    """Body returned by POST /chat."""

    reply: str
    tool_calls: list[ToolCallRecord]
    retrieved: list[RetrievedChunk]
    metadata: Metadata


class Health(BaseModel):
    """Body returned by GET /health."""

    status: str
    version: str
    model: str
    kb_version: str


class ChatError(Exception):
    """A turn that failed, for example 'invalid_response' after the JSON retry."""


def make_llm(settings: Settings) -> LLMClient:
    """Choose the Ollama client or the mock client from settings."""
    if settings.llm_client == "mock":
        return MockClient()
    if settings.llm_client == "ollama":
        return OllamaClient(settings.ollama_url, settings.ollama_model)
    raise ValueError(f"Unknown LLM client: {settings.llm_client}")


def build_messages(prompt: str, retrieved: list[Retrieved], history: list[dict], message: str) -> list[dict]:
    """Build the prompt: system rules, retrieved context, recent history, new message."""
    context = "Context:\n" + "\n".join(f"[{r.chunk_id}] {r.text}" for r in retrieved)
    return [
        {"role": "system", "content": f"{prompt}\n{TOOL_GUIDE}"},
        {"role": "system", "content": context},
        *history,
        {"role": "user", "content": message},
    ]


class HomeMate:
    """The assistant: retriever, LLM client, tools and per-session history."""

    def __init__(self, settings: Settings, llm: LLMClient | None = None) -> None:
        """Load the version config, index the knowledge base and reset the devices."""
        self.settings = settings
        self.config = load_version(settings.version)
        self.prompt = self.config.prompt_text
        chunks = load_chunks(KB_DIR, self.config.max_chunk_words, self.config.overlap_words)
        self.retriever = Retriever(chunks, settings.retriever)
        self.llm = llm or make_llm(settings)
        self.kb_version = kb_version()
        self.sessions: dict[str, list[dict]] = defaultdict(list)
        init_log(settings.db_path)
        reset_devices(settings.db_path)

    def reset(self) -> None:
        """Clear all sessions and put every device back to its initial state."""
        self.sessions.clear()
        reset_devices(self.settings.db_path)

    def decide(self, messages: list[dict], allow_tools: bool, seed: int, tokens: list[int]) -> Decision:
        """Ask the model for a decision, retrying once if the reply is not valid."""
        for _ in range(2):
            reply = self.llm.complete(messages, decision_schema(allow_tools), seed)
            tokens[0] += reply.prompt_tokens
            tokens[1] += reply.completion_tokens
            try:
                decision = Decision.model_validate_json(reply.content)
            except ValidationError:
                continue
            if decision.type == "answer" or allow_tools:
                return decision
        raise ChatError("invalid_response")

    def run_turn(
        self, messages: list[dict], seed: int, calls: list[ToolCallRecord], tokens: list[int]
    ) -> str:
        """Loop: decide, run a tool if asked, until the model answers. At most two tool calls."""
        while True:
            # A new tool call is allowed only first, or as one retry after a failed call.
            allow_tools = len(calls) < MAX_TOOL_CALLS and (not calls or calls[-1].status != "ok")
            decision = self.decide(messages, allow_tools, seed, tokens)
            if decision.type == "answer":
                return decision.text
            outcome = run_tool(
                self.settings.db_path,
                decision.tool,
                decision.arguments,
                validate=self.config.validate_args,
                require_confirmation=self.config.require_unlock_confirmation,
            )
            calls.append(ToolCallRecord(tool=decision.tool, arguments=decision.arguments, **vars(outcome)))
            messages.append({"role": "assistant", "content": decision.model_dump_json(exclude_none=True)})
            messages.append({"role": "tool", "content": json.dumps(vars(outcome))})

    def chat(self, session_id: str, message: str, seed: int | None = None) -> ChatResponse:
        """Handle one user message end to end and log it; seed defaults to LLM_SEED."""
        seed = self.settings.seed if seed is None else seed
        start = time.perf_counter()
        retrieved = self.retriever.search(message, self.config.top_k)
        history = self.sessions[session_id][-2 * HISTORY_TURNS :]
        messages = build_messages(self.prompt, retrieved, history, message)
        calls: list[ToolCallRecord] = []
        tokens = [0, 0]
        reply, error = None, None
        try:
            reply = self.run_turn(messages, seed, calls, tokens)
        except ChatError as err:
            error = str(err)
        except httpx.HTTPError:
            error = "llm_unavailable"
        latency_ms = int((time.perf_counter() - start) * 1000)
        log_interaction(
            self.settings.db_path,
            {
                "session_id": session_id,
                "version": self.config.version,
                "model": self.llm.model_name,
                "message": message,
                "reply": reply,
                "retrieved": [vars(r) for r in retrieved],
                "tool_calls": [c.model_dump() for c in calls],
                "latency_ms": latency_ms,
                "prompt_tokens": tokens[0],
                "completion_tokens": tokens[1],
                "error": error,
            },
        )
        if error:
            raise ChatError(error)
        self.sessions[session_id] += [
            {"role": "user", "content": message},
            {"role": "assistant", "content": reply},
        ]
        return ChatResponse(
            reply=reply,
            tool_calls=calls,
            retrieved=[RetrievedChunk(**vars(r)) for r in retrieved],
            metadata=Metadata(
                version=self.config.version,
                model=self.llm.model_name,
                latency_ms=latency_ms,
                prompt_tokens=tokens[0],
                completion_tokens=tokens[1],
            ),
        )


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    """Build the FastAPI app. Admin routes exist only when ENABLE_TEST_ADMIN=1."""
    settings = settings or Settings.from_env()
    homemate = HomeMate(settings, llm)
    app = FastAPI(title="HomeMate", version=homemate.config.version)
    app.state.homemate = homemate

    @app.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        """Answer a message, possibly calling a device tool."""
        try:
            return homemate.chat(request.session_id, request.message, request.seed)
        except ChatError as err:
            raise HTTPException(status_code=502, detail=str(err)) from err

    @app.get("/devices")
    def devices() -> dict:
        """Return the current state of all devices."""
        return {"devices": read_devices(settings.db_path)}

    @app.get("/health", response_model=Health)
    def health() -> Health:
        """Return status, version, model name and knowledge-base version."""
        return Health(
            status="ok",
            version=homemate.config.version,
            model=homemate.llm.model_name,
            kb_version=homemate.kb_version,
        )

    if settings.enable_test_admin:

        @app.post("/admin/reset")
        def admin_reset() -> dict:
            """Reset all devices and sessions (test-only)."""
            homemate.reset()
            return {"status": "reset"}

    return app
