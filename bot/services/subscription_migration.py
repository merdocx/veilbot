"""
Сервис для миграции пользователя с отдельных ключей на подписку V2Ray
"""
import time
import logging
from typing import Optional, Dict, Any, Tuple
from utils import get_db_cursor
from app.repositories.subscription_repository import SubscriptionRepository
from bot.services.subscription_service import SubscriptionService
from vpn_protocols import V2RayProtocol
from outline import delete_key as outline_delete_key
from app.infra.foreign_keys import safe_foreign_keys_off

logger = logging.getLogger(__name__)


def get_user_tariff_and_expiry(user_id: int) -> Optional[Tuple[int, int]]:
    """
    Определить тариф и остаток срока пользователя на основе его активных ключей
    
    Args:
        user_id: ID пользователя
        
    Returns:
        Tuple (tariff_id, expiry_at) или None, если ключей нет
    """
    now = int(time.time())
    
    with get_db_cursor() as cursor:
        # Найти все активные ключи пользователя (outline и v2ray отдельные)
        # Берем ключ с максимальным expiry_at
        
        # Outline ключи
        cursor.execute("""
            SELECT k.tariff_id, k.expiry_at
            FROM keys k
            WHERE k.user_id = ? AND k.expiry_at > ?
        """, (user_id, now))
        outline_keys = cursor.fetchall()
        
        # V2Ray отдельные ключи (не подписки)
        cursor.execute("""
            SELECT k.tariff_id, k.expiry_at
            FROM v2ray_keys k
            WHERE k.user_id = ? AND k.expiry_at > ? AND k.subscription_id IS NULL
        """, (user_id, now))
        v2ray_keys = cursor.fetchall()
        
        # Объединяем все ключи
        all_keys = list(outline_keys) + list(v2ray_keys)
        
        if not all_keys:
            return None
        
        # Находим ключ с максимальным expiry_at
        max_expiry_key = max(all_keys, key=lambda x: x[1] if x[1] else 0)
        tariff_id, expiry_at = max_expiry_key
        
        # Если tariff_id None, пытаемся найти тариф из других ключей
        if tariff_id is None:
            for key_tariff_id, _ in all_keys:
                if key_tariff_id is not None:
                    tariff_id = key_tariff_id
                    break
        
        # Если все еще None, возвращаем None
        if tariff_id is None:
            logger.warning(f"User {user_id} has keys but no tariff_id")
            return None
        
        return (tariff_id, expiry_at)


async def delete_user_standalone_keys(user_id: int) -> Dict[str, int]:
    """
    Удалить все отдельные ключи пользователя (не связанные с подпиской)
    
    Args:
        user_id: ID пользователя
        
    Returns:
        Словарь с результатами удаления
    """
    results = {
        'outline_deleted': 0,
        'v2ray_deleted': 0,
        'outline_errors': 0,
        'v2ray_errors': 0,
    }
    
    now = int(time.time())
    
    # 1. Удаление Outline ключей
    logger.info(f"Удаление Outline ключей пользователя {user_id}...")
    
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT k.key_id, s.api_url, s.cert_sha256
            FROM keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.key_id IS NOT NULL AND k.key_id != ''
        """, (user_id,))
        outline_keys = cursor.fetchall()
    
    for key_id, api_url, cert_sha256 in outline_keys:
        if key_id and api_url and cert_sha256:
            try:
                logger.info(f"Удаление Outline ключа {key_id} с сервера {api_url}")
                import asyncio
                result = await asyncio.get_event_loop().run_in_executor(
                    None, outline_delete_key, api_url, cert_sha256, key_id
                )
                if result:
                    results['outline_deleted'] += 1
                    logger.info(f"✓ Успешно удален Outline ключ {key_id}")
                else:
                    results['outline_errors'] += 1
            except Exception as exc:
                error_msg = f"Ошибка при удалении Outline ключа {key_id}: {exc}"
                logger.error(error_msg, exc_info=True)
                results['outline_errors'] += 1
    
    # Удалить все Outline ключи из БД
    with get_db_cursor(commit=True) as cursor:
        with safe_foreign_keys_off(cursor):
            cursor.execute("DELETE FROM keys WHERE user_id = ?", (user_id,))
            deleted_count = cursor.rowcount
            logger.info(f"✓ Удалено {deleted_count} Outline ключей из БД")
    
    # 2. Удаление V2Ray отдельных ключей (не подписки)
    logger.info(f"Удаление V2Ray отдельных ключей пользователя {user_id}...")
    
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT k.v2ray_uuid, s.api_url, s.api_key
            FROM v2ray_keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.subscription_id IS NULL
        """, (user_id,))
        v2ray_keys = cursor.fetchall()
    
    for v2ray_uuid, api_url, api_key in v2ray_keys:
        if v2ray_uuid and api_url and api_key:
            try:
                logger.info(f"Удаление V2Ray ключа {v2ray_uuid} с сервера {api_url}")
                protocol_client = V2RayProtocol(api_url, api_key)
                result = await protocol_client.delete_user(v2ray_uuid)
                if result:
                    results['v2ray_deleted'] += 1
                    logger.info(f"✓ Успешно удален V2Ray ключ {v2ray_uuid}")
                else:
                    results['v2ray_errors'] += 1
                await protocol_client.close()
            except Exception as exc:
                error_msg = f"Ошибка при удалении V2Ray ключа {v2ray_uuid}: {exc}"
                logger.error(error_msg, exc_info=True)
                results['v2ray_errors'] += 1
    
    # Удалить все V2Ray отдельные ключи из БД
    with get_db_cursor(commit=True) as cursor:
        with safe_foreign_keys_off(cursor):
            cursor.execute(
                "DELETE FROM v2ray_keys WHERE user_id = ? AND subscription_id IS NULL",
                (user_id,)
            )
            deleted_count = cursor.rowcount
            logger.info(f"✓ Удалено {deleted_count} V2Ray отдельных ключей из БД")
    
    return results


