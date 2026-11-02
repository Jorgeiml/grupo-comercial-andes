"""ERP port: the only ERP interface the rest of the system knows.

Adapters translate between these ERP-neutral models and each ERP's field names.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field


class Product(BaseModel):
    sku: str
    name: str
    unit: str
    list_price: Decimal
    active: bool
    substitute_sku: str | None = None


class StockLevel(BaseModel):
    sku: str
    warehouse: str
    available: int


class Customer(BaseModel):
    code: str
    name: str
    tier: str
    max_discount_pct: Decimal


class Credit(BaseModel):
    customer_code: str
    limit: Decimal
    used: Decimal

    @property
    def available(self) -> Decimal:
        return self.limit - self.used


class OrderLine(BaseModel):
    sku: str
    warehouse: str
    quantity: int
    unit: str
    unit_price: Decimal
    discount_pct: Decimal = Decimal(0)


class OrderRequest(BaseModel):
    # Proposal ID, used by the ERP as idempotency key; SAP limits it to 35 characters
    reference: str = Field(min_length=1, max_length=35)
    customer_code: str
    currency: str = "USD"
    requested_date: date | None = None
    lines: list[OrderLine] = Field(min_length=1)


class OrderStatus(StrEnum):
    CREATED = "created"
    DUPLICATE = "duplicate"  # same reference already processed: nothing new was written
    REJECTED = "rejected"  # the ERP's own business rules refused the order
    CONFLICT = "conflict"  # same reference with different content
    FORBIDDEN = "forbidden"  # the credential cannot write


class OrderResult(BaseModel):
    status: OrderStatus
    order_number: str | None = None
    total: Decimal | None = None
    error_code: str | None = None
    message: str | None = None
    erp_payload: dict | None = None  # raw ERP response, shown in the UI to prove which API was used


class ErpUnavailable(Exception):
    """The ERP could not be reached or answered unexpectedly."""


class ErpPort(Protocol):
    name: str

    def list_products(self) -> list[Product]: ...

    def list_customers(self) -> list[Customer]: ...

    def get_product(self, sku: str) -> Product | None: ...

    def get_stock(self, sku: str, warehouse: str) -> StockLevel | None: ...

    def get_price(self, sku: str) -> Decimal | None: ...

    def get_customer(self, code: str) -> Customer | None: ...

    def get_credit(self, code: str) -> Credit | None: ...

    def create_order(self, order: OrderRequest) -> OrderResult: ...
