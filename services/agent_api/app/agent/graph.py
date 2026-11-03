"""The agent as a LangGraph state machine: it knows when to answer, when to verify and when to stop.

The LLM interprets and proposes -> the engine validates -> a person approves -> the execution service writes.
"""

import json
import operator
import os
from dataclasses import dataclass
from typing import Annotated, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..approvals.service import ApprovalError, Status, create_proposal, get_proposal, record_event
from ..auth.tokens import Identity
from ..engine.alternatives import suggest
from ..engine.models import OrderDraft, RequestedLine
from ..engine.validation import Evaluation, evaluate, usd
from ..erp.port import ErpPort
from ..execution.service import ExecutionStatus, execute
from ..llm.structured import extract
from ..llm.usage import UsageTracker
from ..rag.retriever import RetrievalResult
from ..store.db import connect, now
from . import prompts
from .facts import alternatives_text, as_json, catalog_text, evaluation_text, quantity_text
from .schemas import Answerability, DraftAnswer, InputClassification, OrderExtraction, QueryPlan
from .verifier import CITATION, normalize_citation, verify

MAX_INPUT_CHARS = 7000  # chat messages are capped at 4000 by the API; extracted PDFs at 6000 plus a wrapper


class AgentState(TypedDict, total=False):
    thread_id: str
    turn: int
    message: str
    identity: dict
    history: Annotated[list[dict], operator.add]
    usage: Annotated[list[dict], operator.add]
    route: str | None
    route_reason: str | None
    subqueries: list[str]
    needs_order_data: bool
    chunks: list[dict]
    answerable: bool
    answerability: dict | None
    last_order_facts: str  # kept across turns (not in PER_TURN_DEFAULTS) for follow-up questions
    last_alternatives: list[dict]  # kept across turns too: an alternative can be accepted after a follow-up question
    draft_answer: str | None
    facts: str
    evaluation: dict | None
    alternatives: list[dict]
    proposal: dict | None
    execution: dict | None
    verification: dict | None
    response: str
    response_kind: str


PER_TURN_DEFAULTS = dict(
    route=None, route_reason=None, subqueries=[], needs_order_data=False, chunks=[], answerable=False, answerability=None, draft_answer=None,
    facts="", evaluation=None, alternatives=[], proposal=None, execution=None, verification=None, response="", response_kind="",
)


@dataclass
class Dependencies:
    read_erp: Callable[[], ErpPort]  # the agent's tools: read-only credential
    write_erp: Callable[[], ErpPort]  # used only inside the execution service
    search: Callable[[str], RetrievalResult]


def _identity(state: AgentState) -> Identity:
    return Identity(**state["identity"])


def _tracker(state: AgentState) -> UsageTracker:
    limit = int(os.getenv("MAX_TOKENS_PER_REQUEST", "12000"))
    used = sum(u["input_tokens"] + u["output_tokens"] for u in state.get("usage", []) if u["turn"] == state["turn"])
    return UsageTracker(max_tokens=max(limit - used, 0))


def _usage(tracker: UsageTracker, state: AgentState) -> list[dict]:
    return [{**step, "turn": state["turn"]} for step in tracker.summary()["steps"]]


def _context(state: AgentState) -> str:
    # Previous turns of this conversation, so a clarification can be answered naturally
    previous = state.get("history", [])[:-1][-4:]
    lines = [f"{h['role']}: {h['text']}" for h in previous]
    return ("HISTORIAL:\n" + "\n".join(lines) + "\n\n") if lines else ""


def _user_block(state: AgentState) -> str:
    return f"{_context(state)}<mensaje>\n{state['message']}\n</mensaje>"


def _reply(kind: str, text: str, **extra) -> dict:
    return {"response": text, "response_kind": kind, "history": [{"role": "asistente", "text": text}], **extra}


