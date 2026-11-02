"""Output guardrail: figures and citations must be backed by verified data."""

from decimal import Decimal

import pytest
from app.agent.verifier import numbers_in, parse_number, verify

FACTS = "Total del pedido: $5.050,50. Descuento 9 %. 300 fardo a $18,50. Crédito disponible $60.000,00."
CHUNK = "Política de Descuentos (POL-COM-01) — §2 Descuento por volumen\n| 250 a 499 | 5 % |"


@pytest.mark.parametrize(
    ("token", "value"),
    [("5.050,50", "5050.50"), ("5,050.50", "5050.50"), ("18,50", "18.50"), ("18.50", "18.50"), ("1.200", "1200"), ("60", "60"), ("9.", "9")],
)
def test_parse_spanish_and_english_numbers(token, value):
    assert parse_number(token) == Decimal(value)


def test_citation_numbers_are_not_figures():
    assert numbers_in("según [POL-COM-01 §3.2]") == set()


def test_answer_with_verified_figures_passes():
    answer = "El total es $5.050,50 con 9 % de descuento por 300 fardos [POL-COM-01 §2]."
    result = verify(answer, allowed_texts=[FACTS, CHUNK], retrieved_citations=["POL-COM-01 §2"], require_citation=True)
    assert result.ok, result.issues


def test_invented_figure_is_blocked():
    answer = "El total es $4.999,00 [POL-COM-01 §2]."
    result = verify(answer, allowed_texts=[FACTS, CHUNK], retrieved_citations=["POL-COM-01 §2"], require_citation=True)
    assert not result.ok
    assert "4999.00" in result.issues[0]


def test_citation_not_retrieved_is_blocked():
    answer = "Aplica 5 % [POL-FIN-02 §1]."
    result = verify(answer, allowed_texts=[CHUNK], retrieved_citations=["POL-COM-01 §2"], require_citation=True)
    assert not result.ok


def test_subsection_citation_is_backed_by_its_section():
    answer = "Aplica 5 % [POL-COM-01 §2.1]."
    assert verify(answer, allowed_texts=[CHUNK], retrieved_citations=["POL-COM-01 §2"], require_citation=True).ok


def test_policy_answer_without_citation_is_blocked():
    result = verify("Aplica 5 %.", allowed_texts=[CHUNK], retrieved_citations=["POL-COM-01 §2"], require_citation=True)
    assert not result.ok
