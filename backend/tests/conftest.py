"""Shared test fixtures.

Tests run against a real PostgreSQL database (TEST_DATABASE_URL) and Redis
(TEST_REDIS_URL, a separate logical DB), never against the dev database.
The schema is rebuilt with Alembic (downgrade base -> upgrade head) once per run,
which also checks that migrations apply and roll back cleanly.
"""

import os

# Must happen before any app module reads settings.
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://qr:qr@localhost:5432/qr_reviews_test"
)
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ["SECRET_KEY"] = "test-secret-key-0123456789abcdef-0123456789"
os.environ["HASH_SALT"] = "test-hash-salt-0123456789abcdef-0123456789"
os.environ["INTERNAL_API_SECRET"] = "test-internal-secret-0123456789abcdef"
os.environ["COOKIE_SECURE"] = "false"
os.environ["PUBLIC_BASE_URL"] = "http://localhost:8080"
os.environ["DASHBOARD_BASE_URL"] = "http://localhost:8080"
os.environ["TELEGRAM_BOT_TOKEN"] = ""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.main import app
from app.models import Base, Role
from tests.api_helpers import add_member, login
from tests.factories import Team, add_branch, make_tenant

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

assert "test" in os.environ["DATABASE_URL"], "Refusing to run tests on a non-test database"
assert not os.environ["REDIS_URL"].endswith("/0"), "Refusing to run tests on Redis DB 0"


@pytest.fixture(scope="session", autouse=True)
async def _migrated_database() -> None:
    config = Config(str(ALEMBIC_INI))
    # env.py runs its own event loop, so Alembic must run outside the test loop.
    await asyncio.to_thread(command.downgrade, config, "base")
    await asyncio.to_thread(command.upgrade, config, "head")


@pytest.fixture(autouse=True)
async def _clean_state() -> AsyncIterator[None]:
    yield
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    async with get_sessionmaker()() as session:
        # All data hangs off organizations or users; TRUNCATE is slow, skip it when unused.
        dirty = await session.scalar(
            text("SELECT EXISTS (SELECT 1 FROM organizations) OR EXISTS (SELECT 1 FROM users)")
        )
        if dirty:
            await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
            await session.commit()
    await get_redis().flushdb()


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as db_session:
        yield db_session


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def team(session: AsyncSession, client: httpx.AsyncClient) -> Team:
    tenant = await make_tenant(session, "Лаванда")
    branch_b, employee_b, point_b = await add_branch(session, tenant.org, "Филиал Б")
    await session.commit()
    for role, email, extra in [
        (Role.OWNER, "owner@team.ru", {}),
        (Role.MANAGER, "manager@team.ru", {"branch_id": tenant.branch.id}),
        (Role.EMPLOYEE, "employee@team.ru", {"employee_id": tenant.employee.id}),
    ]:
        await add_member(session, tenant.org.id, role, email=email, **extra)
    return Team(
        tenant=tenant,
        branch_b=branch_b,
        employee_b=employee_b,
        point_b=point_b,
        owner=await login(client, "owner@team.ru"),
        manager=await login(client, "manager@team.ru"),
        employee=await login(client, "employee@team.ru"),
    )
