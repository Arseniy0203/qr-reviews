"""Shared test fixtures.

Tests run against a real PostgreSQL database (TEST_DATABASE_URL) and Redis
(TEST_REDIS_URL, a separate logical DB), never against the dev database.
"""

import os

# Must happen before any app module reads settings.
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://qr:qr@localhost:5432/qr_reviews_test"
)
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("HASH_SALT", "test-hash-salt")
os.environ.setdefault("INTERNAL_API_SECRET", "test-internal-secret")
os.environ["TELEGRAM_BOT_TOKEN"] = ""

from collections.abc import AsyncIterator

import httpx
import pytest

from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
