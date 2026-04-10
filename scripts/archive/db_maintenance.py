#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
import shutil
import sqlite3
from datetime import datetime, timedelta


def get_db_path() -> str:
    try:
        sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from app.settings import settings  # type: ignore
        return settings.DATABASE_PATH
    except Exception:
        return os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vpn.db"))


def apply_pragmas(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA busy_timeout=5000")
    except Exception:
        pass


def optimize_and_vacuum(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        apply_pragmas(conn)
        # Optimize query planner stats
        conn.execute("PRAGMA optimize")
        conn.commit()
    finally:
        conn.close()

    # VACUUM should be executed without open connection
    conn2 = sqlite3.connect(db_path)
    try:
        conn2.execute("VACUUM")
        conn2.commit()
    finally:
        conn2.close()


def backup_db(db_path: str, backups_dir: str, keep_days: int = 7) -> str:
    os.makedirs(backups_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.basename(db_path)
    backup_path = os.path.join(backups_dir, f"{base}.{ts}.sqlite3")
    shutil.copy2(db_path, backup_path)

    # Rotate old backups
    cutoff = datetime.now() - timedelta(days=keep_days)
    for name in os.listdir(backups_dir):
        full = os.path.join(backups_dir, name)
        try:
            if os.path.isfile(full):
                mtime = datetime.fromtimestamp(os.path.getmtime(full))
                if mtime < cutoff:
                    os.remove(full)
        except Exception:
            # Ignore rotation errors
            pass
    return backup_path


def main() -> int:
    db_path = get_db_path()
    backups_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db_backups")
    start = time.time()
    import logging
    logging.info(f"[DB MAINT] Starting maintenance for {db_path}")
    optimize_and_vacuum(db_path)
    backup_path = backup_db(db_path, backups_dir)
    elapsed = (time.time() - start) * 1000
    import logging
    logging.info(f"[DB MAINT] Completed: backup={backup_path}, elapsed_ms={int(elapsed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


