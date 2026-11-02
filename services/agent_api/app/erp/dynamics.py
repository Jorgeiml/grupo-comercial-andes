"""Dynamics 365 Finance & Operations adapter (OData v4 entities under /data)."""

from decimal import Decimal

from .http_base import HttpErp, odata_quote, to_decimal
from .port import Credit, Customer, OrderRequest, OrderResult, OrderStatus, Product, StockLevel


class DynamicsErp(HttpErp):
    name = "dynamics"

    def _first(self, entity: str, filter_: str) -> dict | None:
        rows = self._get(f"/data/{entity}", filter_)["value"]
        return rows[0] if rows else None

    def list_products(self) -> list[Product]:
        return [self._product(row) for row in self._get("/data/ReleasedProductsV2")["value"]]

    def list_customers(self) -> list[Customer]:
        return [self._customer(row) for row in self._get("/data/CustomersV3")["value"]]

    def get_product(self, sku: str) -> Product | None:
        row = self._first("ReleasedProductsV2", f"ItemNumber eq {odata_quote(sku)}")
        return self._product(row) if row else None

    @staticmethod
    def _product(row: dict) -> Product:
        return Product(
            sku=row["ItemNumber"],
            name=row["ProductName"],
            unit=row["SalesUnitSymbol"],
            list_price=to_decimal(row["SalesPrice"]),
            active=row["ProductLifecycleStateId"] == "Active",
            substitute_sku=row["SubstituteItemNumber"],
        )

    def get_stock(self, sku: str, warehouse: str) -> StockLevel | None:
        row = self._first(
            "InventWarehouseOnHandV2",
            f"ItemNumber eq {odata_quote(sku)} and InventoryWarehouseId eq {odata_quote(warehouse)}",
        )
        if row is None:
            return None
        return StockLevel(sku=row["ItemNumber"], warehouse=row["InventoryWarehouseId"], available=int(row["AvailableOnHandQuantity"]))

    def get_price(self, sku: str) -> Decimal | None:
        product = self.get_product(sku)
        return product.list_price if product else None

    def get_customer(self, code: str) -> Customer | None:
        row = self._first("CustomersV3", f"CustomerAccount eq {odata_quote(code)}")
        return self._customer(row) if row else None

    @staticmethod
    def _customer(row: dict) -> Customer:
        return Customer(
            code=row["CustomerAccount"],
            name=row["OrganizationName"],
            tier=row["CustomerGroupId"],
            max_discount_pct=to_decimal(row["MaxDiscountPct"]),
        )

    def get_credit(self, code: str) -> Credit | None:
        row = self._first("CustomersV3", f"CustomerAccount eq {odata_quote(code)}")
        if row is None:
            return None
        return Credit(customer_code=row["CustomerAccount"], limit=to_decimal(row["CreditLimit"]), used=to_decimal(row["CreditUsed"]))

    def create_order(self, order: OrderRequest) -> OrderResult:
        body = {
            "CustomersOrderReference": order.reference,
            "OrderingCustomerAccountNumber": order.customer_code,
            "CurrencyCode": order.currency,
            "RequestedShippingDate": order.requested_date.isoformat() if order.requested_date else None,
            "SalesOrderLines": [
                {
                    "ItemNumber": line.sku,
                    "OrderedSalesQuantity": line.quantity,
                    "ShippingWarehouseId": line.warehouse,
                    "SalesUnitSymbol": line.unit,
                    # Sent so the ERP can refuse a price that differs from its own list
                    "SalesPrice": str(line.unit_price),
                    "LineDiscountPercentage": float(line.discount_pct),
                }
                for line in order.lines
            ],
        }
        status, payload = self._post_order("/data/SalesOrderHeadersV2", body)
        if status not in (OrderStatus.CREATED, OrderStatus.DUPLICATE):
            return self._error_result(status, payload)
        return OrderResult(
            status=status,
            order_number=payload["SalesOrderNumber"],
            total=sum((to_decimal(line["LineAmount"]) for line in payload["SalesOrderLines"]), Decimal(0)),
            erp_payload=payload,
        )
