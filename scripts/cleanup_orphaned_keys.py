#!/usr/bin/env python3
"""
Скрипт для проверки серверов и удаления ключей и подписок, отсутствующих в базе данных.
"""
import asyncio
import sys
import os
from typing import List, Dict, Any, Optional

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils import get_db_cursor
from vpn_protocols import OutlineProtocol, V2RayProtocol
from outline import delete_key as delete_outline_key
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ServerInfo:
    def __init__(self, id: int, name: str, protocol: str, api_url: str, 
                 cert_sha256: Optional[str] = None, api_key: Optional[str] = None,
                 country: Optional[str] = None, domain: Optional[str] = None):
        self.id = id
        self.name = name
        self.protocol = protocol.lower()
        self.api_url = api_url
        self.cert_sha256 = cert_sha256
        self.api_key = api_key
        self.country = country
        self.domain = domain


def load_servers() -> List[ServerInfo]:
    """Загрузить список активных серверов из БД"""
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT id, name, protocol, api_url, cert_sha256, api_key, country, domain
            FROM servers
            WHERE active = 1
        """)
        rows = cursor.fetchall()
    
    servers = []
    for row in rows:
        servers.append(ServerInfo(
            id=row[0],
            name=row[1],
            protocol=row[2] or "outline",
            api_url=row[3] or "",
            cert_sha256=row[4],
            api_key=row[5],
            country=row[6],
            domain=row[7]
        ))
    return servers


def load_db_keys(server: ServerInfo) -> Dict[str, Dict[str, Any]]:
    """Загрузить ключи из БД для конкретного сервера"""
    with get_db_cursor() as cursor:
        if server.protocol == "outline":
            cursor.execute("""
                SELECT id, user_id, email, key_id, access_url, expiry_at
                FROM keys
                WHERE server_id = ?
            """, (server.id,))
        elif server.protocol == "v2ray":
            cursor.execute("""
                SELECT id, user_id, email, v2ray_uuid, level, created_at, expiry_at, subscription_id
                FROM v2ray_keys
                WHERE server_id = ?
            """, (server.id,))
        else:
            return {}
        
        rows = cursor.fetchall()
    
    # Создаем словарь для быстрого поиска
    db_keys = {}
    for row in rows:
        if server.protocol == "outline":
            key_id = str(row[3]) if row[3] else None  # key_id
            email = (row[2] or "").lower() if row[2] else None  # email
            if key_id:
                db_keys[key_id] = {
                    "db_id": row[0],
                    "user_id": row[1],
                    "email": row[2],
                    "key_id": row[3],
                    "access_url": row[4],
                    "expiry_at": row[5]
                }
            if email and email not in db_keys:
                db_keys[email] = {
                    "db_id": row[0],
                    "user_id": row[1],
                    "email": row[2],
                    "key_id": row[3],
                    "access_url": row[4],
                    "expiry_at": row[5]
                }
        elif server.protocol == "v2ray":
            uuid = (row[3] or "").strip() if row[3] else None  # v2ray_uuid
            email = (row[2] or "").lower() if row[2] else None  # email
            if uuid:
                db_keys[uuid] = {
                    "db_id": row[0],
                    "user_id": row[1],
                    "email": row[2],
                    "v2ray_uuid": row[3],
                    "level": row[4],
                    "created_at": row[5],
                    "expiry_at": row[6],
                    "subscription_id": row[7]
                }
            if email and email not in db_keys:
                db_keys[email] = {
                    "db_id": row[0],
                    "user_id": row[1],
                    "email": row[2],
                    "v2ray_uuid": row[3],
                    "level": row[4],
                    "created_at": row[5],
                    "expiry_at": row[6],
                    "subscription_id": row[7]
                }
    
    return db_keys


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


async def cleanup_outline_server(server: ServerInfo) -> Dict[str, Any]:
    """Проверить и очистить Outline сервер"""
    result = {
        "server": server.name,
        "protocol": "outline",
        "db_keys": 0,
        "remote_keys": 0,
        "orphaned_keys": [],
        "deleted": 0,
        "errors": []
    }
    
    try:
        # Загружаем ключи из БД
        db_keys = load_db_keys(server)
        result["db_keys"] = len(db_keys)
        
        # Получаем ключи с сервера
        client = OutlineProtocol(server.api_url, server.cert_sha256 or "")
        remote_keys = await client.get_all_keys()
        
        if remote_keys is None:
            remote_keys = []
        
        result["remote_keys"] = len(remote_keys)
        
        # Находим ключи на сервере, которых нет в БД
        db_key_ids = {str(k.get("key_id", "")) for k in db_keys.values() if k.get("key_id")}
        db_emails = {(k.get("email") or "").lower() for k in db_keys.values() if k.get("email")}
        
        for remote_key in remote_keys:
            key_id = str(remote_key.get("id", ""))
            name = (remote_key.get("name") or "").lower()
            
            # Проверяем, есть ли этот ключ в БД
            if key_id not in db_key_ids and name not in db_emails:
                result["orphaned_keys"].append({
                    "key_id": key_id,
                    "name": remote_key.get("name"),
                    "access_url": remote_key.get("accessUrl")
                })
        
        # Удаляем orphaned ключи
        for orphaned in result["orphaned_keys"]:
            key_id = orphaned["key_id"]
            try:
                logger.info(f"Удаление orphaned ключа {key_id} с сервера {server.name}")
                if delete_outline_key(server.api_url, server.cert_sha256 or "", key_id):
                    result["deleted"] += 1
                    logger.info(f"✓ Ключ {key_id} успешно удален")
                else:
                    result["errors"].append(f"Не удалось удалить ключ {key_id}")
            except Exception as e:
                result["errors"].append(f"Ошибка при удалении ключа {key_id}: {e}")
                logger.error(f"Ошибка при удалении ключа {key_id}: {e}")
    
    except Exception as e:
        result["errors"].append(f"Ошибка при обработке сервера: {e}")
        logger.error(f"Ошибка при обработке сервера {server.name}: {e}")
    
    return result


async def cleanup_v2ray_server(server: ServerInfo) -> Dict[str, Any]:
    """Проверить и очистить V2Ray сервер"""
    result = {
        "server": server.name,
        "protocol": "v2ray",
        "db_keys": 0,
        "remote_keys": 0,
        "orphaned_keys": [],
        "deleted": 0,
        "errors": []
    }
    
    client = None
    try:
        # Загружаем ключи из БД
        db_keys = load_db_keys(server)
        result["db_keys"] = len(db_keys)
        
        # Получаем ключи с сервера
        client = V2RayProtocol(server.api_url, server.api_key or "")
        remote_keys = await client.get_all_keys()
        
        if remote_keys is None:
            remote_keys = []
        
        result["remote_keys"] = len(remote_keys)
        
        # Находим ключи на сервере, которых нет в БД
        db_uuids = {k.get("v2ray_uuid", "").strip() for k in db_keys.values() if k.get("v2ray_uuid")}
        db_emails = {(k.get("email") or "").lower() for k in db_keys.values() if k.get("email")}
        
        for remote_entry in remote_keys:
            uuid = extract_v2ray_uuid(remote_entry)
            name = (remote_entry.get("name") or "").lower()
            
            # Проверяем, есть ли этот ключ в БД
            if uuid and uuid not in db_uuids and name not in db_emails:
                result["orphaned_keys"].append({
                    "uuid": uuid,
                    "name": remote_entry.get("name"),
                    "email": remote_entry.get("email")
                })
        
        # Удаляем orphaned ключи
        for orphaned in result["orphaned_keys"]:
            uuid = orphaned["uuid"]
            try:
                logger.info(f"Удаление orphaned ключа {uuid} с сервера {server.name}")
                if await client.delete_user(uuid):
                    result["deleted"] += 1
                    logger.info(f"✓ Ключ {uuid} успешно удален")
                else:
                    result["errors"].append(f"Не удалось удалить ключ {uuid}")
            except Exception as e:
                result["errors"].append(f"Ошибка при удалении ключа {uuid}: {e}")
                logger.error(f"Ошибка при удалении ключа {uuid}: {e}")
    
    except Exception as e:
        result["errors"].append(f"Ошибка при обработке сервера: {e}")
        logger.error(f"Ошибка при обработке сервера {server.name}: {e}")
    finally:
        if client:
            try:
                await client.close()
            except Exception:
                pass
    
    return result


async def cleanup_orphaned_subscriptions():
    """Удалить подписки, у которых нет активных ключей"""
    with get_db_cursor(commit=True) as cursor:
        # Находим подписки без активных ключей
        cursor.execute("""
            SELECT s.id, s.user_id, s.subscription_token, s.expires_at
            FROM subscriptions s
            LEFT JOIN v2ray_keys k ON s.id = k.subscription_id
            WHERE s.is_active = 1
            GROUP BY s.id
            HAVING COUNT(k.id) = 0
        """)
        orphaned_subscriptions = cursor.fetchall()
        
        deleted_count = 0
        for sub_id, user_id, token, expires_at in orphaned_subscriptions:
            logger.info(f"Удаление orphaned подписки {sub_id} (token: {token[:20]}..., user: {user_id})")
            cursor.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
            deleted_count += 1
        
        return deleted_count


async def main():
    """Основная функция"""
    print("=" * 80)
    print("🧹 Проверка серверов и удаление ключей, отсутствующих в БД")
    print("=" * 80)
    print()
    
    # Загружаем серверы
    servers = load_servers()
    print(f"Найдено активных серверов: {len(servers)}")
    print()
    
    results = []
    
    # Обрабатываем каждый сервер
    for server in servers:
        print(f"Проверка сервера: {server.name} ({server.protocol})")
        
        if server.protocol == "outline":
            result = await cleanup_outline_server(server)
        elif server.protocol == "v2ray":
            result = await cleanup_v2ray_server(server)
        else:
            print(f"  ⚠️  Неподдерживаемый протокол: {server.protocol}")
            continue
        
        results.append(result)
        
        # Выводим результаты
        print(f"  Ключей в БД: {result['db_keys']}")
        print(f"  Ключей на сервере: {result['remote_keys']}")
        print(f"  Orphaned ключей: {len(result['orphaned_keys'])}")
        print(f"  Удалено: {result['deleted']}")
        
        if result['errors']:
            print(f"  Ошибки: {len(result['errors'])}")
            for error in result['errors']:
                print(f"    - {error}")
        
        print()
    
    # Очистка orphaned подписок
    print("Проверка подписок...")
    deleted_subs = await cleanup_orphaned_subscriptions()
    print(f"Удалено orphaned подписок: {deleted_subs}")
    print()
    
    # Итоговая статистика
    print("=" * 80)
    print("📊 ИТОГОВАЯ СТАТИСТИКА")
    print("=" * 80)
    
    total_db_keys = sum(r['db_keys'] for r in results)
    total_remote_keys = sum(r['remote_keys'] for r in results)
    total_orphaned = sum(len(r['orphaned_keys']) for r in results)
    total_deleted = sum(r['deleted'] for r in results)
    total_errors = sum(len(r['errors']) for r in results)
    
    print(f"Всего ключей в БД: {total_db_keys}")
    print(f"Всего ключей на серверах: {total_remote_keys}")
    print(f"Найдено orphaned ключей: {total_orphaned}")
    print(f"Удалено ключей: {total_deleted}")
    print(f"Удалено подписок: {deleted_subs}")
    print(f"Ошибок: {total_errors}")
    print()
    
    if total_deleted > 0 or deleted_subs > 0:
        print("✅ Очистка завершена успешно")
    else:
        print("✅ Orphaned ключей и подписок не найдено")


if __name__ == "__main__":
    asyncio.run(main())













