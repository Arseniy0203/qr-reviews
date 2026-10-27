"""TelegramChannel: request shape and error mapping (Telegram mocked with MockTransport)."""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.models import User
from app.services.notifications import (
    DeliveryError,
    Message,
    RecipientUnreachableError,
    TelegramChannel,
    TemporaryDeliveryError,
    build_channels,
)

TOKEN = "123:test-token"


def channel_with(handler: httpx.MockTransport) -> TelegramChannel:
    return TelegramChannel(TOKEN, httpx.AsyncClient(transport=handler))


def respond(status: int, body: dict | None = None) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _: httpx.Response(status, json=body or {}))


def failure(description: str) -> dict:
    return {"ok": False, "description": description}


async def test_sends_plain_text_to_chat() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    await channel_with(httpx.MockTransport(handler)).send("42", Message("<b>Плохо</b>"))

    (request,) = requests
    assert str(request.url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = json.loads(request.content)
    assert payload["chat_id"] == 42
    assert payload["text"] == "<b>Плохо</b>"  # sent as is: no parse_mode, no markup
    assert "parse_mode" not in payload


async def test_long_text_is_truncated() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True})

    await channel_with(httpx.MockTransport(handler)).send("1", Message("а" * 5000))
    assert len(sent[0]) == 4096
    assert sent[0].endswith("…")


def test_address_only_for_linked_users() -> None:
    channel = channel_with(respond(200))
    assert channel.address_for(User(telegram_chat_id=777)) == "777"
    assert channel.address_for(User(telegram_chat_id=None)) is None


async def test_rate_limit_is_temporary_with_retry_after() -> None:
    channel = channel_with(respond(429, {"ok": False, "parameters": {"retry_after": 7}}))
    with pytest.raises(TemporaryDeliveryError) as exc_info:
        await channel.send("1", Message("x"))
    assert exc_info.value.retry_after == 7


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_server_errors_are_temporary(status: int) -> None:
    with pytest.raises(TemporaryDeliveryError):
        await channel_with(respond(status)).send("1", Message("x"))


async def test_transport_error_is_temporary_and_hides_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    with pytest.raises(TemporaryDeliveryError) as exc_info:
        await channel_with(httpx.MockTransport(handler)).send("1", Message("x"))
    assert TOKEN not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


async def test_blocked_bot_is_unreachable() -> None:
    channel = channel_with(respond(403, failure("Forbidden: bot was blocked by the user")))
    with pytest.raises(RecipientUnreachableError):
        await channel.send("1", Message("x"))


async def test_chat_not_found_is_unreachable() -> None:
    channel = channel_with(respond(400, failure("Bad Request: chat not found")))
    with pytest.raises(RecipientUnreachableError):
        await channel.send("1", Message("x"))


async def test_other_bad_request_is_permanent() -> None:
    channel = channel_with(respond(400, failure("Bad Request: text is empty")))
    with pytest.raises(DeliveryError) as exc_info:
        await channel.send("1", Message("x"))
    assert not isinstance(exc_info.value, RecipientUnreachableError)


def test_channels_depend_on_token(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    client = httpx.AsyncClient()
    monkeypatch.setattr(settings, "telegram_bot_token", None)
    assert build_channels(settings, client) == []
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr(TOKEN))
    (channel,) = build_channels(settings, client)
    assert isinstance(channel, TelegramChannel)
