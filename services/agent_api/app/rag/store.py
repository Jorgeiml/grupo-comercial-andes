"""Embeddings and the Chroma collection shared by ingestion and retrieval."""

import json
import os

import chromadb
from openai import OpenAI

from ..llm import cache

COLLECTION = "policies"


def embedding_model() -> str:
    return os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")


def embed(texts: list[str]) -> tuple[list[list[float]], int]:
    """Returns the vectors and the tokens consumed (for the cost panel); 0 tokens when served from the cache."""
    key = cache.make_key("embed", embedding_model(), texts) if cache.enabled() else None
    if key and (hit := cache.get(key)) is not None:
        return json.loads(hit), 0
    client = OpenAI(timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")))
    response = client.embeddings.create(model=embedding_model(), input=texts)
    vectors, tokens = [item.embedding for item in response.data], response.usage.total_tokens
    if key:
        cache.put(key, "embed", embedding_model(), json.dumps(vectors), tokens, 0)
    return vectors, tokens


def collection():
    client = chromadb.PersistentClient(path=os.getenv("CHROMA_PATH", "/data/chroma"))
    return client.get_or_create_collection(
        COLLECTION,
        configuration={"hnsw": {"space": "cosine"}},
        # We always pass our own OpenAI vectors; None stops Chroma from loading its default local model
        embedding_function=None,
    )
