"""Dynamics 365 Finance & Operations style API (OData v4 under /data)."""

from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .auth import require
from .db import connect
from .odata import apply_filter, cents_from_decimal, money
from .orders import LineIn, OrderIn, create_order, list_orders

router = APIRouter(prefix="/data", tags=["Dynamics 365 F&O"])

DATA_AREA = "gca"  # legal entity (company) of Grupo Comercial Andes


@router.get("/ReleasedProductsV2")
def released_products(filter_: str | None = Query(None, alias="$filter"), _: str = Depends(require("read"))):
    with connect() as conn:
        rows = [
            {
                "dataAreaId": DATA_AREA,
                "ItemNumber": p["sku"],
                "ProductName": p["name"],
                "SalesPrice": money(p["list_price_cents"]),
                "SalesUnitSymbol": p["unit"],
                "ProductLifecycleStateId": "Active" if p["status"] == "active" else "Discontinued",
                "SubstituteItemNumber": p["substitute_sku"],
            }
            for p in conn.execute("SELECT * FROM products ORDER BY sku")
        ]
    return {"value": apply_filter(rows, filter_)}


@router.get("/InventWarehouseOnHandV2")
def warehouse_on_hand(filter_: str | None = Query(None, alias="$filter"), _: str = Depends(require("read"))):
    with connect() as conn:
        rows = [
            {
                "dataAreaId": DATA_AREA,
                "ItemNumber": s["sku"],
                "InventorySiteId": s["warehouse"],
                "InventoryWarehouseId": s["warehouse"],
                "OnHandQuantity": s["quantity"],
                "AvailableOnHandQuantity": s["quantity"],
            }
            for s in conn.execute("SELECT * FROM stock ORDER BY sku, warehouse")
        ]
    return {"value": apply_filter(rows, filter_)}


@router.get("/CustomersV3")
def customers(filter_: str | None = Query(None, alias="$filter"), _: str = Depends(require("read"))):
    with connect() as conn:
        rows = [
            {
                "dataAreaId": DATA_AREA,
                "CustomerAccount": c["code"],
                "OrganizationName": c["name"],
                "CustomerGroupId": c["tier"],
                "CreditLimit": money(c["credit_limit_cents"]),
                # Not standard CustomersV3 fields: simplified in the mock (see docs/erp-mapping.md)
                "CreditUsed": money(c["credit_used_cents"]),
                "MaxDiscountPct": c["max_discount_pct"],
            }
            for c in conn.execute("SELECT * FROM customers ORDER BY code")
        ]
    return {"value": apply_filter(rows, filter_)}


class SalesOrderLineIn(BaseModel):
    ItemNumber: str
    OrderedSalesQuantity: int
    ShippingWarehouseId: str
    SalesUnitSymbol: str | None = None
    SalesPrice: Decimal | None = None
    LineDiscountPercentage: float = 0.0


class SalesOrderHeaderIn(BaseModel):
    CustomersOrderReference: str = Field(min_length=1)
    OrderingCustomerAccountNumber: str
    InvoiceCustomerAccountNumber: str | None = None
    CurrencyCode: str = "USD"
    RequestedShippingDate: str | None = None
    # Real Dynamics creates lines in a separate call or $batch; here header + lines go in one atomic POST
    SalesOrderLines: list[SalesOrderLineIn]


def _to_dynamics(order: dict) -> dict:
    number = f"SO-{order['id']:06d}"
    return {
        "dataAreaId": DATA_AREA,
        "SalesOrderNumber": number,
        "CustomersOrderReference": order["external_ref"],
        "OrderingCustomerAccountNumber": order["customer_code"],
        "InvoiceCustomerAccountNumber": order["customer_code"],
        "CurrencyCode": order["currency"],
        "RequestedShippingDate": order["requested_date"],
        "SalesOrderLines": [
            {
                "SalesOrderNumber": number,
                "LineNumber": line["line_number"],
                "ItemNumber": line["sku"],
                "OrderedSalesQuantity": line["quantity"],
                "SalesUnitSymbol": line["unit"],
                "SalesPrice": money(line["unit_price_cents"]),
                "LineDiscountPercentage": line["discount_pct"],
                "LineAmount": money(line["line_amount_cents"]),
                "ShippingWarehouseId": line["warehouse"],
            }
            for line in order["lines"]
        ],
    }


@router.get("/SalesOrderHeadersV2")
def sales_orders(filter_: str | None = Query(None, alias="$filter"), _: str = Depends(require("read"))):
    with connect() as conn:
        rows = [_to_dynamics(o) for o in list_orders(conn)]
    return {"value": apply_filter(rows, filter_)}


@router.post("/SalesOrderHeadersV2")
def create_sales_order(body: SalesOrderHeaderIn, credential: str = Depends(require("write"))):
    order = OrderIn(
        external_ref=body.CustomersOrderReference,
        customer_code=body.OrderingCustomerAccountNumber,
        currency=body.CurrencyCode,
        requested_date=body.RequestedShippingDate,
        lines=[
            LineIn(
                sku=line.ItemNumber,
                warehouse=line.ShippingWarehouseId,
                quantity=line.OrderedSalesQuantity,
                unit=line.SalesUnitSymbol,
                unit_price_cents=cents_from_decimal(line.SalesPrice) if line.SalesPrice is not None else None,
                discount_pct=line.LineDiscountPercentage,
            )
            for line in body.SalesOrderLines
        ],
    )
    stored, created = create_order(order, credential, api_style="dynamics")
    # 201 for a new order, 200 when the same reference was already processed
    return JSONResponse(status_code=201 if created else 200, content=jsonable_encoder(_to_dynamics(stored)))
