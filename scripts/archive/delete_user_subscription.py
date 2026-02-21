#!/usr/bin/env python3
"""
Скрипт для удаления подписки пользователя через API
"""
import asyncio
import sys
import logging
from pathlib import Path

# Добавляем корневую директорию в путь
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from app.repositories.subscription_repository import SubscriptionRepository
from bot.services.subscription_service import invalidate_subscription_cache
from app.infra.sqlite_utils import get_db_cursor
from app.infra.foreign_keys import safe_foreign_keys_off
from vpn_protocols import V2RayProtocol

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def delete_user_subscription(user_id: int) -> None:
    """Удалить подписку пользователя"""
    repo = SubscriptionRepository()
    
    # Найти активную подписку пользователя
    subscription = repo.get_active_subscription(user_id)
    
    if not subscription:
        logger.warning(f"Активная подписка не найдена для пользователя {user_id}")
        # Попробуем найти любую подписку (включая неактивную)
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified
                FROM subscriptions
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id,),
            )
            subscription = cursor.fetchone()
    
    if not subscription:
        logger.error(f"Подписка не найдена для пользователя {user_id}")
        return
    
    subscription_id, sub_user_id, token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified = subscription
    
    logger.info(f"Найдена подписка ID={subscription_id}, токен={token[:8]}..., активна={is_active}")
    
    # Получить все ключи подписки
    subscription_keys = repo.get_subscription_keys_for_deletion(subscription_id)
    
    logger.info(f"Найдено {len(subscription_keys)} ключей для удаления")
    
    # Удалить ключи через V2Ray API
    deleted_v2ray_count = 0
    for key_identifier, api_url, api_key, protocol in subscription_keys:
        # Обрабатываем только V2Ray ключи
        if protocol != 'v2ray':
            continue
        v2ray_uuid = key_identifier
        if v2ray_uuid and api_url and api_key:
            try:
                logger.info(f"Удаление V2Ray ключа {v2ray_uuid} с сервера {api_url}")
                protocol_client = V2RayProtocol(api_url, api_key)
                result = await protocol_client.delete_user(v2ray_uuid)
                if result:
                    deleted_v2ray_count += 1
                    logger.info(f"Успешно удален V2Ray ключ {v2ray_uuid}")
                else:
                    logger.warning(f"Не удалось удалить V2Ray ключ {v2ray_uuid}")
                await protocol_client.close()
            except Exception as exc:
                logger.error(f"Ошибка при удалении V2Ray ключа {v2ray_uuid}: {exc}", exc_info=True)
    
    # Удалить ключи из БД
    with get_db_cursor(commit=True) as cursor:
        with safe_foreign_keys_off(cursor):
            cursor.execute(
                "DELETE FROM v2ray_keys WHERE subscription_id = ?",
                (subscription_id,),
            )
            deleted_keys_count = cursor.rowcount
    
    logger.info(f"Удалено {deleted_keys_count} ключей из БД")
    
    # Деактивировать подписку
    repo.deactivate_subscription(subscription_id)
    logger.info(f"Подписка {subscription_id} деактивирована")
    
    # Инвалидировать кэш
    invalidate_subscription_cache(token)
    logger.info(f"Кэш подписки инвалидирован")
    
    logger.info(
        f"Подписка {subscription_id} успешно удалена: "
        f"V2Ray ключей удалено={deleted_v2ray_count}, "
        f"ключей из БД удалено={deleted_keys_count}"
    )


async def main():
    if len(sys.argv) < 2:
        print("Использование: python delete_user_subscription.py <user_id>")
        sys.exit(1)
    
    try:
        user_id = int(sys.argv[1])
    except ValueError:
        print(f"Ошибка: {sys.argv[1]} не является валидным user_id")
        sys.exit(1)
    
    await delete_user_subscription(user_id)


if __name__ == "__main__":
    asyncio.run(main())

