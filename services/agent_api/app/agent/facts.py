"""Verified facts handed to the LLM as data. Built only from ERP data and the deterministic engine."""

import json

from ..engine.alternatives import Alternative
from ..engine.validation import Evaluation, pct, usd
from ..erp.port import Customer, Product


PLURAL = {"unidad": "unidades", "caja": "cajas", "fardo": "fardos"}


def quantity_text(quantity: int, unit: str) -> str:
    return f"{quantity} {unit if quantity == 1 else PLURAL.get(unit, unit)}"


def catalog_text(products: list[Product], customers: list[Customer]) -> str:
    product_rows = "\n".join(
        f"{p.sku} | {p.name} | {p.unit} | {'activo' if p.active else 'descontinuado'}" for p in products
    )
    customer_rows = "\n".join(f"{c.code} | {c.name}" for c in customers)
    return f"PRODUCTOS (código | nombre | unidad | estado):\n{product_rows}\n\nCLIENTES (código | nombre):\n{customer_rows}"


def evaluation_text(evaluation: Evaluation, title: str = "PEDIDO EVALUADO") -> str:
    lines = [f"{title} — cliente: {evaluation.customer_name or evaluation.draft.customer_code} (nivel {evaluation.customer_tier})"]
    for line in evaluation.lines:
        lines.append(
            f"- {line.sku} {line.name}: {quantity_text(line.quantity, line.unit)} desde bodega {line.warehouse}, "
            f"precio de lista {usd(line.unit_price)}, descuento {pct(line.discount_pct)}, subtotal {usd(line.amount)}"
        )
    lines.append(f"Total del pedido (sin impuestos): {usd(evaluation.total)}")
    lines.append("Reglas:")
    for rule in evaluation.rules:
        mark = "OK" if rule.ok else "FALLA"
        citation = f" [{rule.citation}]" if rule.citation else ""
        lines.append(f"- {mark}: {rule.reason}{citation}")
    return "\n".join(lines)


def alternatives_text(alternatives: list[Alternative]) -> str:
    if not alternatives:
        return "ALTERNATIVAS: ninguna disponible"
    blocks = ["ALTERNATIVAS CALCULADAS (solo ofrecer las marcadas como VÁLIDA):"]
    for number, alternative in enumerate(alternatives, start=1):
        status = "VÁLIDA" if alternative.evaluation.ok else "NO VÁLIDA"
        blocks.append(f"{number}. {alternative.description} — {status}")
        blocks.append(evaluation_text(alternative.evaluation, title=f"Alternativa {number}"))
    return "\n".join(blocks)


def as_json(model) -> dict:
    return json.loads(model.model_dump_json())
