"""Stock history: every change leaves a movement with the balance after it, and the history cannot be edited."""

import sqlite3

import pytest
from mock_erp.db import connect

from test_orders import ORDERS, order

MOVEMENTS = "/admin/stock-movements"


def history(erp_client, sku, warehouse):
    rows = erp_client("admin").get(MOVEMENTS, params={"sku": sku}).json()
    return [(m["kind"], m["quantity"], m["balance"], m["external_ref"]) for m in rows if m["warehouse"] == warehouse]


def test_seed_stock_is_the_first_movement(erp_client):
    assert history(erp_client, "CM-001", "GYE") == [("initial", 1200, 1200, None)]


def test_a_sale_and_a_demo_adjustment_are_recorded_with_their_balance(erp_client):
    erp_client("write").post(ORDERS, json=order())
    erp_client("admin").patch("/admin/stock", json={"sku": "CM-001", "warehouse": "GYE", "quantity": 100})

    assert history(erp_client, "CM-001", "GYE") == [
        ("initial", 1200, 1200, None),
        ("sale", -300, 900, "PROP-000001"),
        ("adjustment", -800, 100, None),
    ]


def test_a_replayed_order_does_not_record_a_second_sale(erp_client):
    client = erp_client("write")
    client.post(ORDERS, json=order())
    client.post(ORDERS, json=order())
    assert [kind for kind, *_ in history(erp_client, "CM-001", "GYE")] == ["initial", "sale"]


def test_a_rejected_order_records_nothing(erp_client):
    too_many = order(lines=[{"ItemNumber": "CM-001", "OrderedSalesQuantity": 99999, "ShippingWarehouseId": "GYE"}])
    assert erp_client("write").post(ORDERS, json=too_many).status_code >= 400
    assert history(erp_client, "CM-001", "GYE") == [("initial", 1200, 1200, None)]


def test_history_is_append_only(erp_client):
    with connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM stock_movements")


def test_history_requires_the_admin_credential(erp_client):
    assert erp_client("read").get(MOVEMENTS, params={"sku": "CM-001"}).status_code == 403
