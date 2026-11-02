"""Exact-match cache for LLM and embedding calls.

The same question with the same data is never paid twice, and a rehearsed demo replays identically (even offline).
The key covers provider, model, schema and the full input, so any change in the data produces a new call.
Stored in its own file: resetting the demo (agent.db) does not erase it.
"""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def enabled() -> bool:
    return os.getenv("LLM_CACHE_ENABLED", "false").lower() == "true"


def _connect() -> sqlite3.Connection:
    path = Path(os.getenv("LLM_CACHE_PATH", "/data/llm_cache.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, kind TEXT NOT NULL, model TEXT NOT NULL,"
        " value TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, created_at TEXT NOT NULL)"
    )
    return conn


def make_key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def get(key: str) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM llm_cache WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def put(key: str, kind: str, model: str, value: str, input_tokens: int, output_tokens: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, kind, model, value, input_tokens, output_tokens, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
