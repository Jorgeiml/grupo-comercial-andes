"""Deterministic validation engine. Every rule runs every time and explains itself in the UI.

All facts come from the ERP through the read-only adapter. ErpUnavailable is never caught here:
"cannot verify" must never be turned into "valid".
"""

from collections import defaultdict
from decimal import Decimal

from pydantic import BaseModel

from ..erp.port import ErpPort, Product
from .models import ApprovalLevel, OrderDraft, PricedLine, ProposalContent, RuleResult
from .pricing import (
    APPROVAL_CITATION,
    DISCOUNT_CITATION,
    PAYMENT_CITATION,
    PAYMENT_TERM_DAYS,
    approval_level,
    line_amount,
    money,
    policy_discount,
)


def usd(amount: Decimal) -> str:
    """Spanish (Ecuador) money format: $5.050,50"""
    return "$" + f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def pct(value: Decimal) -> str:
    return f"{value.normalize():f} %".replace(".", ",")


class Shortage(BaseModel):
    sku: str
    warehouse: str
    requested: int
    available: int


class Evaluation(BaseModel):
    draft: OrderDraft
    customer_name: str | None
    customer_tier: str | None
    lines: list[PricedLine]
    rules: list[RuleResult]
    shortages: list[Shortage]
    total: Decimal
    approval_level: ApprovalLevel

    @property
    def ok(self) -> bool:
        return all(rule.ok for rule in self.rules)

    @property
    def failed(self) -> list[RuleResult]:
        return [rule for rule in self.rules if not rule.ok]

    def content(self) -> ProposalContent:
        return ProposalContent(
            customer_code=self.draft.customer_code,
            lines=self.lines,
            total=self.total,
            approval_level=self.approval_level,
            payment_days=self.draft.requested_payment_days,
        )


