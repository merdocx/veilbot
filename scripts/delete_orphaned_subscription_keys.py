#!/usr/bin/env python3
"""
Скрипт для удаления ключей несуществующих подписок
"""
import asyncio
import sys
import os
from typing import List, Dict, Any

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.infra.sqlite_utils import get_db_cursor
from app.infra.foreign_keys import safe_foreign_keys_off
from vpn_protocols import ProtocolFactory
import logging
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def delete_orphaned_subscription_keys(dry_run: bool = False) -> Dict[str, Any]:
    """
    Удалить ключи для несуществующих подписок
    
    Returns:
        dict с результатами удаления
    """
    results = {
        'orphaned_v2ray_keys': [],
        'deleted_v2ray_keys': 0,
        'failed_deletions': 0,
        'errors': []
    }
    
    now = int(time.time())
    
    # Найти все v2ray ключи с subscription_id, которых нет в таблице subscriptions
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT vk.id, vk.subscription_id, vk.server_id, vk.v2ray_uuid, 
                   vk.user_id, vk.email, vk.expiry_at,
                   s.name as server_name, s.api_url, s.api_key, s.domain
            FROM v2ray_keys vk
            JOIN servers s ON vk.server_id = s.id
            WHERE vk.subscription_id IS NOT NULL
            AND vk.expiry_at > ?
            AND NOT EXISTS (
                SELECT 1 FROM subscriptions s2 
                WHERE s2.id = vk.subscription_id
            )
        """, (now,))
        orphaned_keys = cursor.fetchall()
    
    if not orphaned_keys:
        logger.info("Не найдено ключей для несуществующих подписок")
        return results
    
    logger.info(f"Найдено {len(orphaned_keys)} ключей для несуществующих подписок")
    
    for key_row in orphaned_keys:
        key_id = key_row[0]
        subscription_id = key_row[1]
        server_id = key_row[2]
        v2ray_uuid = key_row[3]
        user_id = key_row[4]
        email = key_row[5]
        expiry_at = key_row[6]
        server_name = key_row[7]
        api_url = key_row[8]
        api_key = key_row[9]
        domain = key_row[10]
        
        key_info = {
            'key_id': key_id,
            'subscription_id': subscription_id,
            'server_id': server_id,
            'v2ray_uuid': v2ray_uuid,
            'user_id': user_id,
            'email': email,
            'server_name': server_name
        }
        
        results['orphaned_v2ray_keys'].append(key_info)
        
        logger.info(
            f"Найден orphaned ключ: ID={key_id}, subscription_id={subscription_id}, "
            f"server={server_name} (ID={server_id}), UUID={v2ray_uuid[:8]}..., user={user_id}"
        )
        
        if dry_run:
            logger.info(f"[DRY RUN] Пропущено удаление ключа {key_id}")
            continue
        
        # Удалить с сервера
        deleted_from_server = False
        if api_url and api_key and v2ray_uuid:
            try:
                server_config = {
                    'api_url': api_url,
                    'api_key': api_key,
                    'domain': domain or '',
                }
                protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                try:
                    deleted_from_server = await protocol_client.delete_user(v2ray_uuid)
                    if deleted_from_server:
                        logger.info(f"✓ Ключ {v2ray_uuid[:8]}... удален с сервера {server_name}")
                    else:
                        logger.warning(f"✗ Не удалось удалить ключ {v2ray_uuid[:8]}... с сервера {server_name}")
                finally:
                    await protocol_client.close()
            except Exception as e:
                error_msg = f"Ошибка при удалении ключа {v2ray_uuid[:8]}... с сервера {server_name}: {e}"
                logger.error(error_msg, exc_info=True)
                results['errors'].append(error_msg)
        
        # Удалить из БД
        # Для orphaned ключей удаляем из БД даже если не удалось удалить с сервера,
        # т.к. подписка не существует и ключ должен быть удален
        try:
            with get_db_cursor(commit=True) as cursor:
                with safe_foreign_keys_off(cursor):
                    cursor.execute("DELETE FROM v2ray_keys WHERE id = ?", (key_id,))
                    if cursor.rowcount > 0:
                        results['deleted_v2ray_keys'] += 1
                        if deleted_from_server:
                            logger.info(f"✓ Ключ {key_id} удален из БД и с сервера")
                        else:
                            logger.info(f"✓ Ключ {key_id} удален из БД (не удалось удалить с сервера, но подписка не существует)")
                    else:
                        logger.warning(f"✗ Ключ {key_id} не найден в БД")
        except Exception as e:
            error_msg = f"Ошибка при удалении ключа {key_id} из БД: {e}"
            logger.error(error_msg, exc_info=True)
            results['errors'].append(error_msg)
            results['failed_deletions'] += 1
    
    # Проверить outline ключи
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT k.id, k.subscription_id, k.server_id, k.key_id,
                   k.user_id, k.email, k.expiry_at,
                   s.name as server_name, s.api_url, s.cert_sha256
            FROM keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.subscription_id IS NOT NULL
            AND k.protocol = 'outline'
            AND k.expiry_at > ?
            AND NOT EXISTS (
                SELECT 1 FROM subscriptions s2 
                WHERE s2.id = k.subscription_id
            )
        """, (now,))
        orphaned_outline_keys = cursor.fetchall()
    
    if orphaned_outline_keys:
        logger.info(f"Найдено {len(orphaned_outline_keys)} outline ключей для несуществующих подписок")
        # TODO: Добавить удаление outline ключей если нужно
    
    return results


async def main():
    """Основная функция"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Удалить ключи для несуществующих подписок')
    parser.add_argument('--dry-run', action='store_true', help='Только показать, что будет удалено, без реального удаления')
    args = parser.parse_args()
    
    print("=" * 80)
    print("Удаление ключей для несуществующих подписок")
    print("=" * 80)
    
    if args.dry_run:
        print("\n[РЕЖИМ ПРОВЕРКИ] Реальное удаление не будет выполнено\n")
    
    results = await delete_orphaned_subscription_keys(dry_run=args.dry_run)
    
    print("\n" + "=" * 80)
    print("Результаты:")
    print("=" * 80)
    print(f"Найдено orphaned ключей: {len(results['orphaned_v2ray_keys'])}")
    print(f"Удалено из БД: {results['deleted_v2ray_keys']}")
    print(f"Ошибок при удалении: {results['failed_deletions']}")
    
    if results['errors']:
        print(f"\nОшибки ({len(results['errors'])}):")
        for error in results['errors']:
            print(f"  - {error}")
    
    if results['orphaned_v2ray_keys']:
        print(f"\nДетали найденных ключей:")
        for key_info in results['orphaned_v2ray_keys']:
            print(f"  - Key ID: {key_info['key_id']}, Subscription ID: {key_info['subscription_id']}, "
                  f"Server: {key_info['server_name']} (ID: {key_info['server_id']}), "
                  f"User: {key_info['user_id']}")


if __name__ == "__main__":
    asyncio.run(main())

