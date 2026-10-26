"""HTTP-level helpers for API tests."""

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import Membership, Role, User

PASSWORD = "correct-horse-42"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def refresh_cookie(response: httpx.Response) -> str:
    value = response.cookies.get("refresh_token")
    assert value, "refresh cookie was not set"
    return value


async def register(
    client: httpx.AsyncClient, email: str = "owner@example.com", org: str = "Салон"
) -> httpx.Response:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "full_name": "Владелец",
            "organization_name": org,
        },
    )
    assert response.status_code == 201, response.text
    return response


async def owner_token(client: httpx.AsyncClient, email: str = "owner@example.com") -> str:
    return (await register(client, email, org=f"Салон {email}")).json()["access_token"]


async def post_refresh(client: httpx.AsyncClient, token: str) -> httpx.Response:
    # Explicit header: the test client's cookie jar is not used for refresh cookies.
    client.cookies.clear()
    return await client.post("/api/v1/auth/refresh", headers={"Cookie": f"refresh_token={token}"})


async def login(client: httpx.AsyncClient, email: str, password: str = PASSWORD) -> str:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


async def add_member(
    session: AsyncSession,
    organization_id: uuid.UUID,
    role: Role,
    *,
    email: str,
    branch_id: uuid.UUID | None = None,
    employee_id: uuid.UUID | None = None,
) -> User:
    user = User(email=email, password_hash=hash_password(PASSWORD), full_name=email.split("@")[0])
    session.add(user)
    await session.flush()
    session.add(
        Membership(
            organization_id=organization_id,
            user_id=user.id,
            role=role,
            branch_id=branch_id,
            employee_id=employee_id,
        )
    )
    await session.commit()
    return user
