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

## Estructura

- `data/seed/` — datos ficticios de productos y clientes.
- `data/policies/` — documentos de políticas para el RAG.
- `tests/` — suite de evaluación (pytest).
