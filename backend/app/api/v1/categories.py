import uuid

from fastapi import APIRouter, status

from app.api.deps import OwnerScope, ScopeDep, SessionDep
from app.schemas.category import CategoryCreate, CategoryOut, CategoryUpdate
from app.services import category_service

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("")
async def list_categories(
    scope: ScopeDep, session: SessionDep, include_archived: bool = False
) -> list[CategoryOut]:
    categories = await category_service.list_categories(
        session, scope, include_archived=include_archived
    )
    return [CategoryOut.model_validate(c) for c in categories]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_category(
    data: CategoryCreate, scope: OwnerScope, session: SessionDep
) -> CategoryOut:
    return CategoryOut.model_validate(await category_service.create_category(session, scope, data))


@router.patch("/{category_id}")
async def update_category(
    category_id: uuid.UUID, data: CategoryUpdate, scope: OwnerScope, session: SessionDep
) -> CategoryOut:
    category = await category_service.update_category(session, scope, category_id, data)
    return CategoryOut.model_validate(category)


@router.delete("/{category_id}")
async def archive_category(
    category_id: uuid.UUID, scope: OwnerScope, session: SessionDep
) -> CategoryOut:
    category = await category_service.set_archived(session, scope, category_id, archived=True)
    return CategoryOut.model_validate(category)


@router.post("/{category_id}/restore")
async def restore_category(
    category_id: uuid.UUID, scope: OwnerScope, session: SessionDep
) -> CategoryOut:
    category = await category_service.set_archived(session, scope, category_id, archived=False)
    return CategoryOut.model_validate(category)
