"""Runs the graph for one conversation turn and turns any failure into a safe, explicit answer."""

import os
import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError

from ..approvals.service import record_event
from ..auth.tokens import Identity
from ..erp.factory import read_erp, write_erp
from ..erp.port import ErpUnavailable
from ..llm.structured import LLMOutputError
from ..llm.usage import TokenBudgetExceeded
from ..observability import tracing
from ..rag.retriever import search
from ..store.db import init_db
from .graph import Dependencies, build_graph

SAFE_FAILURES = {
    ErpUnavailable: "No pude verificar los datos en el ERP en este momento, así que no doy una respuesta sin verificar. Intenta de nuevo en unos minutos.",
    TokenBudgetExceeded: "La solicitud superó el límite de costo por consulta y se detuvo. Un ejecutivo puede atenderla directamente.",
    GraphRecursionError: "La solicitud superó el máximo de pasos permitidos y se detuvo de forma segura.",
    LLMOutputError: "No obtuve una interpretación confiable de la solicitud. ¿Puedes reformularla con productos, cantidades y ciudad?",
}


class Agent:
    def __init__(self, deps: Dependencies | None = None, checkpoint_path: str | None = None):
        init_db()
        path = checkpoint_path or os.getenv("CHECKPOINT_PATH", "/data/checkpoints.db")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._saver = SqliteSaver(sqlite3.connect(path, check_same_thread=False))
        self.deps = deps or Dependencies(read_erp=read_erp, write_erp=write_erp, search=search)
        self._graph = build_graph(self.deps, self._saver)

    def _config(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": int(os.getenv("MAX_AGENT_ITERATIONS", "12"))}

    def _run(self, thread_id: str, graph_input, identity: Identity, trace_name: str) -> dict:
        with tracing.trace(identity, thread_id, trace_name) as (callbacks, handler):
            config = {**self._config(thread_id), "callbacks": callbacks}
            try:
                self._graph.invoke(graph_input, config)
                result = self.result(thread_id)
            except tuple(SAFE_FAILURES) as e:
                message = next(text for kind, text in SAFE_FAILURES.items() if isinstance(e, kind))
                record_event(identity, "agent_stopped", type(e).__name__, detail=str(e)[:300])
                # Report what was already spent before stopping: the cost panel must never show a stopped request as free
                state = self._graph.get_state(self._config(thread_id)).values
                usage = [u for u in state.get("usage", []) if u.get("turn") == state.get("turn")]
                result = {"response": message, "response_kind": "stopped", "pending_approval": None, "usage": usage, "state": state}
        tracing.record_outcome(handler, result["response_kind"], result["usage"])
        result["trace_id"] = getattr(handler, "last_trace_id", None) if handler else None
        return result

    def send(self, thread_id: str, message: str, identity: Identity) -> dict:
        state_input = {"thread_id": thread_id, "message": message, "identity": identity.model_dump()}
        return self._run(thread_id, state_input, identity, "chat")

    def resume(self, thread_id: str, approver: Identity) -> dict:
        from langgraph.types import Command

        return self._run(thread_id, Command(resume={"approver": approver.model_dump()}), approver, "aprobacion")

    def pending_approval(self, thread_id: str) -> dict | None:
        snapshot = self._graph.get_state(self._config(thread_id))
        interrupts = [i for task in snapshot.tasks for i in task.interrupts]
        return interrupts[0].value if interrupts else None

    def result(self, thread_id: str) -> dict:
        state = self._graph.get_state(self._config(thread_id)).values
        turn = state.get("turn")
        return {
            "response": state.get("response", ""),
            "response_kind": state.get("response_kind", ""),
            "route": state.get("route"),
            "pending_approval": self.pending_approval(thread_id),
            "usage": [u for u in state.get("usage", []) if u.get("turn") == turn],
            "state": state,
        }
