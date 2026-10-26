"""Organization lifecycle."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, ReviewCategory
from app.schemas.organization import OrgSettings
from app.services.consent_service import publish_default_documents
from app.services.niches import get_niche


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
