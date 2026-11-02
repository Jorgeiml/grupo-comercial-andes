"""Structured outputs the LLM must return (strict JSON schema). The LLM fills these; Python decides."""

from typing import Literal

from pydantic import BaseModel, Field


class InputClassification(BaseModel):
    category: Literal["policy_question", "special_order", "out_of_scope", "suspicious"]
    reason: str = Field(description="Una frase en español que explica la clasificación")


class QueryPlan(BaseModel):
    subqueries: list[str] = Field(description="Una búsqueda por cada tema de política que toca la pregunta (máximo 4)")
    needs_order_data: bool = Field(description="True si la pregunta menciona productos y cantidades concretas")


class Answerability(BaseModel):
    answerable: bool = Field(description="True solo si los fragmentos contienen la respuesta, no solo el tema")
    supporting_citations: list[str] = Field(description="Citas exactas de los fragmentos que respaldan la respuesta")
    missing: str | None = Field(description="Qué información falta, si no se puede responder")


class ExtractedLine(BaseModel):
    sku: str | None = Field(description="Código del catálogo; null si el producto no está o es ambiguo")
    product_as_written: str
    quantity: int | None
    warehouse: Literal["UIO", "GYE"] | None = Field(description="UIO si la entrega es en Quito, GYE si es en Guayaquil; null si no se dijo")


class OrderExtraction(BaseModel):
    customer_code: str | None = Field(description="Código del cliente del catálogo; null si no se mencionó")
    lines: list[ExtractedLine]
    requested_discount_pct: float | None = Field(description="Solo si el cliente pidió un descuento explícito")
    requested_payment_days: int | None = Field(description="Solo si el cliente pidió un plazo de pago explícito")
    ambiguities: list[str] = Field(description="Dudas que impiden armar el pedido sin preguntar")


class DraftAnswer(BaseModel):
    answer: str = Field(description="Respuesta en español para el cliente")
