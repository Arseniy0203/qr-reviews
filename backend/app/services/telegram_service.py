"""Linking a dashboard user to a Telegram chat via a one-time deep link code.

Dashboard: POST /api/v1/telegram/link-code -> t.me/<bot>?start=<code>.
Bot: /start <code> -> POST /internal/telegram/link (shared secret) -> chat_id saved, code spent.
Only a hash of the code is stored.
"""

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import hash_token
from app.models import TelegramLinkCode, User

logger = logging.getLogger(__name__)


class LinkCodeInvalidError(AppError):
    status_code = 404
    code = "telegram_code_invalid"
    message = "Код недействителен или устарел"


class TelegramNotConfiguredError(AppError):
    status_code = 503
    code = "telegram_not_configured"
    message = "Telegram-бот не настроен"


async def create_link_code(session: AsyncSession, user: User) -> tuple[str, datetime]:
    """A new code replaces the user's unused ones. Returns the raw code (shown once)."""
    if not get_settings().telegram_bot_username:
        raise TelegramNotConfiguredError()
    await session.execute(
        delete(TelegramLinkCode).where(
            TelegramLinkCode.user_id == user.id, TelegramLinkCode.used_at.is_(None)
        )
    )
    code = secrets.token_urlsafe(24)  # 32 chars; Telegram allows up to 64 in /start
    expires_at = datetime.now(UTC) + timedelta(
        minutes=get_settings().telegram_link_code_ttl_minutes
    )
    session.add(
        TelegramLinkCode(user_id=user.id, code_hash=hash_token(code), expires_at=expires_at)
    )
    await session.commit()
    return code, expires_at


def deep_link(code: str) -> str:
    return f"https://t.me/{get_settings().telegram_bot_username}?start={code}"


async def link_chat(session: AsyncSession, code: str, chat_id: int) -> uuid.UUID:
    """Spend the code and attach the chat to its user. Atomic: a code works exactly once.

    One chat belongs to one account: linking it to a new account unlinks the previous one.
    """
    now = datetime.now(UTC)
    user_id = await session.scalar(
        update(TelegramLinkCode)
        .where(
            TelegramLinkCode.code_hash == hash_token(code),
            TelegramLinkCode.used_at.is_(None),
            TelegramLinkCode.expires_at > now,
        )
        .values(used_at=now)
        .returning(TelegramLinkCode.user_id)
    )
    if user_id is None:
        await session.rollback()
        raise LinkCodeInvalidError()
    await session.execute(
        update(User)
        .where(User.telegram_chat_id == chat_id, User.id != user_id)
        .values(telegram_chat_id=None)
    )
    await session.execute(update(User).where(User.id == user_id).values(telegram_chat_id=chat_id))
    await session.commit()
    logger.info("Telegram linked for user=%s", user_id)
    return user_id


async def unlink(session: AsyncSession, user: User) -> None:
    await session.execute(update(User).where(User.id == user.id).values(telegram_chat_id=None))
    await session.commit()
    logger.info("Telegram unlinked for user=%s", user.id)
