"""The single entry point to the LLM: every call is structured, limited, cached and accounted for."""

import os
import time
from typing import TypeVar

from pydantic import BaseModel

from . import cache
from .provider import ModelKind, get_model, model_name
from .usage import UsageTracker

T = TypeVar("T", bound=BaseModel)


class LLMOutputError(Exception):
    """The model did not return an object that matches the schema."""


def extract(kind: ModelKind, schema: type[T], messages, tracker: UsageTracker, step: str) -> T:
    tracker.ensure_budget()
    name = model_name(kind)

    key = None
    if cache.enabled():
        key = cache.make_key("chat", os.getenv("LLM_PROVIDER", "openai"), name, schema.__name__, schema.model_json_schema(), messages)
        if (hit := cache.get(key)) is not None:
            tracker.record_chat(step, name, 0, 0, 0, cached=True)  # no model call: no tokens, no cost
            return schema.model_validate_json(hit)

    # strict json_schema: the model cannot add fields or omit required ones
    runnable = get_model(kind).with_structured_output(schema, method="json_schema", strict=True, include_raw=True)

    start = time.perf_counter()
    result = runnable.invoke(messages)
    latency_ms = round((time.perf_counter() - start) * 1000)

    usage = getattr(result["raw"], "usage_metadata", None) or {}
    input_tokens, output_tokens = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    tracker.record_chat(step, name, input_tokens, output_tokens, latency_ms)

    if result.get("parsing_error") is not None or result.get("parsed") is None:
        raise LLMOutputError(f"{step}: model output did not match {schema.__name__}")
    if key:
        cache.put(key, "chat", name, result["parsed"].model_dump_json(), input_tokens, output_tokens)
    return result["parsed"]
