"""Passwords, JWT, and keyed hashing of identifiers (IP, device id, one-time tokens)."""

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

# Cheap parameters only in the test environment, where hundreds of hashes are computed.
_password_hasher = (
    PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
    if get_settings().app_env == "test"
    else PasswordHasher()
)
# Verified against when the user does not exist, so login timing does not reveal emails.
DUMMY_PASSWORD_HASH = _password_hasher.hash("dummy-password-for-timing")

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "qr-reviews"
TokenType = Literal["access", "refresh"]


class InvalidTokenError(Exception):
    pass


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    type: TokenType
    jti: str
    expires_at: datetime
    # Refresh-token family: all rotations of one login share it (reuse detection).
    family: str | None = None


def create_token(
    user_id: uuid.UUID, token_type: TokenType, ttl: timedelta, family: str | None = None
) -> tuple[str, TokenClaims]:
    now = datetime.now(UTC)
    claims = TokenClaims(
        user_id=user_id,
        type=token_type,
        jti=uuid.uuid4().hex,
        expires_at=now + ttl,
        family=family,
    )
    payload: dict[str, object] = {
        "sub": str(user_id),
        "type": token_type,
        "jti": claims.jti,
        "iss": JWT_ISSUER,
        "iat": now,
        "exp": claims.expires_at,
    }
    if family:
        payload["fam"] = family
    token = jwt.encode(payload, get_settings().secret_key.get_secret_value(), JWT_ALGORITHM)
    return token, claims


def decode_token(token: str, expected_type: TokenType) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            get_settings().secret_key.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"require": ["sub", "type", "jti", "exp", "iss"]},
        )
        if payload["type"] != expected_type:
            raise InvalidTokenError("wrong token type")
        return TokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            type=payload["type"],
            jti=payload["jti"],
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
            family=payload.get("fam"),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidTokenError(str(exc)) from exc


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _password_hasher.check_needs_rehash(password_hash)


def hash_identifier(value: str) -> str:
    """HMAC-SHA256 with HASH_SALT: used for IPs and device ids, never stored raw."""
    key = get_settings().hash_salt.get_secret_value().encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def hash_token(token: str) -> str:
    """Digest for high-entropy one-time tokens (invitations, Telegram link codes)."""
    return hashlib.sha256(token.encode()).hexdigest()
