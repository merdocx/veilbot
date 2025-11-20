"""
Утилита для определения, является ли платеж продлением существующего ключа/подписки
"""
import logging
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Константа grace period (24 часа)
DEFAULT_GRACE_PERIOD = 86400  # 24 часа в секундах


def is_renewal_payment(
    cursor,
    user_id: int,
    protocol: str,
    grace_period: int = DEFAULT_GRACE_PERIOD
) -> bool:
    """
    Определить, является ли платеж продлением существующего ключа
    
    Args:
        cursor: Курсор базы данных (синхронный)
        user_id: ID пользователя
        protocol: Протокол ('outline' или 'v2ray')
        grace_period: Grace period в секундах (по умолчанию 24 часа)
        
    Returns:
        True если у пользователя есть активный ключ (продление), False если нет (новая покупка)
    """
    try:
        now = int(datetime.now(timezone.utc).timestamp())
        grace_threshold = now - grace_period
        
        if protocol == 'outline':
            # Проверяем наличие активного Outline ключа
            cursor.execute(
                "SELECT 1 FROM keys WHERE user_id = ? AND expiry_at > ? LIMIT 1",
                (user_id, grace_threshold)
            )
            has_key = cursor.fetchone() is not None
            
            if has_key:
                logger.debug(f"User {user_id} has active Outline key - this is a renewal")
                return True
        elif protocol == 'v2ray':
            # Проверяем наличие активного V2Ray ключа
            cursor.execute(
                "SELECT 1 FROM v2ray_keys WHERE user_id = ? AND expiry_at > ? LIMIT 1",
                (user_id, grace_threshold)
            )
            has_key = cursor.fetchone() is not None
            
            if has_key:
                logger.debug(f"User {user_id} has active V2Ray key - this is a renewal")
                return True
        
        logger.debug(f"User {user_id} has no active {protocol} key - this is a new purchase")
        return False
        
    except Exception as e:
        logger.error(f"Error determining if payment is renewal for user {user_id}, protocol {protocol}: {e}")
        # В случае ошибки считаем, что это новая покупка (безопаснее)
        return False


async def is_renewal_payment_async(
    conn,
    user_id: int,
    protocol: str,
    grace_period: int = DEFAULT_GRACE_PERIOD
) -> bool:
    """
    Определить, является ли платеж продлением существующего ключа (асинхронная версия)
    
    Args:
        conn: Асинхронное соединение с БД
        user_id: ID пользователя
        protocol: Протокол ('outline' или 'v2ray')
        grace_period: Grace period в секундах (по умолчанию 24 часа)
        
    Returns:
        True если у пользователя есть активный ключ (продление), False если нет (новая покупка)
    """
    try:
        from datetime import datetime, timezone
        now = int(datetime.now(timezone.utc).timestamp())
        grace_threshold = now - grace_period
        
        if protocol == 'outline':
            # Проверяем наличие активного Outline ключа
            async with conn.execute(
                "SELECT 1 FROM keys WHERE user_id = ? AND expiry_at > ? LIMIT 1",
                (user_id, grace_threshold)
            ) as cursor:
                row = await cursor.fetchone()
                has_key = row is not None
                
                if has_key:
                    logger.debug(f"User {user_id} has active Outline key - this is a renewal")
                    return True
        elif protocol == 'v2ray':
            # Проверяем наличие активного V2Ray ключа
            async with conn.execute(
                "SELECT 1 FROM v2ray_keys WHERE user_id = ? AND expiry_at > ? LIMIT 1",
                (user_id, grace_threshold)
            ) as cursor:
                row = await cursor.fetchone()
                has_key = row is not None
                
                if has_key:
                    logger.debug(f"User {user_id} has active V2Ray key - this is a renewal")
                    return True
        
        logger.debug(f"User {user_id} has no active {protocol} key - this is a new purchase")
        return False
        
    except Exception as e:
        logger.error(f"Error determining if payment is renewal for user {user_id}, protocol {protocol}: {e}")
        # В случае ошибки считаем, что это новая покупка (безопаснее)
        return False


