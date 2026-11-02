"""Shared fixtures. The mock ERP runs in-process on a temporary SQLite database, reset before every test."""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "services" / "agent_api"), str(REPO / "services" / "mock_erp")]

TOKENS = {"read": "test-read-token", "write": "test-write-token", "admin": "test-admin-token"}

# Must be set before importing the mock ERP: it reads its configuration at import time
_TMP = Path(tempfile.mkdtemp())
os.environ.update(
    {
        "ERP_DB_PATH": str(_TMP / "erp.db"),
        "AGENT_DB_PATH": str(_TMP / "agent.db"),
        "JWT_SECRET": "test-jwt-secret-that-is-long-enough-0123456789",
        "ERP_SEED_DIR": str(REPO / "data" / "seed"),
        "ERP_READ_TOKEN": TOKENS["read"],
        "ERP_WRITE_TOKEN": TOKENS["write"],
        "ERP_ADMIN_TOKEN": TOKENS["admin"],
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from mock_erp.db import connect, init_db  # noqa: E402
from mock_erp.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_erp():
    init_db(reset=True)


@pytest.fixture(autouse=True)
def fresh_agent_db():
    from app.store.db import init_db as init_agent_db

    Path(os.environ["AGENT_DB_PATH"]).unlink(missing_ok=True)
    init_agent_db()


@pytest.fixture
def erp_client():
    """Factory: erp_client("read") returns an in-process HTTP client authenticated with that credential."""
    clients = []

    def make(credential: str | None = None) -> TestClient:
        client = TestClient(app)
        if credential:
            client.headers["Authorization"] = f"Bearer {TOKENS[credential]}"
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()


@pytest.fixture
def read_erp(erp_client):
    """The agent's read-only ERP adapter (Dynamics), wired to the in-process mock ERP."""
    from app.erp.factory import create_erp

    return create_erp(TOKENS["read"], "dynamics", http_client=erp_client())


@pytest.fixture
def erp_db():
    """Direct read access to the ERP tables, to assert on state after a request."""

    def query(sql: str, *params):
        with connect() as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    return query
