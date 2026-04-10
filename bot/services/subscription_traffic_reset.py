"""
Функция для сброса трафика подписки при создании/продлении
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from app.repositories.subscription_repository import SubscriptionRepository
from vpn_protocols import ProtocolFactory
from app.infra.sqlite_utils import get_db_cursor

logger = logging.getLogger(__name__)


@dataclass
class TrafficResetResult:
    """Результат сброса трафика: DB — источник истины; API reset — best-effort."""

    success: bool
    keys_total: int
    api_aligned_keys: int
    fallback_keys: int
    server_reset_ok: int
    server_reset_failed: int

    def __bool__(self) -> bool:
        return self.success


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


def _sync_subscription_traffic_cache(subscription_id: int) -> int:
    """Пересчитать subscriptions.traffic_usage_bytes как сумму по ключам."""
    now_ts = int(time.time())
    with get_db_cursor(commit=True) as cursor:
        cursor.execute(
            "SELECT COALESCE(SUM(traffic_usage_bytes), 0) FROM v2ray_keys WHERE subscription_id = ?",
            (subscription_id,),
        )
        total = int((cursor.fetchone() or (0,))[0] or 0)
        cursor.execute(
            "UPDATE subscriptions SET traffic_usage_bytes = ?, last_updated_at = ? WHERE id = ?",
            (total, now_ts, subscription_id),
        )
    return total


async def reconcile_subscription_traffic_usage_from_api(subscription_id: int) -> bool:
    """
    Выровнять traffic_usage_bytes по API: effective = max(0, api_total_bytes - baseline).

    Вызывать после fallback-сброса, когда baseline в БД мог не совпасть с панелью.
    """
    def _load_key_rows() -> list[tuple[int, str, str, str, int]]:
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                SELECT k.id, k.v2ray_uuid, s.api_url, s.api_key,
                       COALESCE(k.traffic_baseline_bytes, 0)
                FROM v2ray_keys k
                JOIN servers s ON s.id = k.server_id
                WHERE k.subscription_id = ?
                ORDER BY k.id
                """,
                (subscription_id,),
            )
            rows = cursor.fetchall()
        out: list[tuple[int, str, str, str, int]] = []
        for r in rows:
            out.append(
                (
                    int(r[0]),
                    str(r[1] or "").strip(),
                    str(r[2] or "").strip(),
                    str(r[3] or "").strip(),
                    int(r[4] or 0),
                )
            )
        return out

    key_rows = await asyncio.to_thread(_load_key_rows)
    if not key_rows:
        logger.info("[TRAFFIC RECONCILE] No keys for subscription %s", subscription_id)
        return True

    updates: list[tuple[int, int]] = []
    for key_id, v2ray_uuid, api_url, api_key, baseline_bytes in key_rows:
        if not api_url or not api_key or not v2ray_uuid:
            continue
        try:
            config = {"api_url": api_url, "api_key": api_key}
            protocol = ProtocolFactory.create_protocol("v2ray", config)
            try:
                _api_ident, stats = await protocol.get_v2ray_key_traffic_resolved(v2ray_uuid)
                if not stats:
                    continue
                total_bytes = stats.get("total_bytes") if isinstance(stats, dict) else None
                if not isinstance(total_bytes, (int, float)) or total_bytes < 0:
                    continue
                effective = max(0, int(total_bytes) - int(baseline_bytes))
                updates.append((effective, key_id))
            finally:
                await protocol.close()
        except Exception as e:
            logger.warning(
                "[TRAFFIC RECONCILE] key %s subscription %s: %s",
                key_id,
                subscription_id,
                e,
            )

    if updates:
        def _write() -> None:
            with get_db_cursor(commit=True) as cursor:
                cursor.executemany(
                    "UPDATE v2ray_keys SET traffic_usage_bytes = ? WHERE id = ? AND subscription_id = ?",
                    [(u, kid, subscription_id) for u, kid in updates],
                )

        await asyncio.to_thread(_write)
    total = await asyncio.to_thread(_sync_subscription_traffic_cache, subscription_id)
    logger.info(
        "[TRAFFIC RECONCILE] subscription %s: updated %s keys, subscription traffic_usage_bytes=%s",
        subscription_id,
        len(updates),
        total,
    )
    return True


