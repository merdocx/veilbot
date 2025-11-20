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
    max_retries: int = 3,
    **kwargs: Any,
) -> Optional[Message]:
    """Отправляет сообщение и мягко обрабатывает отказ Telegram.

    - При блокировке пользователя помечает его как `blocked` в таблице users.
    - При ошибках ChatNotFound и BotBlocked запись не дублируется и функция возвращает None.
    - На RetryAfter выполняется повторная попытка с экспоненциальной задержкой.
    - На другие ошибки выполняется до max_retries попыток с экспоненциальной задержкой.
    
    Args:
        bot: Экземпляр бота
        user_id: ID пользователя
        text: Текст сообщения
        retry: Использовать ли retry механизм (устаревший параметр, используйте max_retries)
        mark_blocked: Помечать ли пользователя как заблокированного
        max_retries: Максимальное количество попыток отправки (по умолчанию 3)
        **kwargs: Дополнительные параметры для bot.send_message
    
    Returns:
        Message объект при успешной отправке, None при ошибке
    """
    if not bot:
        LOGGER.warning("Bot instance is None for user %s", user_id)
        return None
    
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            return await bot.send_message(user_id, text, **kwargs)
        
        except BotBlocked:
            LOGGER.warning("Bot blocked by user %s", user_id)
            if mark_blocked:
                try:
                    with get_db_cursor(commit=True) as cursor:
                        cursor.execute("UPDATE users SET blocked = 1 WHERE user_id = ?", (user_id,))
                except Exception as db_error:
                    LOGGER.error("Failed to mark user %s as blocked: %s", user_id, db_error)
            return None
        
        except ChatNotFound:
            LOGGER.warning("Chat not found for user %s", user_id)
            return None
        
        except RetryAfter as exc:
            wait_for = getattr(exc, "timeout", 1)
            LOGGER.warning("RetryAfter for user %s (attempt %d/%d): waiting %s seconds", 
                          user_id, attempt + 1, max_retries, wait_for)
            if attempt < max_retries - 1:
                await asyncio.sleep(wait_for)
                continue
            LOGGER.error("Exceeded retry attempts (RetryAfter) when sending message to %s", user_id)
            return None
        
        except TelegramAPIError as exc:
            last_exception = exc
            LOGGER.warning("Telegram API error for user %s (attempt %d/%d): %s", 
                          user_id, attempt + 1, max_retries, exc)
            if attempt < max_retries - 1:
                # Экспоненциальная задержка: 1s, 2s, 4s
                wait_time = 2 ** attempt
                await asyncio.sleep(wait_time)
                continue
            LOGGER.error("Exceeded retry attempts (TelegramAPIError) when sending message to %s: %s", 
                        user_id, exc)
            return None
        
        except Exception as exc:  # pragma: no cover - защитная сетка
            last_exception = exc
            LOGGER.warning("Unexpected error when sending message to %s (attempt %d/%d): %s", 
                          user_id, attempt + 1, max_retries, exc)
            if attempt < max_retries - 1:
                # Экспоненциальная задержка: 1s, 2s, 4s
                wait_time = 2 ** attempt
                await asyncio.sleep(wait_time)
                continue
            LOGGER.exception("Exceeded retry attempts (Exception) when sending message to %s", user_id)
            return None
    
    if last_exception:
        LOGGER.error("Failed to send message to user %s after %d attempts: %s", 
                    user_id, max_retries, last_exception)
    return None

