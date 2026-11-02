"""SQLite storage for the mock ERP. Money is stored as integer cents to avoid rounding errors."""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = os.getenv("ERP_DB_PATH", "/data/erp.db")
SEED_DIR = Path(os.getenv("ERP_SEED_DIR", "/seed"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS warehouses (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit TEXT NOT NULL,
    list_price_cents INTEGER NOT NULL CHECK (list_price_cents > 0),
    status TEXT NOT NULL CHECK (status IN ('active', 'discontinued')),
    substitute_sku TEXT REFERENCES products (sku)
);

CREATE TABLE IF NOT EXISTS stock (
    sku TEXT NOT NULL REFERENCES products (sku),
    warehouse TEXT NOT NULL REFERENCES warehouses (code),
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    PRIMARY KEY (sku, warehouse)
);

CREATE TABLE IF NOT EXISTS customers (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    tier TEXT NOT NULL,
    max_discount_pct REAL NOT NULL,
    credit_limit_cents INTEGER NOT NULL,
    credit_used_cents INTEGER NOT NULL
);

-- external_ref is the proposal ID: the UNIQUE constraint is the idempotency backstop
CREATE TABLE IF NOT EXISTS sales_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_ref TEXT NOT NULL UNIQUE,
    customer_code TEXT NOT NULL REFERENCES customers (code),
    currency TEXT NOT NULL,
    requested_date TEXT,
    total_net_cents INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    api_style TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales_order_lines (
    order_id INTEGER NOT NULL REFERENCES sales_orders (id),
    line_number INTEGER NOT NULL,
    sku TEXT NOT NULL REFERENCES products (sku),
    warehouse TEXT NOT NULL REFERENCES warehouses (code),
    quantity INTEGER NOT NULL,
    unit TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    discount_pct REAL NOT NULL,
    line_amount_cents INTEGER NOT NULL,
    PRIMARY KEY (order_id, line_number)
);

-- Technical log of every write attempt (business audit lives in agent-api)
CREATE TABLE IF NOT EXISTS write_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    credential TEXT NOT NULL,
    api_style TEXT,
    external_ref TEXT,
    content_hash TEXT,
    outcome TEXT NOT NULL,
    detail TEXT
);
"""

TABLES = ["write_log", "sales_order_lines", "sales_orders", "stock", "customers", "products", "warehouses"]


def to_cents(amount: float) -> int:
    return round(amount * 100)


@contextmanager
def connect():
    # isolation_level=None: we open transactions explicitly (BEGIN IMMEDIATE) where needed
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db(reset: bool = False) -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        if reset:
            for table in TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            _load_seed(conn)


def _load_seed(conn: sqlite3.Connection) -> None:
    def load(name: str) -> list[dict]:
        return json.loads((SEED_DIR / name).read_text(encoding="utf-8"))

    conn.execute("BEGIN")
    for w in load("warehouses.json"):
        conn.execute("INSERT INTO warehouses VALUES (?, ?, ?)", (w["code"], w["name"], w["city"]))

    products = load("products.json")
    # Substitutes reference other products, so insert all products before linking them
    for p in products:
        conn.execute(
            "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (p["sku"], p["name"], p["category"], p["unit"], to_cents(p["list_price"]), p["status"]),
        )
    for p in products:
        if p["substitute_sku"]:
            conn.execute("UPDATE products SET substitute_sku = ? WHERE sku = ?", (p["substitute_sku"], p["sku"]))
        for warehouse, quantity in p["stock"].items():
            conn.execute("INSERT INTO stock VALUES (?, ?, ?)", (p["sku"], warehouse, quantity))

    for c in load("customers.json"):
        conn.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                c["code"], c["name"], c["type"], c["tier"], c["max_discount_pct"],
                to_cents(c["credit_limit"]), to_cents(c["credit_used"]),
            ),
        )
    conn.execute("COMMIT")
