# Registro de decisiones técnicas

Decisiones tomadas durante la construcción, con su motivo. Complementa el CLAUDE.md.

## Día 1

| # | Decisión | Motivo |
|---|---|---|
| 1 | Los servicios corren en `python:3.12-slim` (Docker), no en el Python local | Demo reproducible e independiente de la máquina; 3.12 tiene soporte completo de todas las librerías |
| 2 | 18 SKUs: los 15 del caso + 3 sustitutos homologados de marca "Cumbre" | Un sustituto debe ser un producto equivalente; con un solo producto por tipo no hay sustituto posible |
| 3 | Precios sin IVA; el umbral de aprobación de USD 5.000 se aplica al total con descuento | El IVA no responde a los dolores del caso; el total con descuento es el monto real comprometido |
| 4 | Dinero en centavos enteros (ERP) y `Decimal` (agente), nunca `float` | Cifras exactas, sin errores de redondeo |
| 5 | Tres credenciales de servicio en el ERP: `read`, `write`, `admin` | El agente solo recibe `read`: el ERP impone que no pueda escribir |
| 6 | Idempotencia con un campo real del ERP (`CustomersOrderReference` / `PurchaseOrderByCustomer`) + restricción UNIQUE | Aprobar dos veces nunca duplica, aunque falle otro control; misma referencia con distinto contenido → 409 |
| 7 | El ERP revalida todas las reglas al escribir, en una transacción | Defensa en profundidad: ninguna escritura inválida llega a la base aunque fallen los controles previos |
| 8 | Registro de escrituras en el ERP, separado de la auditoría de negocio | Evidencia técnica de cada intento (incluidos los rechazados); quién aprobó vive en agent-api |
| 9 | Adaptadores en el cliente (agent-api); el mock expone ambos estilos sobre una sola base | Igual que en producción: cambiar de ERP es cambiar el adaptador, no el sistema |
| 10 | Fragmentos RAG por sección numerada; la cita sale de los metadatos | Citas exactas y verificables; ninguna regla queda partida |

## Calibración del umbral RAG (2026-11-02)

Puntaje (similitud coseno) del mejor fragmento, con `text-embedding-3-small`:

| Grupo | Consulta | Puntaje |
|---|---|---|
| Con respuesta | Plazo de pago clientes Oro | 0,666 |
| Con respuesta | ¿Qué pasa si no hay stock? | 0,595 |
| Con respuesta | Consulta compleja La Pradera | 0,575 |
| Con respuesta | Descuento por 500 cajas | 0,568 |
| Con respuesta | Garantía de una laptop | 0,545 |
| Con respuesta | Devolver una licuadora sin uso | 0,420 |
| Sin respuesta | Envío a Galápagos | 0,468 |
| Sin respuesta | Criptomonedas | 0,420 |
| Sin respuesta | Horario de atención | 0,416 |
| Sin respuesta | Programa de puntos | 0,379 |
| Sin respuesta | Gerente general | 0,273 |

**Hallazgo:** los grupos se superponen. La similitud mide "habla del tema", no "contiene la respuesta" (Galápagos se parece a la tabla de entregas).

**Decisión:** control en dos capas.
1. Umbral `RAG_MIN_SCORE=0.40`: descarta lo claramente ajeno sin gastar en el LLM.
2. Verificación de respuesta con `MODEL_SMALL` (salida estructurada): ¿los fragmentos recuperados contienen la respuesta? ¿qué citas la respaldan? Si no → "no tengo esa información" y escalamiento. Una cita que no esté entre los fragmentos recuperados bloquea la respuesta.

## Día 2

### Selección de modelos (2026-11-02)

El CLAUDE.md exige temperatura 0 (respuestas repetibles para la suite de evaluación y la demo). Probado con una llamada real: extracción estructurada (`json_schema`, `strict=True`) de un pedido corto.

| Modelo | Entrada / salida (USD por 1M tokens) | ¿Acepta temperatura 0? |
|---|---|---|
| gpt-6-luna | 0,10 / 0,50 | ❌ solo temperatura 1 |
| gpt-6.1-terra | 1,00 / 5,00 | ❌ solo temperatura 1 |
| **gpt-5.6-luna** | 0,20 / 1,20 | ✅ |
| gpt-5.4-nano | 0,20 / 1,25 | ✅ |
| **gpt-5.4-mini** | 0,75 / 4,50 | ✅ (además extrajo la unidad) |
| gpt-4.1-mini | 0,40 / 1,60 | ✅ |
| gpt-4.1 | 2,00 / 8,00 | ✅ |
| gpt-4o-mini | 0,15 / 0,60 | ✅ pero completó una unidad que el cliente no dijo |

