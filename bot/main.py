"""
Точка входа для VeilBot
"""
import asyncio
import sys
import traceback
import logging
from logging.handlers import RotatingFileHandler
from aiogram import Bot, Dispatcher, executor
from config import TELEGRAM_BOT_TOKEN, validate_configuration
from db import init_db_with_migrations
from app.logging_config import setup_logging, _SecretMaskingFilter
from bot.core import set_bot_instance, set_dp_instance
from bot_error_handler import setup_error_handler

# Импортируем handlers
from bot.handlers.start import register_start_handler
from bot.handlers.keys import register_keys_handler
from bot.handlers.purchase import register_purchase_handlers
from bot.handlers.renewal import register_renewal_handlers
from bot.handlers.common import register_common_handlers
from bot.handlers.key_management import register_key_management_handlers
from bot.handlers.subscriptions import register_subscription_handlers

# Импортируем фоновые задачи
from bot.services.background_tasks import (
    auto_delete_expired_keys,
    notify_expiring_keys,
    check_key_availability,
    process_pending_paid_payments,
    monitor_subscription_traffic_limits,
)

# Импортируем функции и переменные из bot.py
# Это нужно для передачи в handlers
# Используем importlib для импорта bot.py (не пакета bot/)
# ВАЖНО: Сначала создаем временный dp, чтобы декораторы в bot.py не падали
import sys
import os
import importlib.util
from unittest.mock import MagicMock

LOG_DIR = os.getenv("VEILBOT_LOG_DIR", "/var/log/veilbot")

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bot_py_path = os.path.join(project_root, 'bot.py')

# Создаем mock объекты для dp и bot
# Они будут заменены на реальные в register_all_handlers
temp_dp = MagicMock()
temp_bot = MagicMock()

# Устанавливаем временный dp в bot_module перед импортом
# Это нужно, чтобы декораторы в bot.py не падали
spec = importlib.util.spec_from_file_location("bot_module", bot_py_path)
bot_module = importlib.util.module_from_spec(spec)
sys.modules["bot_module"] = bot_module

# Читаем содержимое bot.py и заменяем dp = None на dp = temp_dp
with open(bot_py_path, 'r', encoding='utf-8') as f:
    bot_code = f.read()
    
# Заменяем dp = None на dp = temp_dp перед импортом
bot_code = bot_code.replace('dp = None', f'dp = temp_dp  # Временно установлен для импорта')
bot_code = bot_code.replace('bot = None', f'bot = temp_bot  # Временно установлен для импорта')

# Добавляем temp_dp и temp_bot в контекст выполнения
bot_module.__dict__['temp_dp'] = temp_dp
bot_module.__dict__['temp_bot'] = temp_bot

# Выполняем код с заменой
exec(compile(bot_code, bot_py_path, 'exec'), bot_module.__dict__)


def setup_bot():
    """
    Настройка и инициализация бота
    
    Returns:
        tuple: (bot, dp, user_states)
    """
    # Проверка токена
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set in config.py")
    
    # Проверка конфигурации
    config_validation = validate_configuration()
    if not config_validation['is_valid']:
        for err in config_validation['errors']:
            logging.error(f"Config error: {err}")
        raise RuntimeError("Invalid configuration. Check environment variables.")
    for warn in config_validation['warnings']:
        logging.warning(f"Config warning: {warn}")
    
    # Создание bot и dispatcher
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher(bot)
    
    # Регистрируем bot instance для использования в других модулях
    set_bot_instance(bot)
    set_dp_instance(dp)
    
    # Получаем user_states из централизованного модуля
    from bot.core.state import get_user_states
    user_states = get_user_states()
    
    # Для обратной совместимости с bot.py устанавливаем в bot_module
    if not hasattr(bot_module, 'user_states'):
        bot_module.user_states = user_states
    
    # Глобальные переменные main_menu, help_keyboard, cancel_keyboard удалены
    # Теперь используются функции get_main_menu(), get_help_keyboard(), get_cancel_keyboard()
    
    return bot, dp, user_states


