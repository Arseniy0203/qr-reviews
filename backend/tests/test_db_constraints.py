"""Database-level guarantees: tenant isolation, PD rules, history invariants."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ConsentDocument,
    ConsentType,
    Membership,
    QRAssignment,
    Review,
    ReviewCategory,
    Role,
    User,
)
from app.services.consent_service import get_current_document, publish_document
from tests.factories import Tenant, make_tenant


def review_for(tenant: Tenant, **overrides: object) -> Review:
    fields: dict[str, object] = {
        "organization_id": tenant.org.id,
        "branch_id": tenant.branch.id,
        "qr_point_id": tenant.point.id,
        "rating": 5,
    }
    fields.update(overrides)
    return Review(**fields)


async def assert_rejected(session: AsyncSession, obj: object) -> None:
    session.add(obj)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_create_organization_copies_niche_categories_and_consents(
    session: AsyncSession,
) -> None:
    tenant = await make_tenant(session)
    names = (
        await session.scalars(
            select(ReviewCategory.name)
            .where(ReviewCategory.organization_id == tenant.org.id)
            .order_by(ReviewCategory.sort_order)
        )
    ).all()
    assert names[0] == "Качество работы"
    assert "Запись" in names
    pd = await get_current_document(session, tenant.org.id, ConsentType.PD)
    marketing = await get_current_document(session, tenant.org.id, ConsentType.MARKETING)
    assert pd is not None
    assert pd.version == 1
    assert "152-ФЗ" in pd.text
    assert tenant.org.name in pd.text
    assert marketing is not None
    assert "38-ФЗ" in marketing.text


async def test_review_cannot_reference_branch_of_another_org(session: AsyncSession) -> None:
    a = await make_tenant(session, "Салон А")
    b = await make_tenant(session, "Салон Б")
    await assert_rejected(session, review_for(a, branch_id=b.branch.id))


async def test_review_cannot_reference_employee_of_another_org(session: AsyncSession) -> None:
    a = await make_tenant(session, "Салон А")
    b = await make_tenant(session, "Салон Б")
    await assert_rejected(session, review_for(a, employee_id=b.employee.id))


async def test_review_cannot_use_consent_of_another_org(session: AsyncSession) -> None:
    a = await make_tenant(session, "Салон А")
    b = await make_tenant(session, "Салон Б")
    foreign_pd = await get_current_document(session, b.org.id, ConsentType.PD)
    assert foreign_pd is not None
    now = datetime.now(UTC)
    await assert_rejected(
        session,
        review_for(
            a,
            customer_phone="+79991234567",
            pd_consent_document_id=foreign_pd.id,
            pd_consent_at=now,
        ),
    )


async def test_contact_requires_valid_pd_consent(session: AsyncSession) -> None:
    tenant = await make_tenant(session)
    pd = await get_current_document(session, tenant.org.id, ConsentType.PD)
    assert pd is not None
    now = datetime.now(UTC)

    await assert_rejected(session, review_for(tenant, customer_phone="+79991234567"))

    tenant = await make_tenant(session)
    pd = await get_current_document(session, tenant.org.id, ConsentType.PD)
    assert pd is not None
    ok = review_for(
        tenant,
        customer_name="Мария",
        customer_phone="+79991234567",
        pd_consent_document_id=pd.id,
        pd_consent_at=now,
    )
    session.add(ok)
    await session.flush()

    # Revoking consent while keeping the contact is impossible.
    with pytest.raises(IntegrityError):
        await session.execute(
            update(Review).where(Review.id == ok.id).values(pd_consent_revoked_at=now)
        )
    await session.rollback()


async def test_marketing_consent_requires_pd_consent(session: AsyncSession) -> None:
    tenant = await make_tenant(session)
    marketing = await get_current_document(session, tenant.org.id, ConsentType.MARKETING)
    assert marketing is not None
    await assert_rejected(
        session,
        review_for(
            tenant,
            marketing_consent_document_id=marketing.id,
            marketing_consent_at=datetime.now(UTC),
        ),
    )


@pytest.mark.parametrize("phone", ["89991234567", "+7 999 123-45-67", "+12"])
async def test_phone_must_be_e164(session: AsyncSession, phone: str) -> None:
    tenant = await make_tenant(session)
    pd = await get_current_document(session, tenant.org.id, ConsentType.PD)
    assert pd is not None
    await assert_rejected(
        session,
        review_for(
            tenant,
            customer_phone=phone,
            pd_consent_document_id=pd.id,
            pd_consent_at=datetime.now(UTC),
        ),
    )


async def test_review_text_length_limit(session: AsyncSession) -> None:
    tenant = await make_tenant(session)
    session.add(review_for(tenant, text="я" * 2000))
    await session.flush()
    await assert_rejected(session, review_for(tenant, text="я" * 2001))


async def test_rating_range(session: AsyncSession) -> None:
    tenant = await make_tenant(session)
    await assert_rejected(session, review_for(tenant, rating=6))


async def test_consent_documents_are_immutable(session: AsyncSession) -> None:
    tenant = await make_tenant(session)
    org_id = tenant.org.id
    await session.commit()
    pd = await get_current_document(session, org_id, ConsentType.PD)
    assert pd is not None

    with pytest.raises(DBAPIError, match="immutable"):
        await session.execute(
            update(ConsentDocument).where(ConsentDocument.id == pd.id).values(text="другой текст")
        )
    await session.rollback()

    new = await publish_document(session, org_id, ConsentType.PD, "Новая редакция")
    assert new.version == 2
    current = await get_current_document(session, org_id, ConsentType.PD)
    assert current is not None
    assert current.id == new.id


async def test_only_one_open_assignment_per_point(session: AsyncSession) -> None:
    tenant = await make_tenant(session)
    session.add(
        QRAssignment(
            organization_id=tenant.org.id,
            qr_point_id=tenant.point.id,
            employee_id=tenant.employee.id,
        )
    )
    await session.flush()
    await assert_rejected(
        session,
        QRAssignment(
            organization_id=tenant.org.id,
            qr_point_id=tenant.point.id,
            employee_id=tenant.employee.id,
        ),
    )


async def test_membership_role_scope(session: AsyncSession) -> None:
    tenant = await make_tenant(session)
    user = User(email="m@example.com", password_hash="x", full_name="Управляющий")
    session.add(user)
    await session.flush()
    # A manager must be bound to a branch.
    await assert_rejected(
        session, Membership(organization_id=tenant.org.id, user_id=user.id, role=Role.MANAGER)
    )
