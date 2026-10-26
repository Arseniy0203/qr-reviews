"""Password hashing and keyed hashing of identifiers (IP, device id, one-time tokens)."""

import hashlib
import hmac

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

_password_hasher = PasswordHasher()


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
