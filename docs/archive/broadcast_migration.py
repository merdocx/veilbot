#!/usr/bin/env python3
"""
Скрипт для рассылки сообщения пользователям с ключами, но без подписок
о переходе на систему подписок
"""
import sys
import os
import asyncio
import logging
from typing import List, Tuple

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.infra.sqlite_utils import get_db_cursor
from bot.core import set_bot_instance
from bot.utils.messaging import safe_send_message

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_users_with_keys_but_no_subscriptions() -> List[Tuple[int, str, str, str]]:
    """
    Получить список пользователей с ключами, но без активных подписок
    
    Returns:
        List[Tuple[user_id, username, first_name, last_name]]
    """
    import time
    now = int(time.time())
    
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT u.user_id, u.username, u.first_name, u.last_name
            FROM users u
            WHERE u.blocked = 0
              AND (
                  -- Есть активные Outline ключи
                  EXISTS (
                      SELECT 1 FROM keys k 
                      WHERE k.user_id = u.user_id AND k.expiry_at > ?
                  )
                  OR
                  -- Есть активные V2Ray ключи без подписки
                  EXISTS (
                      SELECT 1 FROM v2ray_keys vk 
                      WHERE vk.user_id = u.user_id 
                        AND vk.expiry_at > ? 
                        AND vk.subscription_id IS NULL
                  )
              )
              AND NOT EXISTS (
                  -- Нет активных подписок
                  SELECT 1 FROM subscriptions s 
                  WHERE s.user_id = u.user_id 
                    AND s.is_active = 1 
                    AND s.expires_at > ?
              )
            ORDER BY u.user_id
        """, (now, now, now))
        
        return cursor.fetchall()


def create_migration_keyboard() -> InlineKeyboardMarkup:
    """
    Создать инлайн клавиатуру с кнопкой "Перейти на подписку"
    """
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("🔄 Перейти на подписку", callback_data="migrate_to_subscription")
    )
    return keyboard


async def send_migration_message(
    bot: Bot,
    user_id: int,
    message_text: str,
    keyboard: InlineKeyboardMarkup
) -> bool:
    """
    Отправить сообщение пользователю
    
    Args:
        bot: Экземпляр бота
        user_id: ID пользователя
        message_text: Текст сообщения
        keyboard: Клавиатура с кнопками
        
    Returns:
        True если отправлено успешно, False иначе
    """
    try:
        await safe_send_message(
            bot,
            user_id,
            message_text,
            reply_markup=keyboard,
            parse_mode='Markdown',
            mark_blocked=True
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения пользователю {user_id}: {e}")
        return False


async def broadcast_migration_message(
    message_text: str,
    dry_run: bool = False
) -> None:
    """
    Отправить рассылку пользователям о переходе на подписки
    
    Args:
        message_text: Текст сообщения для рассылки
        dry_run: Если True, только показывает список пользователей, не отправляет
    """
    # Получаем список пользователей
    users = get_users_with_keys_but_no_subscriptions()
    
    logger.info(f"Найдено {len(users)} пользователей для рассылки")
    
    if len(users) == 0:
        logger.warning("Нет пользователей для рассылки")
        return
    
    # Показываем список пользователей
    print("\n" + "=" * 80)
    print("СПИСОК ПОЛЬЗОВАТЕЛЕЙ ДЛЯ РАССЫЛКИ:")
    print("=" * 80)
    for i, (user_id, username, first_name, last_name) in enumerate(users, 1):
        name = f'{first_name or ""} {last_name or ""}'.strip() or 'Без имени'
        username_display = f' @{username}' if username else ''
        print(f"{i}. ID: {user_id} - {name}{username_display}")
    
    print("=" * 80)
    print(f"\nТЕКСТ СООБЩЕНИЯ:")
    print("-" * 80)
    print(message_text)
    print("-" * 80)
    
    if dry_run:
        logger.info("\n⚠️  Это был DRY RUN - сообщения не были отправлены")
        logger.info("Запустите скрипт без --dry-run для отправки сообщений")
        return
    
    # Создаем клавиатуру
    keyboard = create_migration_keyboard()
    
    # Инициализируем бота
    from config import TELEGRAM_BOT_TOKEN
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не установлен")
        return
    
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    set_bot_instance(bot)
    
    try:
        success_count = 0
        failed_count = 0
        
        logger.info(f"\nНачинаю рассылку для {len(users)} пользователей...")
        
        for i, (user_id, username, first_name, last_name) in enumerate(users, 1):
            name = f'{first_name or ""} {last_name or ""}'.strip() or 'Без имени'
            username_display = f' @{username}' if username else ''
            
            logger.info(f"[{i}/{len(users)}] Отправка пользователю {user_id} ({name}{username_display})...")
            
            if await send_migration_message(bot, user_id, message_text, keyboard):
                success_count += 1
                logger.info(f"✓ Сообщение отправлено пользователю {user_id}")
            else:
                failed_count += 1
                logger.warning(f"✗ Не удалось отправить сообщение пользователю {user_id}")
            
            # Небольшая задержка, чтобы не превысить лимиты Telegram
            await asyncio.sleep(0.05)
        
        logger.info("\n" + "=" * 80)
        logger.info("ИТОГИ РАССЫЛКИ:")
        logger.info(f"  Успешно отправлено: {success_count}")
        logger.info(f"  Ошибок: {failed_count}")
        logger.info(f"  Всего пользователей: {len(users)}")
        logger.info("=" * 80)
        
    finally:
        # Закрываем сессию бота
        try:
            session = await bot.get_session()
            if session:
                await session.close()
        except Exception as e:
            logger.warning(f"Ошибка при закрытии сессии бота: {e}")


async def main():
    """Главная функция"""
    import argparse
    
    # Текст сообщения по умолчанию
    default_message = """Мы полностью обновили систему и перешли на формат vpn-подписок, которые позволяют одновременно получать доступ ко всем странам и серверам, а все обновления конфигураций происходят автоматически. Теперь в случае любых проблем с блокировками мы сможем вносить изменения, а вам больше не потребуется самостоятельно перевыпускать ключи или что-то настраивать в приложении. 

Все цены и условия остаются прежними, срок действия вашего ключа сохранится. Для перехода на новую систему нажмите кнопку "Перейти на подписку"."""
    
    parser = argparse.ArgumentParser(description='Рассылка сообщений о переходе на подписки')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Показать список пользователей и текст, не отправляя сообщения'
    )
    parser.add_argument(
        '--message',
        type=str,
        default=default_message,
        help='Текст сообщения для рассылки'
    )
    
    args = parser.parse_args()
    
    try:
        await broadcast_migration_message(
            message_text=args.message,
            dry_run=args.dry_run
        )
    except KeyboardInterrupt:
        logger.info("\n\nПрервано пользователем")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

