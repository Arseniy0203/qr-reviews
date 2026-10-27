from fastapi import APIRouter, status

from app.api.deps import CurrentUser, SessionDep
from app.schemas.telegram import LinkCodeOut
from app.services import telegram_service

router = APIRouter(prefix="/telegram", tags=["telegram"])


# Any role may link Telegram (employees too); alerts decide who gets what.
@router.post("/link-code", status_code=status.HTTP_201_CREATED)
async def create_link_code(user: CurrentUser, session: SessionDep) -> LinkCodeOut:
    code, expires_at = await telegram_service.create_link_code(session, user)
    return LinkCodeOut(deep_link=telegram_service.deep_link(code), expires_at=expires_at)


@router.delete("/link", status_code=status.HTTP_204_NO_CONTENT)
async def unlink(user: CurrentUser, session: SessionDep) -> None:
    await telegram_service.unlink(session, user)
