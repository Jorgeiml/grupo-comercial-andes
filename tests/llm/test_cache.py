"""The same question with the same data is answered from the cache: no model call, no cost, identical answer."""

import pytest
from app.llm import structured
from app.llm.structured import extract
from app.llm.usage import UsageTracker
from langchain_core.messages import AIMessage
from pydantic import BaseModel


class Answer(BaseModel):
    value: str


class CountingModel:
    def __init__(self):
        self.calls = 0

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages):
        self.calls += 1
        raw = AIMessage(content="", usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})
        return {"raw": raw, "parsed": Answer(value=f"respuesta {self.calls}"), "parsing_error": None}


@pytest.fixture
def model(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_CACHE_ENABLED", "true")
    monkeypatch.setenv("LLM_CACHE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setenv("MODEL_SMALL", "gpt-5.6-luna")
    fake = CountingModel()
    monkeypatch.setattr(structured, "get_model", lambda kind: fake)
    return fake


def test_repeated_question_is_served_from_cache(model):
    first_tracker, second_tracker = UsageTracker(), UsageTracker()
    first = extract("small", Answer, [("human", "hola")], first_tracker, "classify")
    second = extract("small", Answer, [("human", "hola")], second_tracker, "classify")

    assert first == second  # identical answer
    assert model.calls == 1  # the model was called once
    [step] = second_tracker.steps
    assert step.cached and step.total_tokens == 0 and step.cost_usd == 0


def test_different_input_is_a_new_call(model):
    extract("small", Answer, [("human", "hola")], UsageTracker(), "classify")
    extract("small", Answer, [("human", "hola, otra cosa")], UsageTracker(), "classify")
    assert model.calls == 2


def test_cache_off_always_calls_the_model(model, monkeypatch):
    monkeypatch.setenv("LLM_CACHE_ENABLED", "false")
    extract("small", Answer, [("human", "hola")], UsageTracker(), "classify")
    extract("small", Answer, [("human", "hola")], UsageTracker(), "classify")
    assert model.calls == 2
