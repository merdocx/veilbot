#!/usr/bin/env python3
"""
Санация трафика подписок после оплаты.

Использовать, если после оплаты трафик в v2ray_keys/ subscriptions стал неконсистентным
(например, из‑за временных проблем API/мониторинга).

Алгоритм (для каждой найденной подписки):
1) "Сдвиг baseline" в БД: traffic_baseline_bytes += traffic_usage_bytes, traffic_usage_bytes = 0 (для всех ключей подписки)
2) Сброс кэша/флагов в subscriptions, last_traffic_reset_at = paid_at (последняя оплата)
3) Пересчёт фактического usage по ключам из API как max(0, api_total - baseline) и запись в v2ray_keys
4) Запись суммы в subscriptions.traffic_usage_bytes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DATABASE_PATH") or "/root/veilbot/vpn.db"


class _DbCursorCtx:
    def __init__(self, *, commit: bool):
        self._commit = commit
        self._conn: sqlite3.Connection | None = None
        self._cursor: sqlite3.Cursor | None = None

    def __enter__(self) -> sqlite3.Cursor:
        self._conn = sqlite3.connect(DB_PATH, timeout=30)
        self._cursor = self._conn.cursor()
        return self._cursor

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._conn:
            return
        try:
            if exc is None and self._commit:
                self._conn.commit()
            elif exc is not None:
                self._conn.rollback()
        finally:
            try:
                self._conn.close()
            except Exception:
                pass


def _db_cursor(*, commit: bool = False) -> _DbCursorCtx:
    return _DbCursorCtx(commit=commit)


@dataclass(frozen=True)
class KeyRow:
    db_key_id: int
    server_id: int
    v2ray_uuid: str
    api_url: str
    api_key: str
    baseline_bytes: int
    stored_usage_bytes: int


def _read_candidate_subscription_ids(*, lookback_hours: int, min_keys_usage_gb: float) -> list[int]:
    now = int(time.time())
    lookback_ts = now - int(lookback_hours * 3600)
    min_bytes = int(min_keys_usage_gb * 1024 * 1024 * 1024)
    with _db_cursor() as cursor:
        cursor.execute(
            """
            WITH recent AS (
              SELECT subscription_id, MAX(paid_at) AS last_paid_at
              FROM payments
              WHERE status='completed' AND subscription_id IS NOT NULL AND paid_at IS NOT NULL
                AND paid_at >= ?
              GROUP BY subscription_id
            )
            SELECT s.id
            FROM subscriptions s
            JOIN recent r ON r.subscription_id = s.id
            LEFT JOIN v2ray_keys k ON k.subscription_id = s.id
            GROUP BY s.id
            HAVING COALESCE(SUM(k.traffic_usage_bytes),0) >= ?
            ORDER BY COALESCE(SUM(k.traffic_usage_bytes),0) DESC
            """,
            (lookback_ts, min_bytes),
        )
        return [int(r[0]) for r in cursor.fetchall()]


def _read_last_paid_at(subscription_id: int) -> Optional[int]:
    with _db_cursor() as cursor:
        cursor.execute(
            """
            SELECT MAX(paid_at)
            FROM payments
            WHERE subscription_id = ?
              AND status = 'completed'
              AND paid_at IS NOT NULL
            """,
            (subscription_id,),
        )
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else None


def _read_keys(subscription_id: int) -> list[KeyRow]:
    with _db_cursor() as cursor:
        cursor.execute(
            """
            SELECT
              k.id,
              k.server_id,
              k.v2ray_uuid,
              COALESCE(s.api_url, ''),
              COALESCE(s.api_key, ''),
              COALESCE(k.traffic_baseline_bytes, 0),
              COALESCE(k.traffic_usage_bytes, 0)
            FROM v2ray_keys k
            JOIN servers s ON s.id = k.server_id
            WHERE k.subscription_id = ?
            ORDER BY k.id
            """,
            (subscription_id,),
        )
        out: list[KeyRow] = []
        for r in cursor.fetchall():
            out.append(
                KeyRow(
                    db_key_id=int(r[0]),
                    server_id=int(r[1]),
                    v2ray_uuid=str(r[2] or "").strip(),
                    api_url=str(r[3] or "").strip(),
                    api_key=str(r[4] or "").strip(),
                    baseline_bytes=int(r[5] or 0),
                    stored_usage_bytes=int(r[6] or 0),
                )
            )
        return out


def _baseline_shift_and_zero(subscription_id: int, *, now_ts: int, reset_ts: int) -> None:
    with _db_cursor(commit=True) as cursor:
        cursor.execute(
            """
            UPDATE v2ray_keys
            SET traffic_baseline_bytes = COALESCE(traffic_baseline_bytes, 0) + COALESCE(traffic_usage_bytes, 0),
                traffic_usage_bytes = 0
            WHERE subscription_id = ?
            """,
            (subscription_id,),
        )
    with _db_cursor(commit=True) as cursor:
        cursor.execute(
            """
            UPDATE subscriptions
            SET is_active = 1,
                traffic_usage_bytes = 0,
                traffic_over_limit_at = NULL,
                traffic_over_limit_notified = 0,
                last_updated_at = ?,
                last_traffic_reset_at = ?
            WHERE id = ?
            """,
            (now_ts, reset_ts, subscription_id),
        )


async def _fetch_effective_usage_bytes(key: KeyRow) -> Optional[int]:
    if not key.v2ray_uuid or not key.api_url or not key.api_key:
        return None
    try:
        api_url = key.api_url.rstrip("/")

        def _curl_json(path: str) -> dict:
            raw = subprocess.check_output(
                [
                    "curl",
                    "-skS",
                    "-H",
                    f"Authorization: Bearer {key.api_key}",
                    f"{api_url}{path}",
                ],
                text=True,
            )
            return json.loads(raw)

        key_info = _curl_json(f"/keys/{key.v2ray_uuid}")
        api_key_id = key_info.get("key_id") or key_info.get("id") or key_info.get("uuid")
        if not api_key_id:
            return None

        stats = _curl_json(f"/keys/{api_key_id}/traffic")
        total_bytes = stats.get("total") or stats.get("total_bytes")
        if not isinstance(total_bytes, (int, float)) or total_bytes < 0:
            return None
        effective = max(0, int(total_bytes) - int(key.baseline_bytes))
        return int(effective)
    except Exception as e:
        logger.warning(
            "[fix_traffic] Failed to fetch traffic for key %s (server_id=%s): %s",
            key.db_key_id,
            key.server_id,
            e,
        )
        return None


def _write_key_usages(updates: list[tuple[int, int]]) -> None:
    if not updates:
        return
    with _db_cursor(commit=True) as cursor:
        cursor.executemany(
            "UPDATE v2ray_keys SET traffic_usage_bytes = ? WHERE id = ?",
            updates,
        )


def _recalc_and_write_subscription_cache(subscription_id: int, *, now_ts: int) -> int:
    with _db_cursor() as cursor:
        cursor.execute(
            "SELECT COALESCE(SUM(traffic_usage_bytes), 0) FROM v2ray_keys WHERE subscription_id = ?",
            (subscription_id,),
        )
        total = int((cursor.fetchone() or (0,))[0] or 0)
    with _db_cursor(commit=True) as cursor:
        cursor.execute(
            "UPDATE subscriptions SET traffic_usage_bytes = ?, last_updated_at = ? WHERE id = ?",
            (total, now_ts, subscription_id),
        )
    return total


async def fix_subscription(subscription_id: int, *, dry_run: bool) -> None:
    paid_at = _read_last_paid_at(subscription_id)
    if not paid_at:
        logger.info("[fix_traffic] subscription %s: no paid_at found, skipping", subscription_id)
        return

    keys = _read_keys(subscription_id)
    if not keys:
        logger.info("[fix_traffic] subscription %s: no v2ray_keys, skipping", subscription_id)
        return

    now_ts = int(time.time())
    reset_ts = int(paid_at)

    logger.info(
        "[fix_traffic] subscription %s: paid_at=%s, keys=%s, dry_run=%s",
        subscription_id,
        reset_ts,
        len(keys),
        dry_run,
    )

    if dry_run:
        return

    _baseline_shift_and_zero(subscription_id, now_ts=now_ts, reset_ts=reset_ts)

    # Перечитать baseline после сдвига
    keys_after = _read_keys(subscription_id)
    tasks = [_fetch_effective_usage_bytes(k) for k in keys_after]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    updates: list[tuple[int, int]] = []
    for k, res in zip(keys_after, results):
        if isinstance(res, Exception):
            continue
        if res is None:
            # Если API недоступен, оставляем 0 — но НЕ ухудшаем baseline.
            continue
        updates.append((int(res), int(k.db_key_id)))

    _write_key_usages(updates)
    total = _recalc_and_write_subscription_cache(subscription_id, now_ts=now_ts)
    logger.info("[fix_traffic] subscription %s: cache traffic_usage_bytes=%s", subscription_id, total)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Fix subscription traffic after recent payments")
    parser.add_argument("--subscription-id", type=int, default=None, help="Fix only one subscription id")
    parser.add_argument("--lookback-hours", type=int, default=48, help="Lookback window for recent payments")
    parser.add_argument("--min-keys-usage-gb", type=float, default=50.0, help="Min sum(v2ray_keys usage) to consider")
    parser.add_argument("--dry-run", action="store_true", help="Do not change DB (only show what would be fixed)")
    args = parser.parse_args()

    if args.subscription_id is not None:
        subs = [int(args.subscription_id)]
    else:
        subs = _read_candidate_subscription_ids(
            lookback_hours=int(args.lookback_hours),
            min_keys_usage_gb=float(args.min_keys_usage_gb),
        )

    if not subs:
        logger.info("[fix_traffic] No candidates found")
        return

    logger.info("[fix_traffic] Candidates: %s", subs)
    for sid in subs:
        await fix_subscription(int(sid), dry_run=bool(args.dry_run))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())

