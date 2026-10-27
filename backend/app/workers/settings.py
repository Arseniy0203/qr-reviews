"""arq worker configuration: `arq app.workers.settings.WorkerSettings`."""

from typing import Any, ClassVar

import httpx
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.logging import configure_logging
from app.services.notifications import build_channels
from app.workers import tasks

_settings = get_settings()

HTTP_TIMEOUT_SECONDS = 10


async def on_startup(ctx: dict[str, Any]) -> None:
    configure_logging(_settings.log_level)
    ctx["http"] = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)
    ctx["channels"] = {c.name: c for c in build_channels(_settings, ctx["http"])}


async def on_shutdown(ctx: dict[str, Any]) -> None:
    await ctx["http"].aclose()
    await dispose_engine()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        tasks.ping,
        tasks.send_negative_alert,
        tasks.deliver_alert,
        tasks.analyze_review_stub,
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_tries = 5
