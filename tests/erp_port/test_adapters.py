"""The same contract must hold for every ERP adapter: switching ERP changes nothing upstream."""

from decimal import Decimal

import pytest
from app.erp.factory import create_erp
from app.erp.port import OrderLine, OrderRequest, OrderStatus

from conftest import TOKENS

ADAPTERS = ["dynamics", "sap"]


@pytest.fixture(params=ADAPTERS)
def erp(request, erp_client):
    """Returns (read adapter, write adapter) for the parametrized ERP."""
    make = lambda credential: create_erp(TOKENS[credential], request.param, http_client=erp_client())  # noqa: E731
    return make("read"), make("write")


def pradera_order(reference="PROP-000001"):
    return OrderRequest(
        reference=reference,
        customer_code="CL-001",
        lines=[
            OrderLine(sku="CM-001", warehouse="GYE", quantity=300, unit="fardo", unit_price=Decimal("18.50")),
            OrderLine(sku="CM-002", warehouse="GYE", quantity=200, unit="caja", unit_price=Decimal("28.00"), discount_pct=Decimal("5")),
        ],
    )


def single_line(customer, sku, quantity, warehouse="UIO", unit="unidad", price="0", discount="0", reference="PROP-000002"):
    return OrderRequest(
        reference=reference,
        customer_code=customer,
        lines=[OrderLine(sku=sku, warehouse=warehouse, quantity=quantity, unit=unit, unit_price=Decimal(price), discount_pct=Decimal(discount))],
    )


def test_reads(erp):
    read, _ = erp
    product = read.get_product("CM-001")
    assert (product.name, product.unit, product.list_price, product.active) == ("Arroz Andes 10x2 kg", "fardo", Decimal("18.50"), True)
    assert read.get_price("TE-001") == Decimal("780")
    assert read.get_stock("HG-001", "UIO").available == 25
    assert read.get_customer("CL-004").max_discount_pct == Decimal("6")
    assert read.get_credit("CL-004").available == Decimal("400")


def test_discontinued_product_exposes_substitute(erp):
    read, _ = erp
    product = read.get_product("HG-005")
    assert (product.active, product.substitute_sku) == (False, "HG-105")


@pytest.mark.parametrize("sku", ["XX-999", "CM-001' or 'a' eq 'a"])
def test_unknown_or_injected_sku_finds_nothing(erp, sku):
    read, _ = erp
    assert read.get_product(sku) is None


def test_read_adapter_cannot_write(erp):
    read, _ = erp
    result = read.create_order(pradera_order())
    assert result.status == OrderStatus.FORBIDDEN
    assert read.get_stock("CM-001", "GYE").available == 1200


def test_create_then_replay(erp):
    read, write = erp
    created = write.create_order(pradera_order())
    replay = write.create_order(pradera_order())

    assert (created.status, created.total) == (OrderStatus.CREATED, Decimal("10870"))
    assert (replay.status, replay.order_number) == (OrderStatus.DUPLICATE, created.order_number)
    assert read.get_stock("CM-001", "GYE").available == 900


@pytest.mark.parametrize(
    ("order", "code"),
    [
        (single_line("CL-002", "HG-001", 40, price="45"), "INSUFFICIENT_STOCK"),
        (single_line("CL-004", "TE-001", 1, price="780"), "CREDIT_EXCEEDED"),
        (single_line("CL-003", "CM-003", 10, unit="fardo", price="16", discount="10"), "DISCOUNT_EXCEEDED"),
        (single_line("CL-002", "HG-005", 1, price="29"), "PRODUCT_INACTIVE"),
    ],
)
def test_rejections_are_typed(erp, order, code):
    _, write = erp
    result = write.create_order(order)
    assert (result.status, result.error_code) == (OrderStatus.REJECTED, code)
