"""Order creation shared by the Dynamics-style and SAP-style APIs.

The ERP re-checks every business rule on write (defense in depth): even if every
upstream control failed, an invalid order cannot be stored here.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from .db import connect, record_movement


class OrderRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class IdempotencyConflict(Exception):
    """Same external reference sent again with different content."""


@dataclass
class LineIn:
    sku: str
    warehouse: str
    quantity: int
    unit: str | None = None
    unit_price_cents: int | None = None  # optional: when sent, it must match the ERP list price
    discount_pct: float = 0.0


@dataclass
class OrderIn:
    external_ref: str
    customer_code: str
    currency: str
    requested_date: str | None
    lines: list[LineIn]


def content_hash(order: OrderIn) -> str:
    # Hash of the normalized order, so the same proposal hashes equally from either API style
    canonical = json.dumps(asdict(order), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def line_amount_cents(quantity: int, unit_price_cents: int, discount_pct: float) -> int:
    gross = Decimal(quantity) * Decimal(unit_price_cents)
    net = gross * (Decimal(100) - Decimal(str(discount_pct))) / Decimal(100)
    return int(net.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_write(
    credential: str,
    outcome: str,
    *,
    api_style: str | None = None,
    external_ref: str | None = None,
    content_hash: str | None = None,
    detail: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO write_log (at, credential, api_style, external_ref, content_hash, outcome, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now(), credential, api_style, external_ref, content_hash, outcome, detail),
        )


def create_order(order: OrderIn, credential: str, api_style: str) -> tuple[dict, bool]:
    """Returns (order, created). created=False means the order already existed (idempotent replay)."""
    digest = content_hash(order)
    log = dict(api_style=api_style, external_ref=order.external_ref, content_hash=digest)
    try:
        with connect() as conn:
            # IMMEDIATE takes the write lock up front: stock checks and updates cannot interleave
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = _create_in_transaction(conn, order, digest, api_style)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    except OrderRejected as e:
        log_write(credential, "rejected", detail=f"{e.code}: {e.message}", **log)
        raise
    except IdempotencyConflict as e:
        log_write(credential, "conflict", detail=str(e), **log)
        raise

    stored, created = result
    log_write(credential, "created" if created else "duplicate", detail=f"order {stored['id']}", **log)
    return result


def _create_in_transaction(conn, order: OrderIn, digest: str, api_style: str) -> tuple[dict, bool]:
    existing = conn.execute(
        "SELECT id, content_hash FROM sales_orders WHERE external_ref = ?", (order.external_ref,)
    ).fetchone()
    if existing:
        if existing["content_hash"] != digest:
            raise IdempotencyConflict(f"Reference {order.external_ref} already used with different content")
        return get_order(conn, existing["id"]), False

    if not order.lines:
        raise OrderRejected("EMPTY_ORDER", "The order has no lines")

    customer = conn.execute("SELECT * FROM customers WHERE code = ?", (order.customer_code,)).fetchone()
    if customer is None:
        raise OrderRejected("CUSTOMER_NOT_FOUND", f"Customer {order.customer_code} does not exist")

    priced_lines = []
    needed: dict[tuple[str, str], int] = {}  # aggregated per sku+warehouse, so repeated lines cannot oversell
    for line in order.lines:
        product = conn.execute("SELECT * FROM products WHERE sku = ?", (line.sku,)).fetchone()
        if product is None:
            raise OrderRejected("PRODUCT_NOT_FOUND", f"Product {line.sku} does not exist")
        if product["status"] != "active":
            raise OrderRejected("PRODUCT_INACTIVE", f"Product {line.sku} is {product['status']}")
        if line.quantity <= 0:
            raise OrderRejected("INVALID_QUANTITY", f"Invalid quantity {line.quantity} for {line.sku}")
        if line.unit is not None and line.unit != product["unit"]:
            raise OrderRejected("UNIT_MISMATCH", f"{line.sku} is sold by {product['unit']}, not {line.unit}")
        if line.unit_price_cents is not None and line.unit_price_cents != product["list_price_cents"]:
            raise OrderRejected("PRICE_MISMATCH", f"Price for {line.sku} does not match the list price")
        if not 0 <= line.discount_pct <= customer["max_discount_pct"]:
            raise OrderRejected(
                "DISCOUNT_EXCEEDED",
                f"Discount {line.discount_pct}% exceeds the {customer['max_discount_pct']}% maximum",
            )

        stock = conn.execute(
            "SELECT quantity FROM stock WHERE sku = ? AND warehouse = ?", (line.sku, line.warehouse)
        ).fetchone()
        if stock is None:
            raise OrderRejected("WAREHOUSE_NOT_FOUND", f"Warehouse {line.warehouse} does not exist")
        key = (line.sku, line.warehouse)
        needed[key] = needed.get(key, 0) + line.quantity
        if needed[key] > stock["quantity"]:
            raise OrderRejected(
                "INSUFFICIENT_STOCK",
                f"{line.sku} in {line.warehouse}: requested {needed[key]}, available {stock['quantity']}",
            )

        price = product["list_price_cents"]
        priced_lines.append((line, product["unit"], price, line_amount_cents(line.quantity, price, line.discount_pct)))

    total = sum(amount for *_, amount in priced_lines)
    available = customer["credit_limit_cents"] - customer["credit_used_cents"]
    if total > available:
        raise OrderRejected(
            "CREDIT_EXCEEDED", f"Order total {total / 100:.2f} exceeds available credit {available / 100:.2f}"
        )

    cursor = conn.execute(
        "INSERT INTO sales_orders (external_ref, customer_code, currency, requested_date, total_net_cents,"
        " content_hash, api_style, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (order.external_ref, order.customer_code, order.currency, order.requested_date, total, digest, api_style, _now()),
    )
    order_id = cursor.lastrowid
    for number, (line, unit, price, amount) in enumerate(priced_lines, start=1):
        conn.execute(
            "INSERT INTO sales_order_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (order_id, number, line.sku, line.warehouse, line.quantity, unit, price, line.discount_pct, amount),
        )
        conn.execute(
            "UPDATE stock SET quantity = quantity - ? WHERE sku = ? AND warehouse = ?",
            (line.quantity, line.sku, line.warehouse),
        )
        record_movement(conn, line.sku, line.warehouse, "sale", -line.quantity, order_id=order_id, external_ref=order.external_ref)
    conn.execute(
        "UPDATE customers SET credit_used_cents = credit_used_cents + ? WHERE code = ?", (total, order.customer_code)
    )
    return get_order(conn, order_id), True


def get_order(conn, order_id: int) -> dict:
    header = dict(conn.execute("SELECT * FROM sales_orders WHERE id = ?", (order_id,)).fetchone())
    header["lines"] = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM sales_order_lines WHERE order_id = ? ORDER BY line_number", (order_id,)
        )
    ]
    return header


def list_orders(conn) -> list[dict]:
    return [get_order(conn, row["id"]) for row in conn.execute("SELECT id FROM sales_orders ORDER BY id")]
