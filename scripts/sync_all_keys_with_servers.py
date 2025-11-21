#!/usr/bin/env python3
"""
Скрипт для синхронизации всех ключей V2Ray с серверами
Обновляет client_config в БД актуальными данными с серверов
"""
import sys
import os
import asyncio
import logging
from typing import List, Tuple
import urllib.parse

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.settings import settings
from utils import get_db_cursor
from vpn_protocols import ProtocolFactory, normalize_vless_host, remove_fragment_from_vless
from bot.services.subscription_service import invalidate_subscription_cache

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def extract_sid_sni(config: str) -> Tuple[str, str]:
    """Извлечь short id и SNI из конфигурации"""
    if not config or '?' not in config:
        return None, None
    
    try:
        params_str = config.split('?')[1].split('#')[0]
        params = urllib.parse.parse_qs(params_str)
        sid = params.get('sid', [None])[0]
        sni = params.get('sni', [None])[0]
        return sid, sni
    except Exception:
        return None, None


async def sync_all_keys_with_servers(dry_run: bool = False, server_id: int = None) -> None:
    """
    Синхронизировать все ключи V2Ray с серверами
    
    Args:
        dry_run: Если True, только показывает что будет обновлено, не изменяет БД
        server_id: Если указан, синхронизирует только ключи с этого сервера
    """
    # Получаем все активные ключи V2Ray
    with get_db_cursor() as cursor:
        if server_id:
            cursor.execute("""
                SELECT 
                    k.id,
                    k.v2ray_uuid,
                    k.client_config,
                    k.server_id,
                    k.user_id,
                    k.subscription_id,
                    s.name as server_name,
                    s.domain,
                    s.api_url,
                    s.api_key,
                    s.active
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE s.protocol = 'v2ray'
                  AND s.active = 1
                  AND k.server_id = ?
                ORDER BY k.server_id, k.id
            """, (server_id,))
        else:
            cursor.execute("""
                SELECT 
                    k.id,
                    k.v2ray_uuid,
                    k.client_config,
                    k.server_id,
                    k.user_id,
                    k.subscription_id,
                    s.name as server_name,
                    s.domain,
                    s.api_url,
                    s.api_key,
                    s.active
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE s.protocol = 'v2ray'
                  AND s.active = 1
                ORDER BY k.server_id, k.id
            """)
        keys = cursor.fetchall()
    
    logger.info(f"Найдено {len(keys)} ключей V2Ray для синхронизации")
    
    total_updated = 0
    total_failed = 0
    total_skipped = 0
    total_unchanged = 0
    
    # Группируем ключи по серверам для эффективной обработки
    keys_by_server = {}
    for key_data in keys:
        server_id_key = key_data[3]  # server_id
        if server_id_key not in keys_by_server:
            keys_by_server[server_id_key] = []
        keys_by_server[server_id_key].append(key_data)
    
    logger.info(f"Ключи распределены по {len(keys_by_server)} серверам")
    
    for server_id_key, server_keys in keys_by_server.items():
        server_name = server_keys[0][6]  # server_name
        domain = server_keys[0][7]  # domain
        api_url = server_keys[0][8]  # api_url
        api_key = server_keys[0][9]  # api_key
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Сервер #{server_id_key}: {server_name}")
        logger.info(f"Ключей на сервере: {len(server_keys)}")
        
        if not api_url or not api_key:
            logger.warning(f"  ⚠️  Нет API URL или ключа для сервера #{server_id_key}")
            total_failed += len(server_keys)
            continue
        
        server_config = {
            'api_url': api_url,
            'api_key': api_key,
            'domain': domain,
        }
        
        protocol_client = None
        try:
            protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
        except Exception as e:
            logger.error(f"  ✗ Ошибка создания клиента для сервера #{server_id_key}: {e}")
            total_failed += len(server_keys)
            continue
        
        server_updated = 0
        server_failed = 0
        server_skipped = 0
        server_unchanged = 0
        
        for key_data in server_keys:
            (
                key_id,
                v2ray_uuid,
                old_client_config,
                server_id_db,
                user_id,
                subscription_id,
                server_name_db,
                domain_db,
                api_url_db,
                api_key_db,
                active
            ) = key_data
            
            logger.debug(f"  Ключ #{key_id} (UUID: {v2ray_uuid[:8]}...)")
            
            try:
                # Получаем актуальную конфигурацию с сервера
                fetched_config = await protocol_client.get_user_config(
                    v2ray_uuid,
                    {
                        'domain': domain,
                        'port': 443,
                        'email': f'user_{user_id}@veilbot.com',
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
                
                # Извлекаем short id и SNI для сравнения
                old_sid, old_sni = extract_sid_sni(old_client_config) if old_client_config else (None, None)
                new_sid, new_sni = extract_sid_sni(new_client_config)
                
                # Проверяем, изменилась ли конфигурация
                if old_client_config == new_client_config:
                    logger.debug(f"    ✓ Конфигурация не изменилась")
                    server_unchanged += 1
                    total_unchanged += 1
                    continue
                
                # Логируем изменения
                if old_sid != new_sid:
                    logger.info(f"    🔄 Short ID изменился: {old_sid[:8] if old_sid else 'N/A'}... -> {new_sid[:8] if new_sid else 'N/A'}...")
                if old_sni != new_sni:
                    logger.info(f"    🔄 SNI изменился: {old_sni or 'N/A'} -> {new_sni or 'N/A'}")
                
                if not dry_run:
                    # Обновляем конфигурацию в БД
                    with get_db_cursor(commit=True) as update_cursor:
                        update_cursor.execute("""
                            UPDATE v2ray_keys
                            SET client_config = ?
                            WHERE id = ?
                        """, (new_client_config, key_id))
                    
                    logger.info(f"    ✓ Ключ #{key_id} обновлен (sid={new_sid[:8] if new_sid else 'N/A'}..., sni={new_sni or 'N/A'})")
                    
                    # Инвалидируем кэш подписки, если ключ в подписке
                    if subscription_id:
                        with get_db_cursor() as sub_cursor:
                            sub_cursor.execute(
                                'SELECT subscription_token FROM subscriptions WHERE id = ?',
                                (subscription_id,)
                            )
                            token_row = sub_cursor.fetchone()
                            if token_row:
                                invalidate_subscription_cache(token_row[0])
                                logger.debug(f"      Кэш подписки #{subscription_id} инвалидирован")
                    
                    server_updated += 1
                    total_updated += 1
                else:
                    logger.info(f"    [DRY RUN] Ключ #{key_id} будет обновлен")
                    server_updated += 1
                    total_updated += 1
                
            except Exception as e:
                logger.error(f"    ✗ Ошибка при синхронизации ключа #{key_id}: {e}")
                server_failed += 1
                total_failed += 1
                continue
        
        logger.info(f"\n  Итого для сервера #{server_id_key}:")
        logger.info(f"    Обновлено: {server_updated}")
        logger.info(f"    Не изменилось: {server_unchanged}")
        logger.info(f"    Ошибок: {server_failed}")
        
        if protocol_client:
            try:
                await protocol_client.close()
            except Exception:
                pass
    
    logger.info(f"\n{'='*60}")
    logger.info(f"ИТОГО:")
    logger.info(f"  Обновлено ключей: {total_updated}")
    logger.info(f"  Не изменилось: {total_unchanged}")
    logger.info(f"  Ошибок: {total_failed}")
    
    if dry_run:
        logger.info(f"\n⚠️  Это был DRY RUN - изменения не были применены")
        logger.info(f"Запустите скрипт без --dry-run для применения изменений")


async def main():
    """Главная функция"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Синхронизировать все ключи V2Ray с серверами')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Показать что будет обновлено, не изменяя БД'
    )
    parser.add_argument(
        '--server-id',
        type=int,
        help='Синхронизировать только ключи с указанного сервера'
    )
    
    args = parser.parse_args()
    
    try:
        await sync_all_keys_with_servers(dry_run=args.dry_run, server_id=args.server_id)
    except KeyboardInterrupt:
        logger.info("\n\nПрервано пользователем")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

