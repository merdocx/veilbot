#!/usr/bin/env python3
"""
Скрипт для удаления со всех V2Ray серверов подписок и ключей, которых нет в базе данных.
Убеждается, что удаление подписки всегда сопровождается удалением ключей с серверов.
"""
import asyncio
import sys
import logging
from typing import List, Dict, Set, Tuple
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.infra.sqlite_utils import open_connection
from app.settings import settings
from vpn_protocols import V2RayProtocol

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_v2ray_servers() -> List[Dict]:
    """Получить список всех активных V2Ray серверов"""
    servers = []
    with open_connection(settings.DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, api_url, api_key, domain
            FROM servers
            WHERE protocol = 'v2ray' AND active = 1
        """)
        for row in cursor.fetchall():
            servers.append({
                'id': row[0],
                'name': row[1],
                'api_url': row[2],
                'api_key': row[3],
                'domain': row[4]
            })
    return servers


def get_db_keys(server_id: int) -> Set[str]:
    """Получить множество UUID всех активных ключей из БД для конкретного сервера"""
    uuids = set()
    with open_connection(settings.DATABASE_PATH) as conn:
        cursor = conn.cursor()
        # Получаем все активные ключи (не только истекшие, но и все существующие)
        cursor.execute("""
            SELECT DISTINCT v2ray_uuid
            FROM v2ray_keys
            WHERE server_id = ? AND v2ray_uuid IS NOT NULL AND v2ray_uuid != ''
        """, (server_id,))
        for row in cursor.fetchall():
            uuid = row[0]
            if uuid:
                uuids.add(uuid.strip())
    return uuids


def get_db_subscription_keys() -> Set[str]:
    """Получить множество UUID всех ключей, связанных с активными подписками"""
    uuids = set()
    with open_connection(settings.DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT k.v2ray_uuid
            FROM v2ray_keys k
            JOIN subscriptions s ON k.subscription_id = s.id
            WHERE s.is_active = 1 
              AND k.v2ray_uuid IS NOT NULL 
              AND k.v2ray_uuid != ''
        """)
        for row in cursor.fetchall():
            uuid = row[0]
            if uuid:
                uuids.add(uuid.strip())
    return uuids


async def cleanup_server_keys(server: Dict) -> Tuple[int, int]:
    """
    Очистить сервер от ключей, которых нет в БД
    
    Returns:
        Tuple[deleted_count, error_count]
    """
    server_id = server['id']
    server_name = server['name']
    api_url = server['api_url']
    api_key = server['api_key']
    
    logger.info(f"Обработка сервера {server_name} (ID: {server_id})")
    
    if not api_url or not api_key:
        logger.warning(f"Сервер {server_name} не имеет API URL или API ключа, пропускаем")
        return 0, 0
    
    # Получаем ключи из БД
    db_keys = get_db_keys(server_id)
    logger.info(f"В БД найдено {len(db_keys)} ключей для сервера {server_name}")
    
    # Получаем ключи с сервера
    client = None
    deleted_count = 0
    error_count = 0
    
    try:
        client = V2RayProtocol(api_url, api_key)
        remote_keys = await client.get_all_keys()
        
        if not remote_keys:
            logger.info(f"На сервере {server_name} нет ключей")
            return 0, 0
        
        logger.info(f"На сервере {server_name} найдено {len(remote_keys)} ключей")
        
        # Находим ключи, которых нет в БД
        orphaned_keys = []
        for remote_key in remote_keys:
            uuid = None
            # Пытаемся извлечь UUID из разных форматов ответа
            if isinstance(remote_key, dict):
                uuid = remote_key.get('uuid') or remote_key.get('id')
                if isinstance(uuid, dict):
                    uuid = uuid.get('uuid') or uuid.get('id')
            elif isinstance(remote_key, str):
                uuid = remote_key
            
            if uuid:
                uuid = str(uuid).strip()
                if uuid and uuid not in db_keys:
                    orphaned_keys.append({
                        'uuid': uuid,
                        'data': remote_key
                    })
        
        logger.info(f"Найдено {len(orphaned_keys)} ключей для удаления на сервере {server_name}")
        
        # Удаляем ключи
        for orphaned in orphaned_keys:
            uuid = orphaned['uuid']
            try:
                logger.info(f"Удаление ключа {uuid} с сервера {server_name}")
                result = await client.delete_user(uuid)
                if result:
                    deleted_count += 1
                    logger.info(f"✓ Успешно удален ключ {uuid}")
                else:
                    error_count += 1
                    logger.warning(f"✗ Не удалось удалить ключ {uuid}")
            except Exception as e:
                error_count += 1
                logger.error(f"✗ Ошибка при удалении ключа {uuid}: {e}")
        
    except Exception as e:
        logger.error(f"Ошибка при обработке сервера {server_name}: {e}", exc_info=True)
        error_count += 1
    finally:
        if client:
            try:
                await client.close()
            except Exception:
                pass
    
    return deleted_count, error_count


async def main():
    """Основная функция"""
    logger.info("=== Начало очистки V2Ray серверов ===")
    
    # Получаем список серверов
    servers = get_v2ray_servers()
    if not servers:
        logger.info("V2Ray серверы не найдены")
        return
    
    logger.info(f"Найдено {len(servers)} V2Ray серверов")
    
    # Получаем ключи из активных подписок (для информации)
    subscription_keys = get_db_subscription_keys()
    logger.info(f"В БД найдено {len(subscription_keys)} ключей, связанных с активными подписками")
    
    total_deleted = 0
    total_errors = 0
    
    # Обрабатываем каждый сервер
    for server in servers:
        deleted, errors = await cleanup_server_keys(server)
        total_deleted += deleted
        total_errors += errors
        # Небольшая задержка между серверами
        await asyncio.sleep(1)
    
    logger.info("=== Очистка завершена ===")
    logger.info(f"Всего удалено ключей: {total_deleted}")
    logger.info(f"Всего ошибок: {total_errors}")


if __name__ == "__main__":
    asyncio.run(main())















