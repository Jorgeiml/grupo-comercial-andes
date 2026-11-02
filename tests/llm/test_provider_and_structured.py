"""Every LLM call goes through one gate: temperature 0, limits, strict schema and usage accounting."""

import pytest
from app.llm import structured
from app.llm.provider import get_model
from app.llm.structured import LLMOutputError, extract
from app.llm.usage import TokenBudgetExceeded, UsageTracker
from langchain_core.messages import AIMessage
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from pydantic import BaseModel


@pytest.fixture(autouse=True)
def llm_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("MODEL_SMALL", "gpt-5.6-luna")
    monkeypatch.setenv("MODEL_MAIN", "gpt-5.4-mini")
    monkeypatch.setenv("LLM_PROVIDER", "openai")


def _payload(model):
    # The request that would actually be sent: guards against parameters being dropped silently
    return model._get_request_payload([("human", "x")])


def test_openai_models_use_temperature_zero_and_limits():
    small, main = get_model("small"), get_model("main")
    assert isinstance(small, ChatOpenAI)
    assert (small.model_name, main.model_name) == ("gpt-5.6-luna", "gpt-5.4-mini")
    for model in (small, main):
        payload = _payload(model)
        assert payload["temperature"] == 0
        assert payload["reasoning_effort"] == "none"
    assert small.max_retries == 1
    assert small.max_tokens == 800
    assert small.request_timeout == 30


def test_non_gpt5_models_get_no_reasoning_parameter(monkeypatch):
    monkeypatch.setenv("MODEL_SMALL", "gpt-4.1-mini")
    payload = _payload(get_model("small"))
    assert payload["temperature"] == 0
    assert "reasoning_effort" not in payload


def test_azure_is_selected_by_configuration(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "not-real")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
    model = get_model("small")
    assert isinstance(model, AzureChatOpenAI)
    assert model.deployment_name == "gpt-5.6-luna"
    assert _payload(model)["temperature"] == 0


def test_unknown_provider_fails_loudly(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mystery")
    with pytest.raises(ValueError):
        get_model("small")


class Answer(BaseModel):
    value: str


class FakeModel:
    """Stands in for a chat model: records how it was asked for structured output."""

    def __init__(self, parsed, usage):
        self.parsed, self.usage, self.calls = parsed, usage, []

    def with_structured_output(self, schema, **kwargs):
        self.calls.append(kwargs)
        return self

    def invoke(self, messages):
        raw = AIMessage(content="", usage_metadata=self.usage)
        return {"raw": raw, "parsed": self.parsed, "parsing_error": None}


USAGE = {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}


def test_extract_requests_strict_schema_and_records_usage(monkeypatch):
    fake = FakeModel(Answer(value="ok"), USAGE)
    monkeypatch.setattr(structured, "get_model", lambda kind: fake)
    tracker = UsageTracker()

    result = extract("small", Answer, "hola", tracker, step="classify")

    assert result == Answer(value="ok")
    assert fake.calls == [{"method": "json_schema", "strict": True, "include_raw": True}]
    [step] = tracker.steps
    assert (step.step, step.model, step.input_tokens, step.output_tokens) == ("classify", "gpt-5.6-luna", 120, 30)
    assert step.cost_usd is not None


def test_extract_rejects_output_that_does_not_match(monkeypatch):
    monkeypatch.setattr(structured, "get_model", lambda kind: FakeModel(None, USAGE))
    tracker = UsageTracker()
    with pytest.raises(LLMOutputError):
        extract("small", Answer, "hola", tracker, step="classify")
    assert tracker.total_tokens == 150  # the failed call is still paid for, so it is still counted


def test_extract_refuses_to_call_when_budget_is_spent(monkeypatch):
    fake = FakeModel(Answer(value="ok"), USAGE)
    monkeypatch.setattr(structured, "get_model", lambda kind: fake)
    tracker = UsageTracker(max_tokens=100)
    with pytest.raises(TokenBudgetExceeded):
        extract("small", Answer, "hola", tracker, step="classify")  # 150 > 100 after the call
    with pytest.raises(TokenBudgetExceeded):
        extract("small", Answer, "hola", tracker, step="draft")
    assert len(fake.calls) == 1  # the second call never reached the model
