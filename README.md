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

1. Copia `.env.example` a `.env` y completa los valores.
2. `docker compose up --build` _(disponible a partir del paso de Docker Compose)_.

## Estructura

- `data/seed/` — datos ficticios de productos y clientes.
- `data/policies/` — documentos de políticas para el RAG.
- `tests/` — suite de evaluación (pytest).
