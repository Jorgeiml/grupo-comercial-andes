"""Identity and roles. Authority always comes from a signed token, never from the chat text."""

import os
from datetime import datetime, timedelta, timezone
from typing import Literal, get_args

import jwt
from pydantic import BaseModel

Role = Literal["cliente", "ejecutivo", "supervisor", "gerente"]
ROLES: tuple[str, ...] = get_args(Role)
APPROVER_ROLES = {"supervisor", "gerente"}

ALGORITHM = "HS256"


class AuthError(Exception):
    pass


class Identity(BaseModel):
    user: str
    role: Role


def _secret() -> str:
    secret = os.getenv("JWT_SECRET", "")
    if len(secret) < 32:
        raise AuthError("JWT_SECRET is missing or too short")
    return secret


def issue_token(user: str, role: Role, ttl_hours: float = 8) -> str:
    """Used by the demo's role selector; in production tokens come from the corporate identity provider."""
    if role not in ROLES:
        raise AuthError(f"Unknown role '{role}'")
    now = datetime.now(timezone.utc)
    claims = {"sub": user, "role": role, "iat": now, "exp": now + timedelta(hours=ttl_hours)}
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)


def decode_token(token: str) -> Identity:
    try:
        # algorithms is explicit: a token signed with another algorithm (or "none") is refused
        claims = jwt.decode(token, _secret(), algorithms=[ALGORITHM], options={"require": ["sub", "role", "exp"]})
    except jwt.PyJWTError as e:
        raise AuthError(f"Invalid token: {e}") from e
    if claims["role"] not in ROLES:
        raise AuthError(f"Unknown role '{claims['role']}'")
    return Identity(user=claims["sub"], role=claims["role"])
