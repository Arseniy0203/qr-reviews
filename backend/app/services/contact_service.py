"""Customer contacts in reviews: lookup by phone and consent revocation (152-FZ / 38-FZ).

Revoking PD consent wipes the name and phone in every review of this customer in the
organization and records pd_consent_revoked_at. The consent document reference and the
consent date are kept as evidence; the review itself (rating, text) stays.
Revoking only marketing consent keeps the contact and records marketing_consent_revoked_at.
"""

import logging
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Review, Role
from app.schemas.review import ContactReviewOut, RevokeResult
from app.services.phone import InvalidPhoneError, normalize_phone
from app.services.scope import AccessScope

logger = logging.getLogger(__name__)

RevokeKind = Literal["pd", "marketing"]


def _phone(raw: str) -> str:
    try:
        return normalize_phone(raw)
    except InvalidPhoneError as exc:
        raise AppError("Некорректный номер телефона") from exc


async def find_by_phone(
    session: AsyncSession, scope: AccessScope, phone: str
) -> list[ContactReviewOut]:
    scope.require_role(Role.OWNER)
    reviews = await session.scalars(
        select(Review)
        .where(scope.org_filter(Review), Review.customer_phone == _phone(phone))
        .order_by(Review.created_at.desc())
    )
    return [ContactReviewOut.model_validate(r) for r in reviews]


async def revoke_consent(
    session: AsyncSession, scope: AccessScope, phone: str, kind: RevokeKind
) -> RevokeResult:
    scope.require_role(Role.OWNER)
    normalized = _phone(phone)
    now = datetime.now(UTC)
    base = update(Review).where(scope.org_filter(Review), Review.customer_phone == normalized)

    marketing_ids = list(
        await session.scalars(
            base.where(
                Review.marketing_consent_document_id.is_not(None),
                Review.marketing_consent_revoked_at.is_(None),
            )
            .values(marketing_consent_revoked_at=now)
            .returning(Review.id)
        )
    )
    affected = marketing_ids
    if kind == "pd":
        affected = list(
            await session.scalars(
                base.values(
                    customer_name=None, customer_phone=None, pd_consent_revoked_at=now
                ).returning(Review.id)
            )
        )
    await session.commit()
    logger.info(
        "Consent revoked (%s) in org=%s by user=%s: %s reviews",
        kind,
        scope.organization_id,
        scope.user_id,
        len(affected),
    )
    return RevokeResult(kind=kind, reviews_affected=len(affected), revoked_at=now)
