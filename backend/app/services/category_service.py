"""Organization review categories (seeded from the niche, then owned by the org)."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import ReviewCategory, Role
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.services.scope import AccessScope

NAME_TAKEN = "Категория с таким названием уже есть"


async def list_categories(
    session: AsyncSession, scope: AccessScope, *, include_archived: bool = False
) -> list[ReviewCategory]:
    query = select(ReviewCategory).where(scope.org_filter(ReviewCategory))
    if not include_archived:
        query = query.where(ReviewCategory.archived_at.is_(None))
    return list(
        await session.scalars(query.order_by(ReviewCategory.sort_order, ReviewCategory.name))
    )


async def _get(session: AsyncSession, scope: AccessScope, category_id: uuid.UUID) -> ReviewCategory:
    category = await session.get(ReviewCategory, category_id)
    scope.ensure_same_org(category)
    assert category is not None
    return category


async def _commit_unique(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:  # partial unique index on active names
        await session.rollback()
        raise ConflictError(NAME_TAKEN) from exc


async def create_category(
    session: AsyncSession, scope: AccessScope, data: CategoryCreate
) -> ReviewCategory:
    scope.require_role(Role.OWNER)
    last = await session.scalar(
        select(func.max(ReviewCategory.sort_order)).where(scope.org_filter(ReviewCategory))
    )
    category = ReviewCategory(
        organization_id=scope.organization_id,
        name=data.name.strip(),
        sort_order=(last or 0) + 1,
    )
    session.add(category)
    await _commit_unique(session)
    return category


async def update_category(
    session: AsyncSession, scope: AccessScope, category_id: uuid.UUID, data: CategoryUpdate
) -> ReviewCategory:
    scope.require_role(Role.OWNER)
    category = await _get(session, scope, category_id)
    if data.name is not None:
        category.name = data.name.strip()
    if data.sort_order is not None:
        category.sort_order = data.sort_order
    await _commit_unique(session)
    return category


async def set_archived(
    session: AsyncSession, scope: AccessScope, category_id: uuid.UUID, archived: bool
) -> ReviewCategory:
    """Archived categories disappear from the form but stay attached to old reviews."""
    scope.require_role(Role.OWNER)
    category = await _get(session, scope, category_id)
    category.archived_at = func.now() if archived else None
    await _commit_unique(session)
    await session.refresh(category)
    return category
