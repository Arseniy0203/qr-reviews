"""Negative review alerts: enqueue on submit, recipients, message, delivery errors."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from arq import Retry
from arq.jobs import Job, JobStatus
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.core.queue import get_queue
from app.models import ConsentType, Employee, Review, ReviewCategory, Role, User
from app.services.consent_service import publish_document
from app.services.notifications import TelegramChannel
from app.workers import tasks
from tests.api_helpers import add_member
from tests.factories import Team, make_tenant
from tests.public_helpers import assign, client_from, post_review, set_org_settings

TOKEN = "123:test-token"


class FakeTelegram:
    """Records sent messages; answers with a queue of statuses (200 when it runs out)."""

    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses)
        self.sent: list[dict[str, Any]] = []
        self.channel = TelegramChannel(
            TOKEN, httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        status = self.statuses.pop(0) if self.statuses else 200
        if status == 200:
            self.sent.append(json.loads(request.content))
        return httpx.Response(status, json={"ok": status == 200})

    @property
    def chat_ids(self) -> set[int]:
        return {m["chat_id"] for m in self.sent}


async def link_telegram(session: AsyncSession, email: str, chat_id: int) -> None:
    await session.execute(update(User).where(User.email == email).values(telegram_chat_id=chat_id))
    await session.commit()


async def submit(token: str, rating: int, **kwargs: Any) -> int:
    response = await post_review(client_from("10.0.0.1"), token, rating=rating, **kwargs)
    assert response.status_code == 303
    return await latest_review_id()


async def latest_review_id() -> int:
    async with get_sessionmaker()() as session:
        review_id = await session.scalar(select(Review.id).order_by(Review.id.desc()).limit(1))
    assert review_id is not None
    return review_id


async def alert_queued(review_id: int) -> bool:
    status = await Job(f"negative-alert:{review_id}", get_queue()).status()
    return status == JobStatus.queued


async def run_alert(review_id: int, telegram: FakeTelegram) -> None:
    """What the worker does: fan out, then run every delivery job that was enqueued."""
    ctx: dict[str, Any] = {"channels": {"telegram": telegram.channel}, "redis": get_queue()}
    await tasks.send_negative_alert(ctx, review_id)
    for job in await get_queue().queued_jobs():
        if job.function == "deliver_alert" and job.args[0] == review_id:
            await tasks.deliver_alert({**ctx, "job_try": 1}, *job.args)


# --- enqueue on submit -------------------------------------------------------------


@pytest.mark.parametrize(("rating", "queued"), [(1, True), (3, True), (4, False), (5, False)])
async def test_alert_enqueued_by_default_threshold(team: Team, rating: int, queued: bool) -> None:
    review_id = await submit(team.tenant.point.token, rating)
    assert await alert_queued(review_id) is queued


async def test_threshold_comes_from_org_settings(team: Team, session: AsyncSession) -> None:
    await set_org_settings(session, team.tenant.org.id, negative_threshold=4)
    assert await alert_queued(await submit(team.tenant.point.token, 4))
    await set_org_settings(session, team.tenant.org.id, negative_threshold=1)
    assert not await alert_queued(await submit(team.tenant.point.token, 2))


# --- recipients ----------------------------------------------------------------------


async def test_alert_goes_to_owner_and_branch_manager_only(
    team: Team, session: AsyncSession
) -> None:
    await add_member(
        session, team.tenant.org.id, Role.MANAGER, email="b@team.ru", branch_id=team.branch_b.id
    )
    await add_member(session, team.tenant.org.id, Role.OWNER, email="owner2@team.ru")
    other = await make_tenant(session, "Чужой салон")
    await session.commit()
    await add_member(session, other.org.id, Role.OWNER, email="other@salon.ru")
    for email, chat_id in [
        ("owner@team.ru", 1),
        ("manager@team.ru", 2),  # branch A
        ("employee@team.ru", 3),
        ("b@team.ru", 4),  # manager of branch B
        ("other@salon.ru", 5),  # owner of another organization
    ]:
        await link_telegram(session, email, chat_id)
    # owner2@team.ru has no Telegram linked.

    telegram = FakeTelegram()
    await run_alert(await submit(team.tenant.point.token, 2), telegram)
    assert telegram.chat_ids == {1, 2}

    telegram = FakeTelegram()
    await run_alert(await submit(team.point_b.token, 1), telegram)
    assert telegram.chat_ids == {1, 4}


async def test_inactive_user_gets_nothing(team: Team, session: AsyncSession) -> None:
    await link_telegram(session, "owner@team.ru", 1)
    await session.execute(update(User).where(User.email == "owner@team.ru").values(is_active=False))
    await session.commit()
    telegram = FakeTelegram()
    await run_alert(await submit(team.tenant.point.token, 1), telegram)
    assert telegram.sent == []


# --- message ---------------------------------------------------------------------------


async def test_message_content_without_customer_pd(team: Team, session: AsyncSession) -> None:
    await link_telegram(session, "owner@team.ru", 1)
    await assign(session, team.tenant.employee, team.tenant.point.id)
    category = await session.scalar(
        select(ReviewCategory).where(ReviewCategory.organization_id == team.tenant.org.id)
    )
    assert category is not None
    review_id = await submit(
        team.tenant.point.token, 2, text="Опоздали на 20 минут", category_ids=[category.id]
    )
    consent = await publish_document(session, team.tenant.org.id, ConsentType.PD, "Согласие")
    await session.execute(
        update(Review)
        .where(Review.id == review_id)
        .values(
            customer_name="Иван",
            customer_phone="+79990001122",
            pd_consent_document_id=consent.id,
            pd_consent_at=datetime.now(UTC),
        )
    )
    await session.commit()

    telegram = FakeTelegram()
    await run_alert(review_id, telegram)
    (message,) = telegram.sent
    text = message["text"]
    employee = await session.get_one(Employee, team.tenant.employee.id)
    assert "★★☆☆☆ 2 из 5" in text
    assert f"Филиал: {team.tenant.branch.name}" in text
    assert f"Точка: {team.tenant.point.name}" in text
    assert f"Мастер: {employee.full_name}" in text
    assert f"Категории: {category.name}" in text
    assert "«Опоздали на 20 минут»" in text
    assert f"http://localhost:8080/reviews/{review_id}" in text
    assert "⚠️" not in text
    assert "Иван" not in text
    assert "79990001122" not in text


async def test_suspicious_review_is_alerted_with_mark(team: Team, session: AsyncSession) -> None:
    await link_telegram(session, "owner@team.ru", 1)
    review_id = await submit(team.tenant.point.token, 1)
    await session.execute(
        update(Review)
        .where(Review.id == review_id)
        .values(is_suspicious=True, suspicious_reasons=["device_repeat", "duplicate_text"])
    )
    await session.commit()

    telegram = FakeTelegram()
    await run_alert(review_id, telegram)
    (message,) = telegram.sent
    assert (
        "⚠️ Возможна накрутка: повторный отзыв с того же устройства, "
        "текст почти совпадает с другим отзывом"
    ) in message["text"]


# --- delivery errors ---------------------------------------------------------------


async def delivery_ctx(telegram: FakeTelegram, job_try: int = 1) -> dict[str, Any]:
    return {"channels": {"telegram": telegram.channel}, "redis": get_queue(), "job_try": job_try}


async def owner_id(session: AsyncSession) -> uuid.UUID:
    user_id = await session.scalar(select(User.id).where(User.email == "owner@team.ru"))
    assert user_id is not None
    return user_id


@pytest.mark.parametrize("status", [429, 500, 502])
async def test_temporary_errors_are_retried(team: Team, session: AsyncSession, status: int) -> None:
    await link_telegram(session, "owner@team.ru", 1)
    review_id = await submit(team.tenant.point.token, 1)
    ctx = await delivery_ctx(FakeTelegram(status))
    with pytest.raises(Retry):
        await tasks.deliver_alert(ctx, review_id, str(await owner_id(session)), "telegram")


async def test_blocked_bot_is_logged_and_not_retried(
    team: Team, session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    await link_telegram(session, "owner@team.ru", 1)
    review_id = await submit(team.tenant.point.token, 1, text="Ужасно")
    ctx = await delivery_ctx(FakeTelegram(403))
    sent = await tasks.deliver_alert(ctx, review_id, str(await owner_id(session)), "telegram")
    assert sent is False
    assert "unreachable" in caplog.text
    assert "Ужасно" not in caplog.text


async def test_unlinked_after_fan_out_gets_nothing(team: Team, session: AsyncSession) -> None:
    review_id = await submit(team.tenant.point.token, 1)
    telegram = FakeTelegram()
    sent = await tasks.deliver_alert(
        await delivery_ctx(telegram), review_id, str(await owner_id(session)), "telegram"
    )
    assert sent is False
    assert telegram.sent == []


async def test_disabled_channel_drops_delivery(team: Team, session: AsyncSession) -> None:
    review_id = await submit(team.tenant.point.token, 1)
    ctx = {"channels": {}, "redis": get_queue(), "job_try": 1}
    assert not await tasks.deliver_alert(ctx, review_id, str(await owner_id(session)), "telegram")
