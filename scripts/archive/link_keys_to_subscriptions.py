#!/usr/bin/env python3
"""
Привязка всех ключей пользователя к одной подписке.

Логика:
    1. Для каждого пользователя находим наиболее "свежую" подписку
       (по expires_at, затем по created_at, затем по id).
    2. Всем ключам (outline и v2ray) этого пользователя присваиваем найденный subscription_id.
    3. Формируем статистику по обновлённым ключам.

Скрипт без параметров работает в режиме записи.
Для сухого прогона используйте --dry-run.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Dict, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DATABASE_PATH


@dataclass(frozen=True)
class SubscriptionInfo:
    subscription_id: int
    expires_at: int
    created_at: int


def build_user_subscription_map(
    cursor: sqlite3.Cursor,
) -> Dict[int, SubscriptionInfo]:
    """
    Возвращает mapping user_id -> наиболее "свежая" подписка.
    Приоритет: expires_at (по убыванию), created_at (по убыванию), id (по убыванию).
    """
    query = """
        SELECT id, user_id, expires_at, created_at
        FROM subscriptions
        ORDER BY
            COALESCE(expires_at, 0) DESC,
            COALESCE(created_at, 0) DESC,
            id DESC
    """
    cursor.execute(query)

    mapping: Dict[int, SubscriptionInfo] = {}
    for sub_id, user_id, expires_at, created_at in cursor.fetchall():
        if user_id in mapping:
            continue
        mapping[user_id] = SubscriptionInfo(
            subscription_id=sub_id,
            expires_at=expires_at or 0,
            created_at=created_at or 0,
        )
    return mapping


def update_keys_for_user(
    cursor: sqlite3.Cursor,
    user_id: int,
    subscription_id: int,
) -> Tuple[int, int]:
    """
    Обновляет subscription_id для всех ключей пользователя.

    Возвращает: (outline_updated, v2ray_updated)
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


def link_keys(dry_run: bool = False) -> None:
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        mapping = build_user_subscription_map(cursor)
        if not mapping:
            print("Подписок не найдено — делать нечего.")
            return

        total_outline = 0
        total_v2ray = 0
        processed_users = 0

        now = int(time.time())

        for user_id, sub_info in mapping.items():
            outline_count, v2ray_count = update_keys_for_user(
                cursor, user_id, sub_info.subscription_id
            )

            if outline_count or v2ray_count:
                processed_users += 1
                total_outline += outline_count
                total_v2ray += v2ray_count
                print(
                    f"[user={user_id}] -> subscription {sub_info.subscription_id} "
                    f"(expires={sub_info.expires_at}) "
                    f"outline={outline_count}, v2ray={v2ray_count}"
                )

        if dry_run:
            conn.rollback()
            print(
                f"[DRY-RUN] Изменений не сохранено. "
                f"Пользователей с обновлениями: {processed_users}, "
                f"outline ключей: {total_outline}, v2ray ключей: {total_v2ray}"
            )
        else:
            conn.commit()
            print(
                f"Готово. Пользователей с обновлениями: {processed_users}, "
                f"outline ключей обновлено: {total_outline}, "
                f"v2ray ключей обновлено: {total_v2ray}"
            )
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Связать все ключи пользователя с одной подпиской."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, что будет сделано, без сохранения изменений.",
    )
    args = parser.parse_args()
    link_keys(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

