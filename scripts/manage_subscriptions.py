#!/usr/bin/env python3
"""
Скрипт для управления подписками:
1. Удаление подписки пользователя
2. Удаление подписок с v2ray серверов, которых нет в базе данных
"""
import asyncio
import sys
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

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


def extract_v2ray_uuid(remote_entry: Dict[str, Any]) -> Optional[str]:
    """Извлечь UUID из записи V2Ray"""
    uuid = remote_entry.get("uuid")
    if not uuid:
        key_info = remote_entry.get("key") or {}
        uuid = key_info.get("uuid")
    if not uuid:
        uuid = remote_entry.get("id")
    if isinstance(uuid, str) and uuid.strip():
        return uuid.strip()
    return None


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
    
    # Распаковка подписки (может быть tuple или Row)
    if isinstance(subscription, tuple):
        subscription_id, sub_user_id, token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified = subscription
    else:
        subscription_id = subscription["id"]
        sub_user_id = subscription["user_id"]
        token = subscription["subscription_token"]
        created_at = subscription["created_at"]
        expires_at = subscription["expires_at"]
        tariff_id = subscription["tariff_id"]
        is_active = subscription["is_active"]
        last_updated_at = subscription.get("last_updated_at")
        notified = subscription.get("notified", 0)
    
    logger.info(f"Найдена подписка ID={subscription_id}, токен={token[:8]}..., активна={is_active}")
    
    # Получить все ключи подписки
    subscription_keys = repo.get_subscription_keys_for_deletion(subscription_id)
    
    logger.info(f"Найдено {len(subscription_keys)} ключей для удаления")
    
    # Удалить ключи через V2Ray API
    deleted_v2ray_count = 0
    for v2ray_uuid, api_url, api_key in subscription_keys:
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


async def find_orphaned_v2ray_subscriptions() -> List[Dict[str, Any]]:
    """
    Найти подписки на v2ray серверах, которых нет в базе данных
    """
    orphaned_subscriptions = []
    
    # Получить все активные v2ray серверы
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, name, api_url, api_key, domain, country
            FROM servers
            WHERE protocol = 'v2ray' AND active = 1
            """
        )
        servers = cursor.fetchall()
    
    logger.info(f"Найдено {len(servers)} активных V2Ray серверов")
    
    # Получить все UUID из базы данных
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT v2ray_uuid, server_id, email, user_id
            FROM v2ray_keys
            WHERE v2ray_uuid IS NOT NULL AND v2ray_uuid != ''
            """
        )
        db_keys = cursor.fetchall()
    
    # Создать множество UUID из БД для быстрого поиска
    db_uuid_set = set()
    db_uuid_to_info = {}
    for row in db_keys:
        uuid = (row["v2ray_uuid"] or "").strip() if row["v2ray_uuid"] else ""
        if uuid:
            db_uuid_set.add(uuid)
            db_uuid_to_info[uuid] = {
                "server_id": row["server_id"],
                "email": row["email"],
                "user_id": row["user_id"],
            }
    
    logger.info(f"В базе данных найдено {len(db_uuid_set)} уникальных UUID")
    
    # Проверить каждый сервер
    for server_row in servers:
        server_id = server_row["id"]
        server_name = server_row["name"]
        api_url = server_row["api_url"]
        api_key = server_row["api_key"]
        domain = server_row["domain"]
        country = server_row["country"]
        
        if not api_url or not api_key:
            logger.warning(f"Сервер {server_name} (ID {server_id}) не имеет api_url или api_key, пропускаем")
            continue
        
        try:
            logger.info(f"Проверка сервера {server_name} (ID {server_id})...")
            protocol_client = V2RayProtocol(api_url, api_key)
            remote_keys = await protocol_client.get_all_keys()
            await protocol_client.close()
            
            if not remote_keys:
                logger.info(f"На сервере {server_name} нет ключей")
                continue
            
            logger.info(f"На сервере {server_name} найдено {len(remote_keys)} ключей")
            
            # Проверить каждый ключ с сервера
            for remote_entry in remote_keys:
                uuid = extract_v2ray_uuid(remote_entry)
                name = remote_entry.get("name") or ""
                email = remote_entry.get("email") or ""
                
                if not uuid:
                    logger.warning(f"Не удалось извлечь UUID из записи: {remote_entry}")
                    continue
                
                # Проверить, есть ли этот UUID в базе данных
                if uuid not in db_uuid_set:
                    # Проверить, может быть это подписка (email содержит subscription)
                    is_subscription = "_subscription_" in email.lower() or "subscription" in email.lower()
                    
                    orphaned_subscriptions.append({
                        "server_id": server_id,
                        "server_name": server_name,
                        "server_api_url": api_url,
                        "server_api_key": api_key,
                        "server_domain": domain,
                        "server_country": country,
                        "uuid": uuid,
                        "name": name,
                        "email": email,
                        "remote_entry": remote_entry,
                        "is_subscription": is_subscription,
                    })
                    logger.info(
                        f"Найден orphaned ключ на сервере {server_name}: "
                        f"UUID={uuid}, email={email}, name={name}"
                    )
        
        except Exception as exc:
            logger.error(f"Ошибка при проверке сервера {server_name} (ID {server_id}): {exc}", exc_info=True)
    
    return orphaned_subscriptions


