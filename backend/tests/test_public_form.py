"""Public review form: rendering, intake, contact step with consents, thanks, stubs."""

import re
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.redis import get_redis
from app.models import (
    ConsentType,
    Employee,
    QRAssignment,
    QRPoint,
    Review,
    ReviewCategory,
)
from app.services.consent_service import get_current_document, publish_document
from app.services.review_service import honeypot_counter_key
from tests.factories import Team, make_tenant
from tests.public_helpers import (
    LEGAL,
    assign,
    client_from,
    location_ref,
    open_form,
    post_review,
    set_org_settings,
)


async def latest_review(session: AsyncSession) -> Review | None:
    # populate_existing: re-read rows committed by the app's own sessions without
    # expiring (and lazily reloading) every other object held by the test session.
    return await session.scalar(
        select(Review).order_by(Review.id.desc()).limit(1).execution_options(populate_existing=True)
    )


async def org_categories(session: AsyncSession, team: Team) -> list[ReviewCategory]:
    return list(
        await session.scalars(
            select(ReviewCategory)
            .where(ReviewCategory.organization_id == team.tenant.org.id)
            .order_by(ReviewCategory.sort_order)
        )
    )


# --- rendering --------------------------------------------------------------------


async def test_form_title_names_assigned_master_in_genitive(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    anna = Employee(
        organization_id=team.tenant.org.id,
        branch_id=team.tenant.branch.id,
        full_name="Анна Соколова",
    )
    session.add(anna)
    await session.flush()
    await assign(session, anna, team.tenant.point.id)

    response = await client.get(f"/r/{team.tenant.point.token}")
    assert response.status_code == 200
    assert "<h1>Оцените работу мастера Анны</h1>" in response.text
    assert "Качество работы" in response.text  # niche categories as chips
    assert "Лаванда · Лаванда: филиал" in response.text

    cookie = response.headers["set-cookie"]
    assert "qr_device=" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/r" in cookie


async def test_form_neutral_title_without_master(client: httpx.AsyncClient, team: Team) -> None:
    response = await client.get(f"/r/{team.tenant.point.token}")
    assert "<h1>Как прошёл ваш визит?</h1>" in response.text


async def test_form_is_light_and_self_contained(client: httpx.AsyncClient, team: Team) -> None:
    response = await client.get(f"/r/{team.tenant.point.token}")
    html = response.text
    assert len(response.content) < 15_000
    # No external resources: everything is inline, no third-party hosts.
    assert not re.search(r'(src|href)="https?://', html)
    nonce = re.search(r'<style nonce="([^"]+)"', html)
    assert nonce
    csp = response.headers["content-security-policy"]
    assert f"'nonce-{nonce.group(1)}'" in csp
    assert "default-src 'none'" in csp
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert "mc.yandex.ru" not in html  # Metrika is off unless YANDEX_METRIKA_ID is set


async def test_yandex_metrika_only_when_configured(
    client: httpx.AsyncClient, team: Team, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "yandex_metrika_id", "12345678")
    response = await client.get(f"/r/{team.tenant.point.token}")
    assert "https://mc.yandex.ru/metrika/tag.js" in response.text
    assert "https://mc.yandex.ru" in response.headers["content-security-policy"]


async def test_unavailable_points_show_stubs(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    unknown = await client.get("/r/zzzzzzzzzz")
    assert unknown.status_code == 404
    assert "QR-код недействителен" in unknown.text

    await session.execute(
        update(QRPoint).where(QRPoint.id == team.point_b.id).values(is_active=False)
    )
    await session.execute(
        update(QRPoint)
        .where(QRPoint.id == team.tenant.point.id)
        .values(archived_at=datetime.now(UTC))
    )
    await session.commit()

    paused = await client.get(f"/r/{team.point_b.token}")
    assert paused.status_code == 200
    assert "Приём отзывов приостановлен" in paused.text
    archived = await client.get(f"/r/{team.tenant.point.token}")
    assert archived.status_code == 404

    # Submitting to a paused point stores nothing.
    response = await client.post(f"/r/{team.point_b.token}", data={"rating": "5"})
    assert "Приём отзывов приостановлен" in response.text
    assert await latest_review(session) is None


# --- intake -----------------------------------------------------------------------


async def test_submit_saves_review_with_full_context(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    await assign(session, team.tenant.employee, team.tenant.point.id)
    categories = await org_categories(session, team)
    foreign = await make_tenant(session, "Чужой")
    foreign_category = await session.scalar(
        select(ReviewCategory).where(ReviewCategory.organization_id == foreign.org.id).limit(1)
    )
    assert foreign_category is not None
    await session.commit()

    response = await post_review(
        client,
        team.tenant.point.token,
        rating=4,
        text="  Отличная стрижка, спасибо!  ",
        category_ids=[categories[0].id, categories[2].id, foreign_category.id, "garbage"],
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/r/{team.tenant.point.token}/thanks?ref=")

    review = await latest_review(session)
    assert review is not None
    assert review.rating == 4
    assert review.text == "Отличная стрижка, спасибо!"
    assert review.organization_id == team.tenant.org.id
    assert review.branch_id == team.tenant.branch.id
    assert review.qr_point_id == team.tenant.point.id
    assert review.employee_id == team.tenant.employee.id
    assert review.is_suspicious is False
    assert len(review.device_hash or "") == 64
    assert len(review.ip_hash or "") == 64
    assert "127.0.0.1" not in (review.ip_hash or "")
    linked = await session.scalars(
        select(ReviewCategory.id).join(Review.categories).where(Review.id == review.id)
    )
    assert set(linked) == {categories[0].id, categories[2].id}  # foreign/garbage ignored


async def test_employee_is_taken_at_submit_time(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    token = team.tenant.point.token
    form_token, device = await open_form(client, token)  # page opened before assignment
    await assign(session, team.tenant.employee, team.tenant.point.id)
    await post_review(client, token, form_token=form_token, device=device)
    first = await latest_review(session)
    assert first is not None
    assert first.employee_id == team.tenant.employee.id

    await session.execute(
        update(QRAssignment)
        .where(QRAssignment.qr_point_id == team.tenant.point.id, QRAssignment.ended_at.is_(None))
        .values(ended_at=datetime.now(UTC))
    )
    await session.commit()
    await assign(session, team.employee_b, team.tenant.point.id)  # cross-branch master
    await post_review(client_from("10.0.0.2"), token)
    second = await latest_review(session)
    assert second is not None
    assert second.employee_id == team.employee_b.id
    assert second.branch_id == team.tenant.branch.id  # branch comes from the point


@pytest.mark.parametrize(
    ("rating", "text", "message"),
    [
        ("", "", "Поставьте оценку от 1 до 5"),
        ("9", "", "Поставьте оценку от 1 до 5"),
        ("5", "я" * 2001, "Текст слишком длинный"),
    ],
)
async def test_invalid_submissions_rerender_form(
    client: httpx.AsyncClient,
    team: Team,
    session: AsyncSession,
    rating: str,
    text: str,
    message: str,
) -> None:
    response = await post_review(client, team.tenant.point.token, rating=rating, text=text)
    assert response.status_code == 400
    assert message in response.text
    assert await latest_review(session) is None


async def test_bad_form_token_rejected(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    other_point_token, device = await open_form(client, team.point_b.token)
    response = await post_review(
        client, team.tenant.point.token, form_token=other_point_token, device=device, text="Хорошо"
    )
    assert response.status_code == 400
    assert "Страница устарела" in response.text
    assert "Хорошо" in response.text  # the text is preserved
    assert await latest_review(session) is None


async def test_honeypot_drops_bots(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    response = await post_review(client, team.tenant.point.token, extra={"website": "spam.ru"})
    assert response.status_code == 303
    assert await latest_review(session) is None
    await post_review(client, team.tenant.point.token, extra={"website": "spam.ru"})
    key = honeypot_counter_key(team.tenant.org.id, datetime.now(UTC).date().isoformat())
    assert await get_redis().get(key) == "2"


async def test_form_rate_limit_per_ip(
    team: Team, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_public_form_max", 2)
    salon_wifi = client_from("192.168.1.10")
    codes = [(await post_review(salon_wifi, team.tenant.point.token)).status_code for _ in range(3)]
    assert codes == [303, 303, 429]
    other_network = client_from("192.168.1.11")
    assert (await post_review(other_network, team.tenant.point.token)).status_code == 303


# --- thanks page ------------------------------------------------------------------


async def test_map_buttons_only_when_configured_and_for_any_rating(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    token = team.tenant.point.token
    response = await post_review(client, token, rating=1)
    page = await client.get(response.headers["location"])
    assert "Спасибо за отзыв!" in page.text
    assert "Яндекс" not in page.text
    assert "2ГИС" not in page.text

    await set_org_settings(
        session, team.tenant.org.id, yandex_maps_url="https://yandex.ru/maps/org/1/"
    )
    response = await post_review(client_from("10.0.0.3"), token, rating=1)  # no gating
    page = await client.get(response.headers["location"])
    assert "Оставить отзыв на Яндекс Картах" in page.text
    assert 'href="https://yandex.ru/maps/org/1/"' in page.text
    assert 'rel="noopener noreferrer"' in page.text
    assert "2ГИС" not in page.text


# --- contact step -----------------------------------------------------------------


async def enable_contacts(session: AsyncSession, team: Team) -> None:
    await set_org_settings(session, team.tenant.org.id, collect_contact=True, **LEGAL)


async def test_contact_step_flow(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    await enable_contacts(session, team)
    token = team.tenant.point.token
    submitted = await post_review(client, token, rating=5)
    assert submitted.headers["location"].startswith(f"/r/{token}/contact?ref=")
    ref = location_ref(submitted)

    page = await client.get(submitted.headers["location"])
    assert page.status_code == 200
    assert "скидку 10%" in page.text  # niche default offer
    assert f"/r/{token}/consent/pd?v=" in page.text
    assert f"/r/{token}/consent/marketing?v=" in page.text
    assert " checked" not in page.text  # both consent boxes are off by default
    pd_doc = await get_current_document(session, team.tenant.org.id, ConsentType.PD)
    assert pd_doc is not None

    without_consent = await client.post(
        f"/r/{token}/contact",
        data={"ref": ref, "phone": "8 (999) 123-45-67", "pd_document_id": str(pd_doc.id)},
    )
    assert without_consent.status_code == 400
    assert "Без согласия на обработку" in without_consent.text
    review = await latest_review(session)
    assert review is not None
    assert review.customer_phone is None

    bad_phone = await client.post(
        f"/r/{token}/contact",
        data={"ref": ref, "phone": "123", "pd_consent": "1", "pd_document_id": str(pd_doc.id)},
    )
    assert bad_phone.status_code == 400
    assert "Проверьте номер телефона" in bad_phone.text

    saved = await client.post(
        f"/r/{token}/contact",
        data={
            "ref": ref,
            "name": "Мария",
            "phone": "8 (999) 123-45-67",
            "pd_consent": "1",
            "pd_document_id": str(pd_doc.id),
        },
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == f"/r/{token}/thanks"
    review = await latest_review(session)
    assert review is not None
    assert review.customer_name == "Мария"
    assert review.customer_phone == "+79991234567"
    assert review.pd_consent_document_id == pd_doc.id
    assert review.pd_consent_at is not None
    assert review.marketing_consent_document_id is None  # optional box was not ticked

    again = await client.post(
        f"/r/{token}/contact",
        data={
            "ref": ref,
            "phone": "+79990000000",
            "pd_consent": "1",
            "pd_document_id": str(pd_doc.id),
        },
    )
    assert again.status_code == 400
    review = await latest_review(session)
    assert review is not None
    assert review.customer_phone == "+79991234567"


async def test_contact_with_marketing_consent(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    await enable_contacts(session, team)
    token = team.tenant.point.token
    ref = location_ref(await post_review(client, token))
    pd_doc = await get_current_document(session, team.tenant.org.id, ConsentType.PD)
    marketing_doc = await get_current_document(session, team.tenant.org.id, ConsentType.MARKETING)
    assert pd_doc is not None
    assert marketing_doc is not None
    await client.post(
        f"/r/{token}/contact",
        data={
            "ref": ref,
            "phone": "+7 999 765-43-21",
            "pd_consent": "1",
            "pd_document_id": str(pd_doc.id),
            "marketing_consent": "1",
            "marketing_document_id": str(marketing_doc.id),
        },
    )
    review = await latest_review(session)
    assert review is not None
    assert review.marketing_consent_document_id == marketing_doc.id
    assert review.marketing_consent_at is not None


async def test_skip_contact_step(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    await enable_contacts(session, team)
    token = team.tenant.point.token
    ref = location_ref(await post_review(client, token))
    response = await client.post(f"/r/{token}/contact", data={"ref": ref, "action": "skip"})
    assert response.headers["location"] == f"/r/{token}/thanks"
    review = await latest_review(session)
    assert review is not None
    assert review.customer_phone is None


async def test_contact_step_hidden_without_legal_details(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    # Bypass the API check to simulate an inconsistent state: the form must still be safe.
    await set_org_settings(session, team.tenant.org.id, collect_contact=True)
    token = team.tenant.point.token
    submitted = await post_review(client, token)
    assert submitted.headers["location"].startswith(f"/r/{token}/thanks")
    contact = await client.get(f"/r/{token}/contact?ref={location_ref(submitted)}")
    assert contact.status_code == 303
    assert contact.headers["location"] == f"/r/{token}/thanks"


async def test_contact_ref_is_bound_to_point(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    await enable_contacts(session, team)
    ref = location_ref(await post_review(client, team.tenant.point.token))
    for bad_ref in (ref + "x", "garbage"):
        response = await client.get(f"/r/{team.tenant.point.token}/contact?ref={bad_ref}")
        assert response.headers["location"] == f"/r/{team.tenant.point.token}/thanks"
    # A valid ref of point A cannot be used on point B.
    response = await client.get(f"/r/{team.point_b.token}/contact?ref={ref}")
    assert response.status_code == 303


# --- consent pages ----------------------------------------------------------------


async def test_consent_page_explains_revocation(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    await enable_contacts(session, team)
    old = await get_current_document(session, team.tenant.org.id, ConsentType.PD)
    assert old is not None
    token = team.tenant.point.token

    page = await client.get(f"/r/{token}/consent/pd")
    assert page.status_code == 200
    assert "Согласие на обработку персональных данных" in page.text
    assert "152-ФЗ" in page.text
    assert "Как отозвать согласие" in page.text
    assert 'href="mailto:privacy@lavanda.ru"' in page.text

    await publish_document(session, team.tenant.org.id, ConsentType.PD, "Редакция 2")
    await session.commit()
    pinned = await client.get(f"/r/{token}/consent/pd?v={old.id}")
    assert "152-ФЗ" in pinned.text  # the version the customer actually saw
    current = await client.get(f"/r/{token}/consent/pd")
    assert "Редакция 2" in current.text

    marketing = await client.get(f"/r/{token}/consent/marketing")
    assert "38-ФЗ" in marketing.text
    assert (await client.get(f"/r/{token}/consent/other")).status_code == 422
