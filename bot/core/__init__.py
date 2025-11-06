"""
Централизованное управление bot instance для устранения циклических зависимостей

Этот модуль позволяет получать bot instance из любого места в проекте
без создания циклических зависимостей.
"""
import logging
from typing import Optional
from aiogram import Bot

logger = logging.getLogger(__name__)

# Глобальная переменная для хранения bot instance
_bot_instance: Optional[Bot] = None


def set_bot_instance(bot: Bot) -> None:
    """
    Устанавливает bot instance для использования в других модулях
    
    Args:
        bot: Экземпляр aiogram Bot
    """
    global _bot_instance
    _bot_instance = bot
    logger.info("Bot instance registered in bot.core")


def get_bot_instance() -> Optional[Bot]:
    """
    Получает bot instance
    
    Returns:
        Экземпляр aiogram Bot или None, если еще не установлен
    
    Raises:
        RuntimeError: Если bot instance не установлен и попытка получить его
    """
    if _bot_instance is None:
        logger.warning("Bot instance not yet registered. Attempting lazy import...")
        # Попытка ленивой загрузки из sys.modules (fallback)
        try:
            import sys
            if 'bot' in sys.modules:
                bot_module = sys.modules['bot']
                if hasattr(bot_module, 'bot'):
                    logger.info("Bot instance found via sys.modules fallback")
                    return bot_module.bot
        except Exception as e:
            logger.error(f"Failed to get bot instance via fallback: {e}")
        
        raise RuntimeError(
            "Bot instance not registered. Call set_bot_instance() first. "
            "Usually this is done in bot.py during initialization."
        )
    
    return _bot_instance


def is_bot_registered() -> bool:
    """
    Проверяет, зарегистрирован ли bot instance
    
    Returns:
        True если bot instance установлен, False иначе
    """
    return _bot_instance is not None


