from fastapi import APIRouter

from app.api.deps import OwnerScope, ScopeDep, SessionDep
from app.schemas.auth import MemberOut
from app.schemas.organization import OrganizationOut, OrganizationUpdate
from app.services import organization_service

router = APIRouter(prefix="/organization", tags=["organization"])


@router.get("")
async def get_organization(scope: ScopeDep, session: SessionDep) -> OrganizationOut:
    organization = await organization_service.get_organization(session, scope)
    return organization_service.organization_out(organization)


@router.patch("")
async def update_organization(
    data: OrganizationUpdate, scope: OwnerScope, session: SessionDep
) -> OrganizationOut:
    organization = await organization_service.update_organization(session, scope, data)
    return organization_service.organization_out(organization)


@router.get("/members")
async def list_members(scope: OwnerScope, session: SessionDep) -> list[MemberOut]:
    return await organization_service.list_members(session, scope)
