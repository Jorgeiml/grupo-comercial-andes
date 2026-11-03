"""Demo-only endpoints (admin credential): change stock for scenario 4 and reset the data."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import require
from .db import connect, init_db, record_movement
from .orders import log_write

router = APIRouter(prefix="/admin", tags=["Demo admin"])


class StockChange(BaseModel):
    sku: str
    warehouse: str
    quantity: int = Field(ge=0)


@router.patch("/stock")
def set_stock(body: StockChange, credential: str = Depends(require("admin"))):
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT quantity FROM stock WHERE sku = ? AND warehouse = ?", (body.sku, body.warehouse)).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            raise HTTPException(status_code=404, detail=f"No stock row for {body.sku} in {body.warehouse}")
        conn.execute("UPDATE stock SET quantity = ? WHERE sku = ? AND warehouse = ?", (body.quantity, body.sku, body.warehouse))
        record_movement(conn, body.sku, body.warehouse, "adjustment", body.quantity - row["quantity"])
        conn.execute("COMMIT")
    log_write(credential, "stock_changed", detail=f"{body.sku}@{body.warehouse} = {body.quantity}")
    return body


@router.post("/reset")
def reset(_: str = Depends(require("admin"))):
    init_db(reset=True)
    return {"status": "reset"}


@router.get("/stock-movements")
def stock_movements(sku: str, _: str = Depends(require("admin"))):
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM stock_movements WHERE sku = ? ORDER BY id", (sku,))]


@router.get("/write-log")
def write_log(_: str = Depends(require("admin"))):
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM write_log ORDER BY id DESC")]
