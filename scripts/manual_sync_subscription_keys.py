#!/usr/bin/env python3
"""Скрипт для ручного запуска sync_subscription_keys_with_active_servers()"""

import sys
import os
import asyncio

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bot.services.background_tasks import sync_subscription_keys_with_active_servers

async def main():
    """Запустить синхронизацию вручную"""
    print("Запуск sync_subscription_keys_with_active_servers()...")
    print("=" * 80)
    
    # Вызываем функцию напрямую (без _run_periodic обертки)
    # Нужно вызвать внутреннюю job функцию
    # Для этого создадим временную функцию
    
    async def run_sync():
        """Временная функция для запуска синхронизации"""
        from bot.services.background_tasks import get_db_cursor
        import time
        from collections import defaultdict
        from bot.services.subscription_service import invalidate_subscription_cache
        from bot.services.background_tasks import (
            _process_subscription_sync,
            _delete_orphaned_keys_from_server
        )
        import logging
        
        logger = logging.getLogger(__name__)
        now = int(time.time())
        
        # Получаем все активные подписки
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT id, user_id, subscription_token, expires_at, tariff_id
                FROM subscriptions
                WHERE is_active = 1 AND expires_at > ?
            """, (now,))
            active_subscriptions = cursor.fetchall()
        
        if not active_subscriptions:
            print("Активных подписок не найдено")
            # Все равно проверяем orphaned ключи на всех серверах
            active_subscriptions = []
        
        # Получаем все активные V2Ray серверы
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, api_url, api_key, domain, v2ray_path
                FROM servers
                WHERE protocol = 'v2ray' AND active = 1
                ORDER BY id
            """)
            v2ray_servers = cursor.fetchall()
        
        # Получаем активные Outline серверы (только сервер с id=8 и available_for_purchase=1)
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, api_url, cert_sha256
                FROM servers
                WHERE protocol = 'outline' AND active = 1 
                AND available_for_purchase = 1
                ORDER BY CASE WHEN id = 8 THEN 0 ELSE 1 END, id
                LIMIT 1
            """)
            outline_servers = cursor.fetchall()
        
        # Получаем ВСЕ серверы для проверки orphaned ключей (не только активные)
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, protocol, api_url, api_key, cert_sha256
                FROM servers
                WHERE protocol IN ('v2ray', 'outline')
            """)
            all_servers = cursor.fetchall()
        
        if not v2ray_servers and not outline_servers and not active_subscriptions:
            print("Активных серверов или подписок не найдено")
            # Все равно проверяем orphaned ключи
            if not all_servers:
                return
        
        # Обрабатываем V2Ray серверы
        v2ray_active_server_ids = {server[0] for server in v2ray_servers}
        v2ray_active_servers_dict = {server[0]: server for server in v2ray_servers}
        
        # Обрабатываем Outline серверы
        outline_active_server_ids = {server[0] for server in outline_servers}
        outline_active_servers_dict = {server[0]: server for server in outline_servers}
        
        print(f"Найдено: {len(active_subscriptions)} подписок, "
              f"{len(v2ray_servers)} V2Ray серверов, {len(outline_servers)} Outline серверов, "
              f"{len(all_servers)} всего серверов для проверки orphaned ключей")
        
        # Rate limiting для API-запросов
        api_semaphore = asyncio.Semaphore(10)
        
        # Синхронизация подписок (если есть активные подписки)
        total_created = 0
        total_deleted = 0
        total_failed_create = 0
        total_failed_delete = 0
        tokens_to_invalidate = set()
        
        if active_subscriptions:
            # Получаем все ключи подписок одним запросом для каждого протокола
            subscription_ids = [sub[0] for sub in active_subscriptions]
            placeholders = ','.join('?' * len(subscription_ids))
            
            # V2Ray ключи
            v2ray_keys_by_subscription = defaultdict(list)
            if v2ray_servers:
                with get_db_cursor() as cursor:
                    cursor.execute(f"""
                        SELECT k.id, k.server_id, k.v2ray_uuid, s.api_url, s.api_key, k.subscription_id
                        FROM v2ray_keys k
                        JOIN servers s ON k.server_id = s.id
                        WHERE k.subscription_id IN ({placeholders})
                    """, subscription_ids)
                    all_v2ray_keys = cursor.fetchall()
                
                # Группируем ключи по subscription_id
                for key_row in all_v2ray_keys:
                    key_id, server_id, v2ray_uuid, api_url, api_key, sub_id = key_row
                    v2ray_keys_by_subscription[sub_id].append((key_id, server_id, v2ray_uuid, api_url, api_key))
            
            # Outline ключи
            outline_keys_by_subscription = defaultdict(list)
            if outline_servers:
                with get_db_cursor() as cursor:
                    cursor.execute(f"""
                        SELECT k.id, k.server_id, k.key_id, s.api_url, s.cert_sha256, k.subscription_id
                        FROM keys k
                        JOIN servers s ON k.server_id = s.id
                        WHERE k.subscription_id IN ({placeholders})
                        AND k.protocol = 'outline'
                    """, subscription_ids)
                    all_outline_keys = cursor.fetchall()
                
                # Группируем ключи по subscription_id
                for key_row in all_outline_keys:
                    key_id, server_id, outline_key_id, api_url, cert_sha256, sub_id = key_row
                    outline_keys_by_subscription[sub_id].append((key_id, server_id, outline_key_id, api_url, cert_sha256))
        
        # Параллельная обработка подписок батчами
        batch_size = 20
        
        for i in range(0, len(active_subscriptions), batch_size):
            batch = active_subscriptions[i:i + batch_size]
            
            # Создаем задачи для батча
            tasks = [
                _process_subscription_sync(
                        subscription, 
                        v2ray_keys_by_subscription, outline_keys_by_subscription,
                        v2ray_active_server_ids, v2ray_active_servers_dict,
                        outline_active_server_ids, outline_active_servers_dict,
                    now, api_semaphore
                )
                for subscription in batch
            ]
            
            # Параллельно обрабатываем батч
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Собираем статистику
            for result in batch_results:
                if isinstance(result, Exception):
                    print(f"Ошибка в батче: {result}")
                    continue
                
                total_created += result['created']
                total_deleted += result['deleted']
                total_failed_create += result['failed_create']
                total_failed_delete += result['failed_delete']
                tokens_to_invalidate.update(result.get('tokens_to_invalidate', set()))
        
        # Батчинг инвалидации кэша
        for token in tokens_to_invalidate:
            invalidate_subscription_cache(token)
        
        # Удаление orphaned ключей с ВСЕХ серверов
        total_orphaned_deleted = 0
        total_orphaned_errors = 0
        
        if all_servers:
            print(f"Проверка {len(all_servers)} серверов на orphaned ключи...")
            
            orphaned_tasks = []
            for server_row in all_servers:
                server_id, server_name, protocol, api_url, api_key, cert_sha256 = server_row
                if protocol == 'v2ray':
                    orphaned_tasks.append(
                        _delete_orphaned_keys_from_server(
                            server_id, server_name, 'v2ray', api_url, api_key,
                            None, api_semaphore
                        )
                    )
                elif protocol == 'outline':
                    orphaned_tasks.append(
                        _delete_orphaned_keys_from_server(
                            server_id, server_name, 'outline', api_url, None,
                            cert_sha256, api_semaphore
                        )
                    )
            
            if orphaned_tasks:
                orphaned_results = await asyncio.gather(*orphaned_tasks, return_exceptions=True)
                for result in orphaned_results:
                    if isinstance(result, Exception):
                        print(f"Ошибка при удалении orphaned ключей: {result}")
                        total_orphaned_errors += 1
                    else:
                        deleted, errors = result
                        total_orphaned_deleted += deleted
                        total_orphaned_errors += errors
        
        print("\n" + "=" * 80)
        print("РЕЗУЛЬТАТЫ СИНХРОНИЗАЦИИ:")
        print(f"  Создано ключей: {total_created}")
        print(f"  Удалено ключей: {total_deleted}")
        print(f"  Ошибок при создании: {total_failed_create}")
        print(f"  Ошибок при удалении: {total_failed_delete}")
        print(f"  Orphaned ключей удалено: {total_orphaned_deleted}")
        print(f"  Ошибок при удалении orphaned: {total_orphaned_errors}")
        print(f"  Инвалидировано кэшей: {len(tokens_to_invalidate)}")
        print("=" * 80)
    
    await run_sync()

if __name__ == "__main__":
    asyncio.run(main())
