"""Bot entrypoint: `python -m bot.main`."""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher

from bot.config import get_settings

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    if settings.telegram_bot_token is None:
        # Keep the container alive without crash-looping when the bot is not configured.
        logger.warning("TELEGRAM_BOT_TOKEN is not set: bot is disabled")
        await asyncio.Event().wait()
        return

    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    dispatcher = Dispatcher()
    logger.info("Starting Telegram bot polling")
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run())
