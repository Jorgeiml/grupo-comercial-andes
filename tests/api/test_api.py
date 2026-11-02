"""agent-api: authorization comes from the token, and an approval through the API ends in one ERP order."""

import pytest
from app.auth.tokens import issue_token
from app.main import app
from fastapi.testclient import TestClient

from conftest import classified, order


@pytest.fixture
def api(agent, monkeypatch):
    monkeypatch.setenv("DEMO_TOKENS_ENABLED", "true")
    app.state.agent = agent
    with TestClient(app) as client:
        yield client
    del app.state.agent


def auth(user, role):
    return {"Authorization": f"Bearer {issue_token(user, role)}"}


EJECUTIVO, SUPERVISOR, GERENTE, CLIENTE = auth("luis", "ejecutivo"), auth("sofia", "supervisor"), auth("ana", "gerente"), auth("CL-001", "cliente")


def propose_pradera(api, script):
    script["classify"] = classified("special_order")
    script["extract_order"] = order(days=60)
    body = api.post("/chat", json={"message": "La Pradera: 300 fardos de arroz a Guayaquil"}, headers=EJECUTIVO).json()
    assert body["response_kind"] == "proposal"
    return body


def test_requests_without_token_are_refused(api):
    assert api.post("/chat", json={"message": "hola"}).status_code == 401
    assert api.get("/proposals").status_code == 401


def test_forged_token_is_refused(api):
    assert api.get("/proposals", headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401


def test_demo_tokens_can_be_disabled(api, monkeypatch):
    monkeypatch.setenv("DEMO_TOKENS_ENABLED", "false")
    assert api.post("/auth/demo-token", json={"user": "x", "role": "gerente"}).status_code == 404


def test_chat_returns_response_costs_and_pending_approval(api, script):
    body = propose_pradera(api, script)
    assert body["pending_approval"]["approval_level"] == "gerente"
    assert body["totals"]["tokens"] > 0
    assert [step["step"] for step in body["usage"]] == ["classify", "extract_order"]
    assert all(rule["ok"] for rule in body["evaluation"]["rules"])


def test_customer_cannot_see_the_queue_or_approve(api, script):
    pending = propose_pradera(api, script)["pending_approval"]
    assert api.get("/proposals", headers=CLIENTE).status_code == 403
    decision = {"decision": "aprobar", "content_hash": pending["content_hash"]}
    assert api.post(f"/proposals/{pending['proposal_id']}/decision", json=decision, headers=CLIENTE).status_code == 403


def test_role_in_the_body_is_ignored(api, script):
    pending = propose_pradera(api, script)["pending_approval"]
    decision = {"decision": "aprobar", "content_hash": pending["content_hash"], "role": "gerente"}
    response = api.post(f"/proposals/{pending['proposal_id']}/decision", json=decision, headers=SUPERVISOR)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "LEVEL_INSUFFICIENT"


def test_stale_hash_is_refused(api, script):
    pending = propose_pradera(api, script)["pending_approval"]
    response = api.post(f"/proposals/{pending['proposal_id']}/decision", json={"decision": "aprobar", "content_hash": "0" * 64}, headers=GERENTE)
    assert response.json()["detail"]["code"] == "HASH_MISMATCH"


def test_approval_through_the_api_writes_one_erp_order(api, script, erp_db):
    pending = propose_pradera(api, script)["pending_approval"]
    decision = {"decision": "aprobar", "content_hash": pending["content_hash"]}

    response = api.post(f"/proposals/{pending['proposal_id']}/decision", json=decision, headers=GERENTE).json()

    assert response["proposal"]["status"] == "EJECUTADA"
    assert "SO-000001" in response["response"]
    assert erp_db("SELECT external_ref FROM sales_orders") == [{"external_ref": pending["proposal_id"]}]
    again = api.post(f"/proposals/{pending['proposal_id']}/decision", json=decision, headers=GERENTE)
    assert again.status_code == 409  # double approval
    assert len(erp_db("SELECT * FROM sales_orders")) == 1


def test_approved_proposal_is_executed_even_if_the_conversation_moved_on(api, script, erp_db):
    body = propose_pradera(api, script)
    pending = body["pending_approval"]
    script["classify"] = classified("out_of_scope")
    api.post("/chat", json={"message": "otra cosa", "thread_id": body["thread_id"]}, headers=EJECUTIVO)

    decision = {"decision": "aprobar", "content_hash": pending["content_hash"]}
    response = api.post(f"/proposals/{pending['proposal_id']}/decision", json=decision, headers=GERENTE).json()

    assert response["execution"]["status"] == "executed"
    assert len(erp_db("SELECT * FROM sales_orders")) == 1


def test_costs_and_audit_are_for_approvers(api, script):
    propose_pradera(api, script)
    assert api.get("/costs", headers=EJECUTIVO).status_code == 403
    costs = api.get("/costs?monthly_requests=1000", headers=GERENTE).json()
    assert costs["requests"] == 1 and costs["per_user_licenses_usd"] == "0.00"
    assert api.get("/audit", headers=GERENTE).json()[0]["action"] == "create_proposal"
