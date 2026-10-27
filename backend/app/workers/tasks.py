"""arq background tasks.

ctx (set up in workers.settings.on_startup): `channels` — {name: NotificationChannel},
`redis` — the arq pool (added by arq itself), `job_try` — the attempt number.
"""

import logging
import uuid
from typing import Any

from arq import Retry

from app.core.db import get_sessionmaker
from app.services import alert_service
from app.services.notifications import (
    DeliveryError,
    RecipientUnreachableError,
    TemporaryDeliveryError,
)

logger = logging.getLogger(__name__)

RETRY_BACKOFF_SECONDS = 10


async def ping(_: dict[str, Any]) -> str:
    """Liveness task, handy for checking that the worker consumes the queue."""
    return "pong"


async def send_negative_alert(ctx: dict[str, Any], review_id: int) -> int:
    """Fan a negative review alert out into one delivery job per recipient and channel."""
    channels = list(ctx["channels"].values())
    async with get_sessionmaker()() as session:
        return await alert_service.fan_out_negative_alert(
            session, ctx["redis"], channels, review_id
        )


async def deliver_alert(ctx: dict[str, Any], review_id: int, user_id: str, channel: str) -> bool:
    """Send one alert. 429/5xx are retried by arq (max_tries); a blocked bot is not."""
    notification_channel = ctx["channels"].get(channel)
    if notification_channel is None:
        logger.warning("Channel %s is disabled, alert for review %s dropped", channel, review_id)
        return False
    async with get_sessionmaker()() as session:
        try:
            return await alert_service.deliver_negative_alert(
                session, notification_channel, review_id, uuid.UUID(user_id)
            )
        except TemporaryDeliveryError as exc:
            defer = exc.retry_after or RETRY_BACKOFF_SECONDS * ctx["job_try"]
            logger.warning(
                "Alert for review %s to user %s via %s failed (%s), retry in %ss",
                review_id,
                user_id,
                channel,
                exc,
                defer,
            )
            raise Retry(defer=defer) from None
        except RecipientUnreachableError as exc:
            logger.warning(
                "User %s unreachable via %s (%s), alert for review %s skipped",
                user_id,
                channel,
                exc,
                review_id,
            )
        except DeliveryError as exc:
            logger.error(
                "Alert for review %s to user %s via %s rejected (%s)",
                review_id,
                user_id,
                channel,
                exc,
            )
    return False


async def analyze_review_stub(_: dict[str, Any], review_id: int) -> None:
    """Placeholder for text analytics (aspects, sentiment); see ml/README.md."""
    logger.debug("analyze_review_stub: review %s (no-op)", review_id)
