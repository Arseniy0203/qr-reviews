"""Telegram linking: one-time codes from the dashboard, spent by the bot via /internal."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import TelegramLinkCode, User
from tests.api_helpers import bearer
from tests.factories import Team

SECRET = "test-internal-secret-0123456789abcdef"


@pytest.fixture(autouse=True)
def _bot_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "telegram_bot_username", "qr_reviews_test_bot")


async def new_code(client: httpx.AsyncClient, token: str) -> str:
    response = await client.post("/api/v1/telegram/link-code", headers=bearer(token))
    assert response.status_code == 201
    deep_link = response.json()["deep_link"]
    prefix = "https://t.me/qr_reviews_test_bot?start="
    assert deep_link.startswith(prefix)
    return deep_link.removeprefix(prefix)


async def bot_link(
    client: httpx.AsyncClient, code: str, chat_id: int, secret: str | None = SECRET
) -> httpx.Response:
    headers = {"X-Internal-Secret": secret} if secret is not None else {}
    return await client.post(
        "/internal/telegram/link", json={"code": code, "chat_id": chat_id}, headers=headers
    )


async def chat_id_of(session: AsyncSession, email: str) -> int | None:
    return await session.scalar(
        select(User.telegram_chat_id)
        .where(User.email == email)
        .execution_options(populate_existing=True)
    )


async def test_link_flow(client: httpx.AsyncClient, team: Team, session: AsyncSession) -> None:
    code = await new_code(client, team.owner)
    assert (await bot_link(client, code, 1001)).status_code == 204
    assert await chat_id_of(session, "owner@team.ru") == 1001

    me = await client.get("/api/v1/auth/me", headers=bearer(team.owner))
    assert me.json()["user"]["telegram_linked"] is True
    stored = await session.scalar(select(TelegramLinkCode.code_hash))
    assert stored is not None
    assert code not in stored  # only the hash is stored


async def test_code_works_once(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    code = await new_code(client, team.owner)
    assert (await bot_link(client, code, 1001)).status_code == 204
    response = await bot_link(client, code, 2002)
    assert response.status_code == 404
    assert response.json()["code"] == "telegram_code_invalid"
    assert await chat_id_of(session, "owner@team.ru") == 1001


async def test_expired_code_rejected(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    code = await new_code(client, team.owner)
    await session.execute(
        update(TelegramLinkCode).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    response = await bot_link(client, code, 1001)
    assert response.json()["code"] == "telegram_code_invalid"
    assert await chat_id_of(session, "owner@team.ru") is None


async def test_unknown_code_rejected(client: httpx.AsyncClient, team: Team) -> None:
    response = await bot_link(client, "no-such-code", 1001)
    assert response.status_code == 404
    assert response.json()["code"] == "telegram_code_invalid"


async def test_new_code_replaces_old(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    old = await new_code(client, team.owner)
    new = await new_code(client, team.owner)
    assert (await bot_link(client, old, 1001)).status_code == 404
    assert (await bot_link(client, new, 1001)).status_code == 204


@pytest.mark.parametrize("secret", [None, "wrong-secret"])
async def test_internal_requires_secret(
    client: httpx.AsyncClient, team: Team, session: AsyncSession, secret: str | None
) -> None:
    code = await new_code(client, team.owner)
    response = await bot_link(client, code, 1001, secret=secret)
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert await chat_id_of(session, "owner@team.ru") is None
    assert (await bot_link(client, code, 1001)).status_code == 204  # the code is not spent


async def test_any_role_can_link(client: httpx.AsyncClient, team: Team) -> None:
    for token, chat_id in [(team.manager, 2), (team.employee, 3)]:
        assert (await bot_link(client, await new_code(client, token), chat_id)).status_code == 204


async def test_chat_moves_to_the_latest_account(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    await bot_link(client, await new_code(client, team.owner), 1001)
    await bot_link(client, await new_code(client, team.manager), 1001)
    assert await chat_id_of(session, "owner@team.ru") is None
    assert await chat_id_of(session, "manager@team.ru") == 1001


async def test_unlink(client: httpx.AsyncClient, team: Team, session: AsyncSession) -> None:
    await bot_link(client, await new_code(client, team.owner), 1001)
    response = await client.delete("/api/v1/telegram/link", headers=bearer(team.owner))
    assert response.status_code == 204
    assert await chat_id_of(session, "owner@team.ru") is None


async def test_requires_login(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/v1/telegram/link-code")).status_code == 401
    assert (await client.delete("/api/v1/telegram/link")).status_code == 401


async def test_bot_not_configured(
    client: httpx.AsyncClient, team: Team, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "telegram_bot_username", "")
    response = await client.post("/api/v1/telegram/link-code", headers=bearer(team.owner))
    assert response.status_code == 503
    assert response.json()["code"] == "telegram_not_configured"
