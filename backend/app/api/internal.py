"""Internal endpoints for the bot: /internal/*, guarded by INTERNAL_API_SECRET.

nginx answers 404 for /internal/ from outside; the bot calls the backend inside the docker network.
"""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, status

from app.api.deps import SessionDep
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.schemas.telegram import InternalLinkRequest
from app.services import telegram_service


async def require_internal_secret(
    x_internal_secret: Annotated[str | None, Header()] = None,
) -> None:
    expected = get_settings().internal_api_secret.get_secret_value()
    if x_internal_secret is None or not hmac.compare_digest(x_internal_secret, expected):
        raise NotFoundError()  # do not reveal that the endpoint exists


router = APIRouter(
    prefix="/internal",
    include_in_schema=False,
    dependencies=[Depends(require_internal_secret)],
)


@router.post("/telegram/link", status_code=status.HTTP_204_NO_CONTENT)
async def link_telegram(data: InternalLinkRequest, session: SessionDep) -> None:
    await telegram_service.link_chat(session, data.code, data.chat_id)
