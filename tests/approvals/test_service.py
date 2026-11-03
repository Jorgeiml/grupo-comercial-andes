"""Proposal lifecycle: roles, approval levels, hash binding, segregation of duties and audit trail."""

import sqlite3

import pytest
from app.approvals.service import (
    ApprovalError,
    Decision,
    Status,
    audit_trail,
    create_proposal,
    decide,
    get_proposal,
    last_decisions,
    valid_approval,
)
from app.auth.tokens import Identity
from app.engine.models import OrderDraft, RequestedLine
from app.engine.validation import evaluate
from app.store.db import connect

EJECUTIVO = Identity(user="luis", role="ejecutivo")
SUPERVISOR = Identity(user="sofia", role="supervisor")
GERENTE = Identity(user="ana", role="gerente")
CLIENTE = Identity(user="pradera", role="cliente")


def evaluation(read_erp, customer="CL-002", sku="CM-003", quantity=10, warehouse="UIO"):
    return evaluate(OrderDraft(customer_code=customer, lines=[RequestedLine(sku=sku, quantity=quantity, warehouse=warehouse)]), read_erp)


@pytest.fixture
def small(read_erp):
    """$158,40: the supervisor can approve it."""
    return create_proposal(evaluation(read_erp), EJECUTIVO)


@pytest.fixture
def large(read_erp):
    """$5.050,50: only the manager can approve it."""
    return create_proposal(evaluation(read_erp, "CL-001", "CM-001", 300, "GYE"), EJECUTIVO)


def error_code(call):
    with pytest.raises(ApprovalError) as info:
        call()
    return info.value.code


def test_proposal_is_created_pending_with_short_id(small):
    assert small.id == "PROP-000001"
    assert len(small.id) <= 35  # SAP PurchaseOrderByCustomer limit
    assert small.status == Status.PENDIENTE
    assert small.content.approval_level == "supervisor"


def test_only_executives_prepare_proposals(read_erp):
    for identity in (CLIENTE, SUPERVISOR, GERENTE):
        assert error_code(lambda: create_proposal(evaluation(read_erp), identity)) == "ROLE_NOT_ALLOWED"


def test_invalid_evaluation_cannot_become_a_proposal(read_erp):
    short = evaluation(read_erp, sku="HG-001", quantity=40)  # 25 in stock
    assert error_code(lambda: create_proposal(short, EJECUTIVO)) == "INVALID_PROPOSAL"


def test_supervisor_approves_within_limit(small):
    approved = decide(small.id, SUPERVISOR, Decision.APPROVE, small.content_hash)
    assert approved.status == Status.APROBADA
    assert valid_approval(approved)["actor"] == "sofia"


def test_supervisor_cannot_approve_above_limit(large):
    assert error_code(lambda: decide(large.id, SUPERVISOR, Decision.APPROVE, large.content_hash)) == "LEVEL_INSUFFICIENT"
    assert get_proposal(large.id).status == Status.PENDIENTE


def test_manager_approves_above_limit(large):
    assert decide(large.id, GERENTE, Decision.APPROVE, large.content_hash).status == Status.APROBADA


@pytest.mark.parametrize("identity", [CLIENTE, EJECUTIVO])
def test_non_approvers_cannot_decide(small, identity):
    assert error_code(lambda: decide(small.id, identity, Decision.APPROVE, small.content_hash)) == "NOT_AN_APPROVER"


def test_approval_requires_the_hash_the_approver_saw(small):
    assert error_code(lambda: decide(small.id, SUPERVISOR, Decision.APPROVE, "0" * 64)) == "HASH_MISMATCH"


def test_creator_cannot_approve_own_proposal(read_erp):
    # Same person, approver role: segregation of duties still applies
    proposal = create_proposal(evaluation(read_erp), EJECUTIVO)
    same_person = Identity(user=EJECUTIVO.user, role="supervisor")
    assert error_code(lambda: decide(proposal.id, same_person, Decision.APPROVE, proposal.content_hash)) == "SELF_APPROVAL"


def test_double_approval_is_refused(small):
    decide(small.id, SUPERVISOR, Decision.APPROVE, small.content_hash)
    assert error_code(lambda: decide(small.id, GERENTE, Decision.APPROVE, small.content_hash)) == "INVALID_STATE"


def test_rejected_proposal_is_final(small):
    decide(small.id, SUPERVISOR, Decision.REJECT, small.content_hash, comment="cliente desistió")
    assert error_code(lambda: decide(small.id, SUPERVISOR, Decision.APPROVE, small.content_hash)) == "INVALID_STATE"


def test_correction_creates_new_version_with_new_hash(read_erp, small):
    decide(small.id, SUPERVISOR, Decision.REQUEST_CHANGES, small.content_hash, comment="son 12 fardos")
    corrected = create_proposal(evaluation(read_erp, quantity=12), EJECUTIVO, supersedes=small.id)

    assert corrected.id == "PROP-000002" and corrected.supersedes == small.id
    assert corrected.content_hash != small.content_hash
    assert valid_approval(get_proposal(small.id)) is None


def test_next_proposal_in_the_conversation_is_the_corrected_version(read_erp):
    sent_back = create_proposal(evaluation(read_erp), EJECUTIVO, thread_id="t1")
    decide(sent_back.id, SUPERVISOR, Decision.REQUEST_CHANGES, sent_back.content_hash, comment="son 12 fardos")

    corrected = create_proposal(evaluation(read_erp, quantity=12), EJECUTIVO, thread_id="t1")
    other_conversation = create_proposal(evaluation(read_erp, quantity=5), EJECUTIVO, thread_id="t2")
    next_in_t1 = create_proposal(evaluation(read_erp, quantity=8), EJECUTIVO, thread_id="t1")

    assert corrected.supersedes == sent_back.id and corrected.status == Status.PENDIENTE
    assert other_conversation.supersedes is None
    assert next_in_t1.supersedes is None  # the correction was already made: later proposals are independent
    assert last_decisions()[sent_back.id]["comment"] == "son 12 fardos"


def test_a_proposal_is_corrected_only_once(read_erp, small):
    decide(small.id, SUPERVISOR, Decision.REQUEST_CHANGES, small.content_hash)
    create_proposal(evaluation(read_erp, quantity=12), EJECUTIVO, supersedes=small.id)
    assert error_code(lambda: create_proposal(evaluation(read_erp, quantity=13), EJECUTIVO, supersedes=small.id)) == "ALREADY_CORRECTED"


def test_only_proposals_in_correction_can_be_superseded(read_erp, small):
    assert error_code(lambda: create_proposal(evaluation(read_erp), EJECUTIVO, supersedes=small.id)) == "INVALID_STATE"


def test_every_action_and_denial_is_audited(large):
    decide_supervisor = lambda: decide(large.id, SUPERVISOR, Decision.APPROVE, large.content_hash)  # noqa: E731
    error_code(decide_supervisor)
    decide(large.id, GERENTE, Decision.APPROVE, large.content_hash)

    trail = [(row["actor"], row["action"], row["result"]) for row in audit_trail(large.id)]
    assert trail == [
        ("luis", "create_proposal", "ok"),
        ("sofia", "decide:aprobar", "denied:LEVEL_INSUFFICIENT"),
        ("ana", "decide:aprobar", "ok"),
    ]
    assert all(row["content_hash"] == large.content_hash for row in audit_trail(large.id) if row["result"] == "ok")


def test_audit_trail_cannot_be_modified_or_deleted(small):
    with connect() as conn:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE audit_log SET actor = 'nadie'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM audit_log")
