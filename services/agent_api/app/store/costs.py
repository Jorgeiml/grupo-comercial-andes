"""Per-request cost records and the monthly projection for the CFO panel."""

import json
from decimal import Decimal

from ..auth.tokens import Identity
from .db import connect, now


def record_request(thread_id: str, identity: Identity, response_kind: str, usage: list[dict], latency_ms: int) -> dict:
    tokens = sum(step["input_tokens"] + step["output_tokens"] for step in usage)
    cost = sum((Decimal(step["cost_usd"]) for step in usage if step["cost_usd"] is not None), Decimal(0))
    complete = all(step["cost_usd"] is not None for step in usage)
    with connect() as conn:
        conn.execute(
            "INSERT INTO request_costs (at, thread_id, actor, role, response_kind, tokens, cost_usd, cost_complete, latency_ms, steps_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now(), thread_id, identity.user, identity.role, response_kind, tokens, str(cost), int(complete), latency_ms, json.dumps(usage)),
        )
    return {"tokens": tokens, "cost_usd": str(cost), "cost_complete": complete, "latency_ms": latency_ms}


def cost_summary(monthly_requests: int) -> dict:
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT response_kind, tokens, cost_usd, cost_complete, latency_ms FROM request_costs")]
    if not rows:
        return {"requests": 0, "monthly_requests": monthly_requests, "by_kind": {}, "note": "Sin solicitudes registradas todavía"}

    def stats(items: list[dict]) -> dict:
        costs = [Decimal(r["cost_usd"]) for r in items]
        return {
            "requests": len(items),
            "avg_cost_usd": str(sum(costs) / len(items)),
            "max_cost_usd": str(max(costs)),
            "avg_tokens": round(sum(r["tokens"] for r in items) / len(items)),
            "avg_latency_ms": round(sum(r["latency_ms"] for r in items) / len(items)),
        }

    overall = stats(rows)
    by_kind: dict[str, list[dict]] = {}
    for row in rows:
        by_kind.setdefault(row["response_kind"], []).append(row)
    return {
        **overall,
        "total_cost_usd": str(sum(Decimal(r["cost_usd"]) for r in rows)),
        "cost_complete": all(r["cost_complete"] for r in rows),
        "monthly_requests": monthly_requests,
        "projected_monthly_cost_usd": str((Decimal(overall["avg_cost_usd"]) * monthly_requests).quantize(Decimal("0.01"))),
        "per_user_licenses_usd": "0.00",  # pay per use: no per-seat licenses
        "by_kind": {kind: stats(items) for kind, items in by_kind.items()},
    }
