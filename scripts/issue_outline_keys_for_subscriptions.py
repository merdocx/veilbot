#!/usr/bin/env python3
"""
Выпускает Outline ключи на выбранном сервере для всех пользователей
с активной подпиской. Срок действия ключа синхронизируется с подпиской,
уведомления пользователям не отправляются.
"""

from __future__ import annotations

import argparse
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
from outline import create_key  # noqa: E402

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("issue_outline_keys")


def resolve_user_email(cursor: sqlite3.Cursor, user_id: int) -> str:
    """
    Возвращает email пользователя, используя ту же стратегию, что и UserRepository.
    """
    cursor.execute(
        """
        SELECT email FROM (
            SELECT email, 1 AS priority, created_at AS sort_date
            FROM payments
            WHERE user_id = ? AND email IS NOT NULL AND email != '' AND email NOT LIKE 'user_%@veilbot.com'
            UNION ALL
            SELECT email, 2 AS priority, expiry_at AS sort_date
            FROM keys
            WHERE user_id = ? AND email IS NOT NULL AND email != '' AND email NOT LIKE 'user_%@veilbot.com'
            UNION ALL
            SELECT email, 3 AS priority, expiry_at AS sort_date
            FROM v2ray_keys
            WHERE user_id = ? AND email IS NOT NULL AND email != '' AND email NOT LIKE 'user_%@veilbot.com'
        ) ORDER BY priority ASC, sort_date DESC LIMIT 1
        """,
        (user_id, user_id, user_id),
    )
    row = cursor.fetchone()
    return row[0] if row and row[0] else f"user_{user_id}@veilbot.com"


def fetch_server(cursor: sqlite3.Cursor, server_id: int) -> Tuple[int, str, str, str]:
    cursor.execute(
        """
        SELECT id, name, api_url, COALESCE(cert_sha256, '')
        FROM servers
        WHERE id = ? AND active = 1 AND protocol = 'outline'
        """,
        (server_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError(f"Сервер с id={server_id} не найден или не активен/не outline.")
    return row


def fetch_active_subscriptions(cursor: sqlite3.Cursor) -> list[Dict]:
    now = int(time.time())
    cursor.execute(
        """
        SELECT 
            s.id,
            s.user_id,
            s.expires_at,
            s.tariff_id,
            COALESCE(s.traffic_limit_mb, t.traffic_limit_mb, 0) AS traffic_limit_mb
        FROM subscriptions s
        LEFT JOIN tariffs t ON s.tariff_id = t.id
        WHERE s.is_active = 1
          AND s.expires_at > ?
        """,
        (now,),
    )
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


def has_active_key_on_server(
    cursor: sqlite3.Cursor, user_id: int, server_id: int, reference_ts: int
) -> Optional[int]:
    cursor.execute(
        """
        SELECT id
        FROM keys
        WHERE user_id = ?
          AND server_id = ?
          AND expiry_at > ?
        ORDER BY expiry_at DESC
        LIMIT 1
        """,
        (user_id, server_id, reference_ts),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def issue_key_for_subscription(
    cursor: sqlite3.Cursor,
    server: Tuple[int, str, str, str],
    subscription_data: Dict,
    dry_run: bool = False,
) -> bool:
    server_id, server_name, api_url, cert_sha256 = server
    user_id = subscription_data["user_id"]
    expires_at = subscription_data["expires_at"]
    tariff_id = subscription_data["tariff_id"]
    traffic_limit_mb = subscription_data["traffic_limit_mb"]

    now = int(time.time())

    if has_active_key_on_server(cursor, user_id, server_id, now):
        logger.info(
            "Пропускаю user_id=%s (активный ключ на сервере %s уже существует)", user_id, server_name
        )
        return False

    if dry_run:
        logger.info(
            "[DRY-RUN] Создал бы ключ для user_id=%s (sub_id=%s) с истечением %s",
            user_id,
            subscription_data["subscription_id"],
            expires_at,
        )
        return True

    key_data = create_key(api_url, cert_sha256)
    if not key_data:
        logger.error("Не удалось создать Outline ключ для user_id=%s", user_id)
        return False

    if not isinstance(key_data, dict) or "accessUrl" not in key_data or "id" not in key_data:
        logger.error("Некорректный ответ Outline при создании ключа user_id=%s: %s", user_id, key_data)
        return False

    email = resolve_user_email(cursor, user_id)

    cursor.execute(
        """
        INSERT INTO keys (
            server_id,
            user_id,
            access_url,
            expiry_at,
            traffic_limit_mb,
            notified,
            key_id,
            created_at,
            email,
            tariff_id,
            protocol
        ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 'outline')
        """,
        (
            server_id,
            user_id,
            key_data["accessUrl"],
            expires_at,
            traffic_limit_mb,
            key_data["id"],
            now,
            email,
            tariff_id,
        ),
    )
    logger.info(
        "Создан Outline ключ для user_id=%s (sub_id=%s) на сервере %s",
        user_id,
        subscription_data["subscription_id"],
        server_name,
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Выпустить Outline ключи на выбранном сервере для всех активных подписок"
    )
    parser.add_argument("--server-id", type=int, default=8, help="ID Outline сервера (по умолчанию 8)")
    parser.add_argument("--dry-run", action="store_true", help="Только показать, что будет сделано")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Ограничение количества подписок для обработки (0 = без ограничений)",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    try:
        server = fetch_server(cursor, args.server_id)
    except RuntimeError as exc:
        logger.error(exc)
        sys.exit(1)

    subscriptions = fetch_active_subscriptions(cursor)
    logger.info("Найдено активных подписок: %s", len(subscriptions))

    processed = 0
    created = 0
    skipped = 0
    failed = 0

    for subscription in subscriptions:
        if args.limit and processed >= args.limit:
            break
        processed += 1

        try:
            created_now = issue_key_for_subscription(cursor, server, subscription, args.dry_run)
            if created_now:
                created += 1
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Ошибка при обработке подписки %s (user_id=%s): %s",
                subscription["subscription_id"],
                subscription["user_id"],
                exc,
            )
            failed += 1

    if not args.dry_run:
        conn.commit()

    logger.info("Обработано подписок: %s", processed)
    logger.info("Создано ключей: %s", created)
    logger.info("Пропущено (уже есть ключ): %s", skipped)
    logger.info("Ошибок: %s", failed)

    if args.dry_run:
        logger.info("DRY-RUN завершен, изменений в базе нет.")

    conn.close()


if __name__ == "__main__":
    main()


