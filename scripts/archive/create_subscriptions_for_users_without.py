#!/usr/bin/env python3
"""
Создает подписки для пользователей, у которых есть ключи, но нет подписок.

Логика:
    1. Ищем всех пользователей, у которых есть outline/v2ray ключи, но отсутствуют записи в subscriptions.
    2. Для каждого пользователя определяем тариф/expiry на основе ключей.
    3. Создаем запись подписки, помечая purchase_notification_sent=1 (уведомления не отправляем).
    4. Привязываем все ключи пользователя к новой подписке.

Скрипт поддерживает --dry-run для проверки без изменений.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DATABASE_PATH


@dataclass
class KeyInfo:
    source: str
    tariff_id: int
    expiry_at: int
    created_at: Optional[int]


def fetch_tariff_data(cursor: sqlite3.Cursor, tariff_id: int) -> Tuple[int, int]:
    """
    Возвращает (duration_sec, traffic_limit_mb) для тарифа.
    """
    cursor.execute(
        "SELECT duration_sec, COALESCE(traffic_limit_mb, 0) FROM tariffs WHERE id = ?",
        (tariff_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError(f"Tariff {tariff_id} not found")
    return row[0] or 0, row[1] or 0


def pick_user_key(cursor: sqlite3.Cursor, user_id: int) -> Optional[KeyInfo]:
    """
    Возвращает ключ с максимальным expiry_at (outline или v2ray).
    """
    cursor.execute(
        """
        WITH union_keys AS (
            SELECT 'v2ray' AS source, k.tariff_id, COALESCE(sub.expires_at, 0) as expiry_at, k.created_at
            FROM v2ray_keys k
            LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
            WHERE k.user_id = ?
            UNION ALL
            SELECT 'outline' AS source, k.tariff_id, COALESCE(sub.expires_at, 0) as expiry_at, k.created_at
            FROM keys k
            LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
            WHERE k.user_id = ?
        )
        SELECT source, tariff_id, expiry_at, created_at
        FROM union_keys
        WHERE expiry_at IS NOT NULL AND expiry_at > 0
        ORDER BY expiry_at DESC
        LIMIT 1
        """,
        (user_id, user_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return KeyInfo(
        source=row[0],
        tariff_id=row[1],
        expiry_at=row[2],
        created_at=row[3],
    )


def build_user_list(cursor: sqlite3.Cursor) -> list[int]:
    cursor.execute(
        """
        WITH key_users AS (
            SELECT DISTINCT user_id FROM v2ray_keys WHERE user_id IS NOT NULL
            UNION
            SELECT DISTINCT user_id FROM keys WHERE user_id IS NOT NULL
        )
        SELECT ku.user_id
        FROM key_users ku
        WHERE NOT EXISTS (
            SELECT 1 FROM subscriptions s WHERE s.user_id = ku.user_id
        )
        ORDER BY ku.user_id
        """
    )
    return [row[0] for row in cursor.fetchall()]


def create_subscription_for_user(
    cursor: sqlite3.Cursor,
    user_id: int,
    key_info: KeyInfo,
    tariff_cache: Dict[int, Tuple[int, int]],
) -> int:
    """
    Создает подписку для пользователя и возвращает subscription_id.
    """
    if key_info.tariff_id not in tariff_cache:
        tariff_cache[key_info.tariff_id] = fetch_tariff_data(cursor, key_info.tariff_id)
    duration_sec, traffic_limit_mb = tariff_cache[key_info.tariff_id]

    now = int(time.time())
    expires_at = key_info.expiry_at
    if not expires_at:
        raise RuntimeError(f"User {user_id} has key without expiry_at")

    # ВАЛИДАЦИЯ: Проверяем, что дата истечения разумная
    MAX_REASONABLE_EXPIRY = now + (10 * 365 * 24 * 3600)  # 10 лет
    MAX_DURATION = 365 * 24 * 3600  # 1 год - максимальная разумная длительность для одного тарифа
    
    # Если дата слишком далеко в будущем, используем now + duration_sec
    if expires_at > MAX_REASONABLE_EXPIRY:
        print(f"WARNING: User {user_id} has key with unreasonable expiry date: {expires_at}. Using now + duration instead.")
        expires_at = now + duration_sec
    
    # Если длительность подписки (expires_at - created_at) слишком большая, используем now + duration_sec
    approx_created = (expires_at - duration_sec) if duration_sec > 0 else now
    if approx_created <= 0 or approx_created > now:
        approx_created = now
    
    # Проверяем, что длительность не превышает разумный максимум
    actual_duration = expires_at - approx_created
    if actual_duration > MAX_DURATION:
        print(f"WARNING: User {user_id} subscription duration too long: {actual_duration}s. Using now + duration instead.")
        expires_at = now + duration_sec
        approx_created = now

    subscription_token = str(uuid.uuid4())
    is_active = 1 if expires_at > now else 0

    cursor.execute(
        """
        INSERT INTO subscriptions (
            user_id,
            subscription_token,
            created_at,
            expires_at,
            tariff_id,
            is_active,
            last_updated_at,
            notified,
            traffic_usage_bytes,
            traffic_over_limit_at,
            traffic_over_limit_notified,
            purchase_notification_sent,
            traffic_limit_mb
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, NULL, 0, 1, ?)
        """,
        (
            user_id,
            subscription_token,
            approx_created,
            expires_at,
            key_info.tariff_id,
            is_active,
            now,
            traffic_limit_mb,
        ),
    )
    return cursor.lastrowid


def attach_keys(cursor: sqlite3.Cursor, user_id: int, subscription_id: int) -> Tuple[int, int]:
    """
    Привязывает outline и v2ray ключи пользователя к подписке.
    Возвращает количество обновленных (outline_count, v2ray_count).
    """
    cursor.execute(
        """
        UPDATE keys
        SET subscription_id = ?
        WHERE user_id = ?
          AND (subscription_id IS NULL OR subscription_id != ?)
        """,
        (subscription_id, user_id, subscription_id),
    )
    outline_updated = cursor.rowcount

    cursor.execute(
        """
        UPDATE v2ray_keys
        SET subscription_id = ?
        WHERE user_id = ?
          AND (subscription_id IS NULL OR subscription_id != ?)
        """,
        (subscription_id, user_id, subscription_id),
    )
    v2ray_updated = cursor.rowcount
    return outline_updated, v2ray_updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Создать подписки для пользователей без них и привязать их ключи."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Показывать, что будет сделано, без записи в БД.",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        users = build_user_list(cursor)
        if not users:
            print("✅ Все пользователи с ключами уже имеют подписки.")
            return

        print(f"Найдено {len(users)} пользователей без подписок.")
        tariff_cache: Dict[int, Tuple[int, int]] = {}
        total_outline = 0
        total_v2ray = 0

        for user_id in users:
            key_info = pick_user_key(cursor, user_id)
            if not key_info:
                print(f"[user={user_id}] Пропуск — нет активных ключей.")
                continue

            subscription_id = create_subscription_for_user(cursor, user_id, key_info, tariff_cache)
            outline_count, v2ray_count = attach_keys(cursor, user_id, subscription_id)
            total_outline += outline_count
            total_v2ray += v2ray_count
            print(
                f"[user={user_id}] subscription_id={subscription_id} tariff={key_info.tariff_id} "
                f"expires={key_info.expiry_at} outline_keys={outline_count} v2ray_keys={v2ray_count}"
            )

        if args.dry_run:
            conn.rollback()
            print(
                f"[DRY-RUN] Изменения отменены. Обновлено пользователей: {len(users)}, "
                f"outline ключей: {total_outline}, v2ray ключей: {total_v2ray}"
            )
        else:
            conn.commit()
            print(
                f"Готово. Создано подписок: {len(users)}, outline ключей привязано: {total_outline}, "
                f"v2ray ключей привязано: {total_v2ray}"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()


