#!/usr/bin/env python3
"""
Разово выставить subscriptions.traffic_baseline_bytes = SUM(panel_total_bytes_observed)
по ключам подписки (used периода → 0 относительно текущих тоталов).

Также сбрасывает traffic_over_limit_at / traffic_over_limit_notified для затронутых строк
(как при продлении), чтобы не висели старые флаги перелимита.

Не трогает панели. Использовать в maintenance window.

Usage:
  python3 scripts/snap_subscription_traffic_baselines.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)

from app.settings import settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Snap subscription traffic baselines to sum(observed).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned updates without writing.",
    )
    args = parser.parse_args()

    db_path = settings.DATABASE_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.id,
               COALESCE(SUM(COALESCE(k.panel_total_bytes_observed, 0)), 0) AS sum_observed,
               COALESCE(s.traffic_baseline_bytes, 0) AS old_b,
               s.traffic_over_limit_at,
               COALESCE(s.traffic_over_limit_notified, 0) AS traffic_over_limit_notified
        FROM subscriptions s
        LEFT JOIN v2ray_keys k ON k.subscription_id = s.id
        GROUP BY s.id
        """
    )
    rows = cur.fetchall()
    baseline_updates: list[tuple[int, int]] = []
    stale_flag_ids: list[int] = []
    for r in rows:
        sid = int(r["id"])
        new_b = int(r["sum_observed"] or 0)
        old_b = int(r["old_b"] or 0)
        if new_b != old_b:
            baseline_updates.append((new_b, sid))
        elif r["traffic_over_limit_at"] is not None or int(r["traffic_over_limit_notified"] or 0) != 0:
            # B уже = S, used = 0, но остались флаги перелимита — только сбросить их и кэш
            stale_flag_ids.append(sid)

    print(f"Подписок с изменением baseline: {len(baseline_updates)} из {len(rows)}")
    print(f"Подписок только со сбросом флагов перелимита (baseline без изменений): {len(stale_flag_ids)}")

    if args.dry_run:
        for new_b, sid in baseline_updates[:50]:
            print(f"  subscription_id={sid} traffic_baseline_bytes -> {new_b}")
        if len(baseline_updates) > 50:
            print(f"  ... и ещё {len(baseline_updates) - 50}")
        for sid in stale_flag_ids[:50]:
            print(f"  subscription_id={sid} сброс только traffic_over_limit_* (baseline уже = sum)")
        if len(stale_flag_ids) > 50:
            print(f"  ... и ещё {len(stale_flag_ids) - 50}")
        conn.close()
        return 0

    now = __import__("time").time()
    now_ts = int(now)
    for new_b, sid in baseline_updates:
        cur.execute(
            """
            UPDATE subscriptions
            SET traffic_baseline_bytes = ?,
                traffic_usage_bytes = 0,
                traffic_over_limit_at = NULL,
                traffic_over_limit_notified = 0,
                last_updated_at = ?
            WHERE id = ?
            """,
            (new_b, now_ts, sid),
        )
    for sid in stale_flag_ids:
        cur.execute(
            """
            UPDATE subscriptions
            SET traffic_usage_bytes = 0,
                traffic_over_limit_at = NULL,
                traffic_over_limit_notified = 0,
                last_updated_at = ?
            WHERE id = ?
            """,
            (now_ts, sid),
        )
    conn.commit()
    conn.close()
    print(
        f"Обновлено подписок: baseline={len(baseline_updates)}, "
        f"только флаги перелимита={len(stale_flag_ids)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
