"""Invitations of managers and employees via copyable links (no email in MVP)."""

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError
from app.core.security import hash_token
from app.models import Branch, Employee, Invitation, Membership, Organization, Role, User
from app.schemas.auth import AcceptInvitationRequest, InvitationCreate, InvitationPreview
from app.services.auth_service import create_user
from app.services.scope import AccessScope

logger = logging.getLogger(__name__)

INVITATION_INVALID = "Приглашение недействительно или истекло"


class InvitationInvalidError(AppError):
    status_code = 410
    code = "invitation_invalid"
    message = INVITATION_INVALID


def invitation_url(token: str) -> str:
    return f"{get_settings().dashboard_base_url}/invite/{token}"


async def create_invitation(
    session: AsyncSession, scope: AccessScope, data: InvitationCreate
) -> tuple[Invitation, str]:
    """Returns the invitation and the raw token (shown once, only its hash is stored)."""
    scope.require_role(Role.OWNER)
    branch_id: uuid.UUID | None = None
    employee_id: uuid.UUID | None = None
    if data.role is Role.MANAGER:
        if data.branch_id is None:
            raise AppError("Для управляющего нужно выбрать филиал")
        branch = await session.get(Branch, data.branch_id)
        scope.ensure_same_org(branch)
        branch_id = data.branch_id
    else:
        if data.employee_id is None:
            raise AppError("Для сотрудника нужно выбрать карточку сотрудника")
        employee = await session.get(Employee, data.employee_id)
        scope.ensure_same_org(employee)
        employee_id = data.employee_id

    token = secrets.token_urlsafe(32)
    invitation = Invitation(
        organization_id=scope.organization_id,
        token_hash=hash_token(token),
        role=data.role,
        branch_id=branch_id,
        employee_id=employee_id,
        email=data.email,
        created_by_user_id=scope.user_id,
        expires_at=datetime.now(UTC) + timedelta(days=get_settings().invitation_ttl_days),
    )
    session.add(invitation)
    await session.commit()
    return invitation, token


async def list_invitations(session: AsyncSession, scope: AccessScope) -> list[Invitation]:
    scope.require_role(Role.OWNER)
    result = await session.scalars(
        select(Invitation)
        .where(scope.org_filter(Invitation))
        .order_by(Invitation.created_at.desc())
    )
    return list(result)


async def revoke_invitation(
    session: AsyncSession, scope: AccessScope, invitation_id: uuid.UUID
) -> None:
    scope.require_role(Role.OWNER)
    invitation = await session.get(Invitation, invitation_id)
    scope.ensure_same_org(invitation)
    assert invitation is not None
    if invitation.accepted_at is None and invitation.revoked_at is None:
        invitation.revoked_at = datetime.now(UTC)
        await session.commit()


async def _get_valid_invitation(session: AsyncSession, token: str) -> Invitation:
    invitation = await session.scalar(
        select(Invitation).where(Invitation.token_hash == hash_token(token))
    )
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= datetime.now(UTC)
    ):
        raise InvitationInvalidError()
    return invitation


async def preview_invitation(session: AsyncSession, token: str) -> InvitationPreview:
    invitation = await _get_valid_invitation(session, token)
    organization = await session.get(Organization, invitation.organization_id)
    branch = await session.get(Branch, invitation.branch_id) if invitation.branch_id else None
    employee = (
        await session.get(Employee, invitation.employee_id) if invitation.employee_id else None
    )
    assert organization is not None
    return InvitationPreview(
        organization_name=organization.name,
        role=invitation.role,
        branch_name=branch.name if branch else None,
        employee_name=employee.full_name if employee else None,
        email=invitation.email,
        expires_at=invitation.expires_at,
    )


async def accept_invitation(
    session: AsyncSession,
    token: str,
    data: AcceptInvitationRequest,
    current_user: User | None,
) -> User:
    """Accept as the logged-in user, or register a new user from the request body."""
    invitation = await _get_valid_invitation(session, token)

    if current_user is not None:
        user = current_user
        if invitation.email and invitation.email != user.email:
            raise AppError("Приглашение отправлено на другой email")
    else:
        if not (data.email and data.full_name and data.password):
            raise AppError("Укажите email, имя и пароль")
        if invitation.email and invitation.email != data.email:
            raise AppError("Приглашение отправлено на другой email")
        try:
            user = await create_user(
                session, email=data.email, password=data.password, full_name=data.full_name
            )
        except ConflictError as exc:
            raise ConflictError(
                "Аккаунт с этим email уже есть — войдите и откройте ссылку ещё раз"
            ) from exc

    already_member = await session.scalar(
        select(Membership.id).where(
            Membership.user_id == user.id,
            Membership.organization_id == invitation.organization_id,
        )
    )
    if already_member:
        raise ConflictError("Вы уже состоите в этой компании")

    session.add(
        Membership(
            organization_id=invitation.organization_id,
            user_id=user.id,
            role=invitation.role,
            branch_id=invitation.branch_id,
            employee_id=invitation.employee_id,
        )
    )
    invitation.accepted_at = datetime.now(UTC)
    invitation.accepted_by_user_id = user.id
    await session.commit()
    logger.info("Invitation %s accepted by user=%s", invitation.id, user.id)
    return user
