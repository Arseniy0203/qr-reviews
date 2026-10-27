"""Negative review alerts.

submit_review enqueues `send_negative_alert`; the worker fans it out into one `deliver_alert`
job per recipient and channel, so a retry never re-sends to people who already got it.
Alerts carry no customer PD (name/phone): the contact step happens after the review anyway.
"""

import logging
import uuid
from zoneinfo import ZoneInfo

from arq.connections import ArqRedis
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.queue import enqueue
from app.models import (
    Branch,
    Employee,
    Membership,
    Organization,
    QRPoint,
    Review,
    Role,
    SuspiciousReason,
    User,
)
from app.schemas.organization import OrgSettings
from app.services.notifications import Message, NotificationChannel

logger = logging.getLogger(__name__)

SEND_NEGATIVE_ALERT = "send_negative_alert"
DELIVER_ALERT = "deliver_alert"

REASON_LABELS = {
    SuspiciousReason.DEVICE_REPEAT: "повторный отзыв с того же устройства",
    SuspiciousReason.IP_REPEAT: "много отзывов из одной сети",
    SuspiciousReason.POINT_BURST: "много отзывов на точке за короткое время",
    SuspiciousReason.EMPLOYEE_BURST: "много отзывов о мастере за короткое время",
    SuspiciousReason.DUPLICATE_TEXT: "текст почти совпадает с другим отзывом",
}


async def enqueue_negative_alert(review_id: int) -> None:
    await enqueue(SEND_NEGATIVE_ALERT, review_id, job_id=f"negative-alert:{review_id}")


async def alert_recipients(session: AsyncSession, review: Review) -> list[User]:
    """Active owners of the organization and managers of the review's branch."""
    result = await session.scalars(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.organization_id == review.organization_id,
            User.is_active,
            or_(
                Membership.role == Role.OWNER,
                and_(Membership.role == Role.MANAGER, Membership.branch_id == review.branch_id),
            ),
        )
        .order_by(User.created_at)
    )
    return list(result)


async def fan_out_negative_alert(
    session: AsyncSession,
    queue: ArqRedis,
    channels: list[NotificationChannel],
    review_id: int,
) -> int:
    """Enqueue one delivery per recipient and channel. Returns the number of deliveries."""
    review = await session.get(Review, review_id)
    if review is None:
        return 0
    deliveries = 0
    for user in await alert_recipients(session, review):
        for channel in channels:
            if channel.address_for(user) is None:
                continue
            await queue.enqueue_job(
                DELIVER_ALERT,
                review_id,
                str(user.id),
                channel.name,
                _job_id=f"alert:{review_id}:{user.id}:{channel.name}",
            )
            deliveries += 1
    logger.info("Negative alert for review %s: %s deliveries", review_id, deliveries)
    return deliveries


async def deliver_negative_alert(
    session: AsyncSession, channel: NotificationChannel, review_id: int, user_id: uuid.UUID
) -> bool:
    """Send the alert to one user. Returns False if there is nothing to send any more.

    Delivery errors propagate: the worker decides whether to retry.
    """
    review = await session.scalar(
        select(Review).where(Review.id == review_id).options(selectinload(Review.categories))
    )
    user = await session.get(User, user_id)
    if review is None or user is None:
        return False
    address = channel.address_for(user)  # the user may have unlinked since the fan-out
    if address is None:
        return False
    await channel.send(address, await build_alert_message(session, review))
    return True


async def build_alert_message(session: AsyncSession, review: Review) -> Message:
    organization = await session.get_one(Organization, review.organization_id)
    branch = await session.get_one(Branch, review.branch_id)
    point = await session.get_one(QRPoint, review.qr_point_id)
    employee = await session.get(Employee, review.employee_id) if review.employee_id else None
    timezone = ZoneInfo(OrgSettings.from_db(organization.settings).timezone)

    lines = [
        f"🔴 Негативный отзыв · {organization.name}",
        f"{stars(review.rating)} {review.rating} из 5",
    ]
    if review.is_suspicious:
        reasons = ", ".join(reason_label(code) for code in review.suspicious_reasons)
        lines.append(f"⚠️ Возможна накрутка: {reasons}")
    lines += ["", f"Филиал: {branch.name}", f"Точка: {point.name}"]
    if employee is not None:
        lines.append(f"Мастер: {employee.full_name}")
    if review.categories:
        names = [c.name for c in sorted(review.categories, key=lambda c: c.sort_order)]
        lines.append(f"Категории: {', '.join(names)}")
    lines.append(f"Время: {review.created_at.astimezone(timezone):%d.%m.%Y %H:%M}")
    lines += ["", f"«{review.text}»" if review.text else "Без текста"]
    lines += ["", f"Открыть в кабинете: {review_url(review.id)}"]
    return Message("\n".join(lines))


def stars(rating: int) -> str:
    return "★" * rating + "☆" * (5 - rating)


def reason_label(code: str) -> str:
    try:
        return REASON_LABELS[SuspiciousReason(code)]
    except (ValueError, KeyError):
        return code


def review_url(review_id: int) -> str:
    # The dashboard route lands in stage 8.
    return f"{get_settings().dashboard_base_url}/reviews/{review_id}"
