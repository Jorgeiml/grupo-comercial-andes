"""Proposal lifecycle: the agent proposes, a person approves, a separate service executes.

Every state change and every denied attempt is written to the append-only audit trail.
"""

from enum import StrEnum

from pydantic import BaseModel

from ..auth.tokens import APPROVER_ROLES, Identity
from ..engine.models import OrderDraft, ProposalContent
from ..engine.pricing import APPROVAL_CITATION
from ..engine.validation import Evaluation, usd
from ..store.db import audit, connect, now


class Status(StrEnum):
    PENDIENTE = "PENDIENTE"
    APROBADA = "APROBADA"
    RECHAZADA = "RECHAZADA"
    EN_CORRECCION = "EN_CORRECCION"
    EJECUTADA = "EJECUTADA"
    BLOQUEADA = "BLOQUEADA"


# The only allowed transitions; anything else is refused
TRANSITIONS: dict[Status, set[Status]] = {
    Status.PENDIENTE: {Status.APROBADA, Status.RECHAZADA, Status.EN_CORRECCION},
    Status.APROBADA: {Status.EJECUTADA, Status.BLOQUEADA},
    Status.EN_CORRECCION: set(),  # a correction is a new proposal that supersedes this one
    Status.RECHAZADA: set(),
    Status.EJECUTADA: set(),
    Status.BLOQUEADA: set(),
}


class Decision(StrEnum):
    APPROVE = "aprobar"
    REJECT = "rechazar"
    REQUEST_CHANGES = "corregir"


DECISION_STATUS = {Decision.APPROVE: Status.APROBADA, Decision.REJECT: Status.RECHAZADA, Decision.REQUEST_CHANGES: Status.EN_CORRECCION}


class ApprovalError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Proposal(BaseModel):
    id: str
    status: Status
    content: ProposalContent
    content_hash: str
    draft: OrderDraft
    created_by: str
    created_at: str
    supersedes: str | None = None
    thread_id: str | None = None
    erp_adapter: str | None = None
    erp_order_number: str | None = None


