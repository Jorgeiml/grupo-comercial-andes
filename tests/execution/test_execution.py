"""The execution service writes to the ERP only with a valid approval, a passing revalidation and idempotency."""

import pytest
from app.approvals.service import ApprovalError, Decision, Status, audit_trail, create_proposal, decide, get_proposal
from app.auth.tokens import Identity
from app.engine.models import OrderDraft, RequestedLine
from app.engine.validation import evaluate
from app.erp.factory import create_erp
from app.execution.service import ExecutionStatus, execute

from conftest import TOKENS

EJECUTIVO = Identity(user="luis", role="ejecutivo")
GERENTE = Identity(user="ana", role="gerente")


@pytest.fixture(params=["dynamics", "sap"])
def write_erp(request, erp_client):
    return create_erp(TOKENS["write"], request.param, http_client=erp_client())


@pytest.fixture
def approved(read_erp):
    """La Pradera, 300 rice bales from Guayaquil: $5.050,50, approved by the manager."""
    draft = OrderDraft(customer_code="CL-001", lines=[RequestedLine(sku="CM-001", quantity=300, warehouse="GYE")], requested_payment_days=60)
    proposal = create_proposal(evaluate(draft, read_erp), EJECUTIVO)
    return decide(proposal.id, GERENTE, Decision.APPROVE, proposal.content_hash)


def erp_orders(erp_db):
    return erp_db("SELECT external_ref, total_net_cents FROM sales_orders")


def test_approved_proposal_is_written_once(approved, write_erp, erp_db):
    result = execute(approved.id, GERENTE, write_erp)

    assert result.status == ExecutionStatus.EXECUTED
    assert result.erp_adapter == write_erp.name
    assert erp_orders(erp_db) == [{"external_ref": approved.id, "total_net_cents": 505050}]
    stored = get_proposal(approved.id)
    assert (stored.status, stored.erp_order_number) == (Status.EJECUTADA, result.order_number)


def test_second_execution_does_not_write_again(approved, write_erp, erp_db):
    first = execute(approved.id, GERENTE, write_erp)
    second = execute(approved.id, GERENTE, write_erp)

    assert second.status == ExecutionStatus.ALREADY_EXECUTED
    assert second.order_number == first.order_number
    assert len(erp_orders(erp_db)) == 1


def test_stock_changed_after_approval_blocks_the_write(approved, write_erp, erp_client, erp_db):
    # Demo scenario 4: someone takes stock after the manager approved
    erp_client("admin").patch("/admin/stock", json={"sku": "CM-001", "warehouse": "GYE", "quantity": 100})

    result = execute(approved.id, GERENTE, write_erp)

    assert result.status == ExecutionStatus.BLOCKED
    assert "solicitado 300, disponible 100" in result.reasons[0]
    assert erp_orders(erp_db) == []
    assert get_proposal(approved.id).status == Status.BLOQUEADA


def test_price_changed_after_approval_blocks_the_write(approved, write_erp, erp_db):
    erp_db("UPDATE products SET list_price_cents = 1990 WHERE sku = 'CM-001'")

    result = execute(approved.id, GERENTE, write_erp)

    assert result.status == ExecutionStatus.BLOCKED
    assert any("El precio cambió" in reason for reason in result.reasons)
    assert erp_orders(erp_db) == []


def test_credit_consumed_after_approval_blocks_the_write(approved, write_erp, erp_db):
    erp_db("UPDATE customers SET credit_used_cents = credit_limit_cents WHERE code = 'CL-001'")
    result = execute(approved.id, GERENTE, write_erp)
    assert result.status == ExecutionStatus.BLOCKED
    assert erp_orders(erp_db) == []


def test_pending_proposal_cannot_be_executed(read_erp, write_erp, erp_db):
    draft = OrderDraft(customer_code="CL-002", lines=[RequestedLine(sku="CM-003", quantity=10, warehouse="UIO")])
    pending = create_proposal(evaluate(draft, read_erp), EJECUTIVO)

    with pytest.raises(ApprovalError) as info:
        execute(pending.id, GERENTE, write_erp)
    assert info.value.code == "NOT_APPROVED"
    assert erp_orders(erp_db) == []


def test_read_credential_cannot_execute_even_if_misconfigured(approved, read_erp, erp_db):
    # If the agent's read adapter were wired by mistake, the ERP itself refuses the write
    result = execute(approved.id, GERENTE, read_erp)
    assert result.status == ExecutionStatus.BLOCKED
    assert "FORBIDDEN" in result.reasons[0]
    assert erp_orders(erp_db) == []


def test_execution_is_audited(approved, write_erp, erp_client):
    erp_client("admin").patch("/admin/stock", json={"sku": "CM-001", "warehouse": "GYE", "quantity": 100})
    execute(approved.id, GERENTE, write_erp)

    actions = [(row["action"], row["result"]) for row in audit_trail(approved.id)]
    assert actions == [("create_proposal", "ok"), ("decide:aprobar", "ok"), ("execute", "blocked")]
