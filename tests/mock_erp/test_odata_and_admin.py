"""OData filters cannot be manipulated, and demo admin actions are traceable."""

PRODUCTS = "/data/ReleasedProductsV2"


def test_filter_returns_matching_row(erp_client):
    rows = erp_client("read").get(PRODUCTS, params={"$filter": "ItemNumber eq 'CM-001'"}).json()["value"]
    assert [row["ItemNumber"] for row in rows] == ["CM-001"]


def test_escaped_quote_is_a_literal_not_an_injection(erp_client):
    # What an adapter sends for the input: CM-001' or 'a' eq 'a
    expression = "ItemNumber eq 'CM-001'' or ''a'' eq ''a'"
    response = erp_client("read").get(PRODUCTS, params={"$filter": expression})
    assert response.status_code == 200
    assert response.json()["value"] == []


def test_unsupported_filter_is_refused(erp_client):
    response = erp_client("read").get(PRODUCTS, params={"$filter": "ItemNumber ne 'CM-001'"})
    assert response.status_code == 400


def test_stock_change_is_applied_and_logged(erp_client, erp_db):
    response = erp_client("admin").patch("/admin/stock", json={"sku": "CM-001", "warehouse": "GYE", "quantity": 10})
    assert response.status_code == 200
    assert erp_db("SELECT quantity FROM stock WHERE sku = 'CM-001' AND warehouse = 'GYE'") == [{"quantity": 10}]
    assert erp_db("SELECT credential, outcome FROM write_log") == [{"credential": "admin", "outcome": "stock_changed"}]


def test_stock_cannot_be_set_negative(erp_client):
    response = erp_client("admin").patch("/admin/stock", json={"sku": "CM-001", "warehouse": "GYE", "quantity": -1})
    assert response.status_code == 422


def test_reset_restores_seed_data(erp_client, erp_db):
    admin = erp_client("admin")
    admin.patch("/admin/stock", json={"sku": "CM-001", "warehouse": "GYE", "quantity": 0})
    admin.post("/admin/reset")
    assert erp_db("SELECT quantity FROM stock WHERE sku = 'CM-001' AND warehouse = 'GYE'") == [{"quantity": 1200}]
