#!/usr/bin/env python3
"""
Однократный скрипт: сброс трафика и активация подписки 255.
Запуск: из корня проекта: PYTHONPATH=. python3 scripts/reset_and_activate_subscription_255.py
"""
import asyncio
import logging
import sys
import os

# Корень проекта в PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.settings import settings
from app.infra.sqlite_utils import open_connection
from bot.services.subscription_traffic_reset import reset_subscription_traffic

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SUBSCRIPTION_ID = 255


def activate_and_zero_traffic_in_db(subscription_id: int) -> None:
    """Активировать подписку и обнулить трафик/флаги превышения в БД."""
    db_path = settings.DATABASE_PATH
    with open_connection(db_path) as conn:
        c = conn.cursor()
        
        # Сначала обнуляем трафик всех ключей подписки (только v2ray_keys, т.к. у outline ключей нет traffic_usage_bytes)
        c.execute(
            """
            UPDATE v2ray_keys
            SET traffic_usage_bytes = 0
            WHERE subscription_id = ?
            """,
            (subscription_id,),
        )
        keys_updated = c.rowcount
        logger.info("Updated traffic_usage_bytes to 0 for %s keys in v2ray_keys", keys_updated)
        
        # Обнуляем трафик подписки и сбрасываем флаги превышения
        c.execute(
            """
            UPDATE subscriptions
            SET is_active = 1,
                traffic_usage_bytes = 0,
                traffic_over_limit_at = NULL,
                traffic_over_limit_notified = 0
            WHERE id = ?
            """,
            (subscription_id,),
        )
        conn.commit()
        if c.rowcount:
            logger.info(
                "Subscription %s: set is_active=1, traffic zeroed in subscriptions table, "
                "flags reset (traffic_over_limit_at=NULL, traffic_over_limit_notified=0)",
                subscription_id
            )
        else:
            logger.warning("Subscription %s: no row updated (not found?)", subscription_id)


async def main():
    logger.info("Resetting traffic for subscription %s via API and DB...", SUBSCRIPTION_ID)
    ok = await reset_subscription_traffic(SUBSCRIPTION_ID)
    logger.info("Traffic reset result: %s", ok)

    logger.info("Activating subscription %s and zeroing traffic in subscriptions table...", SUBSCRIPTION_ID)
    activate_and_zero_traffic_in_db(SUBSCRIPTION_ID)
    logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
