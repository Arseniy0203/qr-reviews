import httpx
import pytest

from app.core.config import get_settings
from tests.api_helpers import PASSWORD, bearer, post_refresh, refresh_cookie, register


async def test_register_creates_owner_with_org(client: httpx.AsyncClient) -> None:
    response = await register(client, "Owner@Example.com", org="Барбершоп")
    token = response.json()["access_token"]

    me = await client.get("/api/v1/auth/me", headers=bearer(token))
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"] == "owner@example.com"  # normalized
    assert body["user"]["telegram_linked"] is False
    assert len(body["memberships"]) == 1
    assert body["memberships"][0]["role"] == "owner"
    assert body["memberships"][0]["organization_name"] == "Барбершоп"


async def test_refresh_cookie_flags(client: httpx.AsyncClient) -> None:
    response = await register(client)
    cookie = response.headers["set-cookie"]
    assert "refresh_token=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/api/v1/auth" in cookie
    assert "Secure" not in cookie  # COOKIE_SECURE=false in tests


async def test_refresh_cookie_secure_when_configured(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "cookie_secure", True)
    response = await register(client)
    assert "Secure" in response.headers["set-cookie"]


async def test_duplicate_email_rejected(client: httpx.AsyncClient) -> None:
    await register(client, "dup@example.com")
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "DUP@example.com",
            "password": PASSWORD,
            "full_name": "Другой",
            "organization_name": "Другой салон",
        },
    )
    assert response.status_code == 409


async def test_login_success_and_failures_look_the_same(client: httpx.AsyncClient) -> None:
    await register(client, "login@example.com")
    ok = await client.post(
        "/api/v1/auth/login", json={"email": "LOGIN@example.com", "password": PASSWORD}
    )
    assert ok.status_code == 200
    assert ok.json()["token_type"] == "bearer"

    wrong_password = await client.post(
        "/api/v1/auth/login", json={"email": "login@example.com", "password": "nope-nope"}
    )
    unknown_email = await client.post(
        "/api/v1/auth/login", json={"email": "ghost@example.com", "password": "nope-nope"}
    )
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


async def test_me_requires_valid_access_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=bearer("garbage"))).status_code == 401

    # A refresh token must not work as an access token.
    refresh = refresh_cookie(await register(client))
    assert (await client.get("/api/v1/auth/me", headers=bearer(refresh))).status_code == 401


async def test_refresh_rotates_tokens(client: httpx.AsyncClient) -> None:
    first = refresh_cookie(await register(client))
    response = await post_refresh(client, first)
    assert response.status_code == 200
    second = refresh_cookie(response)
    assert second != first
    me = await client.get("/api/v1/auth/me", headers=bearer(response.json()["access_token"]))
    assert me.status_code == 200


async def test_refresh_token_reuse_revokes_whole_family(client: httpx.AsyncClient) -> None:
    first = refresh_cookie(await register(client))
    second = refresh_cookie(await post_refresh(client, first))

    # The old token is replayed (e.g. stolen): rejected, and the family is revoked...
    assert (await post_refresh(client, first)).status_code == 401
    # ...so even the legitimate latest token stops working.
    assert (await post_refresh(client, second)).status_code == 401


async def test_logout_revokes_refresh_token(client: httpx.AsyncClient) -> None:
    token = refresh_cookie(await register(client))
    client.cookies.clear()
    response = await client.post(
        "/api/v1/auth/logout", headers={"Cookie": f"refresh_token={token}"}
    )
    assert response.status_code == 204
    assert 'refresh_token=""' in response.headers["set-cookie"]
    assert (await post_refresh(client, token)).status_code == 401


async def test_refresh_without_cookie(client: httpx.AsyncClient) -> None:
    client.cookies.clear()
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_login_rate_limited_per_ip(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_login_max", 3)
    payload = {"email": "x@example.com", "password": "whatever-1"}
    statuses = [
        (await client.post("/api/v1/auth/login", json=payload)).status_code for _ in range(4)
    ]
    assert statuses == [401, 401, 401, 429]

    limited = await client.post("/api/v1/auth/login", json=payload)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    assert limited.json()["code"] == "rate_limited"
