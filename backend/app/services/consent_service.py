"""Consent documents (152-FZ / 38-FZ): versioned, immutable, per organization."""

import uuid
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import ConsentDocument, ConsentType, Organization
from app.schemas.organization import OrgSettings

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "consents"


@lru_cache
def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        undefined=StrictUndefined,
        autoescape=False,  # plain text; HTML escaping happens where it is rendered
        keep_trailing_newline=True,
    )


def render_default_text(consent_type: ConsentType, organization: Organization) -> str:
    settings = OrgSettings.from_db(organization.settings)
    template = _environment().get_template(f"{consent_type.value}.txt.j2")
    return template.render(
        operator_name=settings.legal_name or organization.name,
        inn=settings.inn,
        address=settings.legal_address,
        email=settings.privacy_email,
        processor=get_settings().pd_processor_details or None,
    ).strip()


async def publish_document(
    session: AsyncSession, organization_id: uuid.UUID, consent_type: ConsentType, text: str
) -> ConsentDocument:
    """Publish a new version. Existing versions are never modified (DB trigger enforces it)."""
    current = await session.scalar(
        select(func.max(ConsentDocument.version)).where(
            ConsentDocument.organization_id == organization_id,
            ConsentDocument.type == consent_type,
        )
    )
    document = ConsentDocument(
        organization_id=organization_id,
        type=consent_type,
        version=(current or 0) + 1,
        text=text,
    )
    session.add(document)
    await session.flush()
    return document


async def publish_default_documents(
    session: AsyncSession, organization: Organization
) -> list[ConsentDocument]:
    return [
        await publish_document(
            session, organization.id, consent_type, render_default_text(consent_type, organization)
        )
        for consent_type in ConsentType
    ]


async def get_current_document(
    session: AsyncSession, organization_id: uuid.UUID, consent_type: ConsentType
) -> ConsentDocument | None:
    return await session.scalar(
        select(ConsentDocument)
        .where(
            ConsentDocument.organization_id == organization_id,
            ConsentDocument.type == consent_type,
            ConsentDocument.published_at <= func.now(),
        )
        .order_by(ConsentDocument.version.desc())
        .limit(1)
    )
