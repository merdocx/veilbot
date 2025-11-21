#!/usr/bin/env python3
"""
Скрипт для полного удаления всех данных пользователя:
- Все подписки (с удалением ключей с серверов)
- Все ключи v2ray (с удалением с серверов)
- Все ключи outline (с удалением с серверов)
- Все платежи
"""
import asyncio
import sys
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

# Добавляем корневую директорию в путь
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from app.repositories.subscription_repository import SubscriptionRepository
from bot.services.subscription_service import invalidate_subscription_cache
from app.infra.sqlite_utils import get_db_cursor
from app.infra.foreign_keys import safe_foreign_keys_off
from vpn_protocols import V2RayProtocol
from outline import delete_key as outline_delete_key

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def delete_all_user_data(user_id: int) -> Dict[str, int]:
    """
    Полное удаление всех данных пользователя
    
    Returns:
        Словарь с количеством удаленных записей
    """
    results = {
        'subscriptions': 0,
        'subscription_keys_v2ray': 0,
        'v2ray_keys': 0,
        'outline_keys': 0,
        'payments': 0,
        'errors': []
    }
    
    # 1. Удаление всех подписок пользователя
    logger.info(f"Удаление всех подписок пользователя {user_id}...")
    repo = SubscriptionRepository()
    
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, subscription_token
            FROM subscriptions
            WHERE user_id = ?
            """,
            (user_id,),
        )
        all_subscriptions = cursor.fetchall()
    
    for subscription_row in all_subscriptions:
        subscription_id = subscription_row["id"]
        token = subscription_row["subscription_token"]
        
        try:
            # Получить все ключи подписки
            subscription_keys = repo.get_subscription_keys_for_deletion(subscription_id)
            
            # Удалить ключи через V2Ray API
            for v2ray_uuid, api_url, api_key in subscription_keys:
                if v2ray_uuid and api_url and api_key:
                    protocol_client = None
                    try:
                        logger.info(f"Удаление V2Ray ключа подписки {v2ray_uuid} с сервера {api_url}")
                        protocol_client = V2RayProtocol(api_url, api_key)
                        result = await protocol_client.delete_user(v2ray_uuid)
                        if result:
                            results['subscription_keys_v2ray'] += 1
                            logger.info(f"✓ Успешно удален V2Ray ключ {v2ray_uuid}")
                    except Exception as exc:
                        error_msg = f"Ошибка при удалении V2Ray ключа {v2ray_uuid}: {exc}"
                        logger.error(error_msg, exc_info=True)
                        results['errors'].append(error_msg)
                    finally:
                        if protocol_client:
                            try:
                                await protocol_client.close()
                            except Exception:
                                pass
            
            # Удалить ключи из БД
            with get_db_cursor(commit=True) as cursor:
                with safe_foreign_keys_off(cursor):
                    cursor.execute(
                        "DELETE FROM v2ray_keys WHERE subscription_id = ?",
                        (subscription_id,),
                    )
            
            # Инвалидировать кэш перед удалением
            invalidate_subscription_cache(token)
            
            # Удалить подписку из БД
            with get_db_cursor(commit=True) as cursor:
                with safe_foreign_keys_off(cursor):
                    cursor.execute(
                        "DELETE FROM subscriptions WHERE id = ?",
                        (subscription_id,),
                    )
            
            results['subscriptions'] += 1
            logger.info(f"✓ Подписка {subscription_id} удалена")
        
        except Exception as exc:
            error_msg = f"Ошибка при удалении подписки {subscription_id}: {exc}"
            logger.error(error_msg, exc_info=True)
            results['errors'].append(error_msg)
    
    # 2. Удаление всех V2Ray ключей пользователя (включая не связанные с подписками)
    logger.info(f"Удаление всех V2Ray ключей пользователя {user_id}...")
    
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT k.v2ray_uuid, s.api_url, s.api_key, k.id
            FROM v2ray_keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.v2ray_uuid IS NOT NULL AND k.v2ray_uuid != ''
            """,
            (user_id,),
        )
        v2ray_keys = cursor.fetchall()
    
    for key_row in v2ray_keys:
        v2ray_uuid = key_row["v2ray_uuid"]
        api_url = key_row["api_url"]
        api_key = key_row["api_key"]
        db_id = key_row["id"]
        
        if v2ray_uuid and api_url and api_key:
            try:
                logger.info(f"Удаление V2Ray ключа {v2ray_uuid} с сервера {api_url}")
                protocol_client = V2RayProtocol(api_url, api_key)
                result = await protocol_client.delete_user(v2ray_uuid)
                if result:
                    results['v2ray_keys'] += 1
                    logger.info(f"✓ Успешно удален V2Ray ключ {v2ray_uuid}")
                await protocol_client.close()
            except Exception as exc:
                error_msg = f"Ошибка при удалении V2Ray ключа {v2ray_uuid}: {exc}"
                logger.error(error_msg, exc_info=True)
                results['errors'].append(error_msg)
    
    # Удалить все V2Ray ключи из БД
    with get_db_cursor(commit=True) as cursor:
        with safe_foreign_keys_off(cursor):
            cursor.execute(
                "DELETE FROM v2ray_keys WHERE user_id = ?",
                (user_id,),
            )
            deleted_count = cursor.rowcount
            logger.info(f"✓ Удалено {deleted_count} V2Ray ключей из БД")
    
    # 3. Удаление всех Outline ключей пользователя
    logger.info(f"Удаление всех Outline ключей пользователя {user_id}...")
    
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT k.key_id, s.api_url, s.cert_sha256, k.id
            FROM keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.key_id IS NOT NULL AND k.key_id != ''
            """,
            (user_id,),
        )
        outline_keys = cursor.fetchall()
    
    for key_row in outline_keys:
        key_id = key_row["key_id"]
        api_url = key_row["api_url"]
        cert_sha256 = key_row["cert_sha256"]
        db_id = key_row["id"]
        
        if key_id and api_url and cert_sha256:
            try:
                logger.info(f"Удаление Outline ключа {key_id} с сервера {api_url}")
                result = await asyncio.get_event_loop().run_in_executor(
                    None, outline_delete_key, api_url, cert_sha256, key_id
                )
                if result:
                    results['outline_keys'] += 1
                    logger.info(f"✓ Успешно удален Outline ключ {key_id}")
            except Exception as exc:
                error_msg = f"Ошибка при удалении Outline ключа {key_id}: {exc}"
                logger.error(error_msg, exc_info=True)
                results['errors'].append(error_msg)
    
    # Удалить все Outline ключи из БД
    with get_db_cursor(commit=True) as cursor:
        with safe_foreign_keys_off(cursor):
            cursor.execute(
                "DELETE FROM keys WHERE user_id = ?",
                (user_id,),
            )
            deleted_count = cursor.rowcount
            logger.info(f"✓ Удалено {deleted_count} Outline ключей из БД")
    
    # 4. Удаление всех платежей пользователя
    logger.info(f"Удаление всех платежей пользователя {user_id}...")
    
    with get_db_cursor(commit=True) as cursor:
        with safe_foreign_keys_off(cursor):
            cursor.execute(
                "DELETE FROM payments WHERE user_id = ?",
                (user_id,),
            )
            results['payments'] = cursor.rowcount
            logger.info(f"✓ Удалено {results['payments']} платежей из БД")
    
    return results


async def main():
    if len(sys.argv) < 2:
        print("Использование: python delete_user_all_data.py <user_id>")
        sys.exit(1)
    
    try:
        user_id = int(sys.argv[1])
    except ValueError:
        print(f"Ошибка: {sys.argv[1]} не является валидным user_id")
        sys.exit(1)
    
    print(f"\n{'='*80}")
    print(f"⚠️  ВНИМАНИЕ: Будет выполнено полное удаление всех данных пользователя {user_id}")
    print(f"{'='*80}")
    print("\nБудут удалены:")
    print("  - Все подписки (с ключами на серверах)")
    print("  - Все V2Ray ключи (с серверов и из БД)")
    print("  - Все Outline ключи (с серверов и из БД)")
    print("  - Все платежи")
    print()
    
    response = input("Продолжить? (yes/no): ").strip().lower()
    if response != "yes":
        print("❌ Операция отменена")
        sys.exit(0)
    
    print()
    print(f"Начинаю удаление данных пользователя {user_id}...")
    print()
    
    try:
        results = await delete_all_user_data(user_id)
        
        print()
        print(f"{'='*80}")
        print("✅ Удаление завершено")
        print(f"{'='*80}")
        print(f"\nРезультаты:")
        print(f"  Подписок удалено: {results['subscriptions']}")
        print(f"  V2Ray ключей подписок удалено с серверов: {results['subscription_keys_v2ray']}")
        print(f"  V2Ray ключей удалено с серверов: {results['v2ray_keys']}")
        print(f"  Outline ключей удалено с серверов: {results['outline_keys']}")
        print(f"  Платежей удалено: {results['payments']}")
        
        if results['errors']:
            print(f"\n⚠️  Ошибки ({len(results['errors'])}):")
            for error in results['errors']:
                print(f"  - {error}")
        else:
            print("\n✓ Ошибок не было")
        
        print(f"\n{'='*80}\n")
    
    except Exception as exc:
        logger.error(f"Критическая ошибка при удалении данных пользователя: {exc}", exc_info=True)
        print(f"\n❌ Критическая ошибка: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

