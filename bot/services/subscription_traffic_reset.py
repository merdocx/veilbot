"""
Функция для сброса трафика подписки при создании/продлении
"""
import logging
from typing import Optional
from app.repositories.subscription_repository import SubscriptionRepository
from vpn_protocols import ProtocolFactory
from app.infra.sqlite_utils import get_db_cursor

logger = logging.getLogger(__name__)


async def reset_subscription_traffic(subscription_id: int) -> bool:
    """
    Сбросить трафик всех ключей подписки при создании/продлении.
    
    Выполняет:
    1. Получает все ключи подписки с информацией о серверах
    2. Для каждого ключа вызывает POST /api/keys/{key_id}/traffic/reset через V2Ray API
    3. Обнуляет traffic_usage_bytes в БД для всех ключей подписки
    4. Обнуляет traffic_usage_bytes в таблице subscriptions
    
    Args:
        subscription_id: ID подписки
        
    Returns:
        True, если хотя бы один ключ был успешно сброшен, иначе False
    """
    repo = SubscriptionRepository()
    
    # Получить все ключи подписки с информацией о серверах
    keys = repo.get_subscription_keys_with_server_info(subscription_id)
    
    if not keys:
        logger.warning(f"[TRAFFIC RESET] No keys found for subscription {subscription_id}")
        return False
    
    logger.info(f"[TRAFFIC RESET] Resetting traffic for {len(keys)} keys in subscription {subscription_id}")
    
    success_count = 0
    failed_count = 0
    
    # Сбросить трафик через V2Ray API для каждого ключа
    for key_id, v2ray_uuid, server_id, api_url, api_key in keys:
        if not api_url or not api_key:
            logger.warning(
                f"[TRAFFIC RESET] Missing API credentials for server {server_id}, skipping key {key_id}"
            )
            failed_count += 1
            continue
        
        try:
            config = {"api_url": api_url, "api_key": api_key}
            protocol = ProtocolFactory.create_protocol('v2ray', config)
            
            try:
                # Получаем API key_id по UUID
                key_info = await protocol.get_key_info(v2ray_uuid)
                api_key_id = key_info.get('id') or key_info.get('uuid')
                
                if not api_key_id:
                    logger.warning(
                        f"[TRAFFIC RESET] Cannot resolve API key_id for UUID {v2ray_uuid}, skipping key {key_id}"
                    )
                    failed_count += 1
                    continue
                
                # Сбрасываем трафик через новый эндпоинт POST /api/keys/{key_id}/traffic/reset
                reset_success = await protocol.reset_key_traffic(str(api_key_id))
                
                if reset_success:
                    success_count += 1
                    logger.info(f"[TRAFFIC RESET] Successfully reset traffic for key {key_id} (UUID: {v2ray_uuid})")
                else:
                    failed_count += 1
                    logger.warning(f"[TRAFFIC RESET] Failed to reset traffic for key {key_id} (UUID: {v2ray_uuid})")
            finally:
                await protocol.close()
        except Exception as e:
            failed_count += 1
            logger.error(
                f"[TRAFFIC RESET] Error resetting traffic for key {key_id} (UUID: {v2ray_uuid}): {e}",
                exc_info=True
            )
    
    # Обнулить traffic_usage_bytes в БД для всех ключей подписки
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                """
                UPDATE v2ray_keys
                SET traffic_usage_bytes = 0
                WHERE subscription_id = ?
                """,
                (subscription_id,)
            )
            keys_updated = cursor.rowcount
            logger.info(f"[TRAFFIC RESET] Updated traffic_usage_bytes to 0 for {keys_updated} keys in DB")
    except Exception as e:
        logger.error(f"[TRAFFIC RESET] Error updating traffic_usage_bytes in DB: {e}", exc_info=True)
    
    # Обнулить traffic_usage_bytes в таблице subscriptions
    try:
        repo.update_subscription_traffic(subscription_id, 0)
        logger.info(f"[TRAFFIC RESET] Updated subscription {subscription_id} traffic_usage_bytes to 0")
    except Exception as e:
        logger.error(f"[TRAFFIC RESET] Error updating subscription traffic_usage_bytes: {e}", exc_info=True)
    
    logger.info(
        f"[TRAFFIC RESET] Completed for subscription {subscription_id}: "
        f"{success_count} successful, {failed_count} failed"
    )
    
    return success_count > 0

