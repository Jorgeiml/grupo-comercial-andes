"""Policy search. RAG is only for documents: never a source of stock or prices."""

import os
from dataclasses import dataclass, field

from .store import collection, embed


@dataclass(frozen=True)
class RetrievedChunk:
    citation: str  # e.g. "POL-COM-01 §3", built from metadata, never by the LLM
    title: str
    section_title: str
    text: str
    score: float  # cosine similarity, 1.0 = identical


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    embedding_tokens: int = 0
    min_score: float = 0.0

    @property
    def best_score(self) -> float:
        return max((chunk.score for chunk in self.chunks), default=0.0)

    @property
    def sufficient(self) -> bool:
        """False means: answer "no tengo esa información" and escalate to a person."""
        return self.best_score >= self.min_score

    @property
    def relevant(self) -> list[RetrievedChunk]:
        return [chunk for chunk in self.chunks if chunk.score >= self.min_score]


def search(query: str, k: int = 4) -> RetrievalResult:
    # First layer only: similarity says "same topic", not "contains the answer" (see docs/decisiones.md)
    min_score = float(os.getenv("RAG_MIN_SCORE", "0.40"))
    [vector], tokens = embed([query])
    result = collection().query(query_embeddings=[vector], n_results=k, include=["documents", "metadatas", "distances"])
    chunks = [
        RetrievedChunk(
            citation=f"{meta['doc_code']} §{meta['section']}",
            title=meta["title"],
            section_title=meta["section_title"],
            text=document,
            score=round(1 - distance, 4),  # Chroma returns cosine distance
        )
        for document, meta, distance in zip(result["documents"][0], result["metadatas"][0], result["distances"][0])
    ]
    return RetrievalResult(chunks=chunks, embedding_tokens=tokens, min_score=min_score)
