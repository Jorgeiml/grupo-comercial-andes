"""SAP S/4HANA style API (OData V2 under /sap/opu/odata/sap).

Custom fields use the YY1_ prefix and custom services the Z prefix, following SAP extensibility conventions.
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .auth import require
from .db import connect
from .odata import apply_filter, money_str
from .orders import LineIn, OrderIn, OrderRejected, create_order, list_orders

router = APIRouter(prefix="/sap/opu/odata/sap", tags=["SAP S/4HANA"])

# ERP units of measure <-> SAP ISO-style unit codes
UNIT_TO_SAP = {"unidad": "EA", "caja": "CS", "fardo": "BL"}
UNIT_FROM_SAP = {sap: unit for unit, sap in UNIT_TO_SAP.items()}


def _v2(results: list[dict]) -> dict:
    return {"d": {"results": results}}


@router.get("/API_PRODUCT_SRV/A_Product")
def products(filter_: str | None = Query(None, alias="$filter"), _: str = Depends(require("read"))):
    with connect() as conn:
        rows = [
            {
                "Product": p["sku"],
                "ProductType": "HAWA",  # trading goods
                "BaseUnit": UNIT_TO_SAP[p["unit"]],
                "ProductDescription": p["name"],
                "IsMarkedForDeletion": p["status"] != "active",
                "YY1_ListPrice": money_str(p["list_price_cents"]),
                "YY1_Currency": "USD",
                "YY1_SubstituteProduct": p["substitute_sku"],
            }
            for p in conn.execute("SELECT * FROM products ORDER BY sku")
        ]
    return _v2(apply_filter(rows, filter_))


@router.get("/API_MATERIAL_STOCK_SRV/A_MatlStkInAcctMod")
def material_stock(filter_: str | None = Query(None, alias="$filter"), _: str = Depends(require("read"))):
    with connect() as conn:
        rows = [
            {
                "Material": s["sku"],
                "Plant": s["warehouse"],
                "StorageLocation": "0001",
                "InventoryStockType": "01",  # unrestricted-use stock
                "MaterialBaseUnit": UNIT_TO_SAP[s["unit"]],
                "MatlWrhsStkQtyInMatlBaseUnit": f"{s['quantity']}.000",
            }
            for s in conn.execute(
                "SELECT stock.*, products.unit FROM stock JOIN products USING (sku) ORDER BY sku, warehouse"
            )
        ]
    return _v2(apply_filter(rows, filter_))


@router.get("/ZAPI_CUSTOMER_CREDIT_SRV/A_CustomerCredit")
def customer_credit(filter_: str | None = Query(None, alias="$filter"), _: str = Depends(require("read"))):
    # In S/4HANA this lives in Credit Management; simplified as a custom service in the mock
    with connect() as conn:
        rows = [
            {
                "Customer": c["code"],
                "CustomerName": c["name"],
                "YY1_CustomerTier": c["tier"],
                "YY1_MaxDiscountPct": str(c["max_discount_pct"]),
                "CreditLimitAmount": money_str(c["credit_limit_cents"]),
                "CreditExposureAmount": money_str(c["credit_used_cents"]),
                "Currency": "USD",
            }
            for c in conn.execute("SELECT * FROM customers ORDER BY code")
        ]
    return _v2(apply_filter(rows, filter_))


class SalesOrderItemIn(BaseModel):
    Material: str
    RequestedQuantity: Decimal
    ProductionPlant: str
    RequestedQuantityUnit: str | None = None
    YY1_DiscountPct: Decimal = Decimal(0)


class SalesOrderIn(BaseModel):
    SalesOrderType: str = "OR"
    SalesOrganization: str = "1000"
    DistributionChannel: str = "10"
    OrganizationDivision: str = "00"
    SoldToParty: str
    PurchaseOrderByCustomer: str = Field(min_length=1, max_length=35)
    TransactionCurrency: str = "USD"
    # Simplification: ISO date instead of the OData V2 /Date(ms)/ format
    RequestedDeliveryDate: str | None = None
    to_Item: list[SalesOrderItemIn]


def _quantity(item: SalesOrderItemIn) -> int:
    if item.RequestedQuantity != item.RequestedQuantity.to_integral_value():
        raise OrderRejected("INVALID_QUANTITY", f"Invalid quantity {item.RequestedQuantity} for {item.Material}")
    return int(item.RequestedQuantity)


def _to_sap(order: dict) -> dict:
    number = f"{order['id']:010d}"
    return {
        "SalesOrder": number,
        "SalesOrderType": "OR",
        "SalesOrganization": "1000",
        "DistributionChannel": "10",
        "OrganizationDivision": "00",
        "SoldToParty": order["customer_code"],
        "PurchaseOrderByCustomer": order["external_ref"],
        "TransactionCurrency": order["currency"],
        "RequestedDeliveryDate": order["requested_date"],
        "TotalNetAmount": money_str(order["total_net_cents"]),
        "to_Item": {
            "results": [
                {
                    "SalesOrder": number,
                    "SalesOrderItem": str(line["line_number"] * 10),  # SAP numbers items 10, 20, 30...
                    "Material": line["sku"],
                    "RequestedQuantity": str(line["quantity"]),
                    "RequestedQuantityUnit": UNIT_TO_SAP[line["unit"]],
                    "ProductionPlant": line["warehouse"],
                    "NetAmount": money_str(line["line_amount_cents"]),
                    "TransactionCurrency": order["currency"],
                    "YY1_DiscountPct": str(line["discount_pct"]),
                }
                for line in order["lines"]
            ]
        },
    }


@router.get("/API_SALES_ORDER_SRV/A_SalesOrder")
def sales_orders(filter_: str | None = Query(None, alias="$filter"), _: str = Depends(require("read"))):
    with connect() as conn:
        rows = [_to_sap(o) for o in list_orders(conn)]
    return _v2(apply_filter(rows, filter_))


@router.post("/API_SALES_ORDER_SRV/A_SalesOrder")
def create_sales_order(body: SalesOrderIn, credential: str = Depends(require("write"))):
    # Deep insert (header + to_Item in one request) is supported natively by this SAP API
    order = OrderIn(
        external_ref=body.PurchaseOrderByCustomer,
        customer_code=body.SoldToParty,
        currency=body.TransactionCurrency,
        requested_date=body.RequestedDeliveryDate,
        lines=[
            LineIn(
                sku=item.Material,
                warehouse=item.ProductionPlant,
                quantity=_quantity(item),
                # Unknown SAP units pass through unchanged so the ERP rejects them as UNIT_MISMATCH
                unit=UNIT_FROM_SAP.get(item.RequestedQuantityUnit, item.RequestedQuantityUnit),
                discount_pct=float(item.YY1_DiscountPct),
            )
            for item in body.to_Item
        ],
    )
    stored, created = create_order(order, credential, api_style="sap")
    return JSONResponse(status_code=201 if created else 200, content=jsonable_encoder({"d": _to_sap(stored)}))
