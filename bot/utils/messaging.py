import asyncio
import logging
from typing import Any, Optional

from aiogram import Bot
from aiogram.types import Message
from aiogram.utils.exceptions import BotBlocked, ChatNotFound, RetryAfter, TelegramAPIError

from utils import get_db_cursor

LOGGER = logging.getLogger("bot.messaging")


async def safe_send_message(
    bot: Bot,
    user_id: int,
    text: str,
    *,
    retry: bool = True,
    mark_blocked: bool = True,
    **kwargs: Any,
) -> Optional[Message]:
    """Отправляет сообщение и мягко обрабатывает отказ Telegram.

    - При блокировке пользователя помечает его как `blocked` в таблице users.
    - При ошибках ChatNotFound и BotBlocked запись не дублируется и функция возвращает None.
    - На RetryAfter выполняется однократная повторная попытка.
    """

    try:
        return await bot.send_message(user_id, text, **kwargs)

    except BotBlocked:
        LOGGER.warning("Bot blocked by user %s", user_id)
        if mark_blocked:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute("UPDATE users SET blocked = 1 WHERE user_id = ?", (user_id,))
        return None

    except ChatNotFound:
        LOGGER.warning("Chat not found for user %s", user_id)
        return None

    except RetryAfter as exc:
        if retry:
            wait_for = getattr(exc, "timeout", 1)
            LOGGER.warning("RetryAfter for user %s: waiting %s seconds", user_id, wait_for)
            await asyncio.sleep(wait_for)
            return await safe_send_message(
                bot,
                user_id,
                text,
                retry=False,
                mark_blocked=mark_blocked,
                **kwargs,
            )
        LOGGER.error("Exceeded retry attempts when sending message to %s", user_id)
        return None

    except TelegramAPIError as exc:
        LOGGER.error("Telegram API error for user %s: %s", user_id, exc)
        return None

    except Exception as exc:  # pragma: no cover - защитная сетка
        LOGGER.exception("Unexpected error when sending message to %s: %s", user_id, exc)
        return None

