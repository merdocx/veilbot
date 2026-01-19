#!/usr/bin/env python3
"""
Скрипт для создания отсутствующих Outline ключей для пользователей,
у которых есть активные подписки, но нет Outline ключей.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
import time
from typing import Dict, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DATABASE_PATH  # noqa: E402
from vpn_protocols import ProtocolFactory  # noqa: E402
from app.infra.foreign_keys import safe_foreign_keys_off  # noqa: E402

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("fix_missing_outline_keys")


async def create_outline_key_for_subscription(
    cursor: sqlite3.Cursor,
    subscription_id: int,
    user_id: int,
    server_id: int,
    server_name: str,
    api_url: str,
    cert_sha256: str,
    tariff_id: int,
    expires_at: int,
    traffic_limit_mb: int,
    dry_run: bool = False,
) -> bool:
    """
    Создает Outline ключ для подписки на указанном сервере.
    """
    key_email = f"{user_id}_subscription_{subscription_id}@veilbot.com"
    now = int(time.time())

    # Проверяем, есть ли уже ключ для этой подписки на этом сервере
    cursor.execute(
        """
        SELECT id FROM keys
        WHERE subscription_id = ? AND server_id = ? AND protocol = 'outline'
        LIMIT 1
        """,
        (subscription_id, server_id),
    )
    if cursor.fetchone():
        logger.info(
            f"Пропускаю user_id={user_id}, sub_id={subscription_id}: ключ уже существует"
        )
        return False

    if dry_run:
        logger.info(
            f"[DRY-RUN] Создал бы Outline ключ для user_id={user_id}, sub_id={subscription_id} "
            f"на сервере {server_name} (expires_at={expires_at})"
        )
        return True

    try:
        # Создаем протокол-клиент
        server_config = {
            "api_url": api_url,
            "cert_sha256": cert_sha256,
        }
        protocol_client = ProtocolFactory.create_protocol("outline", server_config)

        try:
            # Создаем пользователя на сервере
            user_data = await protocol_client.create_user(key_email)
            if not user_data or not user_data.get("id") or not user_data.get("accessUrl"):
                logger.error(
                    f"Неверный ответ от Outline сервера для user_id={user_id}, sub_id={subscription_id}"
                )
                return False

            access_url = user_data["accessUrl"]
            outline_key_id = user_data["id"]

            # Сохраняем в БД
            # ВАЖНО: expiry_at удалено из таблицы keys - срок действия берется из subscriptions
            with safe_foreign_keys_off(cursor):
                cursor.execute(
                    """
                    INSERT INTO keys (
                        server_id,
                        user_id,
                        access_url,
                        traffic_limit_mb,
                        notified,
                        key_id,
                        created_at,
                        email,
                        tariff_id,
                        protocol,
                        subscription_id
                    )
                    VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, 'outline', ?)
                    """,
                    (
                        server_id,
                        user_id,
                        access_url,
                        traffic_limit_mb,
                        outline_key_id,
                        now,
                        key_email,
                        tariff_id,
                        subscription_id,
                    ),
                )

            logger.info(
                f"Создан Outline ключ (key_id={outline_key_id}) для user_id={user_id}, "
                f"sub_id={subscription_id} на сервере {server_name}"
            )
            return True

        finally:
            try:
                if hasattr(protocol_client, 'close'):
                    await protocol_client.close()
            except Exception as e:
                logger.debug(f"Ошибка при закрытии протокол-клиента: {e}")

    except Exception as e:
        logger.error(
            f"Ошибка при создании Outline ключа для user_id={user_id}, sub_id={subscription_id}: {e}",
            exc_info=True,
        )
        # Если ключ был создан на сервере, но не сохранен в БД - пытаемся удалить
        try:
            if "user_data" in locals() and user_data and user_data.get("id"):
                cleanup_client = ProtocolFactory.create_protocol("outline", server_config)
                await cleanup_client.delete_user(user_data["id"])
                if hasattr(cleanup_client, 'close'):
                    await cleanup_client.close()
                logger.info(f"Удален orphaned ключ с сервера для user_id={user_id}")
        except Exception as cleanup_error:
            logger.warning(f"Не удалось очистить orphaned ключ: {cleanup_error}")

        return False


def find_subscriptions_without_outline_keys(
    cursor: sqlite3.Cursor, user_ids: Optional[list[int]] = None
) -> list[Dict]:
    """
    Находит подписки, у которых нет Outline ключей.
    """
    now = int(time.time())

    if user_ids:
        placeholders = ",".join("?" * len(user_ids))
        user_filter = f"AND s.user_id IN ({placeholders})"
        params = (now,) + tuple(user_ids)
    else:
        user_filter = ""
        params = (now,)

    query = f"""
        SELECT 
            s.id AS subscription_id,
            s.user_id,
            s.expires_at,
            s.tariff_id,
            COALESCE(s.traffic_limit_mb, t.traffic_limit_mb, 0) AS traffic_limit_mb
        FROM subscriptions s
        LEFT JOIN tariffs t ON s.tariff_id = t.id
        WHERE s.is_active = 1
          AND s.expires_at > ?
          {user_filter}
          AND NOT EXISTS (
              SELECT 1 FROM keys k
              WHERE k.subscription_id = s.id 
                AND k.protocol = 'outline'
          )
        ORDER BY s.user_id, s.id
    """

    cursor.execute(query, params)
    rows = cursor.fetchall()

    return [
        {
            "subscription_id": row[0],
            "user_id": row[1],
            "expires_at": row[2],
            "tariff_id": row[3],
            "traffic_limit_mb": row[4] or 0,
        }
        for row in rows
    ]


def get_outline_server(cursor: sqlite3.Cursor, server_id: Optional[int] = None) -> Optional[Tuple]:
    """
    Получает данные Outline сервера.
    """
    if server_id:
        cursor.execute(
            """
            SELECT id, name, api_url, cert_sha256, domain, api_key, v2ray_path
            FROM servers
            WHERE id = ? AND protocol = 'outline' AND active = 1
            LIMIT 1
            """,
            (server_id,),
        )
    else:
        cursor.execute(
            """
            SELECT id, name, api_url, cert_sha256, domain, api_key, v2ray_path
            FROM servers
            WHERE protocol = 'outline' AND active = 1
            ORDER BY RANDOM()
            LIMIT 1
            """,
        )

    row = cursor.fetchone()
    if not row:
        return None

    return row


async def main_async(args) -> None:
    """Асинхронная часть main."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    try:
        # Получаем сервер
        server = get_outline_server(cursor, args.server_id)
        if not server:
            logger.error("Не найден активный Outline сервер")
            sys.exit(1)

        server_id, server_name, api_url, cert_sha256, domain, api_key, v2ray_path = server
        logger.info(f"Используется сервер: {server_name} (ID: {server_id})")

        # Находим подписки без Outline ключей
        user_ids = [int(uid) for uid in args.user_ids] if args.user_ids else None
        subscriptions = find_subscriptions_without_outline_keys(cursor, user_ids)

        if not subscriptions:
            logger.info("Нет подписок без Outline ключей")
            return

        logger.info(f"Найдено подписок без Outline ключей: {len(subscriptions)}")

        created = 0
        skipped = 0
        failed = 0

        for sub in subscriptions:
            try:
                result = await create_outline_key_for_subscription(
                    cursor=cursor,
                    subscription_id=sub["subscription_id"],
                    user_id=sub["user_id"],
                    server_id=server_id,
                    server_name=server_name,
                    api_url=api_url,
                    cert_sha256=cert_sha256 or "",
                    tariff_id=sub["tariff_id"],
                    expires_at=sub["expires_at"],
                    traffic_limit_mb=sub["traffic_limit_mb"],
                    dry_run=args.dry_run,
                )
                if result:
                    created += 1
                else:
                    skipped += 1
                    if not args.dry_run:
                        conn.commit()
            except Exception as e:
                logger.exception(
                    f"Ошибка при обработке подписки {sub['subscription_id']} "
                    f"(user_id={sub['user_id']}): {e}"
                )
                failed += 1

        if not args.dry_run:
            conn.commit()

        logger.info("=" * 60)
        logger.info(f"Обработано подписок: {len(subscriptions)}")
        logger.info(f"Создано ключей: {created}")
        logger.info(f"Пропущено: {skipped}")
        logger.info(f"Ошибок: {failed}")

        if args.dry_run:
            logger.info("DRY-RUN завершен, изменений в базе нет.")

    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Создать отсутствующие Outline ключи для подписок"
    )
    parser.add_argument(
        "--server-id",
        type=int,
        help="ID Outline сервера (по умолчанию - случайный активный)",
    )
    parser.add_argument(
        "--user-ids",
        nargs="+",
        help="Список user_id для обработки (опционально)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, что будет сделано",
    )
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()

