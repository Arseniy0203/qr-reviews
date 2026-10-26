import uuid

from fastapi import APIRouter, status

from app.api.deps import OwnerScope, ScopeDep, SessionDep
from app.schemas.branch import BranchCreate, BranchOut, BranchUpdate
from app.services import branch_service

router = APIRouter(prefix="/branches", tags=["branches"])


@router.get("")
async def list_branches(
    scope: ScopeDep, session: SessionDep, include_archived: bool = False
) -> list[BranchOut]:
    branches = await branch_service.list_branches(session, scope, include_archived=include_archived)
    return [BranchOut.model_validate(b) for b in branches]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_branch(data: BranchCreate, scope: OwnerScope, session: SessionDep) -> BranchOut:
    return BranchOut.model_validate(await branch_service.create_branch(session, scope, data))


@router.get("/{branch_id}")
async def get_branch(branch_id: uuid.UUID, scope: ScopeDep, session: SessionDep) -> BranchOut:
    return BranchOut.model_validate(await branch_service.get_branch(session, scope, branch_id))


@router.patch("/{branch_id}")
async def update_branch(
    branch_id: uuid.UUID, data: BranchUpdate, scope: OwnerScope, session: SessionDep
) -> BranchOut:
    branch = await branch_service.update_branch(session, scope, branch_id, data)
    return BranchOut.model_validate(branch)


@router.delete("/{branch_id}")
async def archive_branch(branch_id: uuid.UUID, scope: OwnerScope, session: SessionDep) -> BranchOut:
    """Soft delete (archive)."""
    branch = await branch_service.archive_branch(session, scope, branch_id)
    return BranchOut.model_validate(branch)


@router.post("/{branch_id}/restore")
async def restore_branch(branch_id: uuid.UUID, scope: OwnerScope, session: SessionDep) -> BranchOut:
    branch = await branch_service.restore_branch(session, scope, branch_id)
    return BranchOut.model_validate(branch)
