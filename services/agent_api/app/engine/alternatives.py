"""Alternatives when stock is short or a product is discontinued (POL-LOG-03 §2.1 and §3.1).

Python builds and fully re-evaluates each alternative; the LLM only explains them.
"""

from typing import Literal

from pydantic import BaseModel

from ..erp.port import ErpPort
from .models import OrderDraft, RequestedLine
from .validation import Evaluation, evaluate

AlternativeKind = Literal["partial", "substitute", "partial_plus_substitute"]

DESCRIPTIONS = {
    "partial": "Entrega parcial con el stock disponible; el saldo queda pendiente hasta la reposición",
    "substitute": "Reemplazar por el sustituto homologado",
    "partial_plus_substitute": "Stock disponible del producto original y el resto con el sustituto homologado",
}


class Alternative(BaseModel):
    kind: AlternativeKind
    description: str
    evaluation: Evaluation  # complete re-validation: an alternative can still fail, e.g. on credit


def suggest(evaluation: Evaluation, erp: ErpPort) -> list[Alternative]:
    draft = evaluation.draft
    short = {(s.sku, s.warehouse): s for s in evaluation.shortages}
    products = {line.sku: erp.get_product(line.sku) for line in draft.lines}
    discontinued = {sku for sku, p in products.items() if p is not None and not p.active and p.substitute_sku}
    if not short and not discontinued:
        return []

    def substitute_of(sku: str) -> str | None:
        product = products.get(sku)
        return product.substitute_sku if product else None

    def build(kind: AlternativeKind) -> OrderDraft | None:
        lines, changed = [], False
        for line in draft.lines:
            shortage = short.get((line.sku, line.warehouse))
            substitute = substitute_of(line.sku)
            if line.sku in discontinued:
                if kind == "partial":
                    return None  # a discontinued product cannot be partially delivered
                lines.append(RequestedLine(sku=substitute, quantity=line.quantity, warehouse=line.warehouse))
                changed = True
            elif shortage is None:
                lines.append(line)
            elif kind == "partial":
                if shortage.available > 0:
                    lines.append(line.model_copy(update={"quantity": shortage.available}))
                changed = True
            elif substitute is None:
                return None  # no homologated substitute for this product
            elif kind == "substitute":
                lines.append(RequestedLine(sku=substitute, quantity=line.quantity, warehouse=line.warehouse))
                changed = True
            else:  # partial_plus_substitute
                if shortage.available > 0:
                    lines.append(line.model_copy(update={"quantity": shortage.available}))
                lines.append(RequestedLine(sku=substitute, quantity=line.quantity - shortage.available, warehouse=line.warehouse))
                changed = True
        if not changed or not lines:
            return None
        return draft.model_copy(update={"lines": lines})

    alternatives, seen = [], set()
    for kind in ("partial", "substitute", "partial_plus_substitute"):
        candidate = build(kind)
        if candidate is None:
            continue
        key = tuple((l.sku, l.quantity, l.warehouse) for l in candidate.lines)
        if key in seen:  # e.g. with nothing available, "partial + substitute" equals "substitute"
            continue
        seen.add(key)
        alternatives.append(Alternative(kind=kind, description=DESCRIPTIONS[kind], evaluation=evaluate(candidate, erp)))
    return alternatives
