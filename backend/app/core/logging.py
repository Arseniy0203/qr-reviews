"""Logging setup shared by the API and the worker."""

import logging
import sys


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # httpx logs every request URL at INFO; the Telegram Bot API URL contains the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
