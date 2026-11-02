"""Validation engine against the in-process ERP: every rule, its reason, and the proposal hash."""

from decimal import Decimal

import pytest
from app.engine.alternatives import suggest
from app.engine.models import OrderDraft, RequestedLine
from app.engine.validation import evaluate

ALL_RULES = [
    "CUSTOMER_EXISTS", "PRODUCT_ACTIVE", "QUANTITY_VALID", "STOCK_AVAILABLE",
    "PRICE_MATCHES_ERP", "DISCOUNT_WITHIN_TIER", "CREDIT_AVAILABLE", "APPROVAL_LEVEL",
]


def draft(customer, *lines, discount=None):
    return OrderDraft(
        customer_code=customer,
        lines=[RequestedLine(sku=sku, quantity=qty, warehouse=wh) for sku, qty, wh in lines],
        requested_discount_pct=Decimal(discount) if discount is not None else None,
    )


def failed(evaluation):
    return [rule.rule for rule in evaluation.failed]


def test_demo_complex_order_passes(read_erp):
    result = evaluate(draft("CL-001", ("CM-001", 300, "GYE")), read_erp)

    assert result.ok
    [line] = result.lines
    assert (line.unit_price, line.discount_pct, line.amount) == (Decimal("18.50"), Decimal("9"), Decimal("5050.50"))
    assert result.total == Decimal("5050.50")
    assert result.approval_level == "gerente"
    assert [rule.rule for rule in result.rules] == ALL_RULES


def test_every_rule_runs_even_when_several_fail(read_erp):
    result = evaluate(draft("CL-004", ("TE-001", 10, "UIO")), read_erp)  # 8 in stock, 400 of credit
    assert [rule.rule for rule in result.rules] == ALL_RULES
    assert failed(result) == ["STOCK_AVAILABLE", "CREDIT_AVAILABLE"]


@pytest.mark.parametrize(
    ("order", "rule", "fragment"),
    [
        (draft("CL-999", ("CM-001", 1, "UIO")), "CUSTOMER_EXISTS", "no existe"),
        (draft("CL-002", ("XX-999", 1, "UIO")), "PRODUCT_ACTIVE", "XX-999 no existe"),
        (draft("CL-002", ("HG-005", 2, "UIO")), "PRODUCT_ACTIVE", "sustituto homologado: HG-105"),
        (draft("CL-002", ("CM-003", 0, "UIO")), "QUANTITY_VALID", "CM-003: 0"),
        (draft("CL-002", ("HG-001", 40, "UIO")), "STOCK_AVAILABLE", "solicitado 40, disponible 25"),
        (draft("CL-002", ("HG-001", 20, "UIO"), ("HG-001", 20, "UIO")), "STOCK_AVAILABLE", "solicitado 40"),
        (draft("CL-002", ("CM-003", 10, "CUE")), "STOCK_AVAILABLE", "la bodega CUE no existe"),
        (draft("CL-002", ("CM-003", 10, "UIO"), discount="90"), "DISCOUNT_WITHIN_TIER", "Se solicitó 90 %"),
        (draft("CL-004", ("TE-001", 1, "UIO")), "CREDIT_AVAILABLE", "excede el crédito disponible $400,00"),
    ],
)
def test_failing_rule_explains_why(read_erp, order, rule, fragment):
    result = evaluate(order, read_erp)
    assert rule in failed(result)
    reason = next(r.reason for r in result.rules if r.rule == rule)
    assert fragment in reason


def test_requested_discount_within_policy_is_accepted(read_erp):
    # Oro, 300 units: policy gives 9 %, asking for 9 % is fine
    assert evaluate(draft("CL-001", ("CM-001", 300, "GYE"), discount="9"), read_erp).ok


def test_requested_discount_above_policy_but_below_tier_max_is_rejected(read_erp):
    # Oro max is 12 %, but for 300 units the policy gives 9 %: 12 % is not allowed (POL-COM-01 §3.2)
    result = evaluate(draft("CL-001", ("CM-001", 300, "GYE"), discount="12"), read_erp)
    assert failed(result) == ["DISCOUNT_WITHIN_TIER"]


def test_changed_list_price_is_detected(read_erp):
    result = evaluate(draft("CL-002", ("CM-003", 10, "UIO")), read_erp, quoted_prices={"CM-003": Decimal("15.00")})
    assert failed(result) == ["PRICE_MATCHES_ERP"]


def test_hash_is_stable_and_binds_content(read_erp):
    order = draft("CL-001", ("CM-001", 300, "GYE"))
    first, again = evaluate(order, read_erp).content(), evaluate(order, read_erp).content()
    changed = evaluate(draft("CL-001", ("CM-001", 301, "GYE")), read_erp).content()

    assert first.hash == again.hash
    assert first.hash != changed.hash


def test_shortage_offers_partial_and_combined_alternatives(read_erp):
    # Don Lucho, 40 blenders in Quito: 25 available; substitute HG-101 has 30
    result = evaluate(draft("CL-002", ("HG-001", 40, "UIO")), read_erp)
    alternatives = {alt.kind: alt for alt in suggest(result, read_erp)}

    partial = alternatives["partial"].evaluation
    assert partial.ok and [(l.sku, l.quantity) for l in partial.lines] == [("HG-001", 25)]

    # 40 of the substitute alone does not fit its stock either: shown, but marked as failing
    assert failed(alternatives["substitute"].evaluation) == ["STOCK_AVAILABLE"]

    combined = alternatives["partial_plus_substitute"].evaluation
    assert combined.ok
    assert [(l.sku, l.quantity) for l in combined.lines] == [("HG-001", 25), ("HG-101", 15)]


def test_discontinued_product_offers_substitute(read_erp):
    result = evaluate(draft("CL-002", ("HG-005", 2, "UIO")), read_erp)
    alternatives = suggest(result, read_erp)
    assert [alt.kind for alt in alternatives] == ["substitute"]
    assert alternatives[0].evaluation.ok
    assert [l.sku for l in alternatives[0].evaluation.lines] == ["HG-105"]


def test_valid_order_has_no_alternatives(read_erp):
    assert suggest(evaluate(draft("CL-002", ("CM-003", 10, "UIO")), read_erp), read_erp) == []
