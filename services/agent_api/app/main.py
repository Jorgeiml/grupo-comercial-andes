"""agent-api: chat, proposals, approvals, audit and costs. The role always comes from the JWT, never from the body."""

import json
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .agent.runner import Agent
from .approvals.service import (
    ApprovalError,
    Decision,
    Status,
    audit_trail,
    decide,
    get_proposal,
    list_proposals,
)
from .auth.tokens import AuthError, Identity, Role, decode_token, issue_token
from .execution.service import execute
from .store.costs import cost_summary, record_request
from .store.db import connect, init_db

STAFF = {"ejecutivo", "supervisor", "gerente"}
APPROVERS = {"supervisor", "gerente"}
HTTP_STATUS = {"NOT_FOUND": 404, "NOT_AN_APPROVER": 403, "ROLE_NOT_ALLOWED": 403}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if not hasattr(app.state, "agent"):  # tests inject an agent with fake dependencies
        app.state.agent = Agent()
    yield


app = FastAPI(title="agent-api — Grupo Comercial Andes", lifespan=lifespan)


def current_identity(authorization: str | None = Header(default=None)) -> Identity:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Falta el token")
    try:
        return decode_token(authorization.removeprefix("Bearer "))
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e


def require(roles: set[str]):
    def dependency(identity: Identity = Depends(current_identity)) -> Identity:
        if identity.role not in roles:
            raise HTTPException(status_code=403, detail=f"El rol {identity.role} no tiene acceso")
        return identity

    return dependency


def approval_http_error(e: ApprovalError) -> HTTPException:
    return HTTPException(status_code=HTTP_STATUS.get(e.code, 409), detail={"code": e.code, "message": e.message})


@app.get("/health")
def health():
    return {"status": "ok"}


class DemoTokenRequest(BaseModel):
    user: str = Field(min_length=1, max_length=40)
    role: Role


@app.post("/auth/demo-token")
def demo_token(body: DemoTokenRequest):
    # Demo role selector only; in production tokens come from the corporate identity provider
    if os.getenv("DEMO_TOKENS_ENABLED", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Not found")
    return {"token": issue_token(body.user, body.role), "user": body.user, "role": body.role}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, max_length=64)


@app.post("/chat")
def chat(body: ChatRequest, request: Request, identity: Identity = Depends(current_identity)):
    agent: Agent = request.app.state.agent
    thread_id = body.thread_id or f"th-{uuid.uuid4().hex[:12]}"
    start = time.perf_counter()
    result = agent.send(thread_id, body.message, identity)
    latency_ms = round((time.perf_counter() - start) * 1000)
    totals = record_request(thread_id, identity, result["response_kind"], result["usage"], latency_ms)
    state = result["state"]
    return {
        "thread_id": thread_id,
        "response": result["response"],
        "response_kind": result["response_kind"],
        "route": result.get("route"),
        "pending_approval": result["pending_approval"],
        "evaluation": state.get("evaluation"),
        "alternatives": state.get("alternatives", []),
        "verification": state.get("verification"),
        "citations": [c["citation"] for c in state.get("chunks", [])],
        "usage": result["usage"],
        "totals": totals,
    }


@app.get("/proposals")
def proposals(status: Status | None = None, _: Identity = Depends(require(STAFF))):
    return [p.model_dump(mode="json") for p in list_proposals(status)]


@app.get("/proposals/{proposal_id}")
def proposal_detail(proposal_id: str, _: Identity = Depends(require(STAFF))):
    try:
        proposal = get_proposal(proposal_id)
    except ApprovalError as e:
        raise approval_http_error(e) from e
    with connect() as conn:
        approvals = [dict(r) for r in conn.execute("SELECT * FROM approvals WHERE proposal_id = ? ORDER BY id", (proposal_id,))]
    return {"proposal": proposal.model_dump(mode="json"), "approvals": approvals, "audit": audit_trail(proposal_id)}


class DecisionRequest(BaseModel):
    decision: Decision
    content_hash: str = Field(min_length=64, max_length=64)  # the hash of what the approver saw
    comment: str | None = Field(default=None, max_length=500)


@app.post("/proposals/{proposal_id}/decision")
def proposal_decision(proposal_id: str, body: DecisionRequest, request: Request, identity: Identity = Depends(require(APPROVERS))):
    agent: Agent = request.app.state.agent
    try:
        proposal = decide(proposal_id, identity, body.decision, body.content_hash, body.comment)
    except ApprovalError as e:
        raise approval_http_error(e) from e

    pending = agent.pending_approval(proposal.thread_id) if proposal.thread_id else None
    if pending and pending["proposal_id"] == proposal_id:
        # Normal path: resume the paused conversation, which runs the execution service
        result = agent.resume(proposal.thread_id, identity)
        response, execution = result["response"], result["state"].get("execution")
    elif proposal.status == Status.APROBADA:
        # The conversation moved on: execute directly so an approved proposal is never orphaned
        outcome = execute(proposal_id, identity, agent.deps.write_erp())
        response, execution = None, outcome.model_dump(mode="json")
    else:
        response, execution = None, None
    return {"proposal": get_proposal(proposal_id).model_dump(mode="json"), "response": response, "execution": execution}


@app.get("/audit")
def audit(proposal_id: str | None = None, _: Identity = Depends(require(APPROVERS))):
    return audit_trail(proposal_id)


@app.get("/escalations")
def escalations(_: Identity = Depends(require(STAFF))):
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM escalations ORDER BY id DESC")]
    for row in rows:
        row["context"] = json.loads(row.pop("context_json"))
    return rows


@app.get("/costs")
def costs(monthly_requests: int = Query(default=3000, ge=0, le=10_000_000), _: Identity = Depends(require(APPROVERS))):
    return cost_summary(monthly_requests)
