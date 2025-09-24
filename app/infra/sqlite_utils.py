from __future__ import annotations

import os
import sqlite3
from typing import Optional


def apply_pragmas_sync(conn: sqlite3.Connection) -> None:
    """Apply recommended SQLite PRAGMAs for this project.

    - WAL journal mode (persistent setting per database file)
    - NORMAL synchronous (balanced durability/perf for WAL)
    - foreign_keys ON (per-connection)
    - busy_timeout 5000 ms
    """
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


def open_connection(db_path: Optional[str]) -> sqlite3.Connection:
    path = db_path or os.getenv("DATABASE_PATH", "vpn.db")
    conn = sqlite3.connect(path)
    apply_pragmas_sync(conn)
    return conn


