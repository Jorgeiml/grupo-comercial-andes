"""Minimal OData helpers shared by both API styles."""

import re
from decimal import Decimal

from fastapi import HTTPException

# A literal may contain doubled single quotes ('') — the OData escape for a quote
_TERM = re.compile(r"\s*(\w+) eq '((?:[^']|'')*)'\s*(and\s|$)")


def apply_filter(rows: list[dict], expression: str | None) -> list[dict]:
    """Supports only `Field eq 'value'` terms joined by `and`: enough for key lookups."""
    if not expression:
        return rows
    conditions, position = [], 0
    while True:
        match = _TERM.match(expression, position)
        if not match:
            raise HTTPException(status_code=400, detail="Only `Field eq 'value'` filters joined by `and` are supported")
        conditions.append((match[1], match[2].replace("''", "'")))
        position = match.end()
        if match[3] == "":
            break
    return [row for row in rows if all(str(row.get(field)) == value for field, value in conditions)]


def cents_from_decimal(amount: Decimal) -> int:
    return round(amount * 100)


def money(cents: int) -> Decimal:
    return Decimal(cents) / 100


def money_str(cents: int) -> str:
    # OData V2 (SAP) serializes Edm.Decimal values as strings
    return f"{money(cents):.2f}"
