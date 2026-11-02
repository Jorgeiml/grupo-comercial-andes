"""The ERP enforces who can write: the agent's read credential can never create orders."""

import pytest

ORDERS = "/data/SalesOrderHeadersV2"
ORDER = {
    "CustomersOrderReference": "PROP-AUTH-1",
    "OrderingCustomerAccountNumber": "CL-002",
    "SalesOrderLines": [{"ItemNumber": "CM-003", "OrderedSalesQuantity": 1, "ShippingWarehouseId": "UIO"}],
}


def test_missing_token_is_rejected(erp_client):
    assert erp_client().get("/data/ReleasedProductsV2").status_code == 401


def test_invalid_token_is_rejected(erp_client):
    client = erp_client()
    client.headers["Authorization"] = "Bearer not-a-real-token"
    assert client.get("/data/ReleasedProductsV2").status_code == 401


@pytest.mark.parametrize("credential", ["read", "admin"])
def test_only_write_credential_can_create_orders(erp_client, erp_db, credential):
    assert erp_client(credential).post(ORDERS, json=ORDER).status_code == 403
    assert erp_db("SELECT * FROM sales_orders") == []


def test_forbidden_write_attempt_is_logged(erp_client, erp_db):
    erp_client("read").post(ORDERS, json=ORDER)
    [entry] = erp_db("SELECT credential, outcome FROM write_log")
    assert entry == {"credential": "read", "outcome": "forbidden"}


@pytest.mark.parametrize("credential", ["read", "write"])
def test_admin_endpoints_require_admin(erp_client, credential):
    client = erp_client(credential)
    assert client.post("/admin/reset").status_code == 403
    assert client.patch("/admin/stock", json={"sku": "CM-001", "warehouse": "UIO", "quantity": 0}).status_code == 403
