"""Branches. Owners manage them; managers see only their own branch."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.models import Branch, QRPoint, Role
from app.schemas.branch import BranchCreate, BranchUpdate
from app.services.assignments import close_open_assignments
from app.services.scope import AccessScope

BRANCH_ARCHIVED = "Филиал в архиве"


async def list_branches(
    session: AsyncSession, scope: AccessScope, *, include_archived: bool = False
) -> list[Branch]:
    query = select(Branch).where(scope.org_filter(Branch), scope.branch_filter(Branch.id))
    if not include_archived:
        query = query.where(Branch.archived_at.is_(None))
    return list(await session.scalars(query.order_by(Branch.name)))


async def get_branch(session: AsyncSession, scope: AccessScope, branch_id: uuid.UUID) -> Branch:
    branch = await session.get(Branch, branch_id)
    scope.ensure_same_org(branch)
    assert branch is not None
    if not scope.can_see_branch(branch.id):
        raise NotFoundError()
    return branch


async def get_active_branch(
    session: AsyncSession, scope: AccessScope, branch_id: uuid.UUID
) -> Branch:
    """A visible, non-archived branch to attach new employees or QR points to."""
    branch = await get_branch(session, scope, branch_id)
    if branch.archived_at is not None:
        raise AppError(BRANCH_ARCHIVED)
    return branch


async def create_branch(session: AsyncSession, scope: AccessScope, data: BranchCreate) -> Branch:
    scope.require_role(Role.OWNER)
    branch = Branch(organization_id=scope.organization_id, name=data.name, address=data.address)
    session.add(branch)
    await session.commit()
    return branch


async def update_branch(
    session: AsyncSession, scope: AccessScope, branch_id: uuid.UUID, data: BranchUpdate
) -> Branch:
    scope.require_role(Role.OWNER)
    branch = await get_branch(session, scope, branch_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "name" and value is None:
            continue
        setattr(branch, field, value)
    await session.commit()
    return branch


async def archive_branch(session: AsyncSession, scope: AccessScope, branch_id: uuid.UUID) -> Branch:
    """Soft delete. The branch's QR points are archived too (they stop accepting reviews);
    employees are kept because they may work in other branches."""
    scope.require_role(Role.OWNER)
    branch = await get_branch(session, scope, branch_id)
    if branch.archived_at is None:
        now = datetime.now(UTC)
        branch.archived_at = now
        point_ids = list(
            await session.scalars(
                update(QRPoint)
                .where(QRPoint.branch_id == branch.id, QRPoint.archived_at.is_(None))
                .values(archived_at=now)
                .returning(QRPoint.id)
            )
        )
        if point_ids:
            await close_open_assignments(session, scope.organization_id, now, point_ids=point_ids)
        await session.commit()
    return branch


async def restore_branch(session: AsyncSession, scope: AccessScope, branch_id: uuid.UUID) -> Branch:
    """QR points stay archived: restore them one by one deliberately."""
    scope.require_role(Role.OWNER)
    branch = await get_branch(session, scope, branch_id)
    branch.archived_at = None
    await session.commit()
    return branch
