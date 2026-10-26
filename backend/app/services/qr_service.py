"""QR points: public tokens (CRUD, rendering and reassignment land in stage 4)."""

import secrets

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QRPoint

# Lowercase, no look-alike characters (0/o, 1/l/i): easy to read off a printed card.
TOKEN_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
TOKEN_LENGTH = 10


def generate_token() -> str:
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


async def generate_unique_token(session: AsyncSession, attempts: int = 5) -> str:
    for _ in range(attempts):
        token = generate_token()
        taken = await session.scalar(select(exists().where(QRPoint.token == token)))
        if not taken:
            return token
    raise RuntimeError("Could not generate a unique QR token")
