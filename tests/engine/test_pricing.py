"""Policy rules applied in code (POL-COM-01, POL-FIN-02)."""

from decimal import Decimal

import pytest
from app.engine.pricing import approval_level, line_amount, policy_discount, volume_discount
from app.engine.validation import usd


@pytest.mark.parametrize(("quantity", "expected"), [(1, 0), (99, 0), (100, 3), (249, 3), (250, 5), (499, 5), (500, 8), (5000, 8)])
def test_volume_tiers(quantity, expected):
    assert volume_discount(quantity) == Decimal(expected)


@pytest.mark.parametrize(
    ("quantity", "tier", "max_pct", "expected"),
    [
        (300, "Oro", 12, "9"),  # 5 + 4: the demo's complex question
        (600, "Oro", 12, "12"),  # 8 + 4 = 12, exactly the maximum
        (300, "Plata", 6, "6"),  # 5 + 1
        (600, "Plata", 6, "6"),  # 8 + 1 = 9, capped at 6
        (300, "Bronce", 3, "3"),  # 5 + 0, capped at 3
        (10, "Oro", 12, "4"),  # tier bonus only
        (10, "Desconocido", 12, "0"),  # unknown tier gets no bonus
    ],
)
def test_policy_discount_is_capped_by_tier_maximum(quantity, tier, max_pct, expected):
    assert policy_discount(quantity, tier, Decimal(max_pct)) == Decimal(expected)


def test_line_amount_matches_demo_total():
    assert line_amount(300, Decimal("18.50"), Decimal("9")) == Decimal("5050.50")


def test_line_amount_rounds_half_up_to_cents():
    assert line_amount(1, Decimal("0.05"), Decimal("50")) == Decimal("0.03")  # 0.025 -> 0.03


@pytest.mark.parametrize(("total", "level"), [("4999.99", "supervisor"), ("5000.00", "supervisor"), ("5000.01", "gerente")])
def test_approval_threshold(total, level):
    assert approval_level(Decimal(total)) == level


def test_spanish_money_format():
    assert usd(Decimal("5050.50")) == "$5.050,50"
