"""Evaluation harness (pytest plugin loaded by the root conftest): real LLM and RAG, in-process ERP reset per case,
results collected for the report. Only active for tests marked `eval`."""

import json
import os
import time
from decimal import Decimal
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOKENS = {name: os.environ.get(f"ERP_{name.upper()}_TOKEN", "") for name in ("read", "write", "admin")}
RESULTS: list[dict] = []
REPORT_DIR = REPO / "reports"


@pytest.fixture
def eval_agent(erp_client, tmp_path):
    from app.agent.graph import Dependencies
    from app.agent.runner import Agent
    from app.erp.factory import create_erp
    from app.rag.retriever import search

    deps = Dependencies(
        read_erp=lambda: create_erp(TOKENS["read"], "dynamics", http_client=erp_client()),
        write_erp=lambda: create_erp(TOKENS["write"], "dynamics", http_client=erp_client()),
        search=search,  # the real index and real embeddings
    )
    return Agent(deps, checkpoint_path=str(tmp_path / "checkpoints.db"))


@pytest.fixture
def record(request, erp_db):
    """Collects what the report needs: outcome, cost, latency and ERP integrity after the case."""
    entry = {"usage": [], "latency_ms": 0}

    def add_turn(result: dict, latency_ms: int) -> None:
        entry["usage"].extend(result.get("usage", []))
        entry["latency_ms"] += latency_ms
        entry["response"] = result.get("response", "")
        entry["response_kind"] = result.get("response_kind", "")

    yield add_turn

    from app.store.db import connect

    case = request.node.callspec.params["case"] if hasattr(request.node, "callspec") else None
    with connect() as conn:
        executed = {row[0] for row in conn.execute("SELECT id FROM proposals WHERE status = 'EJECUTADA'")}
    orders = erp_db("SELECT external_ref FROM sales_orders")
    # Integrity metric: an ERP order is valid only if it comes from an approved and executed proposal
    invalid_orders = [o["external_ref"] for o in orders if o["external_ref"] not in executed]
    report = request.node.stash.get(OUTCOME, None)
    RESULTS.append({
        "id": case.id if case else request.node.name,
        "group": case.group if case else "flujo",
        "description": case.description if case else request.node.name,
        "should_block": case.should_block if case else True,
        "passed": bool(report and report.passed),
        "failure": (str(report.longrepr).splitlines()[-1][:300] if report and report.failed else ""),
        "response_kind": entry.get("response_kind", ""),
        "response": entry.get("response", "")[:600],
        "erp_orders": len(orders),
        "invalid_erp_orders": invalid_orders,
        "has_citation": "[POL-" in entry.get("response", ""),
        "cost_usd": str(sum((Decimal(u["cost_usd"]) for u in entry["usage"] if u.get("cost_usd")), Decimal(0))),
        "tokens": sum(u["input_tokens"] + u["output_tokens"] for u in entry["usage"]),
        "latency_ms": entry["latency_ms"],
    })


OUTCOME = pytest.StashKey()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        item.stash[OUTCOME] = report


def pytest_sessionfinish(session, exitstatus):
    if not RESULTS:
        return
    from eval_report import write_report  # tests/eval_report.py

    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / "eval-results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    path = write_report(RESULTS, REPORT_DIR / "eval-report.html", generated_at=time.strftime("%Y-%m-%d %H:%M"))
    print(f"\nReporte de evaluación: {path}")
