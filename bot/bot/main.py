"""Bot entrypoint: `python -m bot.main`."""

import asyncio
import logging
import sys

import httpx
from aiogram import Bot, Dispatcher

from bot.backend import BackendClient
from bot.config import get_settings
from bot.handlers import router

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 10


async def run() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    # httpx logs request URLs at INFO; keep logs free of anything sensitive.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if settings.telegram_bot_token is None:
        # Keep the container alive without crash-looping when the bot is not configured.
        logger.warning("TELEGRAM_BOT_TOKEN is not set: bot is disabled")
        await asyncio.Event().wait()
        return

    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        dispatcher["backend"] = BackendClient(
            client,
            settings.backend_internal_url,
            settings.internal_api_secret.get_secret_value(),
        )
        logger.info("Starting Telegram bot polling")
        await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run())
