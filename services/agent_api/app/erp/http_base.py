"""HTTP plumbing shared by the ERP adapters."""

from decimal import Decimal

import httpx

from .port import ErpUnavailable, OrderResult, OrderStatus

# Expected ERP answers to an order POST; anything else is treated as ERP unavailable
_ORDER_STATUS = {
    201: OrderStatus.CREATED,
    200: OrderStatus.DUPLICATE,
    422: OrderStatus.REJECTED,
    409: OrderStatus.CONFLICT,
    403: OrderStatus.FORBIDDEN,
}


def odata_quote(value: str) -> str:
    # OData escapes a single quote inside a literal by doubling it
    return "'" + value.replace("'", "''") + "'"


def to_decimal(value) -> Decimal:
    # str() first so floats like 18.5 do not carry binary noise into Decimal
    return Decimal(str(value))


class HttpErp:
    def __init__(self, base_url: str, token: str, timeout_seconds: float = 10.0, http_client: httpx.Client | None = None):
        # http_client lets tests plug in an in-process client; production builds its own
        self._client = http_client or httpx.Client(base_url=base_url, timeout=timeout_seconds)
        self._client.headers["Authorization"] = f"Bearer {token}"

    def _get(self, path: str, filter_: str) -> dict:
        try:
            response = self._client.get(path, params={"$filter": filter_})
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise ErpUnavailable(f"GET {path} failed: {e}") from e
        return response.json()

    def _post_order(self, path: str, body: dict) -> tuple[OrderStatus, dict]:
        try:
            response = self._client.post(path, json=body)
        except httpx.HTTPError as e:
            raise ErpUnavailable(f"POST {path} failed: {e}") from e
        status = _ORDER_STATUS.get(response.status_code)
        if status is None:
            raise ErpUnavailable(f"POST {path} returned {response.status_code}: {response.text[:200]}")
        return status, response.json()

    @staticmethod
    def _error_result(status: OrderStatus, payload: dict) -> OrderResult:
        error = payload.get("error") or {}
        return OrderResult(
            status=status,
            error_code=error.get("code", status.value.upper()),
            message=error.get("message") or payload.get("detail"),
            erp_payload=payload,
        )
