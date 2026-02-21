"""
Функция для сброса трафика подписки при создании/продлении
"""
import asyncio
import logging
import time
from app.repositories.subscription_repository import SubscriptionRepository
from vpn_protocols import ProtocolFactory
from app.infra.sqlite_utils import get_db_cursor

logger = logging.getLogger(__name__)


def _reset_subscription_traffic_sync_db(subscription_id: int) -> None:
    """Синхронное обновление трафика в БД (вызывать из executor, чтобы не блокировать loop)."""
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
            logger.info(
                "[TRAFFIC RESET] Updated traffic_usage_bytes to 0 for %s keys in DB (all keys, regardless of server reset status)",
                keys_updated,
            )
    except Exception as e:
        logger.error("[TRAFFIC RESET] Error updating traffic_usage_bytes in DB: %s", e, exc_info=True)
        raise
    now_ts = int(time.time())
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                """
                UPDATE subscriptions
                SET traffic_usage_bytes = 0,
                    is_active = 1,
                    traffic_over_limit_at = NULL,
                    traffic_over_limit_notified = 0,
                    last_updated_at = ?,
                    last_traffic_reset_at = ?
                WHERE id = ?
                """,
                (now_ts, now_ts, subscription_id),
            )
            rows = cursor.rowcount
            if rows:
                logger.info(
                    "[TRAFFIC RESET] Updated subscription %s: traffic=0, is_active=1, traffic_over_limit flags reset",
                    subscription_id,
                )
            else:
                logger.warning("[TRAFFIC RESET] Subscription %s not found for traffic update", subscription_id)
    except Exception as e:
        logger.error("[TRAFFIC RESET] Error updating subscription traffic/activation: %s", e, exc_info=True)
        raise


async def reset_subscription_traffic(subscription_id: int) -> bool:
    """
    Сбросить трафик всех ключей подписки при создании/продлении.

    Выполняет:
    1. Получает все ключи подписки с информацией о серверах
    2. Для каждого ключа: GET ключа по UUID → POST /api/keys/{key_id}/traffic/reset по числовому key_id с панели
    3. Обнуляет traffic_usage_bytes в БД для всех ключей подписки
    4. Обнуляет traffic_usage_bytes в таблице subscriptions

    Args:
        subscription_id: ID подписки

    Returns:
        True, если хотя бы один ключ был успешно сброшен на сервере, иначе False
    """
    repo = SubscriptionRepository()
    keys = await asyncio.to_thread(repo.get_subscription_keys_with_server_info, subscription_id)

    if not keys:
        logger.warning(f"[TRAFFIC RESET] No keys found for subscription {subscription_id}, updating DB only")
        try:
            await asyncio.to_thread(_reset_subscription_traffic_sync_db, subscription_id)
        except Exception as e:
            logger.error("[TRAFFIC RESET] Error in sync DB update (no keys): %s", e, exc_info=True)
        return False

    logger.info(f"[TRAFFIC RESET] Resetting traffic for {len(keys)} keys in subscription {subscription_id}")

    success_count = 0
    failed_count = 0

    for key_id, v2ray_uuid, server_id, api_url, api_key in keys:
        if not api_url or not api_key:
            logger.warning(
                f"[TRAFFIC RESET] Missing API credentials for server {server_id}, skipping key {key_id}"
            )
            failed_count += 1
            continue

        if not v2ray_uuid:
            logger.warning(f"[TRAFFIC RESET] No v2ray_uuid for key {key_id}, skipping")
            failed_count += 1
            continue

        try:
            config = {"api_url": api_url, "api_key": api_key}
            protocol = ProtocolFactory.create_protocol('v2ray', config)
            try:
                key_info = await protocol.get_key_info(v2ray_uuid)
                api_key_id = key_info.get('id') or key_info.get('uuid')
                if not api_key_id:
                    logger.warning(
                        f"[TRAFFIC RESET] Cannot resolve API key_id for UUID {v2ray_uuid}, skipping key {key_id}"
                    )
                    failed_count += 1
                    continue
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
                exc_info=True,
            )
    
    # ВАЖНО: При продлении обнуляем трафик в БД для ВСЕХ ключей подписки (см. комментарии в _reset_subscription_traffic_sync_db).
    # Выполняем в executor, чтобы не блокировать event loop.
    try:
        await asyncio.to_thread(_reset_subscription_traffic_sync_db, subscription_id)
    except Exception as e:
        logger.error("[TRAFFIC RESET] Error in sync DB update: %s", e, exc_info=True)
    
    logger.info(
        f"[TRAFFIC RESET] Completed for subscription {subscription_id}: "
        f"{success_count} successful, {failed_count} failed"
    )
    
    return success_count > 0

