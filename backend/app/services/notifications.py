"""Notification channels. The worker talks only to `NotificationChannel`.

Adding a channel (email, MAX, SMS) = a new implementation + an address field; no worker changes.
Messages are plain text: no markup, so customer-written review texts need no escaping.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.core.config import Settings
from app.models import User

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org"
TELEGRAM_TEXT_LIMIT = 4096


@dataclass(frozen=True)
class Message:
    text: str


class DeliveryError(Exception):
    """Permanent failure on our side (bad request): retrying will not help."""


class RecipientUnreachableError(DeliveryError):
    """The recipient cannot get messages (e.g. blocked the bot). Not retried."""


class TemporaryDeliveryError(Exception):
    """Rate limit or provider outage: worth retrying, after `retry_after` seconds if known."""

    def __init__(self, reason: str, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(reason)


class NotificationChannel(ABC):
    name: str

    @abstractmethod
    def address_for(self, user: User) -> str | None:
        """The user's address in this channel, or None if the user is not reachable here."""

    @abstractmethod
    async def send(self, address: str, message: Message) -> None:
        """Deliver one message. Raises DeliveryError / TemporaryDeliveryError."""


class TelegramChannel(NotificationChannel):
    """Telegram Bot API over plain httpx (no aiogram in the backend)."""

    name = "telegram"

    def __init__(self, token: str, client: httpx.AsyncClient) -> None:
        self._url = f"{TELEGRAM_API_URL}/bot{token}/sendMessage"
        self._client = client

    def address_for(self, user: User) -> str | None:
        return str(user.telegram_chat_id) if user.telegram_chat_id is not None else None

    async def send(self, address: str, message: Message) -> None:
        payload = {
            "chat_id": int(address),
            "text": truncate(message.text, TELEGRAM_TEXT_LIMIT),
            "link_preview_options": {"is_disabled": True},
        }
        try:
            response = await self._client.post(self._url, json=payload)
        except httpx.HTTPError as exc:
            # The exception text may contain the URL with the token: log only its type.
            raise TemporaryDeliveryError(f"transport error: {type(exc).__name__}") from None

        if response.status_code == 200:
            return
        if response.status_code == 429:
            retry_after = _json(response).get("parameters", {}).get("retry_after")
            raise TemporaryDeliveryError("rate limited", retry_after=retry_after)
        if response.status_code >= 500:
            raise TemporaryDeliveryError(f"telegram {response.status_code}")
        if response.status_code == 403 or _chat_gone(response):
            raise RecipientUnreachableError(f"telegram {response.status_code}")
        raise DeliveryError(f"telegram {response.status_code}")


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _chat_gone(response: httpx.Response) -> bool:
    description = str(_json(response).get("description", "")).lower()
    return response.status_code == 400 and "chat not found" in description


def build_channels(settings: Settings, client: httpx.AsyncClient) -> list[NotificationChannel]:
    """Channels enabled by the configuration. No Telegram token -> no Telegram channel."""
    channels: list[NotificationChannel] = []
    if settings.telegram_bot_token is not None:
        channels.append(TelegramChannel(settings.telegram_bot_token.get_secret_value(), client))
    else:
        logger.warning("TELEGRAM_BOT_TOKEN is not set: Telegram notifications are disabled")
    return channels
