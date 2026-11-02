"""Service credentials. The ERP knows integrations, not people: user roles live in agent-api."""

import hmac
import os

from fastapi import Header, HTTPException, Request

from .orders import log_write

# Scopes granted to each credential. The agent only ever receives the "read" token.
SCOPES = {
    "read": {"read"},
    "write": {"read", "write"},
    "admin": {"read", "admin"},
}


def _configured_tokens() -> dict[str, str]:
    return {
        "read": os.getenv("ERP_READ_TOKEN", ""),
        "write": os.getenv("ERP_WRITE_TOKEN", ""),
        "admin": os.getenv("ERP_ADMIN_TOKEN", ""),
    }


def _identify(token: str) -> str | None:
    for name, expected in _configured_tokens().items():
        # Constant-time comparison so response timing does not leak the token
        if expected and hmac.compare_digest(token.encode(), expected.encode()):
            return name
    return None


def require(scope: str):
    def dependency(request: Request, authorization: str | None = Header(default=None)) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        credential = _identify(authorization.removeprefix("Bearer "))
        if credential is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        if scope not in SCOPES[credential]:
            if scope == "write":
                # Rejected write attempts are logged: this is how we prove the agent cannot write
                log_write(credential, outcome="forbidden", detail=f"{request.method} {request.url.path}")
            raise HTTPException(status_code=403, detail=f"Credential '{credential}' lacks scope '{scope}'")
        return credential

    return dependency
