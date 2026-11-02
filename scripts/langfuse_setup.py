"""Registers our model prices in the Langfuse project, so Langfuse computes the same USD cost as the CFO panel.

Idempotent: models already defined in the project are left untouched. Run once:
    docker compose run --rm -v ./scripts:/scripts:ro agent-api python /scripts/langfuse_setup.py
"""

import re
import sys

sys.path.insert(0, "/app")

from app.llm.pricing import CHAT_PRICES, EMBEDDING_PRICES, ONE_MILLION  # noqa: E402
from langfuse import get_client  # noqa: E402


def existing_models(client) -> set[str]:
    names, page = set(), 1
    while True:
        result = client.api.models.list(page=page, limit=100)
        names.update(model.model_name for model in result.data)
        if page >= result.meta.total_pages:
            return names
        page += 1


def main() -> None:
    client = get_client()
    if not client.auth_check():
        sys.exit("Langfuse authentication failed: check LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL")
    present = existing_models(client)

    wanted = {name: (inp, out) for name, (inp, out) in CHAT_PRICES.items()}
    wanted.update({name: (price, None) for name, price in EMBEDDING_PRICES.items()})
    for name, (input_price, output_price) in wanted.items():
        if name in present:
            print(f"  = {name}: already defined")
            continue
        client.api.models.create(
            model_name=name,
            match_pattern=f"(?i)^{re.escape(name)}$",
            unit="TOKENS",
            # Langfuse expects USD per token; our table is USD per 1M tokens (source: OpenAI pricing page)
            input_price=float(input_price / ONE_MILLION),
            output_price=float(output_price / ONE_MILLION) if output_price is not None else None,
        )
        print(f"  + {name}: input {input_price} / output {output_price} USD per 1M tokens")


if __name__ == "__main__":
    main()