def print_orphaned_subscriptions(orphaned: List[Dict[str, Any]]) -> None:
    """Вывести список orphaned подписок"""
    if not orphaned:
        print("\n✓ Orphaned подписки не найдены")
        return
    
    print(f"\n{'='*80}")
    print(f"НАЙДЕНО {len(orphaned)} ORPHANED ПОДПИСОК НА V2RAY СЕРВЕРАХ")
    print(f"{'='*80}\n")
    
    # Группировать по серверам
    by_server = {}
    for item in orphaned:
        server_id = item["server_id"]
        if server_id not in by_server:
            by_server[server_id] = {
                "server_name": item["server_name"],
                "server_country": item["server_country"],
                "items": [],
            }
        by_server[server_id]["items"].append(item)
    
    for server_id, server_data in sorted(by_server.items()):
        print(f"\nСервер: {server_data['server_name']} (ID: {server_id})")
        if server_data["server_country"]:
            print(f"Страна: {server_data['server_country']}")
        print(f"Количество orphaned подписок: {len(server_data['items'])}")
        print("-" * 80)
        
        for idx, item in enumerate(server_data["items"], 1):
            print(f"\n  {idx}. UUID: {item['uuid']}")
            print(f"     Email: {item['email']}")
            print(f"     Name: {item['name']}")
            print(f"     Подписка: {'Да' if item['is_subscription'] else 'Неизвестно'}")
    
    print(f"\n{'='*80}")


async def delete_orphaned_subscriptions(orphaned: List[Dict[str, Any]]) -> None:
    """Удалить orphaned подписки с серверов"""
    deleted_count = 0
    failed_count = 0
    
    for item in orphaned:
        server_name = item["server_name"]
        uuid = item["uuid"]
        api_url = item["server_api_url"]
        api_key = item["server_api_key"]
        
        try:
            logger.info(f"Удаление orphaned подписки {uuid} с сервера {server_name}")
            protocol_client = V2RayProtocol(api_url, api_key)
            result = await protocol_client.delete_user(uuid)
            await protocol_client.close()
            
            if result:
                deleted_count += 1
                logger.info(f"✓ Успешно удалена orphaned подписка {uuid} с сервера {server_name}")
            else:
                failed_count += 1
                logger.warning(f"✗ Не удалось удалить orphaned подписку {uuid} с сервера {server_name}")
        
        except Exception as exc:
            failed_count += 1
            logger.error(
                f"✗ Ошибка при удалении orphaned подписки {uuid} с сервера {server_name}: {exc}",
                exc_info=True
            )
    
    logger.info(
        f"\n{'='*80}\n"
        f"Результат удаления orphaned подписок:\n"
        f"  Успешно удалено: {deleted_count}\n"
        f"  Ошибок: {failed_count}\n"
        f"{'='*80}"
    )


async def main():
    if len(sys.argv) < 2:
        print("Использование:")
        print("  python manage_subscriptions.py delete_user <user_id>")
        print("  python manage_subscriptions.py find_orphaned")
        print("  python manage_subscriptions.py delete_orphaned")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "delete_user":
        if len(sys.argv) < 3:
            print("Ошибка: требуется user_id")
            print("Использование: python manage_subscriptions.py delete_user <user_id>")
            sys.exit(1)
        
        try:
            user_id = int(sys.argv[2])
        except ValueError:
            print(f"Ошибка: {sys.argv[2]} не является валидным user_id")
            sys.exit(1)
        
        await delete_user_subscription(user_id)
    
    elif command == "find_orphaned":
        print("Поиск orphaned подписок на v2ray серверах...")
        orphaned = await find_orphaned_v2ray_subscriptions()
        print_orphaned_subscriptions(orphaned)
    
    elif command == "delete_orphaned":
        print("Поиск orphaned подписок на v2ray серверах...")
        orphaned = await find_orphaned_v2ray_subscriptions()
        
        if not orphaned:
            print("\n✓ Orphaned подписки не найдены, удаление не требуется")
            return
        
        print_orphaned_subscriptions(orphaned)
        
        print("\n" + "="*80)
        response = input(f"\nУдалить {len(orphaned)} orphaned подписок? (yes/no): ").strip().lower()
        
        if response == "yes":
            await delete_orphaned_subscriptions(orphaned)
        else:
            print("Удаление отменено")
    
    else:
        print(f"Неизвестная команда: {command}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

