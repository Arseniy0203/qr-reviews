"""Registration, login, and refresh-token rotation with reuse detection.

Refresh tokens are JWTs carrying a `family` id shared by all rotations of one login.
On every refresh the presented token's jti is marked used in Redis. Presenting a used
token again means it was stolen (or replayed), so the whole family is revoked.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AuthenticationError, ConflictError
from app.core.redis import get_redis
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    InvalidTokenError,
    TokenClaims,
    create_token,
    decode_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.models import Membership, Organization, Role, User
from app.schemas.auth import MembershipOut, RegisterRequest
from app.services.organization_service import create_organization

logger = logging.getLogger(__name__)

EMAIL_TAKEN = "Пользователь с таким email уже зарегистрирован"
INVALID_CREDENTIALS = "Неверный email или пароль"
SESSION_EXPIRED = "Сессия истекла, войдите снова"


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    access_expires_in: int
    refresh_token: str
    refresh_max_age: int


def issue_tokens(user_id: uuid.UUID, family: str | None = None) -> IssuedTokens:
    settings = get_settings()
    access_ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    refresh_ttl = timedelta(days=settings.refresh_token_ttl_days)
    access, _ = create_token(user_id, "access", access_ttl)
    refresh, _ = create_token(user_id, "refresh", refresh_ttl, family=family or uuid.uuid4().hex)
    return IssuedTokens(
        access_token=access,
        access_expires_in=int(access_ttl.total_seconds()),
        refresh_token=refresh,
        refresh_max_age=int(refresh_ttl.total_seconds()),
    )


async def create_user(session: AsyncSession, *, email: str, password: str, full_name: str) -> User:
    if await session.scalar(select(User.id).where(User.email == email)):
        raise ConflictError(EMAIL_TAKEN)
    user = User(email=email, password_hash=hash_password(password), full_name=full_name)
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:  # concurrent registration with the same email
        raise ConflictError(EMAIL_TAKEN) from exc
    return user


async def register_owner(session: AsyncSession, data: RegisterRequest) -> User:
    user = await create_user(
        session, email=data.email, password=data.password, full_name=data.full_name
    )
    organization = await create_organization(session, name=data.organization_name, niche=data.niche)
    session.add(Membership(organization_id=organization.id, user_id=user.id, role=Role.OWNER))
    await session.commit()
    logger.info("Registered owner user=%s org=%s", user.id, organization.id)
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User:
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        verify_password(password, DUMMY_PASSWORD_HASH)
        raise AuthenticationError(INVALID_CREDENTIALS)
    if not verify_password(password, user.password_hash) or not user.is_active:
        raise AuthenticationError(INVALID_CREDENTIALS)
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = datetime.now(UTC)
    await session.commit()
    return user


# --- Refresh rotation ------------------------------------------------------------


def _ttl_seconds(claims: TokenClaims) -> int:
    return max(int((claims.expires_at - datetime.now(UTC)).total_seconds()), 1)


async def _revoke_family(family: str, ttl: int) -> None:
    await get_redis().set(f"auth:family_revoked:{family}", "1", ex=ttl)


async def rotate_refresh_token(session: AsyncSession, refresh_token: str | None) -> IssuedTokens:
    if not refresh_token:
        raise AuthenticationError(SESSION_EXPIRED)
    try:
        claims = decode_token(refresh_token, "refresh")
    except InvalidTokenError as exc:
        raise AuthenticationError(SESSION_EXPIRED) from exc
    if claims.family is None:
        raise AuthenticationError(SESSION_EXPIRED)

    redis = get_redis()
    ttl = _ttl_seconds(claims)
    if await redis.exists(f"auth:family_revoked:{claims.family}"):
        raise AuthenticationError(SESSION_EXPIRED)
    first_use = await redis.set(f"auth:used:{claims.jti}", "1", ex=ttl, nx=True)
    if not first_use:
        await _revoke_family(claims.family, ttl)
        logger.warning("Refresh token reuse detected, family revoked (user=%s)", claims.user_id)
        raise AuthenticationError(SESSION_EXPIRED)

    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError(SESSION_EXPIRED)
    return issue_tokens(user.id, family=claims.family)


async def logout(refresh_token: str | None) -> None:
    if not refresh_token:
        return
    try:
        claims = decode_token(refresh_token, "refresh")
    except InvalidTokenError:
        return
    if claims.family:
        try:
            await _revoke_family(claims.family, _ttl_seconds(claims))
        except RedisError:
            logger.warning("Could not revoke refresh family on logout")


# --- Current user ------------------------------------------------------------------


async def get_user_from_access_token(session: AsyncSession, token: str) -> User:
    try:
        claims = decode_token(token, "access")
    except InvalidTokenError as exc:
        raise AuthenticationError() from exc
    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError()
    return user


async def list_memberships(session: AsyncSession, user_id: uuid.UUID) -> list[MembershipOut]:
    rows = await session.execute(
        select(Membership, Organization.name)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(Membership.user_id == user_id)
        .order_by(Membership.created_at)
    )
    return [
        MembershipOut(
            organization_id=membership.organization_id,
            organization_name=org_name,
            role=membership.role,
            branch_id=membership.branch_id,
            employee_id=membership.employee_id,
        )
        for membership, org_name in rows
    ]
