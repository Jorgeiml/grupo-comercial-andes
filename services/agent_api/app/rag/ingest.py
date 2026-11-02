"""Indexes the policy documents into Chroma. Run once: python -m app.rag.ingest"""

import os
from pathlib import Path

from .chunking import load_chunks
from .store import collection, embed, embedding_model


def ingest(policies_dir: Path) -> dict:
    chunks = load_chunks(policies_dir)
    vectors, tokens = embed([chunk.text for chunk in chunks])

    store = collection()
    new_ids = [chunk.id for chunk in chunks]
    # Remove sections that no longer exist, so the index mirrors the documents exactly
    stale = sorted(set(store.get(include=[])["ids"]) - set(new_ids))
    if stale:
        store.delete(ids=stale)
    store.upsert(
        ids=new_ids,
        embeddings=vectors,
        documents=[chunk.text for chunk in chunks],
        metadatas=[chunk.metadata() for chunk in chunks],
    )
    return {"chunks": len(chunks), "removed": len(stale), "embedding_tokens": tokens, "model": embedding_model()}


if __name__ == "__main__":
    print(ingest(Path(os.getenv("POLICIES_DIR", "/policies"))))
