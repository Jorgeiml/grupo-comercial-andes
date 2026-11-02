"""Observability is never a point of failure."""

from app.auth.tokens import Identity
from app.observability import tracing

from conftest import classified

EJECUTIVO = Identity(user="luis", role="ejecutivo")


def test_without_keys_tracing_is_off_and_the_agent_works(agent, script, monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    script["classify"] = classified("out_of_scope")

    with tracing.trace(EJECUTIVO, "t1", "chat") as (callbacks, handler):
        assert callbacks == [] and handler is None

    result = agent.send("t1", "hola", EJECUTIVO)
    assert result["response_kind"] == "refused"
    assert result["trace_id"] is None


def test_broken_langfuse_does_not_stop_the_agent(agent, script, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")

    import langfuse.langchain

    def explode(*args, **kwargs):
        raise RuntimeError("langfuse unavailable")

    monkeypatch.setattr(langfuse.langchain, "CallbackHandler", explode)
    script["classify"] = classified("out_of_scope")

    assert agent.send("t1", "hola", EJECUTIVO)["response_kind"] == "refused"


def test_outcome_recording_ignores_missing_trace():
    tracing.record_outcome(None, "answer", [])  # must not raise
