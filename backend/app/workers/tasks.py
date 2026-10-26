"""arq background tasks."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def ping(_: dict[str, Any]) -> str:
    """Liveness task, handy for checking that the worker consumes the queue."""
    return "pong"
