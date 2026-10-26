"""Owner API: find a customer's reviews by phone and revoke consents (152-FZ / 38-FZ)."""

from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConsentType, Review
from app.services.consent_service import get_current_document
from tests.api_helpers import bearer
from tests.factories import Team, Tenant, make_tenant

PHONE = "+79991234567"


async def add_contact_review(
    session: AsyncSession, tenant: Tenant, *, phone: str = PHONE, marketing: bool = False
) -> Review:
    pd = await get_current_document(session, tenant.org.id, ConsentType.PD)
    mk = await get_current_document(session, tenant.org.id, ConsentType.MARKETING)
    assert pd is not None
    assert mk is not None
    now = datetime.now(UTC)
    review = Review(
        organization_id=tenant.org.id,
        branch_id=tenant.branch.id,
        qr_point_id=tenant.point.id,
        rating=2,
        text="Долго ждала",
        customer_name="Мария",
        customer_phone=phone,
        pd_consent_document_id=pd.id,
        pd_consent_at=now,
        marketing_consent_document_id=mk.id if marketing else None,
        marketing_consent_at=now if marketing else None,
    )
    session.add(review)
    await session.commit()
    return review


async def test_find_by_phone_in_any_format_owner_only(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    await add_contact_review(session, team.tenant)
    await add_contact_review(session, team.tenant, phone="+79990000000")

    response = await client.get(
        "/api/v1/reviews/contacts",
        params={"phone": "8 (999) 123-45-67"},
        headers=bearer(team.owner),
    )
    assert response.status_code == 200
    assert [r["customer_phone"] for r in response.json()] == [PHONE]

    for token in (team.manager, team.employee):
        denied = await client.get(
            "/api/v1/reviews/contacts", params={"phone": PHONE}, headers=bearer(token)
        )
        assert denied.status_code == 403

    invalid = await client.get(
        "/api/v1/reviews/contacts", params={"phone": "12345"}, headers=bearer(team.owner)
    )
    assert invalid.status_code == 400


async def test_revoke_pd_consent_wipes_contact_keeps_evidence(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    first_id = (await add_contact_review(session, team.tenant, marketing=True)).id
    second_id = (await add_contact_review(session, team.tenant)).id
    other_customer = await add_contact_review(session, team.tenant, phone="+79990000000")
    stranger = await make_tenant(session, "Другой салон")
    await session.commit()
    other_org = await add_contact_review(session, stranger)  # same phone, another operator

    response = await client.post(
        "/api/v1/reviews/contacts/revoke",
        json={"phone": "+7 (999) 123-45-67", "kind": "pd"},
        headers=bearer(team.owner),
    )
    assert response.status_code == 200
    assert response.json()["reviews_affected"] == 2

    for review_id in (first_id, second_id):
        review = await session.get(Review, review_id, populate_existing=True)
        assert review is not None
        assert review.customer_name is None
        assert review.customer_phone is None
        assert review.pd_consent_revoked_at is not None
        assert review.pd_consent_document_id is not None  # evidence of the original consent
        assert review.pd_consent_at is not None
        assert review.rating == 2  # the review itself stays
        assert review.text == "Долго ждала"
    revoked_first = await session.get(Review, first_id, populate_existing=True)
    assert revoked_first is not None
    assert revoked_first.marketing_consent_revoked_at is not None

    untouched = await session.scalars(
        select(Review).where(Review.id.in_([other_customer.id, other_org.id]))
    )
    assert all(r.customer_phone and r.pd_consent_revoked_at is None for r in untouched)

    search = await client.get(
        "/api/v1/reviews/contacts", params={"phone": PHONE}, headers=bearer(team.owner)
    )
    assert search.json() == []


async def test_revoke_marketing_only_keeps_contact(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    review_id = (await add_contact_review(session, team.tenant, marketing=True)).id
    response = await client.post(
        "/api/v1/reviews/contacts/revoke",
        json={"phone": PHONE, "kind": "marketing"},
        headers=bearer(team.owner),
    )
    assert response.json()["reviews_affected"] == 1

    fresh = await session.get(Review, review_id, populate_existing=True)
    assert fresh is not None
    assert fresh.customer_phone == PHONE
    assert fresh.pd_consent_revoked_at is None
    assert fresh.marketing_consent_revoked_at is not None


async def test_revoke_requires_owner(client: httpx.AsyncClient, team: Team) -> None:
    response = await client.post(
        "/api/v1/reviews/contacts/revoke", json={"phone": PHONE}, headers=bearer(team.manager)
    )
    assert response.status_code == 403
