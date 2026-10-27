"""Client for the backend's internal API (/internal/*, shared secret header)."""

import enum
import logging

import httpx

logger = logging.getLogger(__name__)


class LinkResult(enum.Enum):
    LINKED = "linked"
    INVALID = "invalid"  # unknown, expired or already used code
    ERROR = "error"  # backend unavailable or misconfigured


class BackendClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str, secret: str) -> None:
        self._client = client
        self._base_url = base_url
        self._secret = secret

    async def link(self, code: str, chat_id: int) -> LinkResult:
        try:
            response = await self._client.post(
                f"{self._base_url}/internal/telegram/link",
                json={"code": code, "chat_id": chat_id},
                headers={"X-Internal-Secret": self._secret},
            )
        except httpx.HTTPError as exc:
            logger.error("Backend unavailable: %s", type(exc).__name__)
            return LinkResult.ERROR
        if response.status_code == 204:
            return LinkResult.LINKED
        if response.status_code == 404 and _error_code(response) == "telegram_code_invalid":
            return LinkResult.INVALID
        # A plain 404 here means the backend rejected INTERNAL_API_SECRET.
        logger.error("Backend link call failed: HTTP %s", response.status_code)
        return LinkResult.ERROR


def _error_code(response: httpx.Response) -> str | None:
    try:
        data = response.json()
    except ValueError:
        return None
    return data.get("code") if isinstance(data, dict) else None