async def migrate_user_to_subscription(user_id: int) -> Dict[str, Any]:
    """
    Мигрировать пользователя с отдельных ключей на подписку V2Ray
    
    Args:
        user_id: ID пользователя
        
    Returns:
        Словарь с результатами миграции:
        {
            'success': bool,
            'subscription_token': str или None,
            'expires_at': int или None,
            'errors': List[str],
            'keys_deleted': Dict[str, int]
        }
    """
    result = {
        'success': False,
        'subscription_token': None,
        'expires_at': None,
        'errors': [],
        'keys_deleted': {},
    }
    
    # 1. Проверка наличия активной подписки
    repo = SubscriptionRepository()
    active_subscription = repo.get_active_subscription(user_id)
    if active_subscription:
        result['errors'].append("У пользователя уже есть активная подписка")
        return result
    
    # 2. Определение тарифа и остатка срока
    tariff_info = get_user_tariff_and_expiry(user_id)
    if not tariff_info:
        result['errors'].append("У пользователя нет активных ключей")
        return result
    
    tariff_id, expiry_at = tariff_info
    
    # Вычисляем duration_sec для подписки
    now = int(time.time())
    if expiry_at <= now:
        result['errors'].append("Все ключи пользователя истекли")
        return result
    
    duration_sec = expiry_at - now
    
    # 3. Создание подписки
    try:
        service = SubscriptionService()
        subscription_data = await service.create_subscription(
            user_id=user_id,
            tariff_id=tariff_id,
            duration_sec=duration_sec,
        )
        
        if not subscription_data:
            result['errors'].append("Не удалось создать подписку")
            return result
        
        result['subscription_token'] = subscription_data.get('token')
        result['expires_at'] = subscription_data.get('expires_at')
        
    except Exception as e:
        error_msg = f"Ошибка при создании подписки: {e}"
        logger.error(error_msg, exc_info=True)
        result['errors'].append(error_msg)
        return result
    
    # 4. Удаление отдельных ключей
    try:
        delete_results = await delete_user_standalone_keys(user_id)
        result['keys_deleted'] = delete_results
        
        if delete_results['outline_errors'] > 0 or delete_results['v2ray_errors'] > 0:
            result['errors'].append(
                f"Частичные ошибки при удалении ключей: "
                f"Outline ошибок: {delete_results['outline_errors']}, "
                f"V2Ray ошибок: {delete_results['v2ray_errors']}"
            )
    except Exception as e:
        error_msg = f"Ошибка при удалении ключей: {e}"
        logger.error(error_msg, exc_info=True)
        result['errors'].append(error_msg)
    
    result['success'] = True
    return result

