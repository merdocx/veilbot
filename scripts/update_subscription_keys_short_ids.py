#!/usr/bin/env python3
"""
Скрипт для обновления всех ключей в подписках с реальными short id с серверов
"""
import sys
import os
import asyncio
import logging
from typing import List, Tuple

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.settings import settings
from app.repositories.subscription_repository import SubscriptionRepository
from utils import get_db_cursor
from vpn_protocols import ProtocolFactory, normalize_vless_host, remove_fragment_from_vless
from bot.services.subscription_service import invalidate_subscription_cache

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def update_subscription_keys_short_ids(dry_run: bool = False, force: bool = False) -> None:
    """
    Обновить все ключи в активных подписках с реальными short id с серверов
    
    Args:
        dry_run: Если True, только показывает что будет обновлено, не изменяет БД
        force: Если True, принудительно обновляет все ключи, даже если они уже имеют правильный short id
    """
    repo = SubscriptionRepository()
    
    # Получаем все активные подписки
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT id, subscription_token, user_id
            FROM subscriptions
            WHERE is_active = 1
            ORDER BY id
        """)
        subscriptions = cursor.fetchall()
    
    logger.info(f"Найдено {len(subscriptions)} активных подписок")
    
    total_updated = 0
    total_failed = 0
    total_skipped = 0
    
    for subscription_id, subscription_token, user_id in subscriptions:
        logger.info(f"\n{'='*60}")
        logger.info(f"Обработка подписки #{subscription_id} (токен: {subscription_token[:8]}...)")
        
        # Получаем все ключи подписки
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT 
                    k.v2ray_uuid,
                    k.client_config,
                    s.domain,
                    s.api_url,
                    s.api_key,
                    s.name as server_name,
                    s.id as server_id
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.subscription_id = ?
                ORDER BY s.id, k.id
            """, (subscription_id,))
            keys = cursor.fetchall()
        
        logger.info(f"Найдено {len(keys)} ключей в подписке")
        
        updated_count = 0
        failed_count = 0
        skipped_count = 0
        
        for (
            v2ray_uuid,
            old_client_config,
            domain,
            api_url,
            api_key,
            server_name,
            server_id
        ) in keys:
            logger.info(f"\n  Ключ {v2ray_uuid[:8]}... на сервере #{server_id} ({server_name})")
            
            # Проверяем, есть ли уже правильный short id в сохраненной конфигурации
            if not force and old_client_config and 'vless://' in old_client_config:
                # Извлекаем short id из старой конфигурации
                import urllib.parse
                try:
                    if '?' in old_client_config:
                        params_str = old_client_config.split('?')[1].split('#')[0]
                        params = urllib.parse.parse_qs(params_str)
                        old_sid = params.get('sid', [None])[0]
                        
                        # Если short id не является хардкодом, пропускаем (если не force)
                        if old_sid and old_sid != '827d3b463ef6638f':
                            logger.info(f"    ✓ Уже имеет short id: {old_sid[:8]}... (пропуск, используйте --force для принудительного обновления)")
                            skipped_count += 1
                            continue
                except Exception as e:
                    logger.debug(f"    Не удалось извлечь short id из старой конфигурации: {e}")
            
            # Получаем реальную конфигурацию с сервера
            try:
                if not api_url or not api_key:
                    logger.warning(f"    ⚠️  Нет API URL или ключа для сервера #{server_id}")
                    failed_count += 1
                    continue
                
                server_config = {
                    'api_url': api_url,
                    'api_key': api_key,
                    'domain': domain,
                }
                
                protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                
                # Получаем конфигурацию с реальным short id
                fetched_config = await protocol_client.get_user_config(
                    v2ray_uuid,
                    {
                        'domain': domain,
                        'port': 443,
                        'email': f"user_{user_id}@veilbot.com",
                    },
                )
                
                # Извлекаем VLESS URL из конфигурации
                if 'vless://' in fetched_config:
                    lines = fetched_config.split('\n')
                    for line in lines:
                        if line.strip().startswith('vless://'):
                            fetched_config = line.strip()
                            break
                
                # Нормализуем конфигурацию
                new_client_config = normalize_vless_host(
                    fetched_config,
                    domain,
                    api_url or ''
                )
                
                # Удаляем фрагмент (email) из конфигурации
                new_client_config = remove_fragment_from_vless(new_client_config)
                
                # Извлекаем short id для проверки
                import urllib.parse
                if '?' in new_client_config:
                    params_str = new_client_config.split('?')[1].split('#')[0]
                    params = urllib.parse.parse_qs(params_str)
                    new_sid = params.get('sid', [None])[0]
                    
                    if new_sid:
                        logger.info(f"    ✓ Получен short id: {new_sid[:8]}...")
                        
                        if not dry_run:
                            # Обновляем конфигурацию в БД
                            with get_db_cursor(commit=True) as update_cursor:
                                update_cursor.execute("""
                                    UPDATE v2ray_keys
                                    SET client_config = ?
                                    WHERE v2ray_uuid = ?
                                """, (new_client_config, v2ray_uuid))
                            
                            logger.info(f"    ✓ Конфигурация обновлена в БД")
                            updated_count += 1
                        else:
                            logger.info(f"    [DRY RUN] Конфигурация будет обновлена")
                            updated_count += 1
                    else:
                        logger.warning(f"    ⚠️  Не удалось извлечь short id из новой конфигурации")
                        failed_count += 1
                else:
                    logger.warning(f"    ⚠️  Новая конфигурация не содержит параметров")
                    failed_count += 1
                
                await protocol_client.close()
                
            except Exception as e:
                logger.error(f"    ✗ Ошибка при получении конфигурации: {e}")
                failed_count += 1
                continue
        
        logger.info(f"\n  Итого для подписки #{subscription_id}:")
        logger.info(f"    Обновлено: {updated_count}")
        logger.info(f"    Пропущено (уже правильные): {skipped_count}")
        logger.info(f"    Ошибок: {failed_count}")
        
        total_updated += updated_count
        total_skipped += skipped_count
        total_failed += failed_count
        
        # Инвалидируем кэш подписки
        if not dry_run and updated_count > 0:
            invalidate_subscription_cache(subscription_token)
            logger.info(f"    Кэш подписки инвалидирован")
    
    logger.info(f"\n{'='*60}")
    logger.info(f"ИТОГО:")
    logger.info(f"  Обновлено ключей: {total_updated}")
    logger.info(f"  Пропущено (уже правильные): {total_skipped}")
    logger.info(f"  Ошибок: {total_failed}")
    
    if dry_run:
        logger.info(f"\n⚠️  Это был DRY RUN - изменения не были применены")
        logger.info(f"Запустите скрипт без --dry-run для применения изменений")


async def main():
    """Главная функция"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Обновить все ключи в подписках с реальными short id')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Показать что будет обновлено, не изменяя БД'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Принудительно обновить все ключи, даже если они уже имеют правильный short id'
    )
    
    args = parser.parse_args()
    
    try:
        await update_subscription_keys_short_ids(dry_run=args.dry_run, force=args.force)
    except KeyboardInterrupt:
        logger.info("\n\nПрервано пользователем")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

