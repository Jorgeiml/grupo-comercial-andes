# Grupo Comercial Andes — Asistente B2B con IA gobernada

Caso práctico SEIDOR (IA Engineer). Asistente para **consultas complejas** y **pedidos especiales** de clientes B2B.

**Principio rector:** la IA interpreta y propone → el software valida → una persona aprueba → un servicio separado ejecuta.

## Servicios

| Servicio | Carpeta | Rol |
|---|---|---|
| ui | `services/ui` | Interfaz Streamlit: chat, aprobaciones, auditoría y costos |
| agent-api | `services/agent_api` | Agente LangGraph, validación, aprobaciones y ejecución |
| mock-erp | `services/mock_erp` | ERP simulado (FastAPI + SQLite) |

## Puesta en marcha

1. Copia `.env.example` a `.env`, pega tu `OPENAI_API_KEY` y completa los tokens (`ERP_*_TOKEN`, `JWT_SECRET`) con valores aleatorios.
2. Levanta los servicios: `docker compose up -d --build`
3. Indexa las políticas (una sola vez, o cuando cambien): `docker compose run --rm agent-api python -m app.rag.ingest`
4. Para dejar la demo en cero: `docker compose down -v` (borra la base del ERP y el índice).

## Interfaz

`http://localhost:8501` (Streamlit): selector de rol, chat con carga de PDF, panel del pedido (reglas, alternativas, costo por paso), cola de aprobación, auditoría y panel de costos. Antes de un ensayo o de la presentación: `powershell -ExecutionPolicy Bypass -File scripts/reset_demo.ps1` (ERP con datos iniciales, cola y auditoría vacías; conserva el índice de políticas).

## Guion de la presentación

[docs/guion-demo.md](docs/guion-demo.md) (paso a paso, ≈ 6 min) y [docs/verificacion-demo.md](docs/verificacion-demo.md) (cada cifra recalculada a mano). Ensayo por consola: `python scripts/rehearse_demo.py`. Con `LLM_CACHE_ENABLED=true`, la misma historia se repite idéntica tras `scripts/reset_demo.ps1`, incluso sin internet.

## Escenarios de la demo

Con los servicios levantados: `python scripts/demo_scenarios.py` — corre los 5 escenarios contra la API (incluido el cambio en vivo a SAP), imprime cada respuesta con su costo y termina con un resumen esperado vs. obtenido. Deja el ERP en cero al empezar.

## Pruebas

`docker compose run --rm tests` — corre la suite pytest en un contenedor, con el ERP simulado en memoria (no necesita secretos ni servicios levantados).

## Evaluación (LLM real)

`docker compose run --rm eval` — 28 casos (políticas, pedidos, ataques y flujo de aprobación) con el LLM y el RAG reales; el ERP simulado se reinicia en cada caso. Genera `reports/eval-report.html` y `reports/eval-results.json`. Costo aproximado: USD 0,02 por corrida. La última corrida está en [docs/eval-report.html](docs/eval-report.html).

## Estructura

- `data/seed/` — datos ficticios de productos y clientes.
- `data/policies/` — documentos de políticas para el RAG.
- `tests/` — suite de evaluación (pytest).
