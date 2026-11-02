"""LLM provider selection. Switching provider is configuration (.env), not code.

- openai: OpenAI API (this demo).
- azure_openai: Azure OpenAI, for Microsoft customers. Deployments should be named after the model
  so MODEL_SMALL / MODEL_MAIN also select the right price.
- SAP Generative AI Hub (SAP BTP) would be a third branch here, following the same pattern; it needs
  the SAP SDK and a BTP account, so it is documented but not implemented.
"""

import os
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI, ChatOpenAI

ModelKind = Literal["small", "main"]


def model_name(kind: ModelKind) -> str:
    return os.environ["MODEL_SMALL" if kind == "small" else "MODEL_MAIN"]


def _common_params(name: str) -> dict:
    params = dict(
        temperature=0,
        timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        max_retries=1,
        max_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "800")),
    )
    if name.startswith("gpt-5"):
        # gpt-5 models only honor temperature 0 with internal reasoning disabled; otherwise the API
        # rejects it and langchain-openai silently drops it. Our tasks are extraction and drafting:
        # all reasoning over numbers is done by Python, so no model reasoning is needed.
        params["reasoning_effort"] = "none"
    return params


def get_model(kind: ModelKind) -> BaseChatModel:
    provider = os.getenv("LLM_PROVIDER", "openai")
    name = model_name(kind)
    if provider == "openai":
        return ChatOpenAI(model=name, **_common_params(name))
    if provider == "azure_openai":
        # Endpoint and key come from AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY
        return AzureChatOpenAI(
            azure_deployment=name,
            api_version=os.environ["AZURE_OPENAI_API_VERSION"],
            **_common_params(name),
        )
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Expected 'openai' or 'azure_openai'")