def schedule_traffic_reconcile_after_reset(subscription_id: int) -> None:
    """Фоновый дожим usage из API после reset (fallback baseline или ошибки панели на reset)."""

    async def _run() -> None:
        try:
            await reconcile_subscription_traffic_usage_from_api(subscription_id)
        except Exception:
            logger.exception("[TRAFFIC RECONCILE] background task failed for subscription %s", subscription_id)

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        asyncio.run(_run())


async def reset_subscription_traffic(
    subscription_id: int, *, reset_ts: int | None = None
) -> TrafficResetResult:
    """
    Сбросить трафик всех ключей подписки при создании/продлении.

    Выполняет:
    1. Получает все ключи подписки с информацией о серверах
    2. Для каждого ключа: resolve трафика (GET /keys/{uuid}/traffic по возможности) → POST reset traffic
    3. Обнуляет traffic_usage_bytes в БД для всех ключей подписки
    4. Обнуляет traffic_usage_bytes в таблице subscriptions

    Returns:
        TrafficResetResult (в булевом контексте — success DB reset).
    """
    repo = SubscriptionRepository()
    keys = await asyncio.to_thread(repo.get_subscription_keys_with_server_info, subscription_id)

    if not keys:
        logger.warning(f"[TRAFFIC RESET] No keys found for subscription {subscription_id}, updating DB only")
        try:
            await asyncio.to_thread(_reset_subscription_traffic_sync_db, subscription_id, reset_ts=reset_ts)
        except Exception as e:
            logger.error("[TRAFFIC RESET] Error in sync DB update (no keys): %s", e, exc_info=True)
            return TrafficResetResult(
                success=False,
                keys_total=0,
                api_aligned_keys=0,
                fallback_keys=0,
                server_reset_ok=0,
                server_reset_failed=0,
            )
        return TrafficResetResult(
            success=True,
            keys_total=0,
            api_aligned_keys=0,
            fallback_keys=0,
            server_reset_ok=0,
            server_reset_failed=0,
        )

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
                api_key_id, stats_before = await protocol.get_v2ray_key_traffic_resolved(v2ray_uuid)
                if not api_key_id or not stats_before:
                    logger.warning(
                        f"[TRAFFIC RESET] Cannot resolve traffic/API id for UUID {v2ray_uuid}, skipping key {key_id}"
                    )
                    failed_count += 1
                    continue
                # Пытаемся получить total_bytes и после reset, чтобы выставить baseline консистентно.
                total_before = None
                try:
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

                # Важно: если POST /traffic/reset вернул ошибку, не доверяем total_after (часто 0 или
                # устаревшее значение при баге панели) — иначе SET baseline=? затирает накопленный baseline.
                chosen_total = None
                if reset_success:
                    if isinstance(total_after, int) and total_after >= 0:
                        chosen_total = total_after
                    else:
                        chosen_total = 0
                else:
                    if isinstance(total_before, int) and total_before >= 0:
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
    keys_total = len(keys)
    api_aligned = len(api_totals_by_db_key_id)
    fallback_keys = max(0, keys_total - api_aligned)

    try:
        await asyncio.to_thread(
            _reset_subscription_traffic_sync_db,
            subscription_id,
            reset_ts=reset_ts,
            api_totals_by_key_id=api_totals_by_db_key_id,
        )
    except Exception as e:
        logger.error("[TRAFFIC RESET] Error in sync DB update: %s", e, exc_info=True)
        return TrafficResetResult(
            success=False,
            keys_total=keys_total,
            api_aligned_keys=api_aligned,
            fallback_keys=fallback_keys,
            server_reset_ok=success_count,
            server_reset_failed=failed_count,
        )

    logger.info(
        "[TRAFFIC RESET] Completed for subscription %s: server_ok=%s server_failed=%s "
        "api_aligned=%s fallback=%s keys_total=%s",
        subscription_id,
        success_count,
        failed_count,
        api_aligned,
        fallback_keys,
        keys_total,
    )

    # Дожим из API: если baseline ушёл в fallback ИЛИ POST reset на панели не сработал
    # (иначе счётчик на сервере мог остаться большим — монитор потом покажет «всплеск»).
    if fallback_keys > 0 or failed_count > 0:
        schedule_traffic_reconcile_after_reset(subscription_id)

    # Серверный reset best-effort, но DB reset — источник истины для биллинга/лимитов.
    return TrafficResetResult(
        success=True,
        keys_total=keys_total,
        api_aligned_keys=api_aligned,
        fallback_keys=fallback_keys,
        server_reset_ok=success_count,
        server_reset_failed=failed_count,
    )
