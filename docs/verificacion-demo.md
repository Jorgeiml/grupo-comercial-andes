# Verificación a mano de las cifras de la demo

Para responder *"¿cómo sabemos que la información es correcta?"*: cada cifra del guion recalculada **sin el sistema**, solo con los documentos de política (`data/policies/`) y los datos del ERP (`data/seed/`).

## Datos de partida (ERP)

| Dato | Valor | Fuente |
|---|---|---|
| Supermercados La Pradera | nivel **Oro**, descuento máximo **12 %**, crédito disponible **$60.000** (80.000 − 20.000) | `customers.json` |
| CM-001 Arroz Andes 10x2 kg | **$18,50** por fardo · Guayaquil: 1.200 | `products.json` |
| CM-002 Aceite Andes 12x1 L | **$28,00** por caja · Guayaquil: 350 | `products.json` |
| HG-001 Licuadora 1,5 L | **$45,00** · Guayaquil: **40** · sustituto HG-101 | `products.json` |
| HG-101 Licuadora Cumbre 1,5 L | **$43,00** · Guayaquil: **20** | `products.json` |

## Reglas aplicadas (políticas)

- **Descuento por volumen** (POL-COM-01 §2.1): 100–249 → 3 % · 250–499 → 5 % · 500 o más → 8 %.
- **Descuento por nivel** (§3.1): Oro + 4 %.
- **Tope** (§1.2 y §3.2): la suma nunca supera el máximo del nivel (Oro 12 %).
- **Plazo de pago** (POL-FIN-02 §1.1): Oro hasta 60 días.
- **Aprobación** (POL-FIN-02 §4.1): más de $5.000 → gerente.
- **Entrega** (POL-LOG-03 §1.2): Guayaquil, 1 día hábil; sin costo desde $300.

## Paso 1 — Consulta compleja

| Cifra | Cálculo | Resultado |
|---|---|---|
| Descuento | 300 fardos → 5 % (volumen) + 4 % (Oro) = 9 % ≤ 12 % | **9 %** |
| Total | 300 × $18,50 = $5.550,00 × (1 − 0,09) | **$5.050,50** |
| Pago a 60 días | Oro: máximo 60 | **Permitido** |
| Entrega | Guayaquil | **1 día hábil**, sin costo (> $300) |
| Aprobación | $5.050,50 > $5.000 | **Gerente** |

## Paso 2 — Orden de compra en PDF (300 arroz + 150 aceite + 60 licuadoras, Guayaquil)

| Línea | Descuento | Cálculo | Subtotal |
|---|---|---|---|
| 300 arroz | 5 % + 4 % = 9 % | 300 × 18,50 × 0,91 | **$5.050,50** |
| 150 aceite | 3 % + 4 % = 7 % | 150 × 28,00 × 0,93 | **$3.906,00** |
| 40 licuadoras HG-001 (las disponibles) | 0 % + 4 % = 4 % | 40 × 45,00 × 0,96 | **$1.728,00** |
| 20 licuadoras HG-101 (sustituto) | 0 % + 4 % = 4 % | 20 × 43,00 × 0,96 | **$825,60** |

- **Stock:** se piden 60 licuadoras HG-001 y hay **40** → no se puede procesar tal cual (POL-LOG-03 §2.1).
- **Opción 1 — entrega parcial:** 5.050,50 + 3.906,00 + 1.728,00 = **$10.684,50**
- **Opción 2 — 40 HG-001 + 20 HG-101:** 10.684,50 + 825,60 = **$11.510,10**
- **Sustituto solo (60 HG-101):** hay 20 → **no válida** (el sistema no la ofrece).
- **Crédito:** $11.510,10 ≤ $60.000 → cabe. **Aprobación:** > $5.000 → **gerente**.

## Paso 3 — Aprobación

- Supervisora: $11.510,10 > $5.000 → **rechazada por nivel** (POL-FIN-02 §4.1).
- Gerente: aprueba → pedido **SO-000001** con referencia **PROP-000001** (la clave que impide duplicarlo).

## Cómo comprobarlo en vivo

1. **En pantalla:** "Evidencia: de dónde sale cada dato" muestra exactamente estos cálculos y los fragmentos citados.
2. **En el ERP:** los precios y el stock coinciden con `data/seed/products.json`.
3. **Con pruebas:** `docker compose run --rm tests` (205 pruebas) y la evaluación con LLM real (28/28, 0 pedidos inválidos en el ERP) en [eval-report.html](eval-report.html).
