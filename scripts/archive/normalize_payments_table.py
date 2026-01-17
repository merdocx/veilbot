#!/usr/bin/env python3
"""
Normalize payments data to a single canonical schema.

What it does:
1) Detect and fix very old "swapped fields" rows (legacy corruption) where:
   - payments.user_id contains a YooKassa-like payment_id (TEXT with '-')
   - payments.tariff_id actually contains user_id
   - payments.payment_id actually contains tariff_id
2) (Optional) rebuild the payments table to canonical column order using a
   CREATE TABLE ... AS SELECT ... approach (keeps column names consistent).

This script is intended to be safe and idempotent:
- It only updates rows that match a conservative corruption predicate.
- It can run in --dry-run mode first.
"""

from __future__ import annotations

import argparse
import sqlite3
from typing import Dict, List, Tuple


CANON_COLS: List[Tuple[str, str]] = [
    ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
    ("user_id", "INTEGER NOT NULL"),
    ("tariff_id", "INTEGER"),
    ("payment_id", "TEXT"),
    ("status", "TEXT"),
    ("email", "TEXT"),
    ("revoked", "INTEGER DEFAULT 0"),
    ("protocol", "TEXT"),
    ("amount", "INTEGER"),
    ("created_at", "INTEGER"),
    ("country", "TEXT"),
    ("currency", "TEXT"),
    ("provider", "TEXT"),
    ("method", "TEXT"),
    ("description", "TEXT"),
    ("updated_at", "INTEGER"),
    ("paid_at", "INTEGER"),
    ("metadata", "TEXT"),
    ("crypto_invoice_id", "TEXT"),
    ("crypto_amount", "REAL"),
    ("crypto_currency", "TEXT"),
    ("crypto_network", "TEXT"),
    ("crypto_tx_hash", "TEXT"),
    ("subscription_id", "INTEGER"),
]


def _get_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return [r[1] for r in cur.fetchall()]


def _fix_swapped_rows(conn: sqlite3.Connection, dry_run: bool) -> int:
    """
    Fix legacy corrupted rows where columns were written with swapped semantics:
      user_id <- payment_id (uuid-like text with '-')
      tariff_id <- user_id (int)
      payment_id <- tariff_id (small int)
    """
    cur = conn.cursor()

    # Conservative predicate:
    # - user_id is TEXT and looks like a YooKassa UUID (has '-')
    # - tariff_id looks like a Telegram user_id (integer-ish, > 1000)
    # - payment_id looks like a small integer (tariff id, 1..999)
    #
    # Note: SQLite is dynamically typed; typeof() checks what is stored.
    cur.execute(
        """
        SELECT id, user_id, tariff_id, payment_id
        FROM payments
        WHERE typeof(user_id) = 'text'
          AND user_id LIKE '%-%'
          AND (
                (typeof(tariff_id) = 'integer' AND tariff_id > 1000)
             OR (typeof(tariff_id) = 'text' AND tariff_id GLOB '[0-9]*' AND CAST(tariff_id AS INTEGER) > 1000)
          )
          AND (
                (typeof(payment_id) = 'integer' AND payment_id BETWEEN 1 AND 999)
             OR (typeof(payment_id) = 'text' AND payment_id GLOB '[0-9]*' AND CAST(payment_id AS INTEGER) BETWEEN 1 AND 999)
          )
        """
    )
    rows = cur.fetchall()
    if not rows:
        return 0

    if dry_run:
        return len(rows)

    for row_id, user_id_text, tariff_id_user, payment_id_tariff in rows:
        # Normalize to canonical meaning:
        # payment_id := old user_id_text
        # user_id := old tariff_id_user
        # tariff_id := old payment_id_tariff
        cur.execute(
            """
            UPDATE payments
            SET payment_id = ?,
                user_id = CAST(? AS INTEGER),
                tariff_id = CAST(? AS INTEGER)
            WHERE id = ?
            """,
            (str(user_id_text), str(tariff_id_user), str(payment_id_tariff), row_id),
        )

    conn.commit()
    return len(rows)


def _rebuild_table(conn: sqlite3.Connection, dry_run: bool) -> None:
    cols = _get_columns(conn, "payments")
    missing = [c for c, _decl in CANON_COLS if c not in cols]
    if missing:
        raise RuntimeError(f"payments is missing columns: {missing}. Run migrations first.")

    if dry_run:
        return

    cur = conn.cursor()
    cur.execute("BEGIN")
    try:
        cur.execute("ALTER TABLE payments RENAME TO payments_old")

        create_sql = "CREATE TABLE payments (" + ", ".join([f"{n} {decl}" for n, decl in CANON_COLS]) + ")"
        cur.execute(create_sql)

        col_names = [n for n, _decl in CANON_COLS]
        select_sql = "INSERT INTO payments (" + ", ".join(col_names) + ") SELECT " + ", ".join(col_names) + " FROM payments_old"
        cur.execute(select_sql)

        cur.execute("DROP TABLE payments_old")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Path to sqlite db file")
    parser.add_argument("--dry-run", action="store_true", help="Do not modify DB, only report actions")
    parser.add_argument(
        "--rebuild-table",
        action="store_true",
        help="Rebuild payments table into canonical definition (keeps same columns; useful after many ALTERs).",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")

        swapped = _fix_swapped_rows(conn, dry_run=args.dry_run)
        print(f"legacy_swapped_rows_to_fix: {swapped}{' (dry-run)' if args.dry_run else ''}")

        if args.rebuild_table:
            _rebuild_table(conn, dry_run=args.dry_run)
            print(f"rebuild_table: {'skipped (dry-run)' if args.dry_run else 'done'}")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

