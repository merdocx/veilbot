#!/usr/bin/env python3
"""
Скрипт для создания недостающих ключей для подписки #79
"""
import asyncio
import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.infra.sqlite_utils import get_db_cursor, open_async_connection
from vpn_protocols import ProtocolFactory
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def fix_subscription_79():
    """Создать недостающие ключи для подписки #79"""
    subscription_id = 79
    
    # Получаем информацию о подписке
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT id, user_id, subscription_token, expires_at, tariff_id, is_active
            FROM subscriptions
            WHERE id = ?
        """, (subscription_id,))
        subscription = cursor.fetchone()
    
    if not subscription:
        logger.error(f"Subscription {subscription_id} not found")
        return False
    
    sub_id, user_id, token, expires_at, tariff_id, is_active = subscription
    
    if not is_active:
        logger.warning(f"Subscription {subscription_id} is not active")
        return False
    
    logger.info(f"Found subscription {subscription_id} for user {user_id}, expires_at={expires_at}")
    
    # Получаем все активные V2Ray серверы
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT id, name, api_url, api_key, domain, v2ray_path
            FROM servers
            WHERE protocol = 'v2ray' AND active = 1
            ORDER BY id
        """)
        servers = cursor.fetchall()
    
    if not servers:
        logger.error("No active V2Ray servers found")
        return False
    
    logger.info(f"Found {len(servers)} active V2Ray servers")
    
    created_keys = 0
    failed_servers = []
    now = int(time.time())
    
    for server_id, server_name, api_url, api_key, domain, v2ray_path in servers:
        # Проверяем, есть ли уже ключ для этой подписки на этом сервере
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT id FROM v2ray_keys
                WHERE server_id = ? AND user_id = ? AND subscription_id = ?
            """, (server_id, user_id, subscription_id))
            existing_key = cursor.fetchone()
        
        if existing_key:
            logger.info(f"Key already exists for subscription {subscription_id} on server {server_id}")
            continue
        
        protocol_client = None
        v2ray_uuid = None
        try:
            # Генерация email для ключа
            key_email = f"{user_id}_subscription_{subscription_id}@veilbot.com"
            
            logger.info(f"Creating key for subscription {subscription_id} on server {server_id} ({server_name})")
            
            # Создание ключа через V2Ray API
            server_config = {
                'api_url': api_url,
                'api_key': api_key,
                'domain': domain,
            }
            protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
            user_data = await protocol_client.create_user(key_email, name=server_name)
            
            if not user_data or not user_data.get('uuid'):
                raise Exception("Failed to create user on V2Ray server")
            
            v2ray_uuid = user_data['uuid']
            
            # Получение client_config
            client_config = await protocol_client.get_user_config(
                v2ray_uuid,
                {
                    'domain': domain,
                    'port': 443,
                    'email': key_email,
                },
            )
            
            # Извлекаем VLESS URL из конфигурации
            if 'vless://' in client_config:
                lines = client_config.split('\n')
                for line in lines:
                    if line.strip().startswith('vless://'):
                        client_config = line.strip()
                        break
            
            # Сохранение ключа в БД
            async with open_async_connection(None) as conn:
                await conn.execute("PRAGMA foreign_keys = OFF")
                try:
                    await conn.execute(
                        """
                        INSERT INTO v2ray_keys 
                        (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id, client_config, subscription_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            server_id,
                            user_id,
                            v2ray_uuid,
                            key_email,
                            now,
                            expires_at,
                            tariff_id,
                            client_config,
                            subscription_id,
                        ),
                    )
                    await conn.commit()
                    
                    # Проверяем, что ключ действительно сохранен
                    async with conn.execute(
                        "SELECT id FROM v2ray_keys WHERE server_id = ? AND user_id = ? AND subscription_id = ? AND v2ray_uuid = ?",
                        (server_id, user_id, subscription_id, v2ray_uuid)
                    ) as check_cursor:
                        if not await check_cursor.fetchone():
                            raise Exception(f"Key was not saved to database for server {server_id}")
                    
                finally:
                    await conn.execute("PRAGMA foreign_keys = ON")
            
            created_keys += 1
            logger.info(f"Successfully created key for subscription {subscription_id} on server {server_id}")
            await protocol_client.close()
            
        except Exception as e:
            logger.error(
                f"Failed to create key for subscription {subscription_id} "
                f"on server {server_id} ({server_name}): {e}",
                exc_info=True,
            )
            # Если ключ был создан на сервере, но не сохранен в БД - пытаемся удалить его с сервера
            if v2ray_uuid and protocol_client:
                try:
                    await protocol_client.delete_user(v2ray_uuid)
                    logger.info(f"Cleaned up orphaned key on server {server_id}")
                except Exception as cleanup_error:
                    logger.error(f"Failed to cleanup orphaned key: {cleanup_error}")
            elif protocol_client:
                try:
                    await protocol_client.close()
                except Exception:
                    pass
            failed_servers.append(server_id)
    
    logger.info(
        f"Finished fixing subscription {subscription_id}: "
        f"{created_keys} keys created, {len(failed_servers)} failed"
    )
    
    return created_keys > 0


if __name__ == "__main__":
    import time
    success = asyncio.run(fix_subscription_79())
    sys.exit(0 if success else 1)

