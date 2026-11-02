"""Authority comes from a signed token: forged, expired or unknown-role tokens are refused."""

import jwt
import pytest
from app.auth.tokens import AuthError, decode_token, issue_token


def test_roundtrip():
    identity = decode_token(issue_token("ana", "gerente"))
    assert (identity.user, identity.role) == ("ana", "gerente")


def test_tampered_token_is_refused():
    token = issue_token("luis", "ejecutivo")
    header, payload, signature = token.split(".")
    forged = jwt.encode({"sub": "luis", "role": "gerente", "exp": 9999999999}, "attacker-secret-0123456789abcdef0123", algorithm="HS256")
    with pytest.raises(AuthError):
        decode_token(f"{header}.{forged.split('.')[1]}.{signature}")
    with pytest.raises(AuthError):
        decode_token(forged)


def test_unsigned_token_is_refused():
    unsigned = jwt.encode({"sub": "x", "role": "gerente", "exp": 9999999999}, key=None, algorithm="none")
    with pytest.raises(AuthError):
        decode_token(unsigned)


def test_expired_token_is_refused():
    with pytest.raises(AuthError):
        decode_token(issue_token("ana", "gerente", ttl_hours=-1))


def test_unknown_role_cannot_be_issued():
    with pytest.raises(AuthError):
        issue_token("x", "superadmin")
