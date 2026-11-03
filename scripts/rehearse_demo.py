"""Rehearses the demo story (docs/guion-demo.md) through the API, exactly as the UI does, and prints every answer.

With LLM_CACHE_ENABLED=true the first run fills the cache and later runs replay identically. Run after reset_demo.ps1:
    python scripts/rehearse_demo.py
"""

import json
import sys
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

API = "http://localhost:8000"
SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"
FOLLOW_UP = "¿Y por qué no se puede procesar el pedido?"
QUESTION = ("Somos Supermercados La Pradera. Si pedimos 300 fardos de arroz para entregar en Guayaquil y pagamos a 60 días, "
            "¿qué descuento nos corresponde, cuánto sería el total y en cuántos días llega?")


def call(method, path, body=None, token=None, raw=None, content_type="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(API + path, method=method, data=data, headers={"Content-Type": content_type})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def token(user, role):
    return call("POST", "/auth/demo-token", {"user": user, "role": role})[1]["token"]


def show(step, body):
    usage = body.get("usage", [])
    cached = bool(usage) and all(u.get("cached") for u in usage)
    cost = Decimal(body.get("totals", {}).get("cost_usd", "0"))
    print(f"\n[{step}] {body.get('response_kind')} | USD {cost:.5f} | {'CACHÉ' if cached else 'LLM en vivo'}")
    print("   " + body.get("response", "").replace("\n", "\n   "))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cliente, ejecutivo, supervisor, gerente = (token("CL-001", "cliente"), token("luis", "ejecutivo"),
                                               token("sofia", "supervisor"), token("ana", "gerente"))

    _, body = call("POST", "/chat", {"message": QUESTION, "thread_id": "demo-1"}, cliente)
    show("1 Consulta compleja (cliente)", body)

    pdf = (SAMPLES / "oc-la-pradera-demo.pdf").read_bytes()
    _, body = call("POST", "/chat/upload?filename=oc-la-pradera-demo.pdf&thread_id=demo-2", raw=pdf, token=ejecutivo, content_type="application/pdf")
    show("2 Orden de compra en PDF (ejecutivo)", body)
    alternatives = body.get("alternatives", [])
    for i, a in enumerate(alternatives):
        ok = all(r["ok"] for r in a["evaluation"]["rules"])
        print(f"   alternativa {i}: {a['kind']:24} {'VÁLIDA' if ok else 'no válida'}  total {a['evaluation']['total']}")
    _, follow_up = call("POST", "/chat", {"message": FOLLOW_UP, "thread_id": "demo-2"}, ejecutivo)
    show("2b Pregunta de seguimiento (ejecutivo)", follow_up)
    combined = next(i for i, a in enumerate(alternatives) if a["kind"] == "partial_plus_substitute")
    status, accepted = call("POST", "/alternatives/accept", {"thread_id": "demo-2", "index": combined}, ejecutivo)
    proposal = accepted["proposal"]
    print(f"\n[3 Propuesta con la opción combinada] HTTP {status} | {proposal['id']} | total {proposal['content']['total']} | aprueba {proposal['content']['approval_level']}")

    decision = {"decision": "aprobar", "content_hash": proposal["content_hash"]}
    status, body = call("POST", f"/proposals/{proposal['id']}/decision", decision, supervisor)
    print(f"[4 Supervisora aprueba] HTTP {status} | {body.get('detail')}")
    status, body = call("POST", f"/proposals/{proposal['id']}/decision", decision, gerente)
    print(f"[5 Gerente aprueba] HTTP {status} | {body['proposal']['status']} {body['proposal']['erp_order_number']}")

    pdf = (SAMPLES / "oc-inyeccion.pdf").read_bytes()
    _, body = call("POST", "/chat/upload?filename=oc-inyeccion.pdf&thread_id=demo-3", raw=pdf, token=ejecutivo, content_type="application/pdf")
    show("6 PDF con instrucción oculta (ejecutivo)", body)

    _, costs = call("GET", "/costs?monthly_requests=3000", token=gerente)
    print(f"\n[7 Costos] solicitudes {costs['requests']} | total USD {costs['total_cost_usd']} | modelo pequeño {costs['small_model_share']*100:.0f} % | "
          f"llamadas desde caché {costs['cached_calls']}")
