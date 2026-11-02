"""Technical observability with Langfuse: one trace per request, one span per graph node, one generation per LLM call.

Observability is never a point of failure: without keys, or if Langfuse fails, the agent runs exactly the same.
Business audit (who approved what) stays in our own database and is never sent here.
"""

import logging
import os
from contextlib import contextmanager, nullcontext
from decimal import Decimal

from ..auth.tokens import Identity

log = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


@contextmanager
def trace(identity: Identity, thread_id: str, name: str):
    """Yields the callbacks to pass to the graph (empty list when tracing is off) and a holder for the trace id."""
    handler, context = None, nullcontext()
    if enabled():
        try:
            from langfuse import propagate_attributes
            from langfuse.langchain import CallbackHandler

            handler = CallbackHandler()
            context = propagate_attributes(
                user_id=identity.user,
                session_id=thread_id,
                trace_name=name,
                tags=[f"rol:{identity.role}", f"erp:{os.getenv('ERP_ADAPTER', 'dynamics')}"],
            )
        except Exception as e:  # noqa: BLE001 — tracing must never break the request
            log.warning("Langfuse disabled for this request: %s", e)
            handler, context = None, nullcontext()
    with context:
        yield [handler] if handler else [], handler


def record_outcome(handler, response_kind: str, usage: list[dict]) -> None:
    """Attaches our own cost and outcome to the trace, so Langfuse and the CFO panel show the same numbers."""
    trace_id = getattr(handler, "last_trace_id", None) if handler else None
    if not trace_id:
        return
    try:
        from langfuse import get_client

        client = get_client()
        cost = sum((Decimal(u["cost_usd"]) for u in usage if u.get("cost_usd")), Decimal(0))
        client.create_score(name="costo_usd", value=float(cost), trace_id=trace_id, data_type="NUMERIC")
        client.create_score(name="tipo_respuesta", value=response_kind, trace_id=trace_id, data_type="CATEGORICAL")
    except Exception as e:  # noqa: BLE001
        log.warning("Could not record outcome in Langfuse: %s", e)


def flush() -> None:
    if not enabled():
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as e:  # noqa: BLE001
        log.warning("Langfuse flush failed: %s", e)
