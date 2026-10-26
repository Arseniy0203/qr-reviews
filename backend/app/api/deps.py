"""FastAPI dependencies: DB session, current user, access scope, role guards."""

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.rate_limit import client_ip, enforce_rate_limit
from app.models import Membership, Role, User
from app.services.auth_service import get_user_from_access_token
from app.services.scope import AccessScope

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False)
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


async def get_optional_user(session: SessionDep, credentials: BearerDep) -> User | None:
    if credentials is None:
        return None
    return await get_user_from_access_token(session, credentials.credentials)


async def get_current_user(session: SessionDep, credentials: BearerDep) -> User:
    if credentials is None:
        raise AuthenticationError()
    return await get_user_from_access_token(session, credentials.credentials)


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]


async def get_scope(
    session: SessionDep,
    user: CurrentUser,
    x_organization_id: Annotated[uuid.UUID | None, Header()] = None,
) -> AccessScope:
    """Resolve the membership for the requested organization (or the user's first one).

    Resolved from the DB on every request, so role changes apply immediately.
    """
    query = select(Membership).where(Membership.user_id == user.id)
    if x_organization_id is not None:
        query = query.where(Membership.organization_id == x_organization_id)
    membership = await session.scalar(query.order_by(Membership.created_at).limit(1))
    if membership is None:
        raise PermissionDeniedError("Нет доступа к компании")
    return AccessScope(
        user_id=user.id,
        organization_id=membership.organization_id,
        role=membership.role,
        branch_id=membership.branch_id,
        employee_id=membership.employee_id,
    )


ScopeDep = Annotated[AccessScope, Depends(get_scope)]


def require_roles(*roles: Role) -> Callable[[AccessScope], Awaitable[AccessScope]]:
    async def dependency(scope: ScopeDep) -> AccessScope:
        scope.require_role(*roles)
        return scope

    return dependency


OwnerScope = Annotated[AccessScope, Depends(require_roles(Role.OWNER))]
ManagerScope = Annotated[AccessScope, Depends(require_roles(Role.OWNER, Role.MANAGER))]


async def login_rate_limit(request: Request) -> None:
    settings = get_settings()
    await enforce_rate_limit(
        "login",
        client_ip(request),
        settings.rate_limit_login_max,
        settings.rate_limit_login_window_seconds,
    )