def _row_to_proposal(row) -> Proposal:
    return Proposal(
        id=row["id"],
        status=Status(row["status"]),
        content=ProposalContent.model_validate_json(row["content_json"]),
        content_hash=row["content_hash"],
        draft=OrderDraft.model_validate_json(row["draft_json"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
        supersedes=row["supersedes"],
        thread_id=row["thread_id"],
        erp_adapter=row["erp_adapter"],
        erp_order_number=row["erp_order_number"],
    )


def _fetch(conn, proposal_id: str) -> Proposal:
    row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
    if row is None:
        raise ApprovalError("NOT_FOUND", f"La propuesta {proposal_id} no existe")
    return _row_to_proposal(row)


def _set_status(conn, proposal: Proposal, new: Status, **fields) -> None:
    if new not in TRANSITIONS[proposal.status]:
        raise ApprovalError("INVALID_STATE", f"La propuesta {proposal.id} está {proposal.status}; no puede pasar a {new}")
    assignments = ", ".join(f"{column} = ?" for column in ["status", "updated_at", *fields])
    conn.execute(f"UPDATE proposals SET {assignments} WHERE id = ?", (new, now(), *fields.values(), proposal.id))


def _run(identity: Identity, action: str, proposal_id: str | None, work):
    """Runs work(conn) in one write transaction; denied attempts are audited after the rollback."""
    try:
        with connect() as conn:
            # IMMEDIATE takes the write lock first: two simultaneous approvals cannot both pass the checks
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = work(conn)
                conn.execute("COMMIT")
                return result
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    except ApprovalError as e:
        with connect() as conn:
            audit(conn, actor=identity.user, role=identity.role, action=action, proposal_id=proposal_id, result=f"denied:{e.code}", detail=e.message)
        raise


def get_proposal(proposal_id: str) -> Proposal:
    with connect() as conn:
        return _fetch(conn, proposal_id)


def list_proposals(status: Status | None = None) -> list[Proposal]:
    with connect() as conn:
        query, params = "SELECT * FROM proposals", ()
        if status:
            query, params = query + " WHERE status = ?", (status,)
        return [_row_to_proposal(row) for row in conn.execute(query + " ORDER BY seq", params)]


def _open_correction(conn, thread_id: str | None) -> str | None:
    """The conversation's proposal that was sent back for correction and has no corrected version yet."""
    if not thread_id:
        return None
    row = conn.execute(
        "SELECT id FROM proposals p WHERE thread_id = ? AND status = ?"
        " AND NOT EXISTS (SELECT 1 FROM proposals n WHERE n.supersedes = p.id) ORDER BY seq DESC LIMIT 1",
        (thread_id, Status.EN_CORRECCION),
    ).fetchone()
    return row["id"] if row else None


def create_proposal(evaluation: Evaluation, identity: Identity, *, supersedes: str | None = None, thread_id: str | None = None) -> Proposal:
    def work(conn):
        if identity.role != "ejecutivo":
            raise ApprovalError("ROLE_NOT_ALLOWED", f"El rol {identity.role} no prepara propuestas")
        if not evaluation.ok:
            failed = ", ".join(rule.rule for rule in evaluation.failed)
            raise ApprovalError("INVALID_PROPOSAL", f"La propuesta no pasa la validación: {failed}")
        # A new proposal in a conversation whose proposal was sent back is its corrected version (a new hash, a new approval)
        corrects = supersedes or _open_correction(conn, thread_id)
        if corrects:
            previous = _fetch(conn, corrects)
            if previous.status != Status.EN_CORRECCION:
                raise ApprovalError("INVALID_STATE", f"Solo se corrige una propuesta EN_CORRECCION; {corrects} está {previous.status}")
            if conn.execute("SELECT 1 FROM proposals WHERE supersedes = ?", (corrects,)).fetchone():
                raise ApprovalError("ALREADY_CORRECTED", f"La propuesta {corrects} ya tiene una versión corregida")
        supersedes_id = corrects

        content = evaluation.content()
        seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM proposals").fetchone()[0]
        proposal_id = f"PROP-{seq:06d}"
        timestamp = now()
        conn.execute(
            "INSERT INTO proposals (id, seq, status, content_json, content_hash, draft_json, created_by, created_role,"
            " created_at, updated_at, supersedes, thread_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (proposal_id, seq, Status.PENDIENTE, content.model_dump_json(), content.hash, evaluation.draft.model_dump_json(),
             identity.user, identity.role, timestamp, timestamp, supersedes_id, thread_id),
        )
        detail = f"total {usd(content.total)}, aprueba {content.approval_level}" + (f", corrige {supersedes_id}" if supersedes_id else "")
        audit(conn, actor=identity.user, role=identity.role, action="create_proposal", proposal_id=proposal_id,
              content_hash=content.hash, result="ok", detail=detail)
        return _fetch(conn, proposal_id)

    return _run(identity, "create_proposal", supersedes, work)


def decide(proposal_id: str, identity: Identity, decision: Decision, presented_hash: str, comment: str | None = None) -> Proposal:
    def work(conn):
        proposal = _fetch(conn, proposal_id)
        if identity.role not in APPROVER_ROLES:
            raise ApprovalError("NOT_AN_APPROVER", f"El rol {identity.role} no puede aprobar ni rechazar")
        if proposal.status != Status.PENDIENTE:
            raise ApprovalError("INVALID_STATE", f"La propuesta está {proposal.status}, no PENDIENTE")
        if presented_hash != proposal.content_hash:
            raise ApprovalError("HASH_MISMATCH", "La propuesta cambió desde que se mostró: revísela de nuevo")
        if identity.user == proposal.created_by:
            raise ApprovalError("SELF_APPROVAL", "Quien prepara la propuesta no puede decidir sobre ella")
        if decision == Decision.APPROVE and proposal.content.approval_level == "gerente" and identity.role != "gerente":
            raise ApprovalError("LEVEL_INSUFFICIENT", f"Total {usd(proposal.content.total)}: requiere aprobación del gerente ({APPROVAL_CITATION})")

        _set_status(conn, proposal, DECISION_STATUS[decision])
        conn.execute(
            "INSERT INTO approvals (proposal_id, decision, actor, role, content_hash, comment, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (proposal_id, decision, identity.user, identity.role, presented_hash, comment, now()),
        )
        audit(conn, actor=identity.user, role=identity.role, action=f"decide:{decision}", proposal_id=proposal_id,
              content_hash=presented_hash, result="ok", detail=comment)
        return _fetch(conn, proposal_id)

    return _run(identity, f"decide:{decision}", proposal_id, work)


def mark_executed(proposal_id: str, identity: Identity, *, order_number: str, erp_adapter: str, detail: str) -> Proposal:
    def work(conn):
        proposal = _fetch(conn, proposal_id)
        _set_status(conn, proposal, Status.EJECUTADA, erp_order_number=order_number, erp_adapter=erp_adapter)
        audit(conn, actor=identity.user, role=identity.role, action="execute", proposal_id=proposal_id,
              content_hash=proposal.content_hash, result="executed", detail=f"{erp_adapter} {order_number}: {detail}")
        return _fetch(conn, proposal_id)

    return _run(identity, "execute", proposal_id, work)


def mark_blocked(proposal_id: str, identity: Identity, reasons: list[str]) -> Proposal:
    def work(conn):
        proposal = _fetch(conn, proposal_id)
        _set_status(conn, proposal, Status.BLOQUEADA)
        audit(conn, actor=identity.user, role=identity.role, action="execute", proposal_id=proposal_id,
              content_hash=proposal.content_hash, result="blocked", detail=" | ".join(reasons))
        return _fetch(conn, proposal_id)

    return _run(identity, "execute", proposal_id, work)


def record_event(identity: Identity, action: str, result: str, proposal_id: str | None = None, detail: str | None = None) -> None:
    with connect() as conn:
        audit(conn, actor=identity.user, role=identity.role, action=action, proposal_id=proposal_id, result=result, detail=detail)


def valid_approval(proposal: Proposal) -> dict | None:
    """The approval that authorizes execution: an 'aprobar' bound to the proposal's current hash."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM approvals WHERE proposal_id = ? AND decision = ? AND content_hash = ? ORDER BY id DESC LIMIT 1",
            (proposal.id, Decision.APPROVE, proposal.content_hash),
        ).fetchone()
    return dict(row) if row and proposal.status == Status.APROBADA else None


def last_decisions() -> dict[str, dict]:
    """Each proposal's latest decision (who, role, comment, when): what the executive needs to read to correct it."""
    with connect() as conn:
        rows = conn.execute("SELECT proposal_id, decision, actor, role, comment, at FROM approvals ORDER BY id").fetchall()
    return {row["proposal_id"]: dict(row) for row in rows}


def audit_trail(proposal_id: str | None = None) -> list[dict]:
    with connect() as conn:
        query, params = "SELECT * FROM audit_log", ()
        if proposal_id:
            query, params = query + " WHERE proposal_id = ?", (proposal_id,)
        return [dict(row) for row in conn.execute(query + " ORDER BY id", params)]
