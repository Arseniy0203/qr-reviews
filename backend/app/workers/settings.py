"""arq worker configuration: `arq app.workers.settings.WorkerSettings`."""

from typing import Any, ClassVar

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.workers import tasks

_settings = get_settings()


async def on_startup(_: dict[str, Any]) -> None:
    configure_logging(_settings.log_level)


class WorkerSettings:
    functions: ClassVar[list[Any]] = [tasks.ping]
    on_startup = on_startup
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_tries = 5
