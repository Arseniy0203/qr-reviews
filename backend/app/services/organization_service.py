"""Organization lifecycle and settings."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import Membership, Organization, ReviewCategory, Role, User
from app.schemas.auth import MemberOut
from app.schemas.organization import (
    LEGAL_FIELDS,
    OrganizationOut,
    OrganizationUpdate,
    OrgSettings,
)
from app.services.consent_service import publish_default_documents
from app.services.niches import get_niche
from app.services.scope import AccessScope


async def create_organization(
    session: AsyncSession, *, name: str, niche: str, settings: OrgSettings | None = None
) -> Organization:
    """Create an organization, copy the niche's default categories, publish default consents.

    After this the categories belong to the organization and can be edited freely;
    later changes to the niche template do not affect existing organizations.
    Does not commit: the caller owns the transaction.
    """
    template = get_niche(niche)
    organization = Organization(
        name=name, niche=template.code, settings=(settings or OrgSettings()).to_db()
    )
    session.add(organization)
    await session.flush()

    session.add_all(
        ReviewCategory(
            organization_id=organization.id,
            name=category.name,
            code=category.code,
            sort_order=index,
        )
        for index, category in enumerate(template.categories)
    )
    await session.flush()
    await publish_default_documents(session, organization)
    return organization


async def get_organization(session: AsyncSession, scope: AccessScope) -> Organization:
    organization = await session.get(Organization, scope.organization_id)
    if organization is None:
        raise NotFoundError()
    return organization


def organization_out(organization: Organization) -> OrganizationOut:
    return OrganizationOut(
        id=organization.id,
        name=organization.name,
        niche=organization.niche,
        employee_title=get_niche(organization.niche).employee_title,
        settings=OrgSettings.from_db(organization.settings),
    )


async def update_organization(
    session: AsyncSession, scope: AccessScope, data: OrganizationUpdate
) -> Organization:
    scope.require_role(Role.OWNER)
    organization = await get_organization(session, scope)
    if data.name is not None:
        organization.name = data.name

    republish_consents = False
    if data.settings is not None:
        changes = data.settings.model_dump(exclude_unset=True)
        current = OrgSettings.from_db(organization.settings)
        merged = OrgSettings.model_validate({**current.model_dump(), **changes})
        republish_consents = any(
            getattr(current, field) != getattr(merged, field) for field in LEGAL_FIELDS
        )
        organization.settings = merged.to_db()
    if data.name is not None and not OrgSettings.from_db(organization.settings).legal_name:
        republish_consents = True  # the org name is the operator name in consent texts

    await session.flush()
    if republish_consents:
        await publish_default_documents(session, organization)
    await session.commit()
    return organization


async def list_members(session: AsyncSession, scope: AccessScope) -> list[MemberOut]:
    scope.require_role(Role.OWNER)
    rows = await session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(scope.org_filter(Membership))
        .order_by(Membership.created_at)
    )
    return [
        MemberOut(
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=membership.role,
            branch_id=membership.branch_id,
            employee_id=membership.employee_id,
            telegram_linked=user.telegram_linked,
        )
        for membership, user in rows
    ]
