"""Interaction log: one SQLite row per /chat request."""

import json
import sqlite3
from datetime import UTC, datetime


def init_log(db_path: str) -> None:
    """Create the interaction_log table if it does not exist."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS interaction_log (
                interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                session_id TEXT NOT NULL,
                version TEXT NOT NULL,
                model TEXT NOT NULL,
                message TEXT NOT NULL,
                reply TEXT,
                retrieved_json TEXT NOT NULL,
                tool_calls_json TEXT NOT NULL,
                latency_ms INTEGER NOT NULL,
                prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                error TEXT
            )"""
        )


def log_interaction(db_path: str, row: dict) -> None:
    """Insert one interaction; list values are stored as JSON text."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO interaction_log (ts, session_id, version, model, message, reply,
                retrieved_json, tool_calls_json, latency_ms, prompt_tokens, completion_tokens, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(UTC).isoformat(),
                row["session_id"],
                row["version"],
                row["model"],
                row["message"],
                row["reply"],
                json.dumps(row["retrieved"]),
                json.dumps(row["tool_calls"]),
                row["latency_ms"],
                row["prompt_tokens"],
                row["completion_tokens"],
                row["error"],
            ),
        )
