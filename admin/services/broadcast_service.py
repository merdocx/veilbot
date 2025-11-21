"""
Сервис для рассылки сообщений пользователям бота
"""
import asyncio
import logging
from typing import Optional, Dict, Any

from aiogram import Bot
from config import TELEGRAM_BOT_TOKEN, ADMIN_ID
from bot.core import set_bot_instance, get_bot_instance
from bot.handlers.common import broadcast_message

logger = logging.getLogger(__name__)


async def send_broadcast(message_text: str) -> Dict[str, Any]:
    """
    Отправить рассылку всем пользователям бота
    
    Args:
        message_text: Текст сообщения для рассылки
        
    Returns:
        dict: Результат рассылки с статистикой
    """
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не установлен")
    
    # Создаем экземпляр бота
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    try:
        # Регистрируем bot instance
        set_bot_instance(bot)
        logger.info("Bot instance зарегистрирован для рассылки")
        
        # Запускаем рассылку
        logger.info("Запуск рассылки...")
        await broadcast_message(message_text, admin_id=ADMIN_ID)
        logger.info("Рассылка завершена")
        
        return {
            "success": True,
            "message": "Рассылка успешно запущена. Отчет будет отправлен администратору в Telegram."
        }
        
    except Exception as e:
        logger.error(f"Ошибка при выполнении рассылки: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }
    finally:
        # Закрываем сессию бота
        try:
            bot_instance = get_bot_instance()
            if bot_instance:
                session = await bot_instance.get_session()
                if session:
                    await session.close()
        except Exception as e:
            logger.warning(f"Ошибка при закрытии сессии бота: {e}")

