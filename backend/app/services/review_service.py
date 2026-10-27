"""Public review intake: load the point, save the review, attach a consented contact.

Never log customer names, phones or review texts (152-FZ): only ids and codes.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.core.redis import get_redis
from app.core.security import hash_identifier
from app.models import (
    Branch,
    ConsentDocument,
    ConsentType,
    Employee,
    Organization,
    QRAssignment,
    QRPoint,
    Review,
    ReviewCategory,
)
from app.schemas.organization import OrgSettings
from app.schemas.review import ReviewSubmission
from app.services import alert_service, antifraud
from app.services.consent_service import get_current_document
from app.services.names import first_name, genitive_first_name
from app.services.niches import NicheTemplate, get_niche
from app.services.phone import InvalidPhoneError, normalize_phone

logger = logging.getLogger(__name__)


class PointUnavailableError(Exception):
    """The QR point cannot accept reviews; `paused` distinguishes a temporary stop."""

    def __init__(self, paused: bool) -> None:
        self.paused = paused
        super().__init__("paused" if paused else "not found")


@dataclass(frozen=True)
class PublicPoint:
    point: QRPoint
    organization: Organization
    branch: Branch
    settings: OrgSettings
    niche: NicheTemplate
    employee: Employee | None
    categories: list[ReviewCategory]

    @property
    def title(self) -> str:
        if self.employee is not None:
            name = genitive_first_name(first_name(self.employee.full_name))
            return self.niche.form.title_with_employee.format(name=name)
        return self.niche.form.title

    @property
    def contact_offer(self) -> str:
        return self.settings.contact_offer_text or self.niche.contact_offer_default


async def _current_employee(session: AsyncSession, point_id: uuid.UUID) -> Employee | None:
    return await session.scalar(
        select(Employee)
        .join(QRAssignment, QRAssignment.employee_id == Employee.id)
        .where(QRAssignment.qr_point_id == point_id, QRAssignment.ended_at.is_(None))
    )


async def load_public_point(session: AsyncSession, token: str) -> PublicPoint:
    row = (
        await session.execute(
            select(QRPoint, Organization, Branch)
            .join(Organization, Organization.id == QRPoint.organization_id)
            .join(Branch, Branch.id == QRPoint.branch_id)
            .where(QRPoint.token == token)
        )
    ).first()
    if row is None:
        raise PointUnavailableError(paused=False)
    point, organization, branch = tuple(row)
    if point.archived_at is not None or branch.archived_at is not None:
        raise PointUnavailableError(paused=False)
    if not point.is_active:
        raise PointUnavailableError(paused=True)

    categories = list(
        await session.scalars(
            select(ReviewCategory)
            .where(
                ReviewCategory.organization_id == organization.id,
                ReviewCategory.archived_at.is_(None),
            )
            .order_by(ReviewCategory.sort_order, ReviewCategory.name)
        )
    )
    return PublicPoint(
        point=point,
        organization=organization,
        branch=branch,
        settings=OrgSettings.from_db(organization.settings),
        niche=get_niche(organization.niche),
        employee=await _current_employee(session, point.id),
        categories=categories,
    )


async def submit_review(
    session: AsyncSession,
    public: PublicPoint,
    data: ReviewSubmission,
    *,
    device_id: str | None,
    ip: str,
) -> Review:
    point = public.point
    # The employee assigned right now (not when the page was opened) gets the review.
    employee = await _current_employee(session, point.id)
    allowed = {c.id: c for c in public.categories}
    categories = [allowed[c] for c in dict.fromkeys(data.category_ids) if c in allowed]

    device_hash = hash_identifier(device_id) if device_id else None
    ip_hash = hash_identifier(ip)
    reasons = await antifraud.evaluate(
        antifraud.FraudContext(
            organization_id=point.organization_id,
            qr_point_id=point.id,
            employee_id=employee.id if employee else None,
            device_hash=device_hash,
            ip_hash=ip_hash,
            text=data.text,
        )
    )
    review = Review(
        organization_id=point.organization_id,
        branch_id=point.branch_id,
        qr_point_id=point.id,
        employee_id=employee.id if employee else None,
        rating=data.rating,
        text=data.text,
        is_suspicious=bool(reasons),
        suspicious_reasons=[r.value for r in reasons],
        device_hash=device_hash,
        ip_hash=ip_hash,
    )
    review.categories = categories
    session.add(review)
    await session.commit()
    logger.info(
        "Review %s saved: org=%s point=%s rating=%s flags=%s",
        review.id,
        point.organization_id,
        point.id,
        review.rating,
        review.suspicious_reasons,
    )
    if review.rating <= public.settings.negative_threshold:
        await alert_service.enqueue_negative_alert(review.id)
    return review


HONEYPOT_COUNTER_TTL_SECONDS = 90 * 24 * 3600


def honeypot_counter_key(organization_id: uuid.UUID, day: str) -> str:
    return f"metrics:honeypot:{organization_id}:{day}"


async def record_honeypot_hit(public: PublicPoint) -> None:
    """Count a bot that filled the honeypot. Nothing is stored in the DB; ids only, no PD."""
    point = public.point
    logger.warning("Honeypot hit: org=%s point=%s", point.organization_id, point.id)
    key = honeypot_counter_key(point.organization_id, datetime.now(UTC).date().isoformat())
    try:
        async with get_redis().pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, HONEYPOT_COUNTER_TTL_SECONDS, nx=True)
            await pipe.execute()
    except RedisError:
        logger.warning("Honeypot counter unavailable (Redis error)")


# --------------------------------------------------------------------------- contact step


@dataclass(frozen=True)
class ConsentDocs:
    pd: ConsentDocument
    marketing: ConsentDocument | None


async def current_consents(session: AsyncSession, public: PublicPoint) -> ConsentDocs:
    pd = await get_current_document(session, public.organization.id, ConsentType.PD)
    if pd is None:
        raise NotFoundError()
    marketing = await get_current_document(session, public.organization.id, ConsentType.MARKETING)
    return ConsentDocs(pd=pd, marketing=marketing)


class ContactFormError(AppError):
    code = "contact_invalid"


async def _consent_document(
    session: AsyncSession, public: PublicPoint, document_id: str | None, consent_type: ConsentType
) -> ConsentDocument | None:
    """The exact version shown on the page; must belong to this org and be of this type."""
    if not document_id:
        return None
    try:
        parsed = uuid.UUID(document_id)
    except ValueError:
        return None
    document = await session.get(ConsentDocument, parsed)
    if (
        document is None
        or document.organization_id != public.organization.id
        or document.type != consent_type
    ):
        return None
    return document


async def attach_contact(
    session: AsyncSession,
    public: PublicPoint,
    review_id: int,
    *,
    name: str | None,
    phone: str,
    pd_consent: bool,
    pd_document_id: str | None,
    marketing_consent: bool,
    marketing_document_id: str | None,
) -> Review:
    if not public.settings.contact_step_enabled:
        raise ContactFormError("Сбор контактов отключён")
    review = await session.get(Review, review_id)
    if review is None or review.qr_point_id != public.point.id:
        raise NotFoundError()
    if review.customer_phone is not None or review.pd_consent_document_id is not None:
        raise ContactFormError("Контакт к этому отзыву уже оставлен")

    errors: dict[str, str] = {}
    try:
        normalized_phone = normalize_phone(phone)
    except InvalidPhoneError:
        normalized_phone = ""
        errors["phone"] = "Проверьте номер телефона, например +7 999 123-45-67"
    clean_name = (name or "").strip() or None
    if clean_name and len(clean_name) > 100:
        errors["name"] = "Слишком длинное имя"
    pd_document = await _consent_document(session, public, pd_document_id, ConsentType.PD)
    if not pd_consent or pd_document is None:
        errors["pd_consent"] = (
            "Без согласия на обработку персональных данных мы не можем сохранить контакт"
        )
    marketing_document = (
        await _consent_document(session, public, marketing_document_id, ConsentType.MARKETING)
        if marketing_consent
        else None
    )
    if errors:
        raise ContactFormError("Проверьте форму", errors=errors)

    assert pd_document is not None
    now = datetime.now(UTC)
    review.customer_name = clean_name
    review.customer_phone = normalized_phone
    review.pd_consent_document_id = pd_document.id
    review.pd_consent_at = now
    if marketing_document is not None:
        review.marketing_consent_document_id = marketing_document.id
        review.marketing_consent_at = now
    await session.commit()
    logger.info(
        "Contact attached to review %s (pd_doc=%s, marketing=%s)",
        review.id,
        pd_document.id,
        marketing_document is not None,
    )
    return review
