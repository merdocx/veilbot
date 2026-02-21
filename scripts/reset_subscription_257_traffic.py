#!/usr/bin/env python3
"""
Однократный сброс трафика подписки 257 (после продления трафик не сбросился).
Запуск из корня: PYTHONPATH=. python3 scripts/reset_subscription_257_traffic.py
"""
import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.settings import settings
from app.infra.sqlite_utils import open_connection
from bot.services.subscription_traffic_reset import reset_subscription_traffic

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SUBSCRIPTION_ID = 257


def zero_traffic_in_db(subscription_id: int) -> None:
    """Обнулить traffic_usage_bytes в v2ray_keys и subscriptions для подписки."""
    db_path = settings.DATABASE_PATH
    with open_connection(db_path) as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE v2ray_keys SET traffic_usage_bytes = 0 WHERE subscription_id = ?",
            (subscription_id,),
        )
        logger.info("v2ray_keys updated: %s rows", c.rowcount)
        c.execute(
            """
            UPDATE subscriptions
            SET traffic_usage_bytes = 0, traffic_over_limit_at = NULL, traffic_over_limit_notified = 0
            WHERE id = ?
            """,
            (subscription_id,),
        )
        conn.commit()
        logger.info("subscriptions updated: %s rows", c.rowcount)


async def main():
    logger.info("Resetting traffic for subscription %s (API + DB)...", SUBSCRIPTION_ID)
    ok = await reset_subscription_traffic(SUBSCRIPTION_ID)
    logger.info("API reset result: %s", ok)
    zero_traffic_in_db(SUBSCRIPTION_ID)
    logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
