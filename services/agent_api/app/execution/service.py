"""Execution service: the only component that writes to the ERP, and the only holder of the write token.

It writes only when three conditions hold:
  1. a valid approval bound to the proposal's current hash,
  2. revalidation against fresh ERP data passes and reproduces the approved content,
  3. idempotency: the proposal ID is the ERP reference, so a replay never creates a second order.
"""

from enum import StrEnum

from pydantic import BaseModel

from ..approvals.service import (
    ApprovalError,
    Status,
    get_proposal,
    mark_blocked,
    mark_executed,
    record_event,
    valid_approval,
)
from ..auth.tokens import Identity
from ..engine.validation import evaluate
from ..erp.factory import write_erp
from ..erp.port import ErpPort, ErpUnavailable, OrderLine, OrderRequest, OrderStatus


class ExecutionStatus(StrEnum):
    EXECUTED = "executed"
    ALREADY_EXECUTED = "already_executed"
    BLOCKED = "blocked"
    ERP_UNAVAILABLE = "erp_unavailable"  # nothing written; safe to retry thanks to idempotency


class ExecutionResult(BaseModel):
    status: ExecutionStatus
    proposal_id: str
    order_number: str | None = None
    erp_adapter: str | None = None
    reasons: list[str] = []
    erp_payload: dict | None = None


def execute(proposal_id: str, requested_by: Identity, erp: ErpPort | None = None) -> ExecutionResult:
    erp = erp or write_erp()
    proposal = get_proposal(proposal_id)

    if proposal.status == Status.EJECUTADA:
        record_event(requested_by, "execute", "already_executed", proposal_id, proposal.erp_order_number)
        return ExecutionResult(status=ExecutionStatus.ALREADY_EXECUTED, proposal_id=proposal_id,
                               order_number=proposal.erp_order_number, erp_adapter=proposal.erp_adapter)

    # 1. Valid approval bound to the current hash
    if valid_approval(proposal) is None:
        record_event(requested_by, "execute", "denied:NOT_APPROVED", proposal_id, f"estado {proposal.status}")
        raise ApprovalError("NOT_APPROVED", f"La propuesta {proposal_id} no tiene una aprobación vigente (estado {proposal.status})")

    # 2. Revalidation with fresh ERP data, using the same engine that built the proposal
    quoted = {line.sku: line.unit_price for line in proposal.content.lines}
    try:
        fresh = evaluate(proposal.draft, erp, quoted_prices=quoted)
    except ErpUnavailable as e:
        record_event(requested_by, "execute", "erp_unavailable", proposal_id, str(e))
        return ExecutionResult(status=ExecutionStatus.ERP_UNAVAILABLE, proposal_id=proposal_id, reasons=["No se pudo revalidar: ERP no disponible"])

    reasons = [rule.reason for rule in fresh.failed]
    if not reasons and fresh.content().hash != proposal.content_hash:
        reasons.append("Los datos del ERP cambiaron: el pedido recalculado ya no coincide con lo aprobado")
    if reasons:
        mark_blocked(proposal_id, requested_by, reasons)
        return ExecutionResult(status=ExecutionStatus.BLOCKED, proposal_id=proposal_id, reasons=reasons)

    # 3. Idempotent write: the proposal ID travels as the ERP reference
    order = OrderRequest(
        reference=proposal.id,
        customer_code=proposal.content.customer_code,
        currency=proposal.content.currency,
        lines=[
            OrderLine(sku=l.sku, warehouse=l.warehouse, quantity=l.quantity, unit=l.unit, unit_price=l.unit_price, discount_pct=l.discount_pct)
            for l in proposal.content.lines
        ],
    )
    try:
        result = erp.create_order(order)
    except ErpUnavailable as e:
        record_event(requested_by, "execute", "erp_unavailable", proposal_id, str(e))
        return ExecutionResult(status=ExecutionStatus.ERP_UNAVAILABLE, proposal_id=proposal_id, reasons=["ERP no disponible; reintentar es seguro"])

    if result.status in (OrderStatus.CREATED, OrderStatus.DUPLICATE):
        try:
            mark_executed(proposal_id, requested_by, order_number=result.order_number, erp_adapter=erp.name, detail=result.status.value)
        except ApprovalError:
            # A concurrent execution already marked it: the ERP's idempotency guaranteed a single order
            current = get_proposal(proposal_id)
            return ExecutionResult(status=ExecutionStatus.ALREADY_EXECUTED, proposal_id=proposal_id,
                                   order_number=current.erp_order_number, erp_adapter=current.erp_adapter)
        return ExecutionResult(status=ExecutionStatus.EXECUTED, proposal_id=proposal_id, order_number=result.order_number,
                               erp_adapter=erp.name, erp_payload=result.erp_payload)

    # The ERP's own checks refused it (defense in depth) or the credential cannot write
    erp_reason = f"El ERP rechazó el pedido ({result.error_code}): {result.message}"
    mark_blocked(proposal_id, requested_by, [erp_reason])
    return ExecutionResult(status=ExecutionStatus.BLOCKED, proposal_id=proposal_id, reasons=[erp_reason], erp_payload=result.erp_payload)
