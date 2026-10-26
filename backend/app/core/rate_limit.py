"""Fixed-window rate limiting in Redis, keyed by a hash of the client IP."""

import logging

from fastapi import Request
from redis.exceptions import RedisError

from app.core.errors import RateLimitedError
from app.core.redis import get_redis
from app.core.security import hash_identifier

logger = logging.getLogger(__name__)


def client_ip(request: Request) -> str:
    """Client IP as seen by uvicorn (X-Forwarded-For is applied via --proxy-headers)."""
    return request.client.host if request.client else "unknown"


async def enforce_rate_limit(bucket: str, identity: str, limit: int, window_seconds: int) -> None:
    """Raise RateLimitedError when `identity` exceeds `limit` hits per window.

    Fail-open: if Redis is unavailable the request is allowed and a warning is logged.
    """
    key = f"rl:{bucket}:{hash_identifier(identity)}"
    redis = get_redis()
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_seconds, nx=True)
            count, _ = await pipe.execute()
        if count > limit:
            ttl = await redis.ttl(key)
            raise RateLimitedError(retry_after=max(ttl, 1))
    except RedisError:
        logger.warning("Rate limiter unavailable (bucket=%s), allowing request", bucket)
