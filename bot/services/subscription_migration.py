"""
Сервис для миграции пользователя с отдельных ключей на подписку V2Ray
"""
import time
import logging
from typing import Optional, Dict, Any, Tuple
from app.infra.sqlite_utils import get_db_cursor
from app.repositories.subscription_repository import SubscriptionRepository
from bot.services.subscription_service import SubscriptionService
from vpn_protocols import V2RayProtocol
from app.infra.foreign_keys import safe_foreign_keys_off

logger = logging.getLogger(__name__)


def get_user_tariff_and_expiry(user_id: int) -> Optional[Tuple[int, int]]:
    """
    Определить тариф и остаток срока пользователя на основе его активных V2Ray ключей

    Args:
        user_id: ID пользователя

    Returns:
        Tuple (tariff_id, expiry_at) или None, если ключей нет
    """
    now = int(time.time())

    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT k.tariff_id, COALESCE(sub.expires_at, 0) as expiry_at
            FROM v2ray_keys k
            LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
            WHERE k.user_id = ? AND (sub.expires_at > ? OR (sub.expires_at IS NULL AND k.subscription_id IS NULL))
            AND k.subscription_id IS NULL
        """, (user_id, now))
        v2ray_keys = cursor.fetchall()

        if not v2ray_keys:
            return None

        max_expiry_key = max(v2ray_keys, key=lambda x: x[1] if x[1] else 0)
        tariff_id, expiry_at = max_expiry_key

        if tariff_id is None:
            for key_tariff_id, _ in v2ray_keys:
                if key_tariff_id is not None:
                    tariff_id = key_tariff_id
                    break

        if tariff_id is None:
            logger.warning(f"User {user_id} has keys but no tariff_id")
            return None

        return (tariff_id, expiry_at)


async def delete_user_standalone_keys(user_id: int) -> Dict[str, int]:
    """
    Удалить все отдельные V2Ray ключи пользователя (не связанные с подпиской)

    Args:
        user_id: ID пользователя

    Returns:
        Словарь с результатами удаления
    """
    results = {
        'v2ray_deleted': 0,
        'v2ray_errors': 0,
    }

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

    with get_db_cursor(commit=True) as cursor:
        with safe_foreign_keys_off(cursor):
            cursor.execute(
                "DELETE FROM v2ray_keys WHERE user_id = ? AND subscription_id IS NULL",
                (user_id,),
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
        Словарь с результатами миграции
    """
    result = {
        'success': False,
        'subscription_token': None,
        'expires_at': None,
        'errors': [],
        'keys_deleted': {},
    }

    repo = SubscriptionRepository()
    active_subscription = repo.get_active_subscription(user_id)
    if active_subscription:
        result['errors'].append("У пользователя уже есть активная подписка")
        return result

    tariff_info = get_user_tariff_and_expiry(user_id)
    if not tariff_info:
        result['errors'].append("У пользователя нет активных ключей")
        return result

    tariff_id, expiry_at = tariff_info

    now = int(time.time())
    if expiry_at <= now:
        result['errors'].append("Все ключи пользователя истекли")
        return result

    duration_sec = expiry_at - now

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

    try:
        delete_results = await delete_user_standalone_keys(user_id)
        result['keys_deleted'] = delete_results

        if delete_results['v2ray_errors'] > 0:
            result['errors'].append(
                f"Частичные ошибки при удалении ключей: V2Ray ошибок: {delete_results['v2ray_errors']}"
            )
    except Exception as e:
        error_msg = f"Ошибка при удалении ключей: {e}"
        logger.error(error_msg, exc_info=True)
        result['errors'].append(error_msg)

    result['success'] = True
    return result
