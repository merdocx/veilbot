from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from typing import Optional

import aiosqlite


def apply_pragmas_sync(conn: sqlite3.Connection) -> None:
    """Apply recommended SQLite PRAGMAs for this project.

    - WAL journal mode (persistent setting per database file)
    - NORMAL synchronous (balanced durability/perf for WAL)
    - foreign_keys ON (per-connection)
    - busy_timeout 15000 ms
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
        conn.execute("PRAGMA busy_timeout=15000")
    except Exception:
        pass


def open_connection(db_path: Optional[str]) -> sqlite3.Connection:
    path = db_path or os.getenv("DATABASE_PATH", "vpn.db")
    conn = sqlite3.connect(path)
    apply_pragmas_sync(conn)
    return conn


async def apply_pragmas_async(conn: aiosqlite.Connection) -> None:
    try:
        await conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    try:
        await conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    try:
        await conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        pass
    try:
        await conn.execute("PRAGMA busy_timeout=15000")
    except Exception:
        pass


@asynccontextmanager
async def open_async_connection(db_path: Optional[str]) -> aiosqlite.Connection:
    path = db_path or os.getenv("DATABASE_PATH", "vpn.db")
    conn = await aiosqlite.connect(path)
    await apply_pragmas_async(conn)
    try:
        yield conn
    finally:
        await conn.close()


