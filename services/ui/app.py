"""Demo UI. It only talks to agent-api: permissions are always decided by the API from the token, never here."""

import os
import uuid
from decimal import Decimal

import httpx
import streamlit as st

API = os.getenv("API_URL", "http://agent-api:8000")
ERP = os.getenv("ERP_URL", "http://mock-erp:8001")
DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

ROLES = {
    "cliente": ("CL-001", "Cliente · Supermercados La Pradera"),
    "ejecutivo": ("luis", "Ejecutivo de cuenta · Luis"),
    "supervisor": ("sofia", "Supervisora comercial · Sofía"),
    "gerente": ("ana", "Gerente comercial · Ana"),
}
KIND_LABEL = {
    "answer": "✅ Respuesta verificada", "no_info": "🟡 Sin información · escalado", "clarify": "❓ Pide aclaración",
    "proposal": "📝 Propuesta pendiente", "executed": "🟢 Escrito en el ERP", "blocked": "⛔ Bloqueado",
    "refused": "🛡️ Rechazado por guardrail", "quote": "💬 Cotización", "rejected": "✖ Rechazada",
    "correction": "↩ Corrección pedida", "stopped": "⏹ Detenido de forma segura",
}

st.set_page_config(page_title="Asistente B2B · Grupo Comercial Andes", page_icon="🛒", layout="wide")


# ---------- helpers ----------
def usd(value, decimals: int = 2) -> str:
    text = f"{Decimal(str(value)):,.{decimals}f}"
    return "$" + text.replace(",", "_").replace(".", ",").replace("_", ".")


def md(text: str) -> str:
    # Streamlit renders $...$ as LaTeX: escape dollar signs so amounts display as written
    return str(text).replace("$", "\\$")


def api(method: str, path: str, headers: dict | None = None, **kwargs) -> tuple[int, dict | list]:
    headers = {**(headers or {}), "Authorization": f"Bearer {st.session_state.token}"}
    try:
        response = httpx.request(method, API + path, headers=headers, timeout=180, **kwargs)
    except httpx.HTTPError as e:
        return 503, {"detail": f"agent-api no disponible: {e}"}
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {"detail": response.text}


def get_token(role: str) -> str:
    user, _ = ROLES[role]
    response = httpx.post(f"{API}/auth/demo-token", json={"user": user, "role": role}, timeout=10)
    response.raise_for_status()
    return response.json()["token"]


def detail(body) -> str:
    value = body.get("detail", body) if isinstance(body, dict) else body
    return value.get("message", str(value)) if isinstance(value, dict) else str(value)


# ---------- session ----------
if "messages" not in st.session_state:
    st.session_state.messages, st.session_state.thread_id, st.session_state.last = [], f"ui-{uuid.uuid4().hex[:8]}", None

with st.sidebar:
    st.header("🛒 Grupo Comercial Andes")
    role = st.radio("Rol (demo)", list(ROLES), format_func=lambda r: ROLES[r][1], key="role")
    if st.session_state.get("token_role") != role:
        st.session_state.token, st.session_state.token_role = get_token(role), role
    st.caption("El rol viaja en un token firmado (JWT). La API decide los permisos; esta pantalla no.")
    if st.button("Nueva conversación", use_container_width=True):
        st.session_state.messages, st.session_state.thread_id, st.session_state.last = [], f"ui-{uuid.uuid4().hex[:8]}", None
        st.rerun()
    st.divider()
    st.caption("La IA interpreta y propone → el software valida → una persona aprueba → un servicio separado ejecuta.")


def send(message: str | None = None, pdf: tuple[str, bytes] | None = None) -> None:
    if pdf:
        name, data = pdf
        st.session_state.messages.append({"role": "user", "text": f"📎 Orden de compra adjunta: {name}"})
        status, body = api("POST", f"/chat/upload?filename={name}&thread_id={st.session_state.thread_id}",
                           content=data, headers={"Content-Type": "application/pdf"})
    else:
        st.session_state.messages.append({"role": "user", "text": message})
        status, body = api("POST", "/chat", json={"message": message, "thread_id": st.session_state.thread_id})
    if status != 200:
        st.session_state.messages.append({"role": "assistant", "text": f"Error {status}: {detail(body)}", "kind": "stopped"})
        return
    st.session_state.last = body
    st.session_state.messages.append({"role": "assistant", "text": body["response"], "kind": body["response_kind"], "totals": body["totals"]})


# ---------- tabs ----------
chat_tab, approvals_tab, audit_tab, costs_tab = st.tabs(["💬 Chat", "✅ Aprobaciones", "📜 Auditoría", "💵 Costos"])