def build_graph(deps: Dependencies, checkpointer=None):
    # ---------- shared ----------
    def start_turn(state: AgentState) -> dict:
        return {**PER_TURN_DEFAULTS, "turn": state.get("turn", 0) + 1, "history": [{"role": "usuario", "text": state["message"]}]}

    def classify(state: AgentState) -> dict:
        if len(state["message"]) > MAX_INPUT_CHARS:
            return {"route": "suspicious", "route_reason": "Mensaje demasiado largo"}
        tracker = _tracker(state)
        result = extract("small", InputClassification, [("system", prompts.CLASSIFY), ("human", _user_block(state))], tracker, "classify")
        return {"route": result.category, "route_reason": result.reason, "usage": _usage(tracker, state)}

    def refuse(state: AgentState) -> dict:
        if state["route"] == "suspicious":
            record_event(_identity(state), "guardrail_input", "suspicious", detail=f"{state['route_reason']} | {state['message'][:300]}")
            return _reply(
                "refused",
                "No puedo procesar esta solicitud. Las condiciones comerciales (precios, descuentos y aprobaciones) "
                "solo cambian por los canales autorizados [POL-COM-01 §4]. Si necesitas un pedido, descríbelo "
                "con productos, cantidades y ciudad de entrega.",
            )
        return _reply("refused", "Solo puedo ayudarte con consultas comerciales y pedidos especiales de Grupo Comercial Andes.")

    def _extract_order(state: AgentState, tracker: UsageTracker) -> tuple[OrderDraft | None, list[str]]:
        erp = deps.read_erp()
        catalog = catalog_text(erp.list_products(), erp.list_customers())
        result = extract(
            "small", OrderExtraction,
            [("system", prompts.EXTRACT), ("human", f"{catalog}\n\n{_user_block(state)}")], tracker, "extract_order",
        )
        identity = _identity(state)
        customer = result.customer_code
        if identity.role == "cliente":
            # A customer only ever orders for itself: the account comes from the token, not from the text
            customer = identity.user
        missing = []
        if not customer:
            missing.append("¿Para qué cliente es el pedido?")
        if not result.lines:
            missing.append("¿Qué productos y cantidades necesitas?")
        for line in result.lines:
            if not line.sku:
                missing.append(f"No identifiqué con certeza el producto «{line.product_as_written}»: ¿cuál del catálogo es?")
            if not line.quantity:
                missing.append(f"¿Qué cantidad de «{line.product_as_written}»?")
            if not line.warehouse:
                missing.append(f"¿Entrega en Quito o en Guayaquil para «{line.product_as_written}»?")
        if missing:
            # Python decides when to stop: only a missing structural field blocks the order.
            # The model's notes are shown as context, never as a reason to stop by themselves.
            return None, list(dict.fromkeys([*missing, *result.ambiguities]))
        draft = OrderDraft(
            customer_code=customer,
            lines=[RequestedLine(sku=l.sku, quantity=l.quantity, warehouse=l.warehouse) for l in result.lines],
            requested_discount_pct=result.requested_discount_pct,
            requested_payment_days=result.requested_payment_days,
        )
        return draft, []

    # ---------- route A: policy questions ----------
    def plan(state: AgentState) -> dict:
        tracker = _tracker(state)
        result = extract("small", QueryPlan, [("system", prompts.PLAN), ("human", _user_block(state))], tracker, "plan")
        subqueries = list(dict.fromkeys([state["message"], *result.subqueries]))[:5]
        update = {"subqueries": subqueries, "needs_order_data": result.needs_order_data, "usage": _usage(tracker, state)}
        if state.get("last_order_facts"):
            # A follow-up ("¿por qué no se puede?") is answered with the verified data of the order in this conversation
            update["facts"] = "DATOS DEL ÚLTIMO PEDIDO DE ESTA CONVERSACIÓN (verificados):\n" + state["last_order_facts"]
        return update

    def retrieve(state: AgentState) -> dict:
        tracker = _tracker(state)
        best: dict[str, dict] = {}
        for query in state["subqueries"]:
            result = deps.search(query)
            tracker.record_embedding("retrieve", os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"), result.embedding_tokens, 0,
                                     cached=result.embedding_tokens == 0)
            for chunk in result.relevant:  # layer 1: similarity threshold
                if chunk.citation not in best or chunk.score > best[chunk.citation]["score"]:
                    best[chunk.citation] = {"citation": chunk.citation, "text": chunk.text, "score": chunk.score}
        chunks = sorted(best.values(), key=lambda c: c["score"], reverse=True)[:6]
        return {"chunks": chunks, "usage": _usage(tracker, state)}

    def check_answerable(state: AgentState) -> dict:
        if not state["chunks"]:
            return {"answerable": False}
        tracker = _tracker(state)
        fragments = "\n\n".join(f"[{c['citation']}]\n{c['text']}" for c in state["chunks"])
        facts = state["facts"] or "DATOS DEL ERP: no aplica"
        result = extract(
            "small", Answerability,
            [("system", prompts.ANSWERABLE),
             ("human", f"FRAGMENTOS:\n{fragments}\n\nDATOS DEL ERP (verificados):\n{facts}\n\nPREGUNTA:\n{_user_block(state)}")],
            tracker, "check_answerable",
        )
        retrieved = {c["citation"] for c in state["chunks"]}
        supported = [c for c in result.supporting_citations if normalize_citation(c) in retrieved]
        # layer 2: the model must say "answerable" AND point to chunks that were actually retrieved
        return {
            "answerable": result.answerable and bool(supported),
            "answerability": {"answerable": result.answerable, "supported": supported, "missing": result.missing},
            "usage": _usage(tracker, state),
        }

    def escalate(state: AgentState) -> dict:
        identity = _identity(state)
        context = {"chunks": [c["citation"] for c in state["chunks"]], "subqueries": state["subqueries"]}
        with connect() as conn:
            cursor = conn.execute(
                "INSERT INTO escalations (at, actor, role, question, reason, context_json) VALUES (?, ?, ?, ?, ?, ?)",
                (now(), identity.user, identity.role, state["message"], "Sin respaldo en las políticas", json.dumps(context)),
            )
            escalation = f"ESC-{cursor.lastrowid:06d}"
        record_event(identity, "escalate", "ok", detail=f"{escalation}: {state['message'][:200]}")
        return _reply(
            "no_info",
            f"No tengo esa información en las políticas vigentes, así que no quiero darte un dato inventado. "
            f"Escalé tu consulta a un ejecutivo de cuenta con todo el contexto (caso {escalation}).",
        )

    def gather_facts(state: AgentState) -> dict:
        tracker = _tracker(state)
        draft, missing = _extract_order(state, tracker)
        if draft is None:
            if state.get("facts"):  # keep the verified data of the conversation's last order
                return {"usage": _usage(tracker, state)}
            facts = "DATOS DEL PEDIDO: no se pudo cotizar con certeza. Falta: " + "; ".join(missing)
            return {"facts": facts, "usage": _usage(tracker, state)}
        evaluation = evaluate(draft, deps.read_erp())
        facts = evaluation_text(evaluation)
        return {"facts": facts, "last_order_facts": facts, "last_alternatives": [], "evaluation": as_json(evaluation), "usage": _usage(tracker, state)}

    def draft_policy(state: AgentState) -> dict:
        tracker = _tracker(state)
        fragments = "\n\n".join(f"[{c['citation']}]\n{c['text']}" for c in state["chunks"])
        facts = state["facts"] or "DATOS VERIFICADOS: no aplica"
        result = extract(
            "main", DraftAnswer,
            [("system", prompts.DRAFT_POLICY),
             ("human", f"FRAGMENTOS DE POLÍTICA:\n{fragments}\n\nDATOS VERIFICADOS:\n{facts}\n\nPREGUNTA:\n{_user_block(state)}")],
            tracker, "draft_answer",
        )
        return {"draft_answer": result.answer, "usage": _usage(tracker, state)}

    # ---------- route B: special orders ----------
    def extract_order(state: AgentState) -> dict:
        tracker = _tracker(state)
        draft, missing = _extract_order(state, tracker)
        if draft is None:
            text = "Para preparar el pedido necesito que me confirmes:\n" + "\n".join(f"- {m}" for m in missing)
            return {**_reply("clarify", text), "usage": _usage(tracker, state)}
        return {"evaluation": {"draft": as_json(draft)}, "usage": _usage(tracker, state)}

    def validate(state: AgentState) -> dict:
        erp = deps.read_erp()
        draft = OrderDraft.model_validate(state["evaluation"]["draft"])
        evaluation = evaluate(draft, erp)
        alternatives = suggest(evaluation, erp) if not evaluation.ok else []
        facts = evaluation_text(evaluation) + ("\n\n" + alternatives_text(alternatives) if not evaluation.ok else "")
        computed = [as_json(a) for a in alternatives]
        return {"evaluation": as_json(evaluation), "alternatives": computed, "last_alternatives": computed, "facts": facts, "last_order_facts": facts}

    def explain(state: AgentState) -> dict:
        tracker = _tracker(state)
        result = extract(
            "main", DraftAnswer,
            [("system", prompts.DRAFT_ORDER), ("human", f"DATOS VERIFICADOS:\n{state['facts']}\n\nPEDIDO DEL CLIENTE:\n{_user_block(state)}")],
            tracker, "explain_order",
        )
        return {"draft_answer": result.answer, "usage": _usage(tracker, state)}

    def quote(state: AgentState) -> dict:
        evaluation = Evaluation.model_validate(state["evaluation"])
        text = (
            f"Cotización referencial para {evaluation.customer_name}: total {usd(evaluation.total)} "
            f"(sin impuestos). " + " ".join(f"{quantity_text(l.quantity, l.unit)} de {l.name} a {usd(l.unit_price)} con "
                                              f"{l.discount_pct.normalize():f} % de descuento." for l in evaluation.lines)
            + " Para emitir el pedido formal, tu ejecutivo de cuenta debe prepararlo y se requiere aprobación interna."
        )
        return _reply("quote", text)

    def propose(state: AgentState) -> dict:
        evaluation = Evaluation.model_validate(state["evaluation"])
        try:
            proposal = create_proposal(evaluation, _identity(state), thread_id=state.get("thread_id"))
        except ApprovalError as e:
            return _reply("refused", f"No se pudo crear la propuesta: {e.message}")
        corrects = f", versión corregida de {proposal.supersedes}," if proposal.supersedes else ""
        text = (
            f"Preparé la propuesta {proposal.id}{corrects} para {evaluation.customer_name} por {usd(proposal.content.total)} "
            f"(sin impuestos). Todas las validaciones pasaron. Queda PENDIENTE de aprobación del {proposal.content.approval_level}; "
            f"no se escribe nada en el ERP hasta que se apruebe."
        )
        return {**_reply("proposal", text), "proposal": as_json(proposal)}

    def await_approval(state: AgentState) -> dict:
        proposal_id = state["proposal"]["id"]
        # The graph pauses here; the API records the decision (role from the approver's token) before resuming
        resume = interrupt({"proposal_id": proposal_id, "content_hash": state["proposal"]["content_hash"],
                            "approval_level": state["proposal"]["content"]["approval_level"]})
        proposal = get_proposal(proposal_id)  # the database, not the resume payload, is the source of truth
        if proposal.status == Status.APROBADA:
            result = execute(proposal_id, Identity(**resume["approver"]), deps.write_erp())
            if result.status in (ExecutionStatus.EXECUTED, ExecutionStatus.ALREADY_EXECUTED):
                text = f"Pedido {result.order_number} registrado en el ERP ({result.erp_adapter}) a partir de la propuesta {proposal_id}."
            elif result.status == ExecutionStatus.BLOCKED:
                text = f"La propuesta {proposal_id} se bloqueó al revalidar antes de escribir en el ERP: " + "; ".join(result.reasons)
            else:
                text = f"El ERP no respondió; la propuesta {proposal_id} sigue aprobada y se puede reintentar sin duplicar."
            return {**_reply("executed" if result.status != ExecutionStatus.BLOCKED else "blocked", text), "execution": as_json(result)}
        if proposal.status == Status.RECHAZADA:
            return _reply("rejected", f"La propuesta {proposal_id} fue rechazada. No se escribió nada en el ERP.")
        return _reply("correction", f"Se pidió corregir la propuesta {proposal_id}. El ejecutivo la verá en Aprobaciones con el comentario "
                                    "y preparará una versión corregida, que necesitará una aprobación nueva. No se escribió nada en el ERP.")

    # ---------- output guardrail ----------
    def verify_answer(state: AgentState) -> dict:
        answer = state["draft_answer"]
        is_policy = state["route"] == "policy_question"
        allowed = [state["facts"], state["message"], *(c["text"] for c in state["chunks"])]
        # Citations attached by the deterministic engine to its rules are as trustworthy as retrieved chunks
        cited = [c["citation"] for c in state["chunks"]] + CITATION.findall(state["facts"])
        result = verify(answer, allowed_texts=allowed, retrieved_citations=cited, require_citation=is_policy)
        if result.ok:
            return {**_reply("answer", answer), "verification": as_json(result)}
        record_event(_identity(state), "guardrail_output", "blocked", detail=" | ".join(result.issues))
        sources = ", ".join(c["citation"] for c in state["chunks"]) or "sin fuentes"
        fallback = (
            "La respuesta generada no pasó la verificación automática, así que no la muestro. "
            f"Datos verificados:\n{state['facts'] or 'sin datos de pedido'}\nFuentes: {sources}"
        )
        return {**_reply("blocked", fallback), "verification": as_json(result)}

    # ---------- wiring ----------
    graph = StateGraph(AgentState)
    for name, node in [
        ("start_turn", start_turn), ("classify", classify), ("refuse", refuse),
        ("plan", plan), ("retrieve", retrieve), ("check_answerable", check_answerable), ("escalate", escalate),
        ("gather_facts", gather_facts), ("draft_policy", draft_policy),
        ("extract_order", extract_order), ("validate", validate), ("explain", explain), ("quote", quote),
        ("propose", propose), ("await_approval", await_approval), ("verify_answer", verify_answer),
    ]:
        graph.add_node(name, node)

    graph.add_edge(START, "start_turn")
    graph.add_edge("start_turn", "classify")
    graph.add_conditional_edges("classify", lambda s: {"policy_question": "plan", "special_order": "extract_order"}.get(s["route"], "refuse"))
    graph.add_edge("refuse", END)

    graph.add_edge("plan", "retrieve")
    # ERP facts are gathered before deciding answerability: prices, tier and stock are not in the policies
    graph.add_conditional_edges("retrieve", lambda s: "gather_facts" if s["needs_order_data"] else "check_answerable")
    graph.add_edge("gather_facts", "check_answerable")
    graph.add_conditional_edges("check_answerable", lambda s: "draft_policy" if s["answerable"] else "escalate")
    graph.add_edge("escalate", END)
    graph.add_edge("draft_policy", "verify_answer")

    graph.add_conditional_edges("extract_order", lambda s: END if s["response_kind"] == "clarify" else "validate")
    graph.add_conditional_edges(
        "validate",
        lambda s: ("propose" if s["identity"]["role"] == "ejecutivo" else "quote") if s["evaluation"]["rules"] and all(r["ok"] for r in s["evaluation"]["rules"]) else "explain",
    )
    graph.add_edge("explain", "verify_answer")
    graph.add_edge("quote", END)
    graph.add_conditional_edges("propose", lambda s: "await_approval" if s["proposal"] else END)
    graph.add_edge("await_approval", END)
    graph.add_edge("verify_answer", END)

    return graph.compile(checkpointer=checkpointer)