def register_all_handlers(dp: Dispatcher, bot_instance, user_states: dict):
    """
    Регистрация всех handlers
    
    Args:
        dp: Dispatcher
        bot: Bot instance
        user_states: Словарь состояний пользователей
    """
    # Регистрация основных handlers
    register_start_handler(dp, user_states)
    register_keys_handler(dp)
    register_renewal_handlers(dp, user_states, bot_instance)
    register_common_handlers(dp, user_states)
    register_subscription_handlers(dp, user_states)
    
    # Устанавливаем dp и bot в модуль bot перед регистрацией handlers
    # Это нужно для декораторов @dp.message_handler в bot.py
    bot_module.dp = dp
    bot_module.bot = bot_instance
    
    # Регистрация purchase handlers
    from bot.keyboards import get_main_menu, get_cancel_keyboard
    register_purchase_handlers(
        dp=dp,
        user_states=user_states,
        bot=bot_instance,
        main_menu=get_main_menu,
        cancel_keyboard=get_cancel_keyboard,
        is_valid_email=bot_module.is_valid_email,
        create_payment_with_email_and_protocol=bot_module.create_payment_with_email_and_protocol,
        create_new_key_flow_with_protocol=bot_module.create_new_key_flow_with_protocol,
        handle_free_tariff_with_protocol=bot_module.handle_free_tariff_with_protocol,
        handle_invite_friend=bot_module.handle_invite_friend,
        get_tariff_by_name_and_price=bot_module.get_tariff_by_name_and_price
    )
    
    # Регистрация handlers управления ключами
    register_key_management_handlers(
        dp, bot_instance, user_states,
        bot_module.change_country_for_key,
        bot_module.change_protocol_for_key,
        bot_module.reissue_specific_key,
        bot_module.delete_old_key_after_success
    )
    
    # Дополнительные handlers из bot.py регистрируются автоматически через декораторы
    # @dp.message_handler, так как мы установили bot_module.dp = dp выше


def start_background_tasks(loop):
    """
    Запуск фоновых задач
    
    Args:
        loop: Event loop
    """
    from bot.services.background_tasks import (
        auto_delete_expired_subscriptions,
        notify_expiring_subscriptions,
        check_and_create_keys_for_new_servers,
        retry_failed_subscription_notifications,
        sync_subscription_keys_with_active_servers,
    )
    
    background_tasks = [
        process_pending_paid_payments(),
        auto_delete_expired_keys(),
        notify_expiring_keys(),
        check_key_availability(),
        auto_delete_expired_subscriptions(),
        notify_expiring_subscriptions(),
        monitor_subscription_traffic_limits(),
        check_and_create_keys_for_new_servers(),
        retry_failed_subscription_notifications(),
        sync_subscription_keys_with_active_servers(),
    ]
    
    for task in background_tasks:
        try:
            loop.create_task(task)
            logging.info(f"Фоновая задача {task.__name__} запущена")
        except Exception as e:
            logging.error(f"Ошибка при запуске фоновой задачи {task.__name__}: {e}")


def main():
    """Главная функция запуска бота"""
    # Настройка логирования с маскированием секретов
    os.makedirs(LOG_DIR, exist_ok=True)
    setup_logging(level="INFO")
    try:
        bot_log_path = os.path.join(LOG_DIR, 'bot.log')
        file_handler = RotatingFileHandler(bot_log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        file_handler.addFilter(_SecretMaskingFilter())
        logging.getLogger().addHandler(file_handler)
    except Exception:
        pass
    
    logger = logging.getLogger(__name__)
    
    try:
        # Инициализация базы данных
        logger.info("Инициализация базы данных...")
        init_db_with_migrations()
        logger.info("База данных инициализирована успешно")
        
        # Настройка бота
        logger.info("Настройка бота...")
        bot, dp, user_states = setup_bot()
        logger.info("Бот настроен успешно")
        
        # Регистрация handlers
        logger.info("Регистрация handlers...")
        register_all_handlers(dp, bot, user_states)
        logger.info("Handlers зарегистрированы успешно")
        
        # Создание event loop
        loop = asyncio.get_event_loop()
        
        # Запуск фоновых задач
        logger.info("Запуск фоновых задач...")
        start_background_tasks(loop)
        
        logger.info("Запуск бота...")
        logging.info("🚀 VeilBot запущен с оптимизацией памяти")
        logging.info("Updates were skipped successfully.")
        
        # Настройка централизованной обработки ошибок
        logger.info("Настройка обработчика ошибок...")
        error_handler = setup_error_handler(dp, bot)
        logger.info("Обработчик ошибок настроен")
        
        # Запуск бота с обработкой ошибок
        executor.start_polling(dp, skip_updates=True, loop=loop)
        
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
        logging.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        logging.critical(f"Критическая ошибка: {e}")
        logging.error("Проверьте логи в файле %s", os.path.join(LOG_DIR, 'bot.log'))
        sys.exit(1)


if __name__ == "__main__":
    main()

