from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
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


@contextmanager
def get_db_cursor(commit: bool = False, db_path: Optional[str] = None):
    """
    Context manager для получения курсора БД (совместимость с utils.py).
    
    Это упрощенная версия без connection pool для постепенной миграции.
    Для новых файлов рекомендуется использовать open_connection() напрямую.
    
    Args:
        commit: Автоматически коммитить изменения при выходе
        db_path: Путь к БД (по умолчанию из DATABASE_PATH или vpn.db)
    
    Yields:
        sqlite3.Cursor: Курсор для работы с БД
    """
    conn = open_connection(db_path)
    conn.row_factory = sqlite3.Row  # Для совместимости с utils.py
    cursor = conn.cursor()
    try:
        yield cursor
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


