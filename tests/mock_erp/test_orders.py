"""Order writes: business rules, idempotency and atomicity, checked on the ERP side."""

import copy

import pytest

ORDERS = "/data/SalesOrderHeadersV2"
SAP_ORDERS = "/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder"


def order(reference="PROP-000001", customer="CL-001", lines=None):
    return {
        "CustomersOrderReference": reference,
        "OrderingCustomerAccountNumber": customer,
        "SalesOrderLines": lines
        or [
            {"ItemNumber": "CM-001", "OrderedSalesQuantity": 300, "ShippingWarehouseId": "GYE", "SalesPrice": "18.50"},
            {"ItemNumber": "CM-002", "OrderedSalesQuantity": 200, "ShippingWarehouseId": "GYE", "SalesPrice": "28.00", "LineDiscountPercentage": 5},
        ],
    }


def line(sku="CM-003", quantity=10, warehouse="UIO", **extra):
    return {"ItemNumber": sku, "OrderedSalesQuantity": quantity, "ShippingWarehouseId": warehouse, **extra}


def stock(erp_db, sku, warehouse):
    return erp_db("SELECT quantity FROM stock WHERE sku = ? AND warehouse = ?", sku, warehouse)[0]["quantity"]


def credit_used(erp_db, customer):
    return erp_db("SELECT credit_used_cents FROM customers WHERE code = ?", customer)[0]["credit_used_cents"]


def test_valid_order_is_created_and_updates_stock_and_credit(erp_client, erp_db):
    response = erp_client("write").post(ORDERS, json=order())

    assert response.status_code == 201
    body = response.json()
    assert body["SalesOrderNumber"] == "SO-000001"
    assert [line["LineAmount"] for line in body["SalesOrderLines"]] == [5550, 5320]
    assert stock(erp_db, "CM-001", "GYE") == 1200 - 300
    assert stock(erp_db, "CM-002", "GYE") == 350 - 200
    assert credit_used(erp_db, "CL-001") == 2_000_000 + 1_087_000


def test_replay_returns_existing_order_without_duplicating(erp_client, erp_db):
    client = erp_client("write")
    first = client.post(ORDERS, json=order()).json()
    replay = client.post(ORDERS, json=order())

    assert replay.status_code == 200
    assert replay.json()["SalesOrderNumber"] == first["SalesOrderNumber"]
    assert len(erp_db("SELECT * FROM sales_orders")) == 1
    assert stock(erp_db, "CM-001", "GYE") == 900  # decremented once


def test_same_reference_with_different_content_is_a_conflict(erp_client, erp_db):
    client = erp_client("write")
    client.post(ORDERS, json=order())
    changed = copy.deepcopy(order())
    changed["SalesOrderLines"][0]["OrderedSalesQuantity"] = 301

    response = client.post(ORDERS, json=changed)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert stock(erp_db, "CM-001", "GYE") == 900


def test_same_order_is_visible_through_both_api_styles(erp_client):
    erp_client("write").post(ORDERS, json=order())
    [sap_order] = erp_client("read").get(SAP_ORDERS).json()["d"]["results"]
    assert sap_order["PurchaseOrderByCustomer"] == "PROP-000001"
    assert sap_order["TotalNetAmount"] == "10870.00"


@pytest.mark.parametrize(
    ("customer", "lines", "code"),
    [
        ("CL-999", [line()], "CUSTOMER_NOT_FOUND"),
        ("CL-002", [line(sku="XX-999")], "PRODUCT_NOT_FOUND"),
        ("CL-002", [line(sku="HG-005", quantity=1)], "PRODUCT_INACTIVE"),  # discontinued, even with stock
        ("CL-002", [line(quantity=0)], "INVALID_QUANTITY"),
        ("CL-002", [line(quantity=-5)], "INVALID_QUANTITY"),
        ("CL-002", [line(SalesUnitSymbol="caja")], "UNIT_MISMATCH"),
        ("CL-002", [line(SalesPrice="1.00")], "PRICE_MISMATCH"),
        ("CL-003", [line(LineDiscountPercentage=10)], "DISCOUNT_EXCEEDED"),  # Bronce max 3 %
        ("CL-002", [line(LineDiscountPercentage=-1)], "DISCOUNT_EXCEEDED"),
        ("CL-002", [line(sku="HG-001", quantity=40)], "INSUFFICIENT_STOCK"),  # 25 in UIO
        ("CL-002", [line(warehouse="CUE")], "WAREHOUSE_NOT_FOUND"),
        ("CL-004", [line(sku="TE-001", quantity=1)], "CREDIT_EXCEEDED"),  # 400 available
        ("CL-002", [line(sku="HG-001", quantity=20), line(sku="HG-001", quantity=20)], "INSUFFICIENT_STOCK"),
    ],
)
def test_invalid_orders_are_rejected_and_change_nothing(erp_client, erp_db, customer, lines, code):
    stock_before = erp_db("SELECT * FROM stock ORDER BY sku, warehouse")
    credit_before = erp_db("SELECT code, credit_used_cents FROM customers ORDER BY code")

    response = erp_client("write").post(ORDERS, json=order(customer=customer, lines=lines))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert erp_db("SELECT * FROM sales_orders") == []
    assert erp_db("SELECT * FROM stock ORDER BY sku, warehouse") == stock_before
    assert erp_db("SELECT code, credit_used_cents FROM customers ORDER BY code") == credit_before
    assert erp_db("SELECT outcome FROM write_log") == [{"outcome": "rejected"}]


def test_sap_rejects_fractional_quantities(erp_client, erp_db):
    body = {
        "SoldToParty": "CL-002",
        "PurchaseOrderByCustomer": "PROP-000009",
        "to_Item": [{"Material": "CM-003", "RequestedQuantity": "2.5", "RequestedQuantityUnit": "BL", "ProductionPlant": "UIO"}],
    }
    response = erp_client("write").post(SAP_ORDERS, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_QUANTITY"
    assert erp_db("SELECT * FROM sales_orders") == []


def test_sap_order_uses_sap_numbering_and_units(erp_client):
    body = {
        "SoldToParty": "CL-002",
        "PurchaseOrderByCustomer": "PROP-000010",
        "to_Item": [{"Material": "CM-003", "RequestedQuantity": "10", "RequestedQuantityUnit": "BL", "ProductionPlant": "UIO"}],
    }
    response = erp_client("write").post(SAP_ORDERS, json=body)
    assert response.status_code == 201
    header = response.json()["d"]
    assert header["SalesOrder"] == "0000000001"
    [item] = header["to_Item"]["results"]
    assert (item["SalesOrderItem"], item["RequestedQuantityUnit"], item["NetAmount"]) == ("10", "BL", "160.00")
