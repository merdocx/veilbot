"""
Управление состоянием бота и пользователей

Централизованное хранение глобальных переменных бота для устранения циклических зависимостей.
"""
import logging
from typing import Dict, Any, Optional
from aiogram import Bot, Dispatcher

logger = logging.getLogger(__name__)

# Глобальные переменные бота
_bot_instance: Optional[Bot] = None
_dp_instance: Optional[Dispatcher] = None

# Состояния пользователей для управления диалогами
_user_states: Dict[int, Dict[str, Any]] = {}


def set_bot_instance(bot: Bot) -> None:
    """Установить bot instance"""
    global _bot_instance
    _bot_instance = bot
    logger.info("Bot instance registered in bot.core.state")


def get_bot_instance() -> Optional[Bot]:
    """Получить bot instance"""
    return _bot_instance


def set_dp_instance(dp: Dispatcher) -> None:
    """Установить dispatcher instance"""
    global _dp_instance
    _dp_instance = dp
    logger.info("Dispatcher instance registered in bot.core.state")


def get_dp_instance() -> Optional[Dispatcher]:
    """Получить dispatcher instance"""
    return _dp_instance


def get_user_states() -> Dict[int, Dict[str, Any]]:
    """
    Получить словарь состояний пользователей
    
    Returns:
        Словарь состояний пользователей (user_id -> state dict)
    """
    return _user_states


def clear_user_state(user_id: int) -> None:
    """Очистить состояние пользователя"""
    if user_id in _user_states:
        del _user_states[user_id]
        logger.debug(f"Cleared state for user {user_id}")


def set_user_state(user_id: int, state: Dict[str, Any]) -> None:
    """Установить состояние пользователя"""
    _user_states[user_id] = state
    logger.debug(f"Set state for user {user_id}: {state}")


def get_user_state(user_id: int) -> Optional[Dict[str, Any]]:
    """Получить состояние пользователя"""
    return _user_states.get(user_id)

