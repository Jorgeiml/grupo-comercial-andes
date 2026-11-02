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


# ---------- agent with a scripted LLM (deterministic and free) ----------

from app.agent.schemas import ExtractedLine, InputClassification, OrderExtraction  # noqa: E402
from app.rag.chunking import load_chunks  # noqa: E402
from app.rag.retriever import RetrievalResult, RetrievedChunk  # noqa: E402

CHUNKS = [
    RetrievedChunk(citation=c.citation, title=c.title, section_title=c.section_title, text=c.text, score=0.6)
    for c in load_chunks(REPO / "data" / "policies")
]


def fake_search(query):
    return RetrievalResult(chunks=CHUNKS[:4], embedding_tokens=10, min_score=0.4)


def order(customer="CL-001", sku="CM-001", quantity=300, warehouse="GYE", discount=None, days=None, ambiguities=()):
    return OrderExtraction(
        customer_code=customer,
        lines=[ExtractedLine(sku=sku, product_as_written="producto", quantity=quantity, warehouse=warehouse)],
        requested_discount_pct=discount, requested_payment_days=days, ambiguities=list(ambiguities),
    )


def classified(category):
    return InputClassification(category=category, reason="test")


@pytest.fixture
def script(monkeypatch):
    """script["step"] = value or callable(messages) -> value. Unscripted steps fail the test."""
    from app.agent import graph as graph_module

    answers = {}

    def fake_extract(kind, schema, messages, tracker, step):
        tracker.record_chat(step, "gpt-5.6-luna", 10, 5, latency_ms=1)
        value = answers[step]
        return value(messages) if callable(value) else value

    monkeypatch.setattr(graph_module, "extract", fake_extract)
    return answers


@pytest.fixture
def agent(erp_client, tmp_path):
    from app.agent.graph import Dependencies
    from app.agent.runner import Agent
    from app.erp.factory import create_erp

    deps = Dependencies(
        read_erp=lambda: create_erp(TOKENS["read"], "dynamics", http_client=erp_client()),
        write_erp=lambda: create_erp(TOKENS["write"], "dynamics", http_client=erp_client()),
        search=fake_search,
    )
    return Agent(deps, checkpoint_path=str(tmp_path / "checkpoints.db"))


@pytest.fixture
def erp_db():
    """Direct read access to the ERP tables, to assert on state after a request."""

    def query(sql: str, *params):
        with connect() as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    return query
