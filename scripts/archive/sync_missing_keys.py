#!/usr/bin/env python3
"""Скрипт для синхронизации отсутствующих ключей между БД и сервером"""

import asyncio
import sys
import os
from typing import Dict, Any

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.infra.sqlite_utils import get_db_cursor
from vpn_protocols import V2RayProtocol
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def get_missing_keys_info():
    """Получить информацию о ключах, которые нужно синхронизировать"""
    server_id = 13  # ID сервера "Нидерланды"
    
    # Получаем информацию о сервере
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, name, protocol, api_url, api_key, country, domain
            FROM servers
            WHERE id = ?
            """,
            (server_id,)
        )
        row = cursor.fetchone()
    
    if not row:
        print(f"❌ Сервер с ID {server_id} не найден")
        return None
    
    server_info = dict(row)
    
    # Получаем список ключей с сервера
    protocol = V2RayProtocol(server_info['api_url'], server_info['api_key'])
    try:
        remote_keys = await protocol.get_all_keys()
        
        # Получаем все UUID из БД для этого сервера
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                SELECT v2ray_uuid FROM v2ray_keys WHERE server_id = ?
                """,
                (server_id,)
            )
            db_uuids = {row[0] for row in cursor.fetchall() if row[0]}
        
        # Находим ключи, которых нет в БД
        missing_in_db = []
        for remote_key in remote_keys or []:
            uuid = remote_key.get('uuid') or remote_key.get('id')
            if uuid and uuid not in db_uuids:
                missing_in_db.append(remote_key)
        
        # Находим ключи в БД, которых нет на сервере
        missing_on_server = []
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, user_id, email, v2ray_uuid, level, created_at, expiry_at, 
                       traffic_limit_mb, traffic_usage_bytes, subscription_id
                FROM v2ray_keys
                WHERE server_id = ?
                """,
                (server_id,)
            )
            db_keys = cursor.fetchall()
        
        remote_uuids = set()
        for remote_key in remote_keys or []:
            uuid = remote_key.get('uuid') or remote_key.get('id')
            if uuid:
                remote_uuids.add(uuid)
        
        for db_key_row in db_keys:
            db_key_dict = dict(db_key_row)
            uuid = db_key_dict.get('v2ray_uuid')
            if uuid and uuid not in remote_uuids:
                missing_on_server.append(db_key_dict)
        
        return {
            'server': server_info,
            'db_keys_to_add': missing_on_server,
            'server_keys_to_delete': missing_in_db,
            'protocol': protocol
        }
    except Exception as e:
        logger.error(f"Ошибка при получении ключей с сервера: {e}")
        await protocol.close()
        return None


async def add_key_to_server(protocol: V2RayProtocol, db_key: Dict[str, Any], server_info: Dict[str, Any]):
    """Добавить ключ из БД на сервер"""
    email = db_key.get('email') or f"user_{db_key.get('user_id')}@veilbot.com"
    name = email  # Используем email как имя
    
    print(f"\n📝 Добавление ключа на сервер:")
    print(f"   Email: {email}")
    print(f"   UUID в БД: {db_key.get('v2ray_uuid')}")
    
    try:
        # Создаем ключ на сервере
        user_data = await protocol.create_user(email, name=name)
        
        if not user_data or not user_data.get('uuid'):
            print(f"   ❌ Не удалось создать ключ на сервере")
            return False
        
        new_uuid = user_data.get('uuid')
        print(f"   ✅ Ключ создан на сервере")
        print(f"   Новый UUID: {new_uuid}")
        
        # Обновляем UUID в БД, если он отличается
        if new_uuid != db_key.get('v2ray_uuid'):
            print(f"   ⚠️  UUID на сервере отличается от БД, обновляем...")
            with get_db_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE v2ray_keys
                    SET v2ray_uuid = ?
                    WHERE id = ?
                    """,
                    (new_uuid, db_key.get('id'))
                )
                cursor.connection.commit()
            print(f"   ✅ UUID обновлен в БД")
        
        return True
    except Exception as e:
        logger.error(f"Ошибка при добавлении ключа на сервер: {e}")
        print(f"   ❌ Ошибка: {str(e)}")
        return False


async def delete_key_from_server(protocol: V2RayProtocol, remote_key: Dict[str, Any]):
    """Удалить ключ с сервера"""
    key_id = remote_key.get('id')
    uuid = remote_key.get('uuid') or key_id
    name = remote_key.get('name', 'N/A')
    
    print(f"\n🗑️  Удаление ключа с сервера:")
    print(f"   ID: {key_id}")
    print(f"   UUID: {uuid}")
    print(f"   Имя: {name}")
    
    try:
        # Удаляем ключ с сервера
        result = await protocol.delete_user(key_id)
        
        if result:
            print(f"   ✅ Ключ успешно удален с сервера")
            return True
        else:
            print(f"   ❌ Не удалось удалить ключ с сервера")
            return False
    except Exception as e:
        logger.error(f"Ошибка при удалении ключа с сервера: {e}")
        print(f"   ❌ Ошибка: {str(e)}")
        return False


async def main():
    print("=" * 80)
    print("СИНХРОНИЗАЦИЯ КЛЮЧЕЙ: БД ↔ СЕРВЕР")
    print("=" * 80)
    print()
    
    # Получаем информацию о ключах для синхронизации
    info = await get_missing_keys_info()
    
    if not info:
        print("❌ Не удалось получить информацию о ключах")
        return
    
    server = info['server']
    db_keys = info['db_keys_to_add']
    server_keys = info['server_keys_to_delete']
    protocol = info['protocol']
    
    print(f"🔹 Сервер: {server['name']} (ID: {server['id']})")
    print(f"   API URL: {server['api_url']}")
    print()
    
    # 1. Добавляем ключи из БД на сервер
    print("=" * 80)
    print(f"1️⃣  ДОБАВЛЕНИЕ КЛЮЧЕЙ ИЗ БД НА СЕРВЕР ({len(db_keys)} ключей)")
    print("=" * 80)
    
    added_count = 0
    for db_key in db_keys:
        if await add_key_to_server(protocol, db_key, server):
            added_count += 1
    
    # 2. Удаляем ключи с сервера, которых нет в БД
    print()
    print("=" * 80)
    print(f"2️⃣  УДАЛЕНИЕ КЛЮЧЕЙ С СЕРВЕРА (отсутствуют в БД) ({len(server_keys)} ключей)")
    print("=" * 80)
    
    deleted_count = 0
    for remote_key in server_keys:
        if await delete_key_from_server(protocol, remote_key):
            deleted_count += 1
    
    # Закрываем соединение
    await protocol.close()
    
    # Итоги
    print()
    print("=" * 80)
    print("ИТОГИ СИНХРОНИЗАЦИИ")
    print("=" * 80)
    print(f"✅ Добавлено ключей на сервер: {added_count} из {len(db_keys)}")
    print(f"✅ Удалено ключей с сервера: {deleted_count} из {len(server_keys)}")
    print()
    
    if added_count == len(db_keys) and deleted_count == len(server_keys):
        print("✅ Синхронизация завершена успешно!")
    else:
        print("⚠️  Синхронизация завершена с ошибками")
        if added_count < len(db_keys):
            print(f"   - Не удалось добавить {len(db_keys) - added_count} ключей на сервер")
        if deleted_count < len(server_keys):
            print(f"   - Не удалось удалить {len(server_keys) - deleted_count} ключей с сервера")


if __name__ == "__main__":
    asyncio.run(main())


