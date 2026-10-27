"""Bot handlers. The bot only links Telegram chats to dashboard accounts."""

import logging
import re

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message

from bot.backend import BackendClient, LinkResult

logger = logging.getLogger(__name__)

# Codes are secrets.token_urlsafe(); Telegram allows [A-Za-z0-9_-]{1,64} in /start.
CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

TEXT_LINKED = (
    "Готово! Telegram привязан к вашему аккаунту в QR Отзывах.\n"
    "Уведомления будут приходить в этот чат. Отвязать Telegram можно в личном кабинете."
)
TEXT_INVALID = (
    "Ссылка недействительна или устарела.\n"
    "Откройте личный кабинет и получите новую ссылку для подключения Telegram."
)
TEXT_ERROR = "Не получилось подключить Telegram: сервис временно недоступен. Попробуйте позже."
TEXT_NO_CODE = (
    "Это бот сервиса QR Отзывы: он присылает уведомления о новых отзывах.\n"
    "Чтобы подключить его, откройте личный кабинет и нажмите «Подключить Telegram»."
)

router = Router()
router.message.filter(F.chat.type == "private")


async def start_reply(code: str | None, chat_id: int, backend: BackendClient) -> str:
    if not code:
        return TEXT_NO_CODE
    if not CODE_PATTERN.fullmatch(code):
        return TEXT_INVALID
    result = await backend.link(code, chat_id)
    logger.info("Link attempt: %s", result.value)  # never log the code itself
    if result is LinkResult.LINKED:
        return TEXT_LINKED
    if result is LinkResult.INVALID:
        return TEXT_INVALID
    return TEXT_ERROR


@router.message(CommandStart())
async def on_start(message: Message, command: CommandObject, backend: BackendClient) -> None:
    await message.answer(await start_reply(command.args, message.chat.id, backend))
