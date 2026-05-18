"""
Сброс учёта трафика подписки при создании/продлении.

Сдвигает только baseline на подписке: traffic_baseline_bytes = сумма
panel_total_bytes_observed по ключам (S), usage периода обнуляется: used = max(0, S - S) = 0.
Без запросов к панели.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from app.infra.sqlite_utils import get_db_cursor

logger = logging.getLogger(__name__)


@dataclass
class TrafficResetResult:
    """Результат сброса трафика в БД."""

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
) -> None:
    """Установить subscriptions.traffic_baseline_bytes = S (сумма observed по ключам)."""
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                """
                SELECT COALESCE(SUM(COALESCE(panel_total_bytes_observed, 0)), 0)
                FROM v2ray_keys WHERE subscription_id = ?
                """,
                (subscription_id,),
            )
            row = cursor.fetchone()
            sum_observed = int((row or (0,))[0] or 0)
            logger.info(
                "[TRAFFIC RESET] subscription %s: setting traffic_baseline_bytes=S=%s (sum of panel totals)",
                subscription_id,
                sum_observed,
            )
    except Exception as e:
        logger.error("[TRAFFIC RESET] Error reading key totals: %s", e, exc_info=True)
        raise

    now_ts = int(time.time())
    effective_reset_ts = int(reset_ts) if isinstance(reset_ts, int) and reset_ts > 0 else now_ts
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                """
                UPDATE subscriptions
                SET traffic_baseline_bytes = ?,
                    traffic_usage_bytes = 0,
                    is_active = 1,
                    traffic_over_limit_at = NULL,
                    traffic_over_limit_notified = 0,
                    last_updated_at = ?,
                    last_traffic_reset_at = ?
                WHERE id = ?
                """,
                (sum_observed, now_ts, effective_reset_ts, subscription_id),
            )
            rows = cursor.rowcount
            if rows:
                logger.info(
                    "[TRAFFIC RESET] Updated subscription %s: baseline=S, traffic_usage cache=0, flags reset",
                    subscription_id,
                )
            else:
                logger.warning("[TRAFFIC RESET] Subscription %s not found for traffic update", subscription_id)
    except Exception as e:
        logger.error("[TRAFFIC RESET] Error updating subscription traffic/activation: %s", e, exc_info=True)
        raise


def _count_subscription_keys(subscription_id: int) -> int:
    with get_db_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM v2ray_keys WHERE subscription_id = ?",
            (subscription_id,),
        )
        return int((cursor.fetchone() or (0,))[0] or 0)


async def reset_subscription_traffic(
    subscription_id: int, *, reset_ts: int | None = None
) -> TrafficResetResult:
    """
    Сброс учёта трафика подписки при создании/продлении: baseline подписки = текущая сумма observed по ключам.
    """
    keys_total = await asyncio.to_thread(_count_subscription_keys, subscription_id)

    if keys_total == 0:
        logger.warning(
            "[TRAFFIC RESET] No keys found for subscription %s, updating subscription row only",
            subscription_id,
        )
    else:
        logger.info(
            "[TRAFFIC RESET] Snap subscription baseline to sum(observed) for %s keys, subscription %s",
            keys_total,
            subscription_id,
        )

    try:
        await asyncio.to_thread(_reset_subscription_traffic_sync_db, subscription_id, reset_ts=reset_ts)
    except Exception as e:
        logger.error("[TRAFFIC RESET] Error in sync DB update: %s", e, exc_info=True)
        return TrafficResetResult(
            success=False,
            keys_total=keys_total,
            api_aligned_keys=0,
            fallback_keys=0,
            server_reset_ok=0,
            server_reset_failed=0,
        )

    logger.info(
        "[TRAFFIC RESET] Completed for subscription %s: keys_total=%s",
        subscription_id,
        keys_total,
    )

    return TrafficResetResult(
        success=True,
        keys_total=keys_total,
        api_aligned_keys=0,
        fallback_keys=keys_total,
        server_reset_ok=0,
        server_reset_failed=0,
    )
