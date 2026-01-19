from __future__ import annotations

import os
import sqlite3
import time
import asyncio
import logging
from contextlib import asynccontextmanager, contextmanager
from typing import Optional, Callable, TypeVar, Any, Coroutine, Dict

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
    # IMPORTANT: Increase SQLite connect timeout to reduce "database is locked" errors under concurrent writers.
    # This works together with PRAGMA busy_timeout (applied below) and WAL mode.
    conn = sqlite3.connect(path, timeout=30)
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
    # IMPORTANT: Increase SQLite connect timeout to reduce "database is locked" errors under concurrent writers.
    # This works together with PRAGMA busy_timeout (applied below) and WAL mode.
    conn = await aiosqlite.connect(path, timeout=30)
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
    operation_name: Optional[str] = None,
    operation_context: Optional[Dict[str, Any]] = None,
) -> T:
    """
    Выполняет операцию с БД с автоматическим retry при ошибке "database is locked".
    
    Args:
        operation: Функция, которая выполняет операцию с БД (должна использовать get_db_cursor)
        max_attempts: Максимальное количество попыток (по умолчанию 3)
        initial_delay: Начальная задержка перед повторной попыткой в секундах (по умолчанию 0.1)
        backoff_multiplier: Множитель для экспоненциального backoff (по умолчанию 2.0)
        db_path: Путь к БД (передается в operation, если нужно)
        operation_name: Название операции для логирования (например, "update_user")
        operation_context: Дополнительный контекст для логирования (например, {"user_id": 123})
    
    Returns:
        Результат выполнения operation
    
    Raises:
        sqlite3.OperationalError: Если все попытки исчерпаны
        Любое другое исключение, которое не является "database is locked"
    """
    last_exception = None
    delay = initial_delay
    context_str = ""
    if operation_context:
        context_str = f" Context: {operation_context}"
    
    op_name = operation_name or "db_operation"
    
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except sqlite3.OperationalError as e:
            error_msg = str(e).lower()
            if "database is locked" in error_msg or "database is locked" in str(e):
                last_exception = e
                if attempt < max_attempts:
                    logger.warning(
                        f"[DB RETRY] {op_name}: Attempt {attempt}/{max_attempts} failed: database is locked.{context_str} "
                        f"Retrying in {delay:.2f}s..."
                    )
                    time.sleep(delay)
                    delay *= backoff_multiplier
                else:
                    logger.error(
                        f"[DB RETRY] {op_name}: All {max_attempts} attempts failed: database is locked.{context_str}"
                    )
            else:
                # Другие OperationalError не обрабатываем, но логируем с контекстом
                logger.error(
                    f"[DB ERROR] {op_name}: OperationalError (not retrying): {e}.{context_str}",
                    exc_info=True
                )
                raise
        except Exception as e:
            # Другие исключения логируем с контекстом и пробрасываем дальше
            logger.error(
                f"[DB ERROR] {op_name}: Unexpected error: {type(e).__name__}: {e}.{context_str}",
                exc_info=True
            )
            raise
    
    # Если дошли сюда, все попытки исчерпаны
    if last_exception:
        logger.error(
            f"[DB RETRY] {op_name}: All retry attempts exhausted.{context_str}",
            exc_info=True
        )
        raise last_exception
    raise RuntimeError(f"Unexpected error in retry_db_operation for {op_name}")


async def retry_async_db_operation(
    operation: Callable[[], Coroutine[Any, Any, T]],
    max_attempts: int = 3,
    initial_delay: float = 0.1,
    backoff_multiplier: float = 2.0,
    operation_name: Optional[str] = None,
    operation_context: Optional[Dict[str, Any]] = None,
) -> T:
    """
    Выполняет async операцию с БД с автоматическим retry при ошибке "database is locked".
    
    Args:
        operation: Async функция (корутина), которая выполняет операцию с БД
        max_attempts: Максимальное количество попыток (по умолчанию 3)
        initial_delay: Начальная задержка перед повторной попыткой в секундах (по умолчанию 0.1)
        backoff_multiplier: Множитель для экспоненциального backoff (по умолчанию 2.0)
        operation_name: Название операции для логирования (например, "update_payment_status")
        operation_context: Дополнительный контекст для логирования (например, {"payment_id": "123"})
    
    Returns:
        Результат выполнения operation
    
    Raises:
        aiosqlite.OperationalError: Если все попытки исчерпаны
        Любое другое исключение, которое не является "database is locked"
    """
    last_exception = None
    delay = initial_delay
    context_str = ""
    if operation_context:
        context_str = f" Context: {operation_context}"
    
    op_name = operation_name or "async_db_operation"
    
    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except (sqlite3.OperationalError, aiosqlite.OperationalError) as e:
            error_msg = str(e).lower()
            if "database is locked" in error_msg or "database is locked" in str(e):
                last_exception = e
                if attempt < max_attempts:
                    logger.warning(
                        f"[DB RETRY] {op_name}: Attempt {attempt}/{max_attempts} failed: database is locked.{context_str} "
                        f"Retrying in {delay:.2f}s..."
                    )
                    await asyncio.sleep(delay)
                    delay *= backoff_multiplier
                else:
                    logger.error(
                        f"[DB RETRY] {op_name}: All {max_attempts} attempts failed: database is locked.{context_str}"
                    )
            else:
                # Другие OperationalError не обрабатываем, но логируем с контекстом
                logger.error(
                    f"[DB ERROR] {op_name}: OperationalError (not retrying): {e}.{context_str}",
                    exc_info=True
                )
                raise
        except Exception as e:
            # Другие исключения логируем с контекстом и пробрасываем дальше
            logger.error(
                f"[DB ERROR] {op_name}: Unexpected error: {type(e).__name__}: {e}.{context_str}",
                exc_info=True
            )
            raise
    
    # Если дошли сюда, все попытки исчерпаны
    if last_exception:
        logger.error(
            f"[DB RETRY] {op_name}: All retry attempts exhausted.{context_str}",
            exc_info=True
        )
        raise last_exception
    raise RuntimeError(f"Unexpected error in retry_async_db_operation for {op_name}")