with chat_tab:
    left, right = st.columns([3, 2], gap="large")
    with left:
        for m in st.session_state.messages:
            with st.chat_message(m["role"]):
                if m.get("kind"):
                    st.caption(KIND_LABEL.get(m["kind"], m["kind"]))
                st.markdown(md(m["text"]))
                if m.get("totals"):
                    t = m["totals"]
                    st.caption(md(f"Costo {usd(t['cost_usd'], 5)} · {t['tokens']} tokens · {t['latency_ms'] / 1000:.1f} s"))
        uploaded = st.file_uploader("Adjuntar orden de compra (PDF)", type=["pdf"], key=f"pdf-{len(st.session_state.messages)}")
        if uploaded and st.button("Enviar PDF"):
            with st.spinner("Leyendo la orden de compra…"):
                send(pdf=(uploaded.name, uploaded.getvalue()))
            st.rerun()
        if prompt := st.chat_input("Escribe una consulta o un pedido especial…"):
            with st.spinner("El agente está trabajando…"):
                send(prompt)
            st.rerun()

    with right:
        st.subheader("Panel del pedido")
        last = st.session_state.last
        if not last:
            st.info("Aquí verás las validaciones, las alternativas y el costo de cada paso.")
        else:
            evaluation = last.get("evaluation") or {}
            if evaluation.get("rules"):
                st.markdown(f"**Cliente:** {evaluation.get('customer_name')} · nivel {evaluation.get('customer_tier')}")
                for line in evaluation.get("lines", []):
                    st.markdown(md(f"- `{line['sku']}` {line['name']}: {line['quantity']} {line['unit']} × {usd(line['unit_price'])}"
                                   f" − {Decimal(line['discount_pct']).normalize():f} % = **{usd(line['amount'])}**"))
                valid = all(rule["ok"] for rule in evaluation["rules"])
                st.metric("Total (sin impuestos)" if valid else "Total solicitado (no válido)", usd(evaluation["total"]),
                          help=f"Aprueba: {evaluation['approval_level']}")
                st.markdown("**Validaciones (motor determinístico)**")
                for rule in evaluation["rules"]:
                    citation = f" · _{rule['citation']}_" if rule.get("citation") else ""
                    st.markdown(md(f"{'✅' if rule['ok'] else '❌'} {rule['reason']}{citation}"))
            alternatives = [a for a in last.get("alternatives", []) if all(r["ok"] for r in a["evaluation"]["rules"])]
            if alternatives:
                st.markdown("**Alternativas válidas (calculadas por Python)**")
                for a in alternatives:
                    lines = ", ".join(f"{l['quantity']} × {l['sku']}" for l in a["evaluation"]["lines"])
                    st.success(md(f"{a['description']}: {lines} — {usd(a['evaluation']['total'])}"))
            if last.get("pending_approval"):
                p = last["pending_approval"]
                st.warning(f"Propuesta **{p['proposal_id']}** pendiente de aprobación del **{p['approval_level']}**. "
                           f"Hash `{p['content_hash'][:12]}…`. Nada se escribe en el ERP hasta aprobar.")
            verification = last.get("verification")
            if verification:
                st.markdown(md("**Verificador de cifras y citas:** " + ("✅ todo respaldado" if verification["ok"] else "⛔ " + "; ".join(verification["issues"]))))
            if last.get("citations"):
                st.caption("Fuentes recuperadas: " + ", ".join(last["citations"]))
            with st.expander("Costo por paso", expanded=False):
                rows = [{"paso": u["step"], "modelo": u["model"], "tokens": u["input_tokens"] + u["output_tokens"],
                         "USD": float(u["cost_usd"] or 0), "ms": u["latency_ms"]} for u in last.get("usage", [])]
                st.dataframe(rows, hide_index=True, use_container_width=True)
                if last.get("trace_id"):
                    st.caption(f"Traza en Langfuse: `{last['trace_id']}`")

