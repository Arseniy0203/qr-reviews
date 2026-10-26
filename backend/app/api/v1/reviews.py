from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import OwnerScope, SessionDep
from app.schemas.review import ContactReviewOut, RevokeRequest, RevokeResult
from app.services import contact_service

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("/contacts")
async def find_contact_reviews(
    phone: Annotated[str, Query(min_length=5, max_length=30)],
    scope: OwnerScope,
    session: SessionDep,
) -> list[ContactReviewOut]:
    """Reviews of one customer by phone (any common format), for consent requests."""
    return await contact_service.find_by_phone(session, scope, phone)


@router.post("/contacts/revoke")
async def revoke_consent(
    data: RevokeRequest, scope: OwnerScope, session: SessionDep
) -> RevokeResult:
    return await contact_service.revoke_consent(session, scope, data.phone, data.kind)