**Decisión:** `MODEL_SMALL=gpt-5.6-luna`, `MODEL_MAIN=gpt-5.4-mini`. Una extracción consume ~240 tokens (≈ USD 0,0001 con el modelo pequeño).

**Corrección (detectada por las pruebas):** `langchain-openai` elimina en silencio `temperature` en los modelos `gpt-5*` salvo que el razonamiento interno esté desactivado, así que la primera prueba de los `gpt-5.x` no envió temperatura 0. Verificado directo con el SDK de OpenAI: con `reasoning_effort="none"`, `gpt-5.6-luna`, `gpt-5.4-nano` y `gpt-5.4-mini` aceptan temperatura 0 (sin él, `gpt-5.6-luna` la rechaza). La capa de LLM agrega `reasoning_effort="none"` a los `gpt-5*`, y una prueba revisa el **payload real** que se enviaría, no solo la configuración. Desactivar el razonamiento es coherente con el diseño: el razonamiento sobre cifras lo hace Python. Llamada real de verificación: clasificación con ambos modelos, 252 tokens, USD 0,00021.

**Regla derivada:** la extracción deja en blanco lo que el cliente no dijo; Python completa con datos del ERP.

### Versiones del stack del agente

`langgraph==1.2.19`, `langgraph-checkpoint-sqlite==3.1.3`, `langchain-core==1.7.3` (no 1.7.4: publicada el mismo día), `langchain-openai==1.7.3`, `pyjwt==2.15.1`. Verificado: `interrupt()` + `Command(resume=...)` con `SqliteSaver` funciona con estas versiones.

### Motor de validación

- Además de las 7 reglas del CLAUDE.md: `CUSTOMER_EXISTS` (condición para verificar crédito y descuento) y `PAYMENT_TERM_WITHIN_TIER` (POL-FIN-02 §1.1; aprobada por el usuario). El plazo se valida y forma parte del hash de la propuesta, pero no se escribe en el ERP (el mock no tiene ese campo).
- El descuento lo calcula Python (volumen + nivel, con tope en el máximo del ERP). Un descuento solicitado mayor al de la política se rechaza aunque esté bajo el máximo del nivel.
- Todas las reglas corren siempre; las alternativas (parcial, sustituto, combinada) se revalidan completas.

### Grafo del agente (hallazgos de las pruebas reales)

- **Orden del flujo de políticas:** la primera prueba real escaló la consulta compleja porque la verificación de respuesta pedía precio y nivel del cliente, que no están en las políticas. Ahora, si la consulta trae productos y cantidades, los datos del ERP (vía motor) se obtienen **antes** de decidir si es respondible.
- **Quién decide detenerse:** el modelo de extracción marcaba como "ambigüedad" cosas que calcula el sistema (precio, plazo). Python decide: solo un campo estructural faltante (cliente, producto, cantidad, bodega) detiene el pedido; las notas del modelo se muestran como contexto.
- **Presupuesto de tokens:** con 4.000 la consulta compleja (~5.500 tokens, USD 0,0028) se detuvo de forma segura. Subido a 12.000 (el doble de lo medido; peor caso ≈ USD 0,05). Una solicitud detenida reporta lo que alcanzó a gastar.
- **Citas del motor:** las citas que el motor adjunta a sus reglas (p. ej. POL-COM-01 §3.2) cuentan como respaldadas para el verificador, igual que los fragmentos recuperados.

Costos medidos (LLM real): consulta compleja USD 0,0028 · Galápagos USD 0,0005 · pedido especial USD 0,0004 · falta de stock con alternativas USD 0,0019.

## Pendientes para el día 2

- Implementar la verificación de respuesta (capa 2 del RAG).
- Descomponer las consultas complejas en subconsultas antes de buscar: en la consulta de La Pradera, los 3 mejores fragmentos fueron de entregas y no apareció la política de descuentos.
- Rechazos del ERP que ocurren antes de `create_order` (p. ej. cantidad decimal en SAP) no quedan en el registro de escrituras; evaluar si registrarlos.
