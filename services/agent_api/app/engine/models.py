"""Domain models of the deterministic engine. The LLM fills OrderDraft; everything else is computed."""

import hashlib
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

ApprovalLevel = Literal["supervisor", "gerente"]


class RequestedLine(BaseModel):
    sku: str
    quantity: int
    warehouse: str


class OrderDraft(BaseModel):
    """What the customer asked for, as extracted from the chat or the PDF. No prices, no totals."""

    customer_code: str
    lines: list[RequestedLine]
    # Only set when the customer explicitly asked for a discount; it is checked, never applied as is
    requested_discount_pct: Decimal | None = None
    # Only set when the customer asked for a payment term; validated against the tier (not sent to the ERP)
    requested_payment_days: int | None = None


class RuleResult(BaseModel):
    rule: str
    ok: bool
    reason: str  # shown in the UI, in Spanish
    citation: str | None = None


class PricedLine(BaseModel):
    sku: str
    name: str
    warehouse: str
    quantity: int
    unit: str
    unit_price: Decimal
    discount_pct: Decimal
    amount: Decimal


class ProposalContent(BaseModel):
    """Everything an approver signs off on. Its hash binds the approval to this exact content."""

    customer_code: str
    currency: str = "USD"
    lines: list[PricedLine] = Field(min_length=1)
    total: Decimal
    approval_level: ApprovalLevel
    payment_days: int | None = None  # part of what is approved, so it is part of the hash

    @property
    def hash(self) -> str:
        # Money and percentages are quantized by the engine, so equal content always serializes equally
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()
