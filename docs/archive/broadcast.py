#!/usr/bin/env python3
"""
Скрипт для рассылки сообщений всем пользователям бота
"""
import asyncio
import logging
import sys
import os

# Добавляем корневую директорию проекта в путь
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from aiogram import Bot
from config import TELEGRAM_BOT_TOKEN, ADMIN_ID
from db import init_db_with_migrations
from bot.core import set_bot_instance, get_bot_instance
from bot.handlers.common import broadcast_message
from app.logging_config import setup_logging

# Настройка логирования
setup_logging(level="INFO")
logger = logging.getLogger(__name__)


async def main():
    """Основная функция для запуска рассылки"""
    
    # Текст рассылки
    message_text = """В сети произошёл крупный сбой: наблюдаются проблемы у ChatGPT, Spotify, X и многих других сервисов — пострадали сотни сайтов и часть AWS. Причина — массовые неполадки у Cloudflare, о которых за последние минуты поступило более 5000 жалоб со всего мира. Срок восстановления пока неизвестен.

Важно: инфраструктура Vee VPN практически не зависит от Cloudflare, сервис полноценно работает, но могут наблюдаться кратковременные сбои."""
    
    try:
        # Инициализация базы данных
        logger.info("Инициализация базы данных...")
        init_db_with_migrations()
        logger.info("База данных инициализирована успешно")
        
        # Проверка токена
        if not TELEGRAM_BOT_TOKEN:
            logger.error("TELEGRAM_BOT_TOKEN не установлен!")
            sys.exit(1)
        
        # Создание экземпляра бота
        logger.info("Создание экземпляра бота...")
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        
        # Регистрация bot instance
        set_bot_instance(bot)
        logger.info("Bot instance зарегистрирован")
        
        # Запуск рассылки
        logger.info("Запуск рассылки...")
        await broadcast_message(message_text, admin_id=ADMIN_ID)
        logger.info("Рассылка завершена")
        
    except Exception as e:
        logger.error(f"Ошибка при выполнении рассылки: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Закрытие сессии бота
        bot = get_bot_instance()
        if bot:
            session = await bot.get_session()
            if session:
                await session.close()


if __name__ == "__main__":
    asyncio.run(main())

