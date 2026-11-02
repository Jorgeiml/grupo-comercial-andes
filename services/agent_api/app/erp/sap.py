"""SAP S/4HANA adapter (OData V2 services under /sap/opu/odata/sap)."""

from decimal import Decimal

from .http_base import HttpErp, odata_quote, to_decimal
from .port import Credit, Customer, OrderRequest, OrderResult, OrderStatus, Product, StockLevel

ROOT = "/sap/opu/odata/sap"

# Internal units of measure <-> SAP unit codes
UNIT_TO_SAP = {"unidad": "EA", "caja": "CS", "fardo": "BL"}
UNIT_FROM_SAP = {sap: unit for unit, sap in UNIT_TO_SAP.items()}


class SapErp(HttpErp):
    name = "sap"

    def _first(self, path: str, filter_: str) -> dict | None:
        rows = self._get(f"{ROOT}/{path}", filter_)["d"]["results"]
        return rows[0] if rows else None

    def get_product(self, sku: str) -> Product | None:
        row = self._first("API_PRODUCT_SRV/A_Product", f"Product eq {odata_quote(sku)}")
        if row is None:
            return None
        return Product(
            sku=row["Product"],
            name=row["ProductDescription"],
            unit=UNIT_FROM_SAP.get(row["BaseUnit"], row["BaseUnit"]),
            list_price=to_decimal(row["YY1_ListPrice"]),
            active=not row["IsMarkedForDeletion"],
            substitute_sku=row["YY1_SubstituteProduct"],
        )

    def get_stock(self, sku: str, warehouse: str) -> StockLevel | None:
        row = self._first(
            "API_MATERIAL_STOCK_SRV/A_MatlStkInAcctMod",
            f"Material eq {odata_quote(sku)} and Plant eq {odata_quote(warehouse)}",
        )
        if row is None:
            return None
        return StockLevel(sku=row["Material"], warehouse=row["Plant"], available=int(to_decimal(row["MatlWrhsStkQtyInMatlBaseUnit"])))

    def get_price(self, sku: str) -> Decimal | None:
        product = self.get_product(sku)
        return product.list_price if product else None

    def _credit_row(self, code: str) -> dict | None:
        return self._first("ZAPI_CUSTOMER_CREDIT_SRV/A_CustomerCredit", f"Customer eq {odata_quote(code)}")

    def get_customer(self, code: str) -> Customer | None:
        row = self._credit_row(code)
        if row is None:
            return None
        return Customer(
            code=row["Customer"],
            name=row["CustomerName"],
            tier=row["YY1_CustomerTier"],
            max_discount_pct=to_decimal(row["YY1_MaxDiscountPct"]),
        )

    def get_credit(self, code: str) -> Credit | None:
        row = self._credit_row(code)
        if row is None:
            return None
        return Credit(
            customer_code=row["Customer"],
            limit=to_decimal(row["CreditLimitAmount"]),
            used=to_decimal(row["CreditExposureAmount"]),
        )

    def create_order(self, order: OrderRequest) -> OrderResult:
        # SAP determines the price itself through pricing conditions, so no price is sent
        body = {
            "SalesOrderType": "OR",
            "SalesOrganization": "1000",
            "DistributionChannel": "10",
            "OrganizationDivision": "00",
            "SoldToParty": order.customer_code,
            "PurchaseOrderByCustomer": order.reference,
            "TransactionCurrency": order.currency,
            "RequestedDeliveryDate": order.requested_date.isoformat() if order.requested_date else None,
            "to_Item": [
                {
                    "Material": line.sku,
                    "RequestedQuantity": str(line.quantity),
                    "RequestedQuantityUnit": UNIT_TO_SAP.get(line.unit, line.unit),
                    "ProductionPlant": line.warehouse,
                    "YY1_DiscountPct": str(line.discount_pct),
                }
                for line in order.lines
            ],
        }
        status, payload = self._post_order(f"{ROOT}/API_SALES_ORDER_SRV/A_SalesOrder", body)
        if status not in (OrderStatus.CREATED, OrderStatus.DUPLICATE):
            return self._error_result(status, payload)
        header = payload["d"]
        return OrderResult(
            status=status,
            order_number=header["SalesOrder"],
            total=to_decimal(header["TotalNetAmount"]),
            erp_payload=payload,
        )
