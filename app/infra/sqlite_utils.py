from __future__ import annotations

import os
import sqlite3
import time
import logging
from contextlib import asynccontextmanager, contextmanager
from typing import Optional, Callable, TypeVar, Any

import aiosqlite

logger = logging.getLogger(__name__)

T = TypeVar('T')


def apply_pragmas_sync(conn: sqlite3.Connection) -> None:
    """Apply recommended SQLite PRAGMAs for this project.

    - WAL journal mode (persistent setting per database file)
    - NORMAL synchronous (balanced durability/perf for WAL)
    - foreign_keys ON (per-connection)
    - busy_timeout 30000 ms (increased from 15000 to reduce lock errors)
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
        conn.execute("PRAGMA busy_timeout=30000")
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
        await conn.execute("PRAGMA busy_timeout=30000")
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


def retry_db_operation(
    operation: Callable[[], T],
    max_attempts: int = 3,
    initial_delay: float = 0.1,
    backoff_multiplier: float = 2.0,
    db_path: Optional[str] = None,
) -> T:
    """
    Выполняет операцию с БД с автоматическим retry при ошибке "database is locked".
    
    Args:
        operation: Функция, которая выполняет операцию с БД (должна использовать get_db_cursor)
        max_attempts: Максимальное количество попыток (по умолчанию 3)
        initial_delay: Начальная задержка перед повторной попыткой в секундах (по умолчанию 0.1)
        backoff_multiplier: Множитель для экспоненциального backoff (по умолчанию 2.0)
        db_path: Путь к БД (передается в operation, если нужно)
    
    Returns:
        Результат выполнения operation
    
    Raises:
        sqlite3.OperationalError: Если все попытки исчерпаны
        Любое другое исключение, которое не является "database is locked"
    """
    last_exception = None
    delay = initial_delay
    
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except sqlite3.OperationalError as e:
            error_msg = str(e).lower()
            if "database is locked" in error_msg or "database is locked" in str(e):
                last_exception = e
                if attempt < max_attempts:
                    logger.warning(
                        f"[DB RETRY] Attempt {attempt}/{max_attempts} failed: database is locked. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    time.sleep(delay)
                    delay *= backoff_multiplier
                else:
                    logger.error(
                        f"[DB RETRY] All {max_attempts} attempts failed: database is locked"
                    )
            else:
                # Другие OperationalError не обрабатываем
                raise
        except Exception as e:
            # Другие исключения не обрабатываем
            raise
    
    # Если дошли сюда, все попытки исчерпаны
    if last_exception:
        raise last_exception
    raise RuntimeError("Unexpected error in retry_db_operation")


