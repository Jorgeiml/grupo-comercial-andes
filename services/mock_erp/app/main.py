"""Mock ERP: one SQLite database exposed through Dynamics 365 F&O and SAP S/4HANA style APIs."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import routes_admin, routes_dynamics, routes_sap
from .db import init_db
from .orders import IdempotencyConflict, OrderRejected


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Mock ERP — Grupo Comercial Andes", lifespan=lifespan)
app.include_router(routes_dynamics.router)
app.include_router(routes_sap.router)
app.include_router(routes_admin.router)


# Both Dynamics and SAP report OData errors as {"error": {"code", "message"}}
@app.exception_handler(OrderRejected)
async def order_rejected(_: Request, exc: OrderRejected):
    return JSONResponse(status_code=422, content={"error": {"code": exc.code, "message": exc.message}})


@app.exception_handler(IdempotencyConflict)
async def idempotency_conflict(_: Request, exc: IdempotencyConflict):
    return JSONResponse(status_code=409, content={"error": {"code": "IDEMPOTENCY_CONFLICT", "message": str(exc)}})


@app.get("/health")
def health():
    return {"status": "ok"}