def evaluate(draft: OrderDraft, erp: ErpPort, quoted_prices: dict[str, Decimal] | None = None) -> Evaluation:
    """quoted_prices: prices stored in an existing proposal, compared against the ERP on revalidation."""
    rules: list[RuleResult] = []

    customer = erp.get_customer(draft.customer_code)
    credit = erp.get_credit(draft.customer_code) if customer else None
    rules.append(
        RuleResult(rule="CUSTOMER_EXISTS", ok=True, reason=f"Cliente {customer.name} (nivel {customer.tier})")
        if customer
        else RuleResult(rule="CUSTOMER_EXISTS", ok=False, reason=f"El cliente {draft.customer_code} no existe en el ERP")
    )

    # 1. Product exists and is active
    products: dict[str, Product | None] = {sku: erp.get_product(sku) for sku in dict.fromkeys(l.sku for l in draft.lines)}
    product_problems = []
    for sku, product in products.items():
        if product is None:
            product_problems.append(f"{sku} no existe en el catálogo")
        elif not product.active:
            substitute = f"; sustituto homologado: {product.substitute_sku}" if product.substitute_sku else ""
            product_problems.append(f"{sku} ({product.name}) está descontinuado{substitute}")
    rules.append(
        RuleResult(
            rule="PRODUCT_ACTIVE",
            ok=not product_problems,
            reason="; ".join(product_problems) or "Todos los productos existen y están activos",
            citation="POL-LOG-03 §3" if product_problems else None,
        )
    )
    sellable = {sku: p for sku, p in products.items() if p is not None and p.active}

    # 2. Quantity is valid
    bad_quantities = [f"{l.sku}: {l.quantity}" for l in draft.lines if l.quantity <= 0]
    if not draft.lines:
        bad_quantities = ["el pedido no tiene líneas"]
    rules.append(
        RuleResult(
            rule="QUANTITY_VALID",
            ok=not bad_quantities,
            reason=("Cantidades inválidas: " + ", ".join(bad_quantities)) if bad_quantities else "Cantidades válidas",
        )
    )
    valid_lines = [l for l in draft.lines if l.sku in sellable and l.quantity > 0]

    # 3. Enough stock in the warehouse (aggregated: repeated lines cannot oversell)
    needed: dict[tuple[str, str], int] = defaultdict(int)
    for line in valid_lines:
        needed[(line.sku, line.warehouse)] += line.quantity
    shortages, stock_notes = [], []
    for (sku, warehouse), quantity in needed.items():
        stock = erp.get_stock(sku, warehouse)
        available = stock.available if stock else 0
        if quantity > available:
            shortages.append(Shortage(sku=sku, warehouse=warehouse, requested=quantity, available=available))
            where = f"en {warehouse}" if stock else f"(la bodega {warehouse} no existe)"
            stock_notes.append(f"{sku} {where}: solicitado {quantity}, disponible {available}")
    rules.append(
        RuleResult(
            rule="STOCK_AVAILABLE",
            ok=not shortages,
            reason="Stock insuficiente: " + "; ".join(stock_notes) if shortages else "Stock suficiente en bodega",
            citation="POL-LOG-03 §2.1" if shortages else None,
        )
    )

    # Pricing: list price from the ERP, discount from the policy, capped by the ERP tier maximum
    tier = customer.tier if customer else ""
    max_pct = customer.max_discount_pct if customer else Decimal(0)
    priced = []
    for line in valid_lines:
        product = sellable[line.sku]
        unit_price = money(product.list_price)
        discount = policy_discount(line.quantity, tier, max_pct)
        priced.append(
            PricedLine(
                sku=line.sku, name=product.name, warehouse=line.warehouse, quantity=line.quantity, unit=product.unit,
                unit_price=unit_price, discount_pct=discount, amount=line_amount(line.quantity, unit_price, discount),
            )
        )
    total = money(sum((line.amount for line in priced), Decimal(0)))

    # 4. Price matches the official ERP list
    changed = [
        f"{line.sku}: cotizado {usd(quoted_prices[line.sku])}, lista actual {usd(line.unit_price)}"
        for line in priced
        if quoted_prices and line.sku in quoted_prices and quoted_prices[line.sku] != line.unit_price
    ]
    rules.append(
        RuleResult(
            rule="PRICE_MATCHES_ERP",
            ok=not changed,
            reason=("El precio cambió en el ERP: " + "; ".join(changed)) if changed else "Precios de la lista oficial del ERP",
            citation="POL-COM-01 §4.3",
        )
    )

    # 5. Discount does not exceed what the policy and the tier allow
    requested = draft.requested_discount_pct
    over = [line for line in priced if requested is not None and requested > line.discount_pct]
    if over:
        reason = (
            f"Se solicitó {pct(requested)}; según la política corresponde {pct(over[0].discount_pct)} "
            f"en {over[0].sku} (máximo del nivel {tier}: {pct(max_pct)}). Las excepciones solo se piden por escrito"
        )
    else:
        reason = "Descuento según política: " + (", ".join(f"{l.sku} {pct(l.discount_pct)}" for l in priced) or "sin líneas")
    rules.append(RuleResult(rule="DISCOUNT_WITHIN_TIER", ok=not over, reason=reason, citation=DISCOUNT_CITATION))

    # Payment term does not exceed the tier maximum
    max_days = PAYMENT_TERM_DAYS.get(tier)
    days = draft.requested_payment_days
    if max_days is None:
        term_ok, reason = False, "No se puede verificar el plazo de pago: nivel de cliente desconocido"
    elif days is None:
        term_ok, reason = True, f"Plazo estándar del nivel {tier}: hasta {max_days} días"
    elif 0 < days <= max_days:
        term_ok, reason = True, f"Pago a {days} días: permitido para el nivel {tier} (máximo {max_days})"
    else:
        term_ok, reason = False, f"Se solicitó pago a {days} días; el máximo para el nivel {tier} es {max_days} días"
    rules.append(RuleResult(rule="PAYMENT_TERM_WITHIN_TIER", ok=term_ok, reason=reason, citation=PAYMENT_CITATION))

    # 6. Order fits the available credit
    if credit is None:
        rules.append(RuleResult(rule="CREDIT_AVAILABLE", ok=False, reason="No se puede verificar el crédito del cliente"))
    else:
        fits = total <= credit.available
        verb = "cabe en" if fits else "excede"
        rules.append(
            RuleResult(
                rule="CREDIT_AVAILABLE",
                ok=fits,
                reason=f"El total {usd(total)} {verb} el crédito disponible {usd(credit.available)}",
                citation="POL-FIN-02 §3.2" if fits else "POL-FIN-02 §3.3",
            )
        )

    # 7. The amount sets the approval level
    level = approval_level(total)
    rules.append(
        RuleResult(rule="APPROVAL_LEVEL", ok=True, reason=f"Total {usd(total)}: aprueba el {level}", citation=APPROVAL_CITATION)
    )

    return Evaluation(
        draft=draft,
        customer_name=customer.name if customer else None,
        customer_tier=customer.tier if customer else None,
        lines=priced,
        rules=rules,
        shortages=shortages,
        total=total,
        approval_level=level,
    )
