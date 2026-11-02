"""SQLite storage for proposals, approvals and the business audit trail."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,                 -- PROP-000001: also the ERP idempotency key
    seq INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL,
    content_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    draft_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    supersedes TEXT REFERENCES proposals (id),
    thread_id TEXT,
    erp_adapter TEXT,
    erp_order_number TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL REFERENCES proposals (id),
    decision TEXT NOT NULL,
    actor TEXT NOT NULL,
    role TEXT NOT NULL,
    content_hash TEXT NOT NULL,          -- what the approver saw; must equal the proposal hash
    comment TEXT,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    proposal_id TEXT,
    content_hash TEXT,
    result TEXT NOT NULL,
    detail TEXT
);

-- Questions the agent could not answer, handed to a person with their context
CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    role TEXT NOT NULL,
    question TEXT NOT NULL,
    reason TEXT NOT NULL,
    context_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ABIERTA'
);

-- The audit trail is append-only, enforced by the database itself, not only by the code
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""


def db_path() -> str:
    return os.getenv("AGENT_DB_PATH", "/data/agent.db")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = sqlite3.connect(db_path(), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    Path(db_path()).parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


def audit(conn, *, actor: str, role: str, action: str, result: str, proposal_id=None, content_hash=None, detail=None) -> None:
    conn.execute(
        "INSERT INTO audit_log (at, actor, role, action, proposal_id, content_hash, result, detail)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (now(), actor, role, action, proposal_id, content_hash, result, detail),
    )
