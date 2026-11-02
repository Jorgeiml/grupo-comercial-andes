"""The single entry point to the LLM: every call is structured, limited and accounted for."""

import time
from typing import TypeVar

from pydantic import BaseModel

from .provider import ModelKind, get_model, model_name
from .usage import UsageTracker

T = TypeVar("T", bound=BaseModel)


class LLMOutputError(Exception):
    """The model did not return an object that matches the schema."""


def extract(kind: ModelKind, schema: type[T], messages, tracker: UsageTracker, step: str) -> T:
    tracker.ensure_budget()
    # strict json_schema: the model cannot add fields or omit required ones
    runnable = get_model(kind).with_structured_output(schema, method="json_schema", strict=True, include_raw=True)

    start = time.perf_counter()
    result = runnable.invoke(messages)
    latency_ms = round((time.perf_counter() - start) * 1000)

    usage = getattr(result["raw"], "usage_metadata", None) or {}
    tracker.record_chat(step, model_name(kind), usage.get("input_tokens", 0), usage.get("output_tokens", 0), latency_ms)

    if result.get("parsing_error") is not None or result.get("parsed") is None:
        raise LLMOutputError(f"{step}: model output did not match {schema.__name__}")
    return result["parsed"]
