#!/usr/bin/env python3
"""
Скрипт для синхронизации ключей: удаление с серверов всех ключей, которых нет в базе данных.
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
from vpn_protocols import V2RayProtocol
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


async def sync_delete_keys_from_server(server_id: int, server_name: str, api_url: str, api_key: str, dry_run: bool = False) -> Dict[str, Any]:
    """
    Синхронизировать ключи для одного сервера: удалить с сервера все ключи, которых нет в БД
    
    Args:
        server_id: ID сервера
        server_name: Имя сервера
        api_url: URL API сервера
        api_key: API ключ сервера
        dry_run: Если True, только показывает что будет удалено, не удаляет
    
    Returns:
        dict: Словарь со статистикой синхронизации
    """
    result = {
        "server_id": server_id,
        "server_name": server_name,
        "db_keys_count": 0,
        "server_keys_count": 0,
        "keys_to_delete": [],
        "deleted_count": 0,
        "errors": []
    }
    
    protocol_client = None
    try:
        # Получаем все UUID ключей из БД для этого сервера
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                SELECT v2ray_uuid, email
                FROM v2ray_keys
                WHERE server_id = ? AND v2ray_uuid IS NOT NULL AND v2ray_uuid != ''
                """,
                (server_id,)
            )
            db_keys = cursor.fetchall()
        
        db_uuids: Set[str] = {row[0].strip() for row in db_keys if row[0]}
        db_emails: Set[str] = {(row[1] or "").lower().strip() for row in db_keys if row[1]}
        
        result["db_keys_count"] = len(db_uuids)
        
        logger.info(f"Сервер {server_name} (ID: {server_id}): найдено {len(db_uuids)} ключей в БД")
        
        # Получаем все ключи с сервера
        protocol_client = V2RayProtocol(api_url, api_key)
        remote_keys = await protocol_client.get_all_keys()
        
        if remote_keys is None:
            remote_keys = []
        
        result["server_keys_count"] = len(remote_keys)
        logger.info(f"Сервер {server_name} (ID: {server_id}): найдено {len(remote_keys)} ключей на сервере")
        
        # Находим ключи на сервере, которых нет в БД
        keys_to_delete = []
        for remote_entry in remote_keys:
            uuid = extract_v2ray_uuid(remote_entry)
            name = (remote_entry.get("name") or "").lower().strip()
            email = (remote_entry.get("email") or "").lower().strip()
            
            # Проверяем, есть ли этот ключ в БД по UUID или email/name
            if uuid and uuid not in db_uuids:
                # Дополнительная проверка по email/name
                if name not in db_emails and email not in db_emails:
                    keys_to_delete.append({
                        "uuid": uuid,
                        "id": remote_entry.get("id"),
                        "name": remote_entry.get("name"),
                        "email": remote_entry.get("email")
                    })
        
        result["keys_to_delete"] = keys_to_delete
        
        logger.info(f"Сервер {server_name} (ID: {server_id}): найдено {len(keys_to_delete)} ключей для удаления")
        
        if not keys_to_delete:
            logger.info(f"Сервер {server_name} (ID: {server_id}): синхронизация не требуется")
            return result
        
        # Удаляем ключи с сервера
        if dry_run:
            logger.info(f"[DRY RUN] Будет удалено {len(keys_to_delete)} ключей с сервера {server_name}")
            for key_info in keys_to_delete:
                logger.info(f"  [DRY RUN] Удаление ключа: UUID={key_info['uuid'][:8]}..., name={key_info.get('name', 'N/A')}")
            result["deleted_count"] = len(keys_to_delete)
        else:
            for key_info in keys_to_delete:
                uuid = key_info["uuid"]
                key_id = key_info.get("id") or uuid
                name = key_info.get("name", "N/A")
                
                try:
                    logger.info(f"Удаление ключа с сервера {server_name}: UUID={uuid[:8]}..., name={name}")
                    delete_result = await protocol_client.delete_user(key_id)
                    
                    if delete_result:
                        result["deleted_count"] += 1
                        logger.info(f"✓ Ключ {uuid[:8]}... успешно удален")
                    else:
                        error_msg = f"Не удалось удалить ключ {uuid[:8]}..."
                        result["errors"].append(error_msg)
                        logger.warning(f"✗ {error_msg}")
                except Exception as e:
                    error_msg = f"Ошибка при удалении ключа {uuid[:8]}...: {e}"
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
    Синхронизировать все серверы: удалить с серверов все ключи, которых нет в БД
    
    Args:
        dry_run: Если True, только показывает что будет удалено, не удаляет
        server_id: Если указан, синхронизирует только указанный сервер
    """
    logger.info("=" * 80)
    logger.info("СИНХРОНИЗАЦИЯ КЛЮЧЕЙ: УДАЛЕНИЕ С СЕРВЕРОВ")
    logger.info("=" * 80)
    if dry_run:
        logger.info("⚠️  РЕЖИМ DRY RUN - изменения не будут применены")
    logger.info("")
    
    # Получаем все активные V2Ray серверы
    with get_db_cursor() as cursor:
        if server_id:
            cursor.execute(
                """
                SELECT id, name, api_url, api_key
                FROM servers
                WHERE protocol = 'v2ray' AND active = 1 AND id = ?
                ORDER BY id
                """,
                (server_id,)
            )
        else:
            cursor.execute(
                """
                SELECT id, name, api_url, api_key
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
    
    total_deleted = 0
    total_errors = 0
    total_keys_to_delete = 0
    
    # Обрабатываем каждый сервер
    for server_row in servers:
        server_id_db, server_name, api_url, api_key = server_row
        
        if not api_url or not api_key:
            logger.warning(f"Сервер {server_name} (ID: {server_id_db}): пропущен (нет API URL или ключа)")
            continue
        
        logger.info("-" * 80)
        result = await sync_delete_keys_from_server(
            server_id_db,
            server_name,
            api_url,
            api_key,
            dry_run=dry_run
        )
        
        total_keys_to_delete += len(result["keys_to_delete"])
        total_deleted += result["deleted_count"]
        total_errors += len(result["errors"])
        
        logger.info("")
    
    # Итоги
    logger.info("=" * 80)
    logger.info("ИТОГИ СИНХРОНИЗАЦИИ")
    logger.info("=" * 80)
    logger.info(f"Обработано серверов: {len(servers)}")
    logger.info(f"Найдено ключей для удаления: {total_keys_to_delete}")
    logger.info(f"Удалено ключей: {total_deleted}")
    logger.info(f"Ошибок: {total_errors}")
    logger.info("")
    
    if dry_run:
        logger.info("⚠️  Это был DRY RUN - изменения не были применены")
        logger.info("Запустите скрипт без --dry-run для применения изменений")
    else:
        if total_deleted == total_keys_to_delete and total_errors == 0:
            logger.info("✅ Синхронизация завершена успешно!")
        else:
            logger.warning("⚠️  Синхронизация завершена с ошибками")


async def main():
    """Главная функция"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Удалить с серверов все ключи, которых нет в базе данных'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Показать что будет удалено, не удаляя с серверов'
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

