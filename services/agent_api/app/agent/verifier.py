"""Output guardrail, pure Python: every figure and citation in a response must be backed by verified data.

A response that fails is never shown; it is replaced by a summary built only from verified data.
"""

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel

CITATION = re.compile(r"POL-[A-Z]{3}-\d{2}\s*§\s*\d+(?:\.\d+)?")
# Money, percentages and plain numbers, in Spanish or English notation
NUMBER = re.compile(r"\d[\d.,]*")


class Verification(BaseModel):
    ok: bool
    issues: list[str]
    citations: list[str]


def parse_number(token: str) -> Decimal | None:
    token = token.rstrip(".,")
    if not token:
        return None
    if "," in token and "." in token:  # 5.050,50 (Spanish) or 5,050.50 (English)
        token = token.replace(".", "").replace(",", ".") if token.rfind(",") > token.rfind(".") else token.replace(",", "")
    elif "," in token:  # 18,50 -> decimal comma
        token = token.replace(",", ".") if re.fullmatch(r"\d+,\d{1,2}", token) else token.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", token):  # 5.050 -> thousands separator
        token = token.replace(".", "")
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def numbers_in(text: str) -> set[Decimal]:
    without_citations = CITATION.sub(" ", text)
    return {value for value in map(parse_number, NUMBER.findall(without_citations)) if value is not None}


def normalize_citation(citation: str) -> str:
    # "POL-COM-01 §3.2" is backed by the chunk "POL-COM-01 §3"
    match = re.match(r"(POL-[A-Z]{3}-\d{2})\s*§\s*(\d+)", citation)
    return f"{match[1]} §{match[2]}" if match else citation


def verify(answer: str, *, allowed_texts: list[str], retrieved_citations: list[str], require_citation: bool) -> Verification:
    """allowed_texts: verified facts, retrieved policy chunks and the user's own message."""
    issues: list[str] = []

    allowed = set().union(*(numbers_in(text) for text in allowed_texts)) if allowed_texts else set()
    for value in sorted(numbers_in(answer)):
        if value not in allowed:
            issues.append(f"Cifra no respaldada por los datos: {value}")

    found = [normalize_citation(c) for c in CITATION.findall(answer)]
    retrieved = {normalize_citation(c) for c in retrieved_citations}
    for citation in found:
        if citation not in retrieved:
            issues.append(f"Cita que no corresponde a los fragmentos recuperados: {citation}")
    if require_citation and not found:
        issues.append("La respuesta sobre políticas no incluye citas")

    return Verification(ok=not issues, issues=issues, citations=sorted(set(found)))
