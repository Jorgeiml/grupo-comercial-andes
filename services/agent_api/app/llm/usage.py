"""Per-request accounting of tokens, cost and latency, step by step."""

from dataclasses import dataclass, field
from decimal import Decimal

from .pricing import chat_cost_usd, embedding_cost_usd


class TokenBudgetExceeded(Exception):
    """The request used more tokens than MAX_TOKENS_PER_REQUEST: the flow must stop."""


@dataclass(frozen=True)
class StepUsage:
    step: str  # e.g. "classify", "extract_order"
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None  # None = price unknown
    latency_ms: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class UsageTracker:
    max_tokens: int | None = None
    steps: list[StepUsage] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(step.total_tokens for step in self.steps)

    @property
    def total_cost_usd(self) -> Decimal:
        """Sum of the known costs; check cost_complete before presenting it as the full cost."""
        return sum((step.cost_usd for step in self.steps if step.cost_usd is not None), Decimal(0))

    @property
    def cost_complete(self) -> bool:
        return all(step.cost_usd is not None for step in self.steps)

    def ensure_budget(self) -> None:
        if self.max_tokens is not None and self.total_tokens >= self.max_tokens:
            raise TokenBudgetExceeded(f"Request used {self.total_tokens} tokens (limit {self.max_tokens})")

    def record_chat(self, step: str, model: str, input_tokens: int, output_tokens: int, latency_ms: int) -> StepUsage:
        usage = StepUsage(step, model, input_tokens, output_tokens, chat_cost_usd(model, input_tokens, output_tokens), latency_ms)
        self.steps.append(usage)
        self.ensure_budget()
        return usage

    def record_embedding(self, step: str, model: str, tokens: int, latency_ms: int) -> StepUsage:
        usage = StepUsage(step, model, tokens, 0, embedding_cost_usd(model, tokens), latency_ms)
        self.steps.append(usage)
        self.ensure_budget()
        return usage

    def summary(self) -> dict:
        return {
            "total_tokens": self.total_tokens,
            "total_cost_usd": str(self.total_cost_usd),
            "cost_complete": self.cost_complete,
            "steps": [
                {
                    "step": s.step,
                    "model": s.model,
                    "input_tokens": s.input_tokens,
                    "output_tokens": s.output_tokens,
                    "cost_usd": str(s.cost_usd) if s.cost_usd is not None else None,
                    "latency_ms": s.latency_ms,
                }
                for s in self.steps
            ],
        }
