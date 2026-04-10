#!/usr/bin/env python3
"""
Скрипт для отправки уведомления о продлении подписки пользователю
"""
import argparse
import sys
import os
import asyncio
import logging
from datetime import datetime

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.tariff_repository import TariffRepository
from app.infra.sqlite_utils import get_db_cursor
from bot.core import get_bot_instance
from bot.utils import safe_send_message
from bot.keyboards import get_main_menu
from vpn_protocols import format_duration

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def send_renewal_notification(user_id: int):
    """Отправить уведомление о продлении подписки пользователю"""
    try:
        subscription_repo = SubscriptionRepository()
        tariff_repo = TariffRepository()
        
        # Получаем активную подписку пользователя
        subscription = subscription_repo.get_active_subscription(user_id)
        
        if not subscription:
            logger.error(f"Активная подписка не найдена для пользователя {user_id}")
            return False
        
        subscription_id, sub_user_id, token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified = subscription
        
        # Проверяем флаг отправки уведомления
        with get_db_cursor() as cursor:
            cursor.execute(
                "SELECT purchase_notification_sent FROM subscriptions WHERE id = ?",
                (subscription_id,)
            )
            row = cursor.fetchone()
            row[0] if row else 0
        
        # Определяем, было ли это продление или создание
        # Если last_updated_at > created_at, то это продление
        is_renewal = last_updated_at and created_at and last_updated_at > created_at
        
        # Получаем тариф
        tariff_row = tariff_repo.get_tariff(tariff_id)
        if not tariff_row:
            logger.error(f"Тариф {tariff_id} не найден")
            return False
        
        tariff = {
            'id': tariff_row[0],
            'name': tariff_row[1],
            'duration_sec': tariff_row[2],
            'price_rub': tariff_row[3],
        }
        
        # Формируем сообщение
        subscription_url = f"https://veil-bot.ru/api/subscription/{token}"
        
        if is_renewal:
            # Уведомление о продлении
            msg = (
                f"✅ *Подписка V2Ray успешно продлена!*\n\n"
                f"🔗 *Ссылка подписки:*\n"
                f"`{subscription_url}`\n\n"
                f"⏳ *Добавлено времени:* {format_duration(tariff['duration_sec'])}\n"
                f"📅 *Новый срок действия:* до {datetime.fromtimestamp(expires_at).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"💡 Подписка автоматически обновится в вашем приложении"
            )
        else:
            # Уведомление о создании
            msg = (
                f"✅ *Подписка V2Ray успешно создана!*\n\n"
                f"🔗 *Ссылка подписки:*\n"
                f"`{subscription_url}`\n\n"
                f"⏳ *Срок действия:* {format_duration(tariff['duration_sec'])}\n\n"
                f"💡 *Как использовать:*\n"
                f"1. Откройте приложение V2Ray\n"
                f"2. Нажмите \"+\" → \"Импорт подписки\"\n"
                f"3. Вставьте ссылку выше\n"
                f"4. Все серверы будут добавлены автоматически"
            )
        
        # Отправляем уведомление
        bot = get_bot_instance()
        if not bot:
            logger.error("Bot instance не доступен")
            return False
        
        result = await safe_send_message(
            bot,
            user_id,
            msg,
            reply_markup=get_main_menu(user_id),
            disable_web_page_preview=True,
            parse_mode="Markdown"
        )
        
        if result:
            # Помечаем уведомление как отправленное
            subscription_repo.mark_purchase_notification_sent(subscription_id)
            logger.info(f"Уведомление успешно отправлено пользователю {user_id} (подписка {subscription_id}, тип: {'продление' if is_renewal else 'создание'})")
            return True
        else:
            logger.warning(f"Не удалось отправить уведомление пользователю {user_id}")
            return False
            
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}", exc_info=True)
        return False


async def main():
    parser = argparse.ArgumentParser(
        description="Отправить уведомление о создании/продлении подписки пользователю"
    )
    parser.add_argument(
        "user_id",
        type=int,
        help="Идентификатор пользователя, которому нужно отправить уведомление",
    )
    args = parser.parse_args()
    user_id = args.user_id

    logger.info(f"Проверка и отправка уведомления для пользователя {user_id}")
    
    success = await send_renewal_notification(user_id)
    
    if success:
        logger.info("Уведомление успешно отправлено")
        sys.exit(0)
    else:
        logger.error("Не удалось отправить уведомление")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

