"""agent-api: authorization comes from the token, and an approval through the API ends in one ERP order."""

import pytest
from app.agent.schemas import Answerability, DraftAnswer, QueryPlan
from app.auth.tokens import issue_token
from app.main import app
from fastapi.testclient import TestClient

from conftest import CHUNKS, classified, order


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


def test_queue_shows_customer_name_without_changing_the_hash(api, script):
    pending = propose_pradera(api, script)["pending_approval"]
    [proposal] = api.get("/proposals", params={"status": "PENDIENTE"}, headers=GERENTE).json()
    assert proposal["customer_name"] == "Supermercados La Pradera"
    assert proposal["content_hash"] == pending["content_hash"]


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


def test_executive_turns_a_valid_alternative_into_a_proposal(api, script, erp_db):
    script["classify"] = classified("special_order")
    script["extract_order"] = order(customer="CL-002", sku="HG-001", quantity=40, warehouse="UIO")
    script["explain_order"] = DraftAnswer(answer="Hay 25 disponibles en Quito.")
    body = api.post("/chat", json={"message": "40 licuadoras"}, headers=EJECUTIVO).json()
    combined = next(i for i, a in enumerate(body["alternatives"]) if a["kind"] == "partial_plus_substitute")

    response = api.post("/alternatives/accept", json={"thread_id": body["thread_id"], "index": combined}, headers=EJECUTIVO)

    assert response.status_code == 200
    proposal = response.json()["proposal"]
    assert proposal["status"] == "PENDIENTE"
    assert [(l["sku"], l["quantity"]) for l in proposal["content"]["lines"]] == [("HG-001", 25), ("HG-101", 15)]
    assert erp_db("SELECT * FROM sales_orders") == []  # still needs approval


def test_alternative_can_be_accepted_after_a_follow_up_question(api, script):
    script["classify"] = classified("special_order")
    script["extract_order"] = order(customer="CL-002", sku="HG-001", quantity=40, warehouse="UIO")
    script["explain_order"] = DraftAnswer(answer="Hay 25 disponibles en Quito.")
    body = api.post("/chat", json={"message": "40 licuadoras"}, headers=EJECUTIVO).json()
    combined = next(i for i, a in enumerate(body["alternatives"]) if a["kind"] == "partial_plus_substitute")

    script["classify"] = classified("out_of_scope")  # any later turn resets the per-turn state
    api.post("/chat", json={"message": "¿y el partido?", "thread_id": body["thread_id"]}, headers=EJECUTIVO)

    response = api.post("/alternatives/accept", json={"thread_id": body["thread_id"], "index": combined}, headers=EJECUTIVO)
    assert response.status_code == 200
    assert [(l["sku"], l["quantity"]) for l in response.json()["proposal"]["content"]["lines"]] == [("HG-001", 25), ("HG-101", 15)]


def test_alternative_survives_a_follow_up_that_re_reads_the_order(api, script):
    # Seen live: the planner sometimes decides the follow-up needs order data and re-reads the same order from the ERP
    script["classify"] = classified("special_order")
    script["extract_order"] = order(customer="CL-002", sku="HG-001", quantity=40, warehouse="UIO")
    script["explain_order"] = DraftAnswer(answer="Hay 25 disponibles en Quito.")
    body = api.post("/chat", json={"message": "40 licuadoras"}, headers=EJECUTIVO).json()
    combined = next(i for i, a in enumerate(body["alternatives"]) if a["kind"] == "partial_plus_substitute")

    script["classify"] = classified("policy_question")
    script["plan"] = QueryPlan(subqueries=["falta de stock"], needs_order_data=True)
    script["check_answerable"] = Answerability(answerable=True, supporting_citations=[CHUNKS[1].citation], missing=None)
    script["draft_answer"] = DraftAnswer(answer=f"Pediste 40 y hay 25 en Quito [{CHUNKS[1].citation}].")
    api.post("/chat", json={"message": "¿Y por qué no se puede?", "thread_id": body["thread_id"]}, headers=EJECUTIVO)

    response = api.post("/alternatives/accept", json={"thread_id": body["thread_id"], "index": combined}, headers=EJECUTIVO)
    assert response.status_code == 200


def test_correction_request_reaches_the_executive_and_links_the_new_version(api, script, erp_db):
    pending = propose_pradera(api, script)["pending_approval"]
    decision = {"decision": "corregir", "content_hash": pending["content_hash"], "comment": "bájalo a 250 fardos"}
    assert api.post(f"/proposals/{pending['proposal_id']}/decision", json=decision, headers=SUPERVISOR).status_code == 200

    [sent_back] = api.get("/proposals", headers=EJECUTIVO).json()
    assert sent_back["status"] == "EN_CORRECCION" and sent_back["corrected_by"] is None
    assert sent_back["last_decision"]["comment"] == "bájalo a 250 fardos" and sent_back["last_decision"]["actor"] == "sofia"

    script["extract_order"] = order(quantity=250, days=60)
    corrected = api.post("/chat", json={"message": "250 fardos", "thread_id": sent_back["thread_id"]}, headers=EJECUTIVO).json()
    assert corrected["response_kind"] == "proposal"
    assert f"versión corregida de {sent_back['id']}" in corrected["response"]

    by_id = {p["id"]: p for p in api.get("/proposals", headers=EJECUTIVO).json()}
    new_id = corrected["pending_approval"]["proposal_id"]
    assert by_id[new_id]["supersedes"] == sent_back["id"] and by_id[sent_back["id"]]["corrected_by"] == new_id
    assert [p["id"] for p in api.get("/proposals", params={"status": "PENDIENTE"}, headers=EJECUTIVO).json()] == [new_id]
    assert erp_db("SELECT * FROM sales_orders") == []  # the corrected version still needs its own approval


def test_invalid_alternative_cannot_become_a_proposal(api, script):
    script["classify"] = classified("special_order")
    script["extract_order"] = order(customer="CL-002", sku="HG-001", quantity=40, warehouse="UIO")
    script["explain_order"] = DraftAnswer(answer="Hay 25 disponibles en Quito.")
    body = api.post("/chat", json={"message": "40 licuadoras"}, headers=EJECUTIVO).json()
    substitute_only = next(i for i, a in enumerate(body["alternatives"]) if a["kind"] == "substitute")  # 40 > 30 in stock
    response = api.post("/alternatives/accept", json={"thread_id": body["thread_id"], "index": substitute_only}, headers=EJECUTIVO)
    assert response.json()["detail"]["code"] == "INVALID_PROPOSAL"
    assert api.post("/alternatives/accept", json={"thread_id": body["thread_id"], "index": 0}, headers=GERENTE).status_code == 403


def test_costs_and_audit_are_for_approvers(api, script):
    propose_pradera(api, script)
    assert api.get("/costs", headers=EJECUTIVO).status_code == 403
    costs = api.get("/costs?monthly_requests=1000", headers=GERENTE).json()
    assert costs["requests"] == 1 and costs["per_user_licenses_usd"] == "0.00"
    assert api.get("/audit", headers=GERENTE).json()[0]["action"] == "create_proposal"
