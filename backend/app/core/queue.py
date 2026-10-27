"""arq job queue used by the API: services enqueue jobs, the worker runs them."""

import logging
from functools import lru_cache
from typing import Any

from arq.connections import ArqRedis
from redis.exceptions import RedisError

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_queue() -> ArqRedis:
    return ArqRedis.from_url(get_settings().redis_url)


async def close_queue() -> None:
    if get_queue.cache_info().currsize:
        await get_queue().aclose()
        get_queue.cache_clear()


async def enqueue(function: str, *args: Any, job_id: str | None = None) -> bool:
    """Enqueue a job; False if Redis is down (fail-open: the caller's work is already saved)."""
    try:
        await get_queue().enqueue_job(function, *args, _job_id=job_id)
    except (RedisError, OSError):
        logger.warning("Job queue unavailable, job %s not enqueued", function)
        return False
    return True
