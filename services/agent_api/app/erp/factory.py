"""Builds the ERP adapter selected by ERP_ADAPTER. The caller decides which credential it gets."""

import os

from .dynamics import DynamicsErp
from .port import ErpPort
from .sap import SapErp

ADAPTERS = {"dynamics": DynamicsErp, "sap": SapErp}


def create_erp(token: str, adapter: str | None = None, base_url: str | None = None) -> ErpPort:
    name = adapter or os.getenv("ERP_ADAPTER", "dynamics")
    if name not in ADAPTERS:
        raise ValueError(f"Unknown ERP_ADAPTER '{name}'. Expected one of: {', '.join(ADAPTERS)}")
    return ADAPTERS[name](
        base_url=base_url or os.getenv("ERP_BASE_URL", "http://mock-erp:8001"),
        token=token,
        timeout_seconds=float(os.getenv("ERP_TIMEOUT_SECONDS", "10")),
    )


def read_erp() -> ErpPort:
    """Read-only adapter: the only one the agent is ever given."""
    return create_erp(os.environ["ERP_READ_TOKEN"])


def write_erp() -> ErpPort:
    """Write adapter: used exclusively by the execution service."""
    return create_erp(os.environ["ERP_WRITE_TOKEN"])
