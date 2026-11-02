"""Demo UI. It only talks to agent-api: permissions are always decided by the API from the token, never here.

Design rules: status first, business language (never internal codes), color always paired with an icon,
one question per page, no emojis (Material Symbols only).
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pandas as pd
import streamlit as st

API = os.getenv("API_URL", "http://agent-api:8000")
ERP = os.getenv("ERP_URL", "http://mock-erp:8001")
DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

st.set_page_config(page_title="Asistente B2B · Grupo Comercial Andes", page_icon=":material/storefront:", layout="wide")

# ---------- vocabulary: what the business reads instead of internal codes ----------
ROLES = {
    "cliente": ("CL-001", "Cliente", "Supermercados La Pradera"),
    "ejecutivo": ("luis", "Ejecutivo de cuenta", "Luis"),
    "supervisor": ("sofia", "Supervisora comercial", "Sofía"),
    "gerente": ("ana", "Gerente comercial", "Ana"),
}
RESPONSE = {  # response_kind -> (label, color, icon)
    "answer": ("Respuesta verificada", "green", "verified"),
    "order_blocked": ("Pedido no procesable: se explica por qué y qué alternativas hay", "red", "block"),
    "no_info": ("Sin información: escalado a una persona", "orange", "support_agent"),
    "clarify": ("Necesita aclaración", "blue", "help"),
    "proposal": ("Propuesta pendiente de aprobación", "orange", "pending_actions"),
    "executed": ("Registrado en el ERP", "green", "task_alt"),
    "blocked": ("Bloqueado", "red", "block"),
    "refused": ("Rechazado por seguridad", "red", "shield"),
    "quote": ("Cotización referencial", "blue", "request_quote"),
    "rejected": ("Propuesta rechazada", "gray", "cancel"),
    "correction": ("Corrección solicitada", "orange", "edit_note"),
    "stopped": ("Detenido de forma segura", "gray", "stop_circle"),
}
STATUS = {
    "PENDIENTE": ("Pendiente", "orange", "schedule"),
    "APROBADA": ("Aprobada", "blue", "thumb_up"),
    "EJECUTADA": ("Registrada en el ERP", "green", "task_alt"),
    "BLOQUEADA": ("Bloqueada al revalidar", "red", "block"),
    "RECHAZADA": ("Rechazada", "gray", "cancel"),
    "EN_CORRECCION": ("En corrección", "orange", "edit_note"),
}
RULES = {
    "CUSTOMER_EXISTS": "Cliente registrado",
    "PRODUCT_ACTIVE": "Producto existente y activo",
    "QUANTITY_VALID": "Cantidad válida",
    "STOCK_AVAILABLE": "Stock en bodega",
    "PRICE_MATCHES_ERP": "Precio de lista oficial",
    "DISCOUNT_WITHIN_TIER": "Descuento dentro de la política",
    "PAYMENT_TERM_WITHIN_TIER": "Plazo de pago permitido",
    "CREDIT_AVAILABLE": "Crédito disponible",
    "APPROVAL_LEVEL": "Nivel de aprobación",
}
STEPS = {
    "classify": "Filtro de entrada", "plan": "Planificación de la búsqueda", "retrieve": "Búsqueda en políticas",
    "extract_order": "Lectura del pedido", "check_answerable": "Verificación de respaldo",
    "draft_answer": "Redacción de la respuesta", "explain_order": "Explicación al cliente",
}
ACTIONS = {
    "create_proposal": "Preparó la propuesta", "decide:aprobar": "Aprobó", "decide:rechazar": "Rechazó",
    "decide:corregir": "Pidió corrección", "execute": "Ejecución en el ERP", "guardrail_input": "Filtro de entrada",
    "guardrail_output": "Verificador de salida", "escalate": "Escaló a una persona", "agent_stopped": "Agente detenido",
}
DENIALS = {
    "LEVEL_INSUFFICIENT": "nivel de aprobación insuficiente", "HASH_MISMATCH": "la propuesta cambió",
    "SELF_APPROVAL": "no puede aprobar lo que preparó", "NOT_AN_APPROVER": "rol sin permiso para aprobar",
    "INVALID_STATE": "la propuesta ya fue decidida", "NOT_APPROVED": "sin aprobación vigente",
    "ROLE_NOT_ALLOWED": "rol sin permiso", "INVALID_PROPOSAL": "no pasa las validaciones",
}
RESULTS = {
    "ok": ("Correcto", "green"), "executed": ("Escrito en el ERP", "green"), "blocked": ("Bloqueado", "red"),
    "suspicious": ("Bloqueado por seguridad", "red"), "already_executed": ("Ya estaba ejecutado", "gray"),
    "erp_unavailable": ("ERP no disponible", "orange"),
}
SHORT = {  # chart and table labels
    "answer": "Respuesta", "no_info": "Escalada", "clarify": "Aclaración", "proposal": "Propuesta", "executed": "Ejecutada",
    "blocked": "Bloqueada", "refused": "Rechazada (seguridad)", "quote": "Cotización", "stopped": "Detenida",
}
COLORS = {"green": "#1E7A4F", "red": "#B42318", "orange": "#B54708", "blue": "#0F5B78", "gray": "#5F6B73"}
ECUADOR = timezone(timedelta(hours=-5))  # Ecuador has no daylight saving time

# Pages shown per role (the API still enforces every permission)
SECTION_TITLES = {"conversation": "Conversación", "approvals": "Aprobaciones", "audit": "Auditoría", "costs": "Costos"}
VISIBLE = {
    "cliente": ["conversation"],
    "ejecutivo": ["conversation", "approvals"],
    "supervisor": ["conversation", "approvals", "audit", "costs"],
    "gerente": ["conversation", "approvals", "audit", "costs"],
}


# ---------- helpers ----------
def usd(value, decimals: int = 2) -> str:
    text = f"{Decimal(str(value)):,.{decimals}f}"
    return "$" + text.replace(",", "_").replace(".", ",").replace("_", ".")


def md(text) -> str:
    return str(text).replace("$", "\\$")  # Streamlit renders $...$ as LaTeX


def badge(label: str, color: str, icon: str) -> str:
    return f":{color}-badge[:material/{icon}: {label}]"


def pct(value) -> str:
    return f"{Decimal(str(value)).normalize():f} %".replace(".", ",")


def ago(iso: str) -> str:
    minutes = int((datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() // 60)
    if minutes < 1:
        return "hace menos de un minuto"
    if minutes < 60:
        return f"hace {minutes} min"
    return f"hace {minutes // 60} h {minutes % 60} min"


def local_time(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(ECUADOR).strftime("%d/%m %H:%M:%S")


def result_label(result: str) -> tuple[str, str]:
    if result.startswith("denied:"):
        return "Denegado: " + DENIALS.get(result.split(":", 1)[1], result), "red"
    return RESULTS.get(result, (result, "gray"))


def api(method: str, path: str, headers: dict | None = None, **kwargs) -> tuple[int, dict | list]:
    headers = {**(headers or {}), "Authorization": f"Bearer {st.session_state.token}"}
    try:
        response = httpx.request(method, API + path, headers=headers, timeout=180, **kwargs)
    except httpx.HTTPError as e:
        return 503, {"detail": f"El servicio del agente no responde: {e}"}
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {"detail": response.text}


def detail(body) -> str:
    value = body.get("detail", body) if isinstance(body, dict) else body
    return value.get("message", str(value)) if isinstance(value, dict) else str(value)


def page_header(title: str, purpose: str) -> None:
    st.title(title)
    st.caption(purpose)


def denied(status: int, body) -> None:
    st.error(md(f"La API no permite esta vista para tu rol ({status}): {detail(body)}"), icon=":material/lock:")


def styled(rows: list[dict], color_columns: dict[str, dict[str, str]]):
    """Table with status columns colored as text (always next to a readable label, never color alone)."""
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    styler = frame.style
    for column, palette in color_columns.items():
        if column in frame:
            styler = styler.map(lambda v, p=palette: f"color: {COLORS[p.get(v, 'gray')]}; font-weight: 600", subset=[column])
    return styler


# ---------- session and identity ----------
if "messages" not in st.session_state:
    st.session_state.messages, st.session_state.thread_id, st.session_state.last = [], f"ui-{uuid.uuid4().hex[:8]}", None


def get_token(role: str) -> str:
    user, _, _ = ROLES[role]
    response = httpx.post(f"{API}/auth/demo-token", json={"user": user, "role": role}, timeout=10)
    response.raise_for_status()
    return response.json()["token"]


st.logo(os.path.join(os.path.dirname(__file__), "logo.svg"), size="large")

with st.sidebar:
    # The demo role is kept in the URL so a page reload keeps it; it only selects which demo token to request
    requested = st.query_params.get("rol", "cliente")
    role = st.selectbox("Ingresar como", list(ROLES), format_func=lambda r: f"{ROLES[r][1]} · {ROLES[r][2]}", key="role",
                        index=list(ROLES).index(requested) if requested in ROLES else 0)
    st.query_params["rol"] = role
    st.caption("Secciones para tu rol: " + ", ".join(SECTION_TITLES[name] for name in VISIBLE[role])
               + ("." if role in {"supervisor", "gerente"} else ". Cambia a Supervisora o Gerente para ver Aprobaciones, Auditoría y Costos."))
    if st.session_state.get("token_role") != role:
        try:
            st.session_state.token, st.session_state.token_role = get_token(role), role
        except httpx.HTTPError as e:
            st.error(f"No se pudo obtener el token de demo: {e}")
            st.stop()
    st.caption(":material/key: Tu rol viaja en un token firmado. La API decide los permisos; esta pantalla solo los muestra.")
    if st.button("Nueva conversación", icon=":material/add_comment:", use_container_width=True):
        st.session_state.messages, st.session_state.thread_id, st.session_state.last = [], f"ui-{uuid.uuid4().hex[:8]}", None
        st.rerun()
    st.divider()
    st.caption("La IA interpreta y propone · el software valida · una persona aprueba · un servicio separado ejecuta.")


# ---------- page: conversation ----------
def send(message: str | None = None, pdf: tuple[str, bytes] | None = None) -> None:
    if pdf:
        name, data = pdf
        st.session_state.messages.append({"role": "user", "text": f"Orden de compra adjunta: **{name}**"})
        status, body = api("POST", f"/chat/upload?filename={name}&thread_id={st.session_state.thread_id}",
                           content=data, headers={"Content-Type": "application/pdf"})
    else:
        st.session_state.messages.append({"role": "user", "text": message})
        status, body = api("POST", "/chat", json={"message": message, "thread_id": st.session_state.thread_id})
    if status != 200:
        st.session_state.messages.append({"role": "assistant", "text": f"Error {status}: {detail(body)}", "kind": "stopped"})
        return
    st.session_state.last = body
    kind = body["response_kind"]
    rules = (body.get("evaluation") or {}).get("rules") or []
    if kind in {"answer", "blocked"} and body.get("route") == "special_order" and any(not r["ok"] for r in rules):
        kind = "order_blocked"  # one status for the whole screen: the chat and the summary must say the same thing
    usage = body.get("usage") or []
    cached = bool(usage) and all(step.get("cached") for step in usage)
    st.session_state.messages.append({"role": "assistant", "text": body["response"], "kind": kind, "totals": body["totals"], "cached": cached})


def evidence_panel(last: dict) -> None:
    """Answers the question 'how do we know this is right?': the source of every figure in the answer."""
    evaluation = last.get("evaluation") or {}
    sources = last.get("sources") or []
    if not evaluation.get("evidence") and not sources:
        return
    with st.expander("Evidencia: de dónde sale cada dato", icon=":material/plagiarism:"):
        if evaluation.get("evidence"):
            st.markdown("**Datos leídos del ERP en este momento** (no los escribe la IA)")
            lines = {l["sku"]: l for l in evaluation.get("lines", [])}
            rows = []
            for e in evaluation["evidence"]:
                line = lines.get(e["sku"], {})
                rows.append({"Producto": e["sku"], "Precio de lista (ERP)": usd(e["erp_list_price"]),
                             "Stock en bodega (ERP)": e["erp_stock_available"], "Cantidad pedida": line.get("quantity", "—")})
            st.dataframe(rows, hide_index=True, use_container_width=True)
            if evaluation.get("credit_available") is not None:
                st.caption(md(f"Crédito disponible del cliente (ERP): {usd(evaluation['credit_available'])}"))

            st.markdown("**Cómo se calculó** (motor de reglas en Python)")
            for e in evaluation["evidence"]:
                line = lines.get(e["sku"])
                if not line:
                    continue
                total_policy = Decimal(e["volume_discount_pct"]) + Decimal(e["tier_bonus_pct"])
                capped = " → se aplica el tope del nivel" if total_policy > Decimal(e["tier_max_pct"]) else ""
                st.markdown(md(
                    f"- **{e['sku']}** · descuento: {pct(e['volume_discount_pct'])} por volumen (POL-COM-01 §2.1) + "
                    f"{pct(e['tier_bonus_pct'])} por nivel {evaluation.get('customer_tier')} (§3.1) = {pct(total_policy)}, "
                    f"tope del nivel {pct(e['tier_max_pct'])} (§1.2){capped} → **{pct(e['applied_discount_pct'])}**  \n"
                    f"  subtotal: {line['quantity']} × {usd(line['unit_price'])} × (1 − {pct(line['discount_pct'])}) = **{usd(line['amount'])}**"))
            st.markdown(md(f"- **Total:** suma de subtotales = **{usd(evaluation['total'])}**"))

        if sources:
            st.markdown("**Fragmentos de política que la respuesta pudo usar** (cualquier otra cita se bloquea)")
            for source in sources:
                with st.container(border=True):
                    st.caption(f"{source['citation']} · similitud {source['score']:.2f}")
                    st.markdown(md(source["text"]))


def order_summary(last: dict) -> None:
    kind = last["response_kind"]
    evaluation = last.get("evaluation") or {}
    rules = evaluation.get("rules") or []
    failed = [r for r in rules if not r["ok"]]

    # Status first
    if last.get("pending_approval"):
        p = last["pending_approval"]
        st.markdown(badge(f"Esperando aprobación del {p['approval_level']}", "orange", "pending_actions"))
        st.caption(f"Propuesta {p['proposal_id']}. Nada se escribe en el ERP hasta que se apruebe.")
    elif failed:
        st.markdown(badge(f"Bloqueado: {len(failed)} regla{'s' if len(failed) > 1 else ''} no se cumple{'n' if len(failed) > 1 else ''}", "red", "block"))
    else:
        label, color, icon = RESPONSE.get(kind, (kind, "gray", "info"))
        st.markdown(badge(label, color, icon))

    if rules:
        with st.container(border=True):
            st.markdown(f"**{evaluation.get('customer_name')}** · nivel {evaluation.get('customer_tier')}")
            lines = evaluation.get("lines", [])
            discounts = sorted({pct(l["discount_pct"]) for l in lines})
            st.metric("Total sin impuestos" if not failed else "Total solicitado (no se puede procesar así)", usd(evaluation["total"]))
            c1, c2 = st.columns(2)
            c1.metric("Descuento aplicado", " / ".join(discounts) if discounts else "—")
            c2.metric("Aprueba", evaluation["approval_level"].capitalize())
            if lines:
                st.dataframe(
                    [{"Producto": l["name"], "Cantidad": f"{l['quantity']} {l['unit']}", "Precio": usd(l["unit_price"]),
                      "Desc.": pct(l["discount_pct"]), "Subtotal": usd(l["amount"])} for l in lines],
                    hide_index=True, use_container_width=True,
                )

        st.markdown("**Validaciones** · las calcula el motor de reglas, no la IA")
        for rule in sorted(rules, key=lambda r: r["ok"]):
            icon, color = ("check_circle", "green") if rule["ok"] else ("cancel", "red")
            citation = f" · _{rule['citation']}_" if rule.get("citation") else ""
            st.markdown(md(f":{color}[:material/{icon}:] **{RULES.get(rule['rule'], rule['rule'])}**: {rule['reason']}{citation}"))

    valid_alternatives = [(i, a) for i, a in enumerate(last.get("alternatives", [])) if all(r["ok"] for r in a["evaluation"]["rules"])]
    if valid_alternatives:
        st.markdown("**Alternativas válidas** · calculadas y revalidadas por el motor de reglas")
        for number, (index, alternative) in enumerate(valid_alternatives, start=1):
            with st.container(border=True):
                lines = " + ".join(f"{l['quantity']} {l['unit']} {l['sku']}" for l in alternative["evaluation"]["lines"])
                st.markdown(md(f":green[:material/alt_route:] **Opción {number}: {alternative['description']}**"))
                st.markdown(md(f"{lines} · total **{usd(alternative['evaluation']['total'])}**"))
                if st.session_state.role == "ejecutivo" and st.button("Preparar propuesta con esta opción", key=f"alt-{index}",
                                                                      icon=":material/note_add:"):
                    code, body = api("POST", "/alternatives/accept", json={"thread_id": last["thread_id"], "index": index})
                    if code == 200:
                        p = body["proposal"]
                        text = (f"Preparé la propuesta {p['id']} con la opción {number} por {usd(p['content']['total'])} (sin impuestos). "
                                f"Queda PENDIENTE de aprobación del {p['content']['approval_level']}; no se escribe nada en el ERP hasta que se apruebe.")
                        st.session_state.messages.append({"role": "assistant", "text": text, "kind": "proposal"})
                        st.session_state.last = {**last, "response_kind": "proposal", "evaluation": body["evaluation"], "alternatives": [],
                                                 "pending_approval": {"proposal_id": p["id"], "content_hash": p["content_hash"],
                                                                      "approval_level": p["content"]["approval_level"]}}
                    else:
                        st.session_state.messages.append({"role": "assistant", "text": f"No se pudo preparar la propuesta: {detail(body)}", "kind": "blocked"})
                    st.rerun()

    evidence_panel(last)

    verification = last.get("verification")
    if verification:
        if verification["ok"]:
            st.markdown(":green[:material/fact_check:] **Verificador:** cada cifra y cita de la respuesta está respaldada.")
        else:
            st.markdown(md(":red[:material/gpp_bad:] **Verificador:** la respuesta generada se bloqueó. " + "; ".join(verification["issues"])))
    if last.get("citations"):
        st.caption("Fuentes consultadas: " + ", ".join(last["citations"]))

    with st.expander("Cómo se procesó", icon=":material/account_tree:"):
        usage = last.get("usage", [])
        if usage:
            st.dataframe(
                [{"Paso": STEPS.get(u["step"], u["step"]), "Modelo": u["model"], "Tokens": u["input_tokens"] + u["output_tokens"],
                  "Costo (USD)": f"{Decimal(u['cost_usd'] or 0):.6f}", "Tiempo": f"{u['latency_ms'] / 1000:.1f} s" if u["latency_ms"] else "—"}
                 for u in usage],
                hide_index=True, use_container_width=True,
            )
        totals = last.get("totals", {})
        st.caption(md(f"Total: {usd(totals.get('cost_usd', 0), 5)} · {totals.get('tokens', 0)} tokens · {totals.get('latency_ms', 0) / 1000:.1f} s"
                      + (f" · traza en Langfuse {last['trace_id'][:12]}…" if last.get("trace_id") else "")))


def page_conversation() -> None:
    page_header("Conversación", "Consultas y pedidos especiales. A la derecha, el resumen verificado de cada respuesta.")
    left, right = st.columns([1, 1], gap="large")
    with left:
        if not st.session_state.messages:
            st.info("Escribe una consulta sobre políticas o un pedido especial, o adjunta una orden de compra en PDF.", icon=":material/chat:")
        for m in st.session_state.messages:
            with st.chat_message(m["role"], avatar=":material/person:" if m["role"] == "user" else ":material/smart_toy:"):
                if m.get("kind"):
                    label, color, icon = RESPONSE.get(m["kind"], (m["kind"], "gray", "info"))
                    st.markdown(badge(label, color, icon))
                st.markdown(md(m["text"]))
                if m.get("cached"):
                    st.caption(":material/cached: Respuesta desde la caché: misma pregunta con los mismos datos, sin llamar al modelo y sin costo.")
                elif m.get("totals"):
                    t = m["totals"]
                    st.caption(md(f"{usd(t['cost_usd'], 5)} · {t['tokens']} tokens · {t['latency_ms'] / 1000:.1f} s"))
        with st.popover("Adjuntar orden de compra (PDF)", icon=":material/attach_file:"):
            uploaded = st.file_uploader("Orden de compra", type=["pdf"], key=f"pdf-{len(st.session_state.messages)}", label_visibility="collapsed")
            if uploaded and st.button("Enviar orden de compra", icon=":material/send:"):
                with st.spinner("Leyendo la orden de compra…"):
                    send(pdf=(uploaded.name, uploaded.getvalue()))
                st.rerun()
        if prompt := st.chat_input("Escribe una consulta o un pedido especial"):
            with st.spinner("El agente está trabajando…"):
                send(prompt)
            st.rerun()
    with right:
        st.subheader("Resumen")
        if st.session_state.last:
            order_summary(st.session_state.last)
        else:
            st.caption("Aquí verás el estado, las validaciones, las alternativas y el costo de cada respuesta.")


# ---------- page: approvals ----------
def page_approvals() -> None:
    page_header("Aprobaciones", "Aquí el supervisor o la gerente deciden. Nada llega al ERP sin esta aprobación.")
    status, proposals = api("GET", "/proposals")
    if status != 200:
        return denied(status, proposals)

    if "decision_result" in st.session_state:
        code, body = st.session_state.pop("decision_result")
        if code == 200:
            proposal = body["proposal"]
            label, color, icon = STATUS[proposal["status"]]
            reasons = (body.get("execution") or {}).get("reasons") or []
            message = body.get("response") or "; ".join(reasons) or label
            (st.error if proposal["status"] == "BLOQUEADA" else st.success)(md(f"**{proposal['id']}:** {message}"), icon=f":material/{icon}:")
        else:
            st.error(md(f"La API rechazó la decisión: {detail(body)}"), icon=":material/block:")

    pending = [p for p in reversed(proposals) if p["status"] == "PENDIENTE"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Pendientes", len(pending))
    c2.metric("Monto pendiente", usd(sum(Decimal(p["content"]["total"]) for p in pending)))
    c3.metric("Requieren gerente", sum(p["content"]["approval_level"] == "gerente" for p in pending))

    role = st.session_state.role
    can_decide = role in {"supervisor", "gerente"}
    if not can_decide:
        st.info("Puedes ver la cola, pero solo la supervisora o la gerente pueden decidir.", icon=":material/visibility:")

    if not pending:
        st.caption("No hay propuestas pendientes.")
    for p in pending:
        content = p["content"]
        with st.container(border=True):
            top_left, top_right = st.columns([3, 1])
            with top_left:
                st.markdown(badge("Pendiente", "orange", "schedule") + " " + badge(f"Aprueba: {content['approval_level']}", "gray", "verified_user"))
                st.markdown(f"#### {p['customer_name']}")
                st.caption(f"{p['id']} · preparó {p['created_by']} · {ago(p['created_at'])}"
                           + (f" · pago a {content['payment_days']} días" if content.get("payment_days") else ""))
            top_right.metric("Total sin impuestos", usd(content["total"]))
            st.dataframe(
                [{"Producto": f"{l['sku']} · {l['name']}", "Cantidad": f"{l['quantity']} {l['unit']}", "Bodega": l["warehouse"],
                  "Desc.": pct(l["discount_pct"]), "Subtotal": usd(l["amount"])} for l in content["lines"]],
                hide_index=True, use_container_width=True,
            )
            st.caption(f"Huella de lo que estás viendo: {p['content_hash'][:16]}…  Si la propuesta cambia, la aprobación deja de valer.")
            if can_decide:
                if content["approval_level"] == "gerente" and role != "gerente":
                    st.warning("Este monto requiere a la gerente. Puedes rechazar o pedir corrección; si apruebas, la API lo bloqueará.", icon=":material/info:")
                comment = st.text_input("Comentario (opcional)", key=f"c-{p['id']}")
                b1, b2, b3 = st.columns(3)
                buttons = [(b1, "aprobar", "Aprobar", ":material/check:", "primary"), (b2, "rechazar", "Rechazar", ":material/close:", "secondary"),
                           (b3, "corregir", "Pedir corrección", ":material/edit_note:", "secondary")]
                for column, decision, label, icon, kind in buttons:
                    if column.button(label, key=f"{decision}-{p['id']}", icon=icon, type=kind, use_container_width=True):
                        code, body = api("POST", f"/proposals/{p['id']}/decision",
                                         json={"decision": decision, "content_hash": p["content_hash"], "comment": comment or None})
                        st.session_state.decision_result = (code, body)
                        st.rerun()

    decided = [p for p in reversed(proposals) if p["status"] != "PENDIENTE"]
    if decided:
        st.subheader("Historial")
        st.dataframe(
            styled([{"Estado": STATUS[p["status"]][0], "Propuesta": p["id"], "Cliente": p["customer_name"], "Total": usd(p["content"]["total"]),
                     "Pedido en el ERP": p.get("erp_order_number") or "—", "ERP": (p.get("erp_adapter") or "—").capitalize()} for p in decided],
                   {"Estado": {label: color for label, color, _ in STATUS.values()}}),
            hide_index=True, use_container_width=True,
        )

    if DEMO_MODE and can_decide:
        with st.expander("Herramienta de demo: simular otra venta (baja el stock en el ERP)", icon=":material/science:"):
            c1, c2, c3 = st.columns(3)
            sku = c1.text_input("Producto (SKU)", "CM-001")
            warehouse = c2.selectbox("Bodega", ["GYE", "UIO"], format_func=lambda w: {"GYE": "Guayaquil", "UIO": "Quito"}[w])
            quantity = c3.number_input("Nuevo stock", min_value=0, value=100, step=10)
            if st.button("Aplicar en el ERP", icon=":material/inventory:"):
                response = httpx.patch(f"{ERP}/admin/stock", json={"sku": sku, "warehouse": warehouse, "quantity": int(quantity)},
                                       headers={"Authorization": f"Bearer {os.getenv('ERP_ADMIN_TOKEN', '')}"}, timeout=10)
                if response.status_code == 200:
                    st.success(f"Stock de {sku} en {warehouse}: {int(quantity)}", icon=":material/inventory:")
                else:
                    st.error(f"El ERP respondió {response.status_code}: {response.text}")


# ---------- page: audit ----------
def page_audit() -> None:
    page_header("Auditoría", "Quién hizo qué y cuándo. El registro solo admite filas nuevas: no se puede editar ni borrar.")
    status, trail = api("GET", "/audit")
    if status != 200:
        return denied(status, trail)
    rows = []
    for row in reversed(trail):
        label, color = result_label(row["result"])
        rows.append({"Cuándo (hora de Ecuador)": local_time(row["at"]), "Quién": f"{row['actor']} ({row['role']})",
                     "Acción": ACTIONS.get(row["action"], row["action"]), "Resultado": label,
                     "Propuesta": row["proposal_id"] or "—", "Detalle": row["detail"] or ""})
    c1, c2 = st.columns(2)
    actions = c1.multiselect("Acción", sorted({r["Acción"] for r in rows}), placeholder="Todas las acciones")
    outcomes = c2.multiselect("Resultado", sorted({r["Resultado"] for r in rows}), placeholder="Todos los resultados")
    rows = [r for r in rows if (not actions or r["Acción"] in actions) and (not outcomes or r["Resultado"] in outcomes)]
    palette = {result_label(r)[0]: result_label(r)[1] for r in {row["result"] for row in trail}}
    if rows:
        st.dataframe(styled(rows, {"Resultado": palette}), hide_index=True, use_container_width=True, height=420)
    else:
        st.caption("Sin eventos para estos filtros.")

    code, escalations = api("GET", "/escalations")
    st.subheader("Consultas escaladas a una persona")
    if code == 200 and escalations:
        st.dataframe([{"Caso": f"ESC-{e['id']:06d}", "Cuándo (hora de Ecuador)": local_time(e["at"]), "Quién": e["actor"],
                       "Pregunta": e["question"], "Estado": e["status"].capitalize()} for e in escalations],
                     hide_index=True, use_container_width=True)
    else:
        st.caption("No hay consultas escaladas.")


# ---------- page: costs ----------
def page_costs() -> None:
    page_header("Costos", "Para el Gerente Financiero: cuánto cuesta cada solicitud, cuánto costaría al mes y cuánto tiempo ahorra.")
    with st.container(border=True):
        st.markdown(":material/tune: **Supuestos** · edítalos para ver distintos escenarios")
        c1, c2, c3 = st.columns(3)
        monthly = c1.number_input("Solicitudes por mes", min_value=0, value=3000, step=500)
        current_hours = c2.number_input("Tiempo de atención actual (horas)", min_value=0.0, value=48.0, step=4.0)
        approval_minutes = c3.number_input("Tiempo de aprobación humana (minutos)", min_value=0.0, value=30.0, step=5.0)

    status, costs = api("GET", "/costs", params={"monthly_requests": int(monthly)})
    if status != 200:
        return denied(status, costs)
    if not costs.get("requests"):
        st.info("Todavía no hay solicitudes registradas. Usa la conversación y vuelve aquí.", icon=":material/hourglass_empty:")
        return

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Costo por solicitud", usd(costs["avg_cost_usd"], 5), help="Promedio medido en solicitudes reales")
    m2.metric("Costo mensual proyectado", usd(costs["projected_monthly_cost_usd"]), help="Solicitudes por mes × costo promedio medido")
    m3.metric("Licencias por usuario", usd(costs["per_user_licenses_usd"]), help="Pago por uso: no hay licencias por asiento")
    attention_minutes = costs["avg_latency_ms"] / 60000 + approval_minutes
    m4.metric("Tiempo de atención", f"{attention_minutes:.0f} min", delta=f"antes {current_hours:.0f} h", delta_color="off")

    labels = {kind: SHORT.get(kind, kind) for kind in costs["by_kind"]}
    chart = pd.DataFrame({"Tipo de solicitud": [labels[k] for k in costs["by_kind"]],
                          "Costo promedio (USD)": [float(s["avg_cost_usd"]) for s in costs["by_kind"].values()]}).set_index("Tipo de solicitud")
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("**Costo promedio por tipo de solicitud (USD)**")
        st.bar_chart(chart, horizontal=True, color="#0F5B78")
    with right:
        st.markdown("**Detalle**")
        st.dataframe([{"Tipo": labels[kind], "Solicitudes": s["requests"], "Tokens prom.": s["avg_tokens"],
                       "Tiempo prom.": f"{s['avg_latency_ms'] / 1000:.1f} s"} for kind, s in costs["by_kind"].items()],
                     hide_index=True, use_container_width=True)
    st.subheader("Cómo ahorramos tokens")
    st.caption("Cada estrategia está implementada y medida; las cifras salen de las solicitudes registradas.")
    by_kind = costs["by_kind"]

    def avg(kind: str) -> str:
        return usd(by_kind[kind]["avg_cost_usd"], 5) if kind in by_kind else "sin datos aún"

    small_share = f"{costs.get('small_model_share', 0) * 100:.0f} %"
    strategies = [
        ("Modelo según la tarea", "El modelo pequeño clasifica, extrae y verifica; el principal solo redacta.",
         f"{small_share} de las llamadas usan el modelo pequeño (4 veces más barato)"),
        ("Python calcula", "Precios, descuentos, totales, stock, crédito y alternativas no usan la IA.", "0 tokens en la parte más delicada"),
        ("Rechazo temprano", "Un ataque o una consulta fuera de tema se detiene con una sola llamada pequeña.", f"{avg('refused')} por rechazo"),
        ("Respuestas sin redacción de IA", "Propuestas, cotizaciones y rechazos se arman con plantillas.", f"{avg('proposal')} por propuesta"),
        ("Solo lo relevante", "Se envían 4 a 6 fragmentos de política, no los documentos completos.", "Menos tokens de entrada"),
        ("Sin razonamiento oculto", "Se desactivan los tokens de pensamiento interno del modelo.", "No se pagan tokens invisibles"),
        ("Topes por solicitud", "Máximo 12.000 tokens por solicitud y 800 de salida por llamada.", "Peor caso ≈ $0,05 por solicitud"),
        ("Índice de políticas una sola vez", "Los documentos se vectorizan al cargarlos, no en cada consulta.", "2.592 tokens en total (< $0,0001)"),
        ("Caché de respuestas", "La misma pregunta con los mismos datos no se vuelve a pagar.", f"{costs.get('cached_calls', 0)} llamadas evitadas"),
    ]
    st.dataframe([{"Estrategia": s, "Qué hace": what, "Efecto medido": effect} for s, what, effect in strategies],
                 hide_index=True, use_container_width=True)
    if costs.get("by_model"):
        with st.expander("Consumo por modelo", icon=":material/memory:"):
            st.dataframe([{"Modelo": model, "Tipo": "Embeddings" if v["kind"] == "embedding" else "Lenguaje", "Llamadas": v["calls"],
                           "Desde caché": v["cached_calls"], "Tokens": v["tokens"], "Costo (USD)": f"{Decimal(v['cost_usd']):.6f}"}
                          for model, v in costs["by_model"].items()], hide_index=True, use_container_width=True)

    st.caption(md(f"Basado en {costs['requests']} solicitudes reales · costo total medido {usd(costs['total_cost_usd'], 4)} · "
                  f"máximo por solicitud {usd(costs['max_cost_usd'], 5)} · las cifras coinciden con las trazas de Langfuse"
                  + ("" if costs["cost_complete"] else " · hay pasos con precio desconocido")))


# ---------- navigation by role (the API still enforces every permission) ----------
PAGES = {
    "conversation": st.Page(page_conversation, title="Conversación", icon=":material/forum:", url_path="conversacion", default=True),
    "approvals": st.Page(page_approvals, title="Aprobaciones", icon=":material/fact_check:", url_path="aprobaciones"),
    "audit": st.Page(page_audit, title="Auditoría", icon=":material/history:", url_path="auditoria"),
    "costs": st.Page(page_costs, title="Costos", icon=":material/payments:", url_path="costos"),
}
st.navigation([PAGES[name] for name in VISIBLE[st.session_state.role]], position="top").run()