with approvals_tab:
    status, pending = api("GET", "/proposals", params={"status": "PENDIENTE"})
    if status != 200:
        st.error(f"Acceso denegado por la API ({status}): {detail(pending)}")
    else:
        # Result of the last decision, shown above the queue so it is always visible
        if "decision_result" in st.session_state:
            code, body = st.session_state.pop("decision_result")
            if code == 200:
                proposal = body["proposal"]
                message = body.get("response") or "; ".join((body.get("execution") or {}).get("reasons") or [])
                st.success(md(f"{proposal['id']}: {proposal['status']} {proposal.get('erp_order_number') or ''} — {message}"))
            else:
                st.error(md(f"La API rechazó la decisión ({code}): {detail(body)}"))
        st.subheader(f"Pendientes de aprobación ({len(pending)})")
        if not pending:
            st.info("No hay propuestas pendientes.")
        for p in reversed(pending):  # newest first
            content = p["content"]
            with st.container(border=True):
                st.markdown(md(f"**{p['id']}** · cliente `{content['customer_code']}` · total **{usd(content['total'])}** · "
                               f"aprueba **{content['approval_level']}** · preparó {p['created_by']}"))
                for line in content["lines"]:
                    st.markdown(md(f"- {line['quantity']} {line['unit']} `{line['sku']}` {line['name']} desde {line['warehouse']} → {usd(line['amount'])}"))
                st.caption(f"Hash de lo que ves: `{p['content_hash']}`")
                comment = st.text_input("Comentario", key=f"c-{p['id']}")
                cols = st.columns(3)
                for col, (decision, label) in zip(cols, [("aprobar", "Aprobar"), ("rechazar", "Rechazar"), ("corregir", "Pedir corrección")]):
                    if col.button(label, key=f"{decision}-{p['id']}", use_container_width=True):
                        code, body = api("POST", f"/proposals/{p['id']}/decision",
                                         json={"decision": decision, "content_hash": p["content_hash"], "comment": comment or None})
                        st.session_state.decision_result = (code, body)
                        st.rerun()

    if DEMO_MODE:
        with st.expander("🧪 Demo: simular otra venta (bajar stock en el ERP)"):
            c1, c2, c3 = st.columns(3)
            sku = c1.text_input("SKU", "CM-001")
            warehouse = c2.selectbox("Bodega", ["GYE", "UIO"])
            quantity = c3.number_input("Nuevo stock", min_value=0, value=100, step=10)
            if st.button("Bajar stock"):
                response = httpx.patch(f"{ERP}/admin/stock", json={"sku": sku, "warehouse": warehouse, "quantity": int(quantity)},
                                       headers={"Authorization": f"Bearer {os.getenv('ERP_ADMIN_TOKEN', '')}"}, timeout=10)
                (st.success if response.status_code == 200 else st.error)(f"ERP respondió {response.status_code}: {response.text}")

with audit_tab:
    status, trail = api("GET", "/audit")
    if status != 200:
        st.error(f"Acceso denegado por la API ({status}): {detail(trail)}")
    else:
        st.subheader("Auditoría de negocio (solo se agregan filas)")
        st.dataframe([{k: row[k] for k in ("at", "actor", "role", "action", "proposal_id", "result", "detail")} for row in reversed(trail)],
                     hide_index=True, use_container_width=True)
        code, escalations = api("GET", "/escalations")
        if code == 200 and escalations:
            st.subheader("Casos escalados a una persona")
            st.dataframe([{k: e[k] for k in ("id", "at", "actor", "question", "status")} for e in escalations], hide_index=True, use_container_width=True)

with costs_tab:
    st.subheader("Panel de costos — Gerente Financiero")
    c1, c2, c3 = st.columns(3)
    monthly = c1.number_input("Solicitudes por mes (supuesto)", min_value=0, value=3000, step=500)
    current_hours = c2.number_input("Tiempo de atención actual (horas)", min_value=0.0, value=48.0, step=4.0)
    approval_minutes = c3.number_input("Tiempo de aprobación humana (minutos)", min_value=0.0, value=30.0, step=5.0)
    status, costs = api("GET", "/costs", params={"monthly_requests": int(monthly)})
    if status != 200:
        st.error(f"Acceso denegado por la API ({status}): {detail(costs)}")
    elif not costs.get("requests"):
        st.info("Todavía no hay solicitudes registradas.")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Costo promedio por solicitud", usd(costs["avg_cost_usd"], 5))
        m2.metric("Costo mensual proyectado", usd(costs["projected_monthly_cost_usd"]), help=f"{int(monthly):,} solicitudes × costo promedio medido".replace(",", "."))
        m3.metric("Licencias por usuario", usd(costs["per_user_licenses_usd"]), help="Pago por uso: sin licencias por asiento")
        agent_minutes = costs["avg_latency_ms"] / 60000
        m4.metric("Tiempo de atención", f"{(agent_minutes + approval_minutes):.0f} min", delta=f"antes {current_hours:.0f} h", delta_color="off")
        st.caption(md(f"Basado en {costs['requests']} solicitudes reales registradas · costo total medido {usd(costs['total_cost_usd'], 4)} · "
                      f"máximo por solicitud {usd(costs['max_cost_usd'], 5)}" + ("" if costs["cost_complete"] else " · ⚠ hay pasos con precio desconocido")))
        st.dataframe([{"tipo": kind, "solicitudes": s["requests"], "costo promedio USD": float(s["avg_cost_usd"]),
                       "tokens promedio": s["avg_tokens"], "latencia promedio (s)": round(s["avg_latency_ms"] / 1000, 1)}
                      for kind, s in costs["by_kind"].items()], hide_index=True, use_container_width=True)
