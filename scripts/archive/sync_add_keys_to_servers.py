#!/usr/bin/env python3
"""
Скрипт для синхронизации ключей: добавление на серверы всех ключей, которые есть в базе данных, но отсутствуют на серверах.
"""

import asyncio
import sys
import os
from typing import Optional, Dict, Any, List, Set

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.infra.sqlite_utils import get_db_cursor
from vpn_protocols import V2RayProtocol, ProtocolFactory, normalize_vless_host, remove_fragment_from_vless
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def extract_v2ray_uuid(remote_entry: Dict[str, Any]) -> Optional[str]:
    """Извлечь UUID из V2Ray ключа"""
    uuid = remote_entry.get("uuid")
    if not uuid:
        key_info = remote_entry.get("key") or {}
        uuid = key_info.get("uuid")
    if not uuid:
        uuid = remote_entry.get("id")
    if isinstance(uuid, str) and uuid.strip():
        return uuid.strip()
    return None


async def sync_add_keys_to_server(
    server_id: int,
    server_name: str,
    api_url: str,
    api_key: str,
    domain: str,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Синхронизировать ключи для одного сервера: добавить на сервер все ключи из БД, которых нет на сервере
    
    Args:
        server_id: ID сервера
        server_name: Имя сервера
        api_url: URL API сервера
        api_key: API ключ сервера
        domain: Домен сервера
        dry_run: Если True, только показывает что будет добавлено, не добавляет
    
    Returns:
        dict: Словарь со статистикой синхронизации
    """
    result = {
        "server_id": server_id,
        "server_name": server_name,
        "db_keys_count": 0,
        "server_keys_count": 0,
        "keys_to_add": [],
        "added_count": 0,
        "errors": []
    }
    
    protocol_client = None
    try:
        # Получаем все ключи из БД для этого сервера
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                SELECT 
                    id,
                    v2ray_uuid,
                    user_id,
                    email,
                    client_config,
                    subscription_id
                FROM v2ray_keys
                WHERE server_id = ?
                """,
                (server_id,)
            )
            db_keys = cursor.fetchall()
        
        result["db_keys_count"] = len(db_keys)
        logger.info(f"Сервер {server_name} (ID: {server_id}): найдено {len(db_keys)} ключей в БД")
        
        # Получаем все ключи с сервера
        server_config = {
            'api_url': api_url,
            'api_key': api_key,
            'domain': domain,
        }
        protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
        remote_keys = await protocol_client.get_all_keys()
        
        if remote_keys is None:
            remote_keys = []
        
        result["server_keys_count"] = len(remote_keys)
        logger.info(f"Сервер {server_name} (ID: {server_id}): найдено {len(remote_keys)} ключей на сервере")
        
        # Создаем множества UUID и email для быстрого поиска
        remote_uuids: Set[str] = set()
        remote_emails: Set[str] = set()
        
        for remote_entry in remote_keys:
            uuid = extract_v2ray_uuid(remote_entry)
            if uuid:
                remote_uuids.add(uuid)
            email = (remote_entry.get("email") or "").lower().strip()
            if email:
                remote_emails.add(email)
            name = (remote_entry.get("name") or "").lower().strip()
            if name and "@" in name:
                remote_emails.add(name)
        
        # Находим ключи в БД, которых нет на сервере
        keys_to_add = []
        for db_key_row in db_keys:
            key_id, v2ray_uuid, user_id, email, client_config, subscription_id = db_key_row
            
            # Проверяем, есть ли этот ключ на сервере
            uuid_in_db = (v2ray_uuid or "").strip()
            email_in_db = (email or "").lower().strip()
            
            # Если есть UUID, проверяем по UUID
            if uuid_in_db and uuid_in_db in remote_uuids:
                continue
            
            # Если есть email, проверяем по email
            if email_in_db and email_in_db in remote_emails:
                continue
            
            # Если нет ни UUID, ни email, но есть user_id, генерируем email
            if not email_in_db and user_id:
                email_in_db = f"user_{user_id}@veilbot.com"
                if email_in_db in remote_emails:
                    continue
            
            # Ключ отсутствует на сервере, нужно добавить
            keys_to_add.append({
                "id": key_id,
                "v2ray_uuid": uuid_in_db,
                "user_id": user_id,
                "email": email or f"user_{user_id}@veilbot.com",
                "client_config": client_config,
                "subscription_id": subscription_id
            })
        
        result["keys_to_add"] = keys_to_add
        
        logger.info(f"Сервер {server_name} (ID: {server_id}): найдено {len(keys_to_add)} ключей для добавления")
        
        if not keys_to_add:
            logger.info(f"Сервер {server_name} (ID: {server_id}): синхронизация не требуется")
            return result
        
        # Добавляем ключи на сервер
        if dry_run:
            logger.info(f"[DRY RUN] Будет добавлено {len(keys_to_add)} ключей на сервер {server_name}")
            for key_info in keys_to_add:
                logger.info(f"  [DRY RUN] Добавление ключа: user_id={key_info['user_id']}, email={key_info['email']}, UUID в БД={key_info['v2ray_uuid'][:8] if key_info['v2ray_uuid'] else 'N/A'}...")
            result["added_count"] = len(keys_to_add)
        else:
            for key_info in keys_to_add:
                key_id = key_info["id"]
                user_id = key_info["user_id"]
                email = key_info["email"]
                old_uuid = key_info["v2ray_uuid"]
                
                try:
                    logger.info(f"Добавление ключа на сервер {server_name}: user_id={user_id}, email={email}")
                    
                    # Создаем ключ на сервере
                    user_data = await protocol_client.create_user(email, name=email)
                    
                    if not user_data or not user_data.get('uuid'):
                        error_msg = f"Не удалось создать ключ на сервере для user_id={user_id}"
                        result["errors"].append(error_msg)
                        logger.error(f"✗ {error_msg}")
                        continue
                    
                    new_uuid = user_data.get('uuid')
                    logger.info(f"✓ Ключ создан на сервере, новый UUID: {new_uuid[:8]}...")
                    
                    # Получаем client_config с сервера
                    try:
                        fetched_config = await protocol_client.get_user_config(
                            new_uuid,
                            {
                                'domain': domain,
                                'port': 443,
                                'email': email,
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
                        
                    except Exception as config_error:
                        logger.warning(f"Не удалось получить client_config для ключа {new_uuid[:8]}...: {config_error}")
                        new_client_config = key_info.get("client_config") or ""
                    
                    # Обновляем данные в БД
                    with get_db_cursor(commit=True) as cursor:
                        if new_uuid != old_uuid:
                            # Обновляем UUID и client_config
                            cursor.execute(
                                """
                                UPDATE v2ray_keys
                                SET v2ray_uuid = ?, client_config = ?
                                WHERE id = ?
                                """,
                                (new_uuid, new_client_config, key_id)
                            )
                            logger.info(f"✓ UUID и client_config обновлены в БД")
                        elif new_client_config and new_client_config != key_info.get("client_config"):
                            # Обновляем только client_config
                            cursor.execute(
                                """
                                UPDATE v2ray_keys
                                SET client_config = ?
                                WHERE id = ?
                                """,
                                (new_client_config, key_id)
                            )
                            logger.info(f"✓ client_config обновлен в БД")
                        else:
                            # UUID уже правильный, просто подтверждаем
                            logger.info(f"✓ UUID в БД совпадает с сервером")
                    
                    result["added_count"] += 1
                    logger.info(f"✓ Ключ {new_uuid[:8]}... успешно добавлен и синхронизирован")
                    
                except Exception as e:
                    error_msg = f"Ошибка при добавлении ключа user_id={user_id}: {e}"
                    result["errors"].append(error_msg)
                    logger.error(error_msg, exc_info=True)
        
    except Exception as e:
        error_msg = f"Ошибка при обработке сервера {server_name}: {e}"
        result["errors"].append(error_msg)
        logger.error(error_msg, exc_info=True)
    finally:
        if protocol_client:
            try:
                await protocol_client.close()
            except Exception:
                pass
    
    return result


async def sync_all_servers(dry_run: bool = False, server_id: Optional[int] = None) -> None:
    """
    Синхронизировать все серверы: добавить на серверы все ключи из БД, которых нет на серверах
    
    Args:
        dry_run: Если True, только показывает что будет добавлено, не добавляет
        server_id: Если указан, синхронизирует только указанный сервер
    """
    logger.info("=" * 80)
    logger.info("СИНХРОНИЗАЦИЯ КЛЮЧЕЙ: ДОБАВЛЕНИЕ НА СЕРВЕРЫ")
    logger.info("=" * 80)
    if dry_run:
        logger.info("⚠️  РЕЖИМ DRY RUN - изменения не будут применены")
    logger.info("")
    
    # Получаем все активные V2Ray серверы
    with get_db_cursor() as cursor:
        if server_id:
            cursor.execute(
                """
                SELECT id, name, api_url, api_key, domain
                FROM servers
                WHERE protocol = 'v2ray' AND active = 1 AND id = ?
                ORDER BY id
                """,
                (server_id,)
            )
        else:
            cursor.execute(
                """
                SELECT id, name, api_url, api_key, domain
                FROM servers
                WHERE protocol = 'v2ray' AND active = 1
                ORDER BY id
                """
            )
        servers = cursor.fetchall()
    
    if not servers:
        logger.warning("Не найдено активных V2Ray серверов")
        return
    
    logger.info(f"Найдено {len(servers)} активных V2Ray серверов для синхронизации")
    logger.info("")
    
    total_added = 0
    total_errors = 0
    total_keys_to_add = 0
    
    # Обрабатываем каждый сервер
    for server_row in servers:
        server_id_db, server_name, api_url, api_key, domain = server_row
        
        if not api_url or not api_key:
            logger.warning(f"Сервер {server_name} (ID: {server_id_db}): пропущен (нет API URL или ключа)")
            continue
        
        logger.info("-" * 80)
        result = await sync_add_keys_to_server(
            server_id_db,
            server_name,
            api_url,
            api_key,
            domain or "",
            dry_run=dry_run
        )
        
        total_keys_to_add += len(result["keys_to_add"])
        total_added += result["added_count"]
        total_errors += len(result["errors"])
        
        logger.info("")
    
    # Итоги
    logger.info("=" * 80)
    logger.info("ИТОГИ СИНХРОНИЗАЦИИ")
    logger.info("=" * 80)
    logger.info(f"Обработано серверов: {len(servers)}")
    logger.info(f"Найдено ключей для добавления: {total_keys_to_add}")
    logger.info(f"Добавлено ключей: {total_added}")
    logger.info(f"Ошибок: {total_errors}")
    logger.info("")
    
    if dry_run:
        logger.info("⚠️  Это был DRY RUN - изменения не были применены")
        logger.info("Запустите скрипт без --dry-run для применения изменений")
    else:
        if total_added == total_keys_to_add and total_errors == 0:
            logger.info("✅ Синхронизация завершена успешно!")
        else:
            logger.warning("⚠️  Синхронизация завершена с ошибками")


async def main():
    """Главная функция"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Добавить на серверы все ключи, которые есть в базе данных, но отсутствуют на серверах'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Показать что будет добавлено, не добавляя на серверы'
    )
    parser.add_argument(
        '--server-id',
        type=int,
        help='Синхронизировать только указанный сервер'
    )
    
    args = parser.parse_args()
    
    try:
        await sync_all_servers(dry_run=args.dry_run, server_id=args.server_id)
    except KeyboardInterrupt:
        logger.info("\n\nПрервано пользователем")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

