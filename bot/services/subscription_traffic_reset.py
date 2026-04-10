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


def _reset_subscription_traffic_sync_db(
    subscription_id: int,
    *,
    reset_ts: int | None = None,
    api_totals_by_key_id: dict[int, int] | None = None,
) -> None:
    """Синхронное обновление трафика в БД (вызывать из executor, чтобы не блокировать loop).

    reset_ts: timestamp, который будет записан в subscriptions.last_traffic_reset_at (если None — используем now).
    api_totals_by_key_id: map(db_key_id -> api_total_bytes) для консистентного baseline (baseline=api_total, usage=0).
    """
    api_totals_by_key_id = api_totals_by_key_id or {}
    try:
        with get_db_cursor(commit=True) as cursor:
            # Для ключей, у которых мы смогли получить api_total_bytes, делаем baseline=api_total_bytes, usage=0.
            # Это гарантирует "нулевой период" даже при лаге между API и БД.
            totals_items = [
                (int(total_bytes), int(key_id))
                for key_id, total_bytes in api_totals_by_key_id.items()
                if isinstance(key_id, int) and isinstance(total_bytes, int) and total_bytes >= 0
            ]
            keys_updated_from_api = 0
            if totals_items:
                cursor.executemany(
                    """
                    UPDATE v2ray_keys
                    SET traffic_baseline_bytes = ?,
                        traffic_usage_bytes = 0
                    WHERE id = ? AND subscription_id = ?
                    """,
                    [(total, key_id, subscription_id) for (total, key_id) in totals_items],
                )
                # rowcount для executemany в sqlite не всегда надёжен; используем len как ожидаемое.
                keys_updated_from_api = len(totals_items)

            # Для остальных ключей fallback: "сдвиг baseline" на накопленный stored usage и usage=0.
            # Ветвим SQL, чтобы не городить NOT IN (NULL) и вложенные SELECT.
            if api_totals_by_key_id:
                excluded_ids = list(api_totals_by_key_id.keys())
                placeholders = ",".join(["?"] * len(excluded_ids))
                cursor.execute(
                    f"""
                    UPDATE v2ray_keys
                    SET traffic_baseline_bytes = COALESCE(traffic_baseline_bytes, 0) + COALESCE(traffic_usage_bytes, 0),
                        traffic_usage_bytes = 0
                    WHERE subscription_id = ?
                      AND id NOT IN ({placeholders})
                    """,
                    (subscription_id, *excluded_ids),
                )
            else:
                cursor.execute(
                    """
                    UPDATE v2ray_keys
                    SET traffic_baseline_bytes = COALESCE(traffic_baseline_bytes, 0) + COALESCE(traffic_usage_bytes, 0),
                        traffic_usage_bytes = 0
                    WHERE subscription_id = ?
                    """,
                    (subscription_id,),
                )
            keys_updated_fallback = cursor.rowcount
            logger.info(
                "[TRAFFIC RESET] DB baseline reset done for subscription %s: api_baseline=%s keys, fallback_shift=%s keys",
                subscription_id,
                keys_updated_from_api,
                keys_updated_fallback,
            )
    except Exception as e:
        logger.error("[TRAFFIC RESET] Error updating traffic_usage_bytes in DB: %s", e, exc_info=True)
        raise
    now_ts = int(time.time())
    effective_reset_ts = int(reset_ts) if isinstance(reset_ts, int) and reset_ts > 0 else now_ts
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
                (now_ts, effective_reset_ts, subscription_id),
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


async def reset_subscription_traffic(subscription_id: int, *, reset_ts: int | None = None) -> bool:
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
        True, если сброс в БД выполнен успешно (серверный reset best-effort).
    """
    repo = SubscriptionRepository()
    keys = await asyncio.to_thread(repo.get_subscription_keys_with_server_info, subscription_id)

    if not keys:
        logger.warning(f"[TRAFFIC RESET] No keys found for subscription {subscription_id}, updating DB only")
        try:
            await asyncio.to_thread(_reset_subscription_traffic_sync_db, subscription_id, reset_ts=reset_ts)
        except Exception as e:
            logger.error("[TRAFFIC RESET] Error in sync DB update (no keys): %s", e, exc_info=True)
            return False
        return True

    logger.info(f"[TRAFFIC RESET] Resetting traffic for {len(keys)} keys in subscription {subscription_id}")

    success_count = 0
    failed_count = 0
    api_totals_by_db_key_id: dict[int, int] = {}

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
                # Пытаемся получить total_bytes и после reset, чтобы выставить baseline консистентно.
                total_before = None
                try:
                    stats_before = await protocol.get_key_traffic_stats(str(api_key_id))
                    total_before = stats_before.get("total_bytes") if isinstance(stats_before, dict) else None
                except Exception:
                    total_before = None

                reset_success = await protocol.reset_key_traffic(str(api_key_id))

                total_after = None
                try:
                    stats_after = await protocol.get_key_traffic_stats(str(api_key_id))
                    total_after = stats_after.get("total_bytes") if isinstance(stats_after, dict) else None
                except Exception:
                    total_after = None

                chosen_total = None
                if isinstance(total_after, int) and total_after >= 0:
                    chosen_total = total_after
                elif reset_success:
                    # Если серверный reset сработал, предполагаем что счётчик обнулился.
                    chosen_total = 0
                elif isinstance(total_before, int) and total_before >= 0:
                    chosen_total = total_before

                if isinstance(chosen_total, int) and chosen_total >= 0:
                    api_totals_by_db_key_id[int(key_id)] = int(chosen_total)
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
    
    # ВАЖНО: При продлении/создании обнуляем трафик в БД для ВСЕХ ключей подписки.
    # Для ключей, где удалось получить api_total, фиксируем baseline=api_total для консистентности.
    try:
        await asyncio.to_thread(
            _reset_subscription_traffic_sync_db,
            subscription_id,
            reset_ts=reset_ts,
            api_totals_by_key_id=api_totals_by_db_key_id,
        )
    except Exception as e:
        logger.error("[TRAFFIC RESET] Error in sync DB update: %s", e, exc_info=True)
        return False
    
    logger.info(
        f"[TRAFFIC RESET] Completed for subscription {subscription_id}: "
        f"{success_count} successful, {failed_count} failed"
    )

    # Серверный reset best-effort, но DB reset — источник истины для биллинга/лимитов.
    return True

