"""Builds the evaluation report (single self-contained HTML), ready for a screenshot in the presentation."""

import html
from decimal import Decimal
from pathlib import Path

GROUPS = ["políticas", "pedidos", "ataques", "flujo"]


def metrics(results: list[dict]) -> dict:
    blocks = [r for r in results if r["should_block"]]
    policy_answers = [r for r in results if r["group"] == "políticas" and r["response_kind"] == "answer"]
    costs = [Decimal(r["cost_usd"]) for r in results]
    return {
        "total": len(results),
        "passed": sum(r["passed"] for r in results),
        "invalid_erp_orders": sum(len(r["invalid_erp_orders"]) for r in results),
        "blocks_ok": sum(r["passed"] for r in blocks),
        "blocks": len(blocks),
        "cited": sum(r["has_citation"] for r in policy_answers),
        "policy_answers": len(policy_answers),
        "avg_cost": sum(costs) / len(costs),
        "total_cost": sum(costs),
        "avg_latency_s": sum(r["latency_ms"] for r in results) / len(results) / 1000,
    }


def _usd(value: Decimal) -> str:
    return f"USD {value:.4f}".replace(".", ",")


def write_report(results: list[dict], path: Path, generated_at: str) -> Path:
    m = metrics(results)
    by_group = []
    for group in GROUPS:
        items = [r for r in results if r["group"] == group]
        if items:
            cost = sum(Decimal(r["cost_usd"]) for r in items) / len(items)
            latency = sum(r["latency_ms"] for r in items) / len(items) / 1000
            by_group.append(f"<tr><td>{group}</td><td>{sum(r['passed'] for r in items)}/{len(items)}</td>"
                            f"<td>{_usd(cost)}</td><td>{latency:.1f} s</td></tr>")

    rows = []
    for r in results:
        status = '<span class="ok">✔ pasa</span>' if r["passed"] else '<span class="bad">✘ falla</span>'
        detail = html.escape(r["failure"] or r["response"][:220])
        rows.append(
            f"<tr><td class='id'>{r['id']}</td><td>{html.escape(r['description'])}</td><td>{status}</td>"
            f"<td><code>{html.escape(r['response_kind'])}</code></td><td class='num'>{len(r['invalid_erp_orders'])}</td>"
            f"<td class='num'>{_usd(Decimal(r['cost_usd']))}</td><td class='num'>{r['latency_ms'] / 1000:.1f} s</td>"
            f"<td class='detail'>{detail}</td></tr>"
        )

    integrity_class = "good" if m["invalid_erp_orders"] == 0 else "alert"
    page = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Evaluación del agente</title>
<style>
:root {{ --bg:#f7f7f5; --card:#fff; --ink:#1d2327; --muted:#5f6b73; --line:#e3e3df; --good:#1e7a4f; --bad:#b42318; --accent:#1f4e79; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16191c; --card:#1f2327; --ink:#e8eaec; --muted:#9aa4ab; --line:#30363b; --good:#4cc38a; --bad:#ff7b72; --accent:#7fb2e5; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:1200px; margin:0 auto; padding:32px 16px 48px; }}
h1 {{ margin:0 0 4px; font-size:26px; }} .sub {{ color:var(--muted); margin:0 0 24px; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin-bottom:24px; }}
.kpi {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }}
.kpi .v {{ font-size:32px; font-weight:700; font-variant-numeric:tabular-nums; }} .kpi .l {{ color:var(--muted); font-size:13px; }}
.kpi.good .v {{ color:var(--good); }} .kpi.alert .v {{ color:var(--bad); }}
section {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:16px; overflow-x:auto; }}
h2 {{ font-size:16px; margin:0 0 12px; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th, td {{ text-align:left; padding:8px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--muted); font-weight:600; }} .num {{ text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }}
.id {{ font-weight:700; color:var(--accent); white-space:nowrap; }} .detail {{ color:var(--muted); max-width:420px; }}
.ok {{ color:var(--good); font-weight:600; white-space:nowrap; }} .bad {{ color:var(--bad); font-weight:600; white-space:nowrap; }}
code {{ font-size:12px; }}
</style></head><body><main>
<h1>Evaluación del asistente B2B — Grupo Comercial Andes</h1>
<p class="sub">{m['total']} casos con LLM y RAG reales · ERP simulado reiniciado en cada caso · {html.escape(generated_at)}</p>
<div class="kpis">
  <div class="kpi {integrity_class}"><div class="v">{m['invalid_erp_orders']}</div><div class="l">pedidos inválidos que llegaron al ERP (meta: 0)</div></div>
  <div class="kpi"><div class="v">{m['blocks_ok']}/{m['blocks']}</div><div class="l">bloqueos correctos</div></div>
  <div class="kpi"><div class="v">{m['cited']}/{m['policy_answers']}</div><div class="l">respuestas de política con cita</div></div>
  <div class="kpi"><div class="v">{m['passed']}/{m['total']}</div><div class="l">casos como se esperaba</div></div>
  <div class="kpi"><div class="v">{_usd(m['avg_cost'])}</div><div class="l">costo promedio por solicitud · total {_usd(m['total_cost'])}</div></div>
  <div class="kpi"><div class="v">{m['avg_latency_s']:.1f} s</div><div class="l">latencia promedio</div></div>
</div>
<section><h2>Por grupo</h2><table><thead><tr><th>Grupo</th><th>Casos</th><th>Costo promedio</th><th>Latencia promedio</th></tr></thead>
<tbody>{''.join(by_group)}</tbody></table></section>
<section><h2>Casos</h2><table><thead><tr><th>ID</th><th>Caso</th><th>Resultado</th><th>Tipo</th><th class="num">Pedidos inválidos</th>
<th class="num">Costo</th><th class="num">Latencia</th><th>Respuesta / motivo</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></section>
</main></body></html>"""
    path.write_text(page, encoding="utf-8")
    return path
