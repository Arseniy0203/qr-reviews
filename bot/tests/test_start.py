"""/start replies and the internal API client (backend mocked with httpx.MockTransport)."""

import json

import httpx
import pytest

from bot.backend import BackendClient, LinkResult
from bot.handlers import TEXT_ERROR, TEXT_INVALID, TEXT_LINKED, TEXT_NO_CODE, start_reply

SECRET = "internal-secret"


def backend_with(handler: httpx.MockTransport) -> BackendClient:
    return BackendClient(httpx.AsyncClient(transport=handler), "http://backend:8000", SECRET)


def respond(status: int, body: dict | None = None) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _: httpx.Response(status, json=body))


async def test_link_calls_internal_api_with_secret() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    reply = await start_reply("abc_DEF-123", 777, backend_with(httpx.MockTransport(handler)))

    assert reply == TEXT_LINKED
    (request,) = requests
    assert str(request.url) == "http://backend:8000/internal/telegram/link"
    assert request.headers["X-Internal-Secret"] == SECRET
    assert json.loads(request.content) == {"code": "abc_DEF-123", "chat_id": 777}


async def test_invalid_or_expired_code() -> None:
    backend = backend_with(respond(404, {"code": "telegram_code_invalid"}))
    assert await start_reply("expired", 1, backend) == TEXT_INVALID


async def test_malformed_code_is_not_sent() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("backend must not be called")

    backend = backend_with(httpx.MockTransport(handler))
    assert await start_reply("bad code!", 1, backend) == TEXT_INVALID
    assert await start_reply("x" * 65, 1, backend) == TEXT_INVALID


async def test_start_without_code() -> None:
    backend = backend_with(respond(204))
    assert await start_reply(None, 1, backend) == TEXT_NO_CODE
    assert await start_reply("", 1, backend) == TEXT_NO_CODE


@pytest.mark.parametrize(
    "transport",
    [
        respond(404, {"code": "not_found"}),  # wrong INTERNAL_API_SECRET
        respond(500, {"detail": "boom"}),
        httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))),
    ],
)
async def test_backend_problems(transport: httpx.MockTransport) -> None:
    backend = backend_with(transport)
    assert await backend.link("code", 1) is LinkResult.ERROR
    assert await start_reply("code", 1, backend) == TEXT_ERROR
