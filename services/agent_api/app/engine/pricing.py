"""Commercial rules from the policy documents, applied in code. Documents explain; code enforces."""

from decimal import ROUND_HALF_UP, Decimal

from .models import ApprovalLevel

CENT = Decimal("0.01")

# POL-COM-01 §2.1 — volume discount per line, by quantity of the same product
VOLUME_TIERS = [(500, Decimal(8)), (250, Decimal(5)), (100, Decimal(3))]
# POL-COM-01 §3.1 — additional discount by customer tier
TIER_BONUS = {"Oro": Decimal(4), "Plata": Decimal(1), "Bronce": Decimal(0)}
# POL-FIN-02 §4.1 — above this total the manager must approve
SUPERVISOR_APPROVAL_LIMIT = Decimal("5000.00")

DISCOUNT_CITATION = "POL-COM-01 §3.2"
APPROVAL_CITATION = "POL-FIN-02 §4.1"


def money(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def volume_discount(quantity: int) -> Decimal:
    for minimum, pct in VOLUME_TIERS:
        if quantity >= minimum:
            return pct
    return Decimal(0)


def policy_discount(quantity: int, tier: str, max_discount_pct: Decimal) -> Decimal:
    """POL-COM-01 §3.2: volume + tier bonus, never above the tier maximum stored in the ERP."""
    total = volume_discount(quantity) + TIER_BONUS.get(tier, Decimal(0))
    return min(total, max_discount_pct).quantize(CENT)


def line_amount(quantity: int, unit_price: Decimal, discount_pct: Decimal) -> Decimal:
    # Same formula and rounding as the ERP, so both sides always agree to the cent
    return money(Decimal(quantity) * unit_price * (Decimal(100) - discount_pct) / Decimal(100))


def approval_level(total: Decimal) -> ApprovalLevel:
    return "supervisor" if total <= SUPERVISOR_APPROVAL_LIMIT else "gerente"
