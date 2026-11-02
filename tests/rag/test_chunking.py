"""Policy chunking is deterministic and produces exact, verifiable citations."""

import re

from app.rag.chunking import load_chunks

from conftest import REPO

CHUNKS = load_chunks(REPO / "data" / "policies")


def test_one_chunk_per_numbered_section():
    assert len(CHUNKS) == 14
    assert len({chunk.id for chunk in CHUNKS}) == len(CHUNKS)


def test_citations_follow_code_and_section_format():
    assert all(re.fullmatch(r"POL-[A-Z]{3}-\d{2} §\d+", chunk.citation) for chunk in CHUNKS)


def test_every_chunk_carries_its_document_title():
    assert all(chunk.text.startswith(f"{chunk.title} ({chunk.doc_code})") for chunk in CHUNKS)


def test_tables_are_not_split_across_chunks():
    discount = next(chunk for chunk in CHUNKS if chunk.citation == "POL-COM-01 §2")
    assert "500 o más" in discount.text and "Menos de 100" in discount.text


def test_unanswerable_demo_question_has_no_source():
    # The demo relies on the policies NOT covering Galápagos
    assert not any(re.search(r"gal[aá]pagos|insular", chunk.text, re.IGNORECASE) for chunk in CHUNKS)
