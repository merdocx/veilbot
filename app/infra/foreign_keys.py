"""
Контекстный менеджер для безопасной работы с foreign key constraints в SQLite
"""
import logging
from contextlib import contextmanager
from typing import Generator

logger = logging.getLogger(__name__)


@contextmanager
def foreign_keys_off(connection) -> Generator[None, None, None]:
    """
    Контекстный менеджер для временного отключения проверки foreign keys
    
    Использование:
        with foreign_keys_off(cursor.connection):
            cursor.execute("INSERT INTO ...")
    
    Args:
        connection: SQLite connection object (не cursor!)
    
    Example:
        with get_db_cursor(commit=True) as cursor:
            with foreign_keys_off(cursor.connection):
                cursor.execute("INSERT INTO v2ray_keys (...) VALUES (...)")
    """
    original_state = None
    temp_cursor = None
    try:
        # Получаем текущее состояние foreign keys
        temp_cursor = connection.cursor()
        temp_cursor.execute("PRAGMA foreign_keys")
        result = temp_cursor.fetchone()
        original_state = result[0] if result else 1
        
        # Отключаем foreign keys (PRAGMA выполняется через cursor)
        temp_cursor.execute("PRAGMA foreign_keys=OFF")
        logger.debug("Foreign keys temporarily disabled")
        
        yield
        
    except Exception as e:
        logger.error(f"Error in foreign_keys_off context manager: {e}", exc_info=True)
        raise
    finally:
        # Восстанавливаем исходное состояние
        try:
            if temp_cursor is None:
                temp_cursor = connection.cursor()
            
            if original_state is not None:
                temp_cursor.execute(f"PRAGMA foreign_keys={'ON' if original_state else 'OFF'}")
                logger.debug(f"Foreign keys restored to original state: {original_state}")
            else:
                # Если не удалось получить исходное состояние, включаем обратно
                temp_cursor.execute("PRAGMA foreign_keys=ON")
                logger.warning("Foreign keys restored to ON (default) - original state unknown")
        except Exception as e:
            logger.error(f"Error restoring foreign keys state: {e}", exc_info=True)
            # Все равно пытаемся включить foreign keys для безопасности
            try:
                if temp_cursor is None:
                    temp_cursor = connection.cursor()
                temp_cursor.execute("PRAGMA foreign_keys=ON")
            except:
                pass


@contextmanager
def safe_foreign_keys_off(cursor) -> Generator[None, None, None]:
    """
    Удобный контекстный менеджер, который принимает cursor вместо connection
    
    Использование:
        with get_db_cursor(commit=True) as cursor:
            with safe_foreign_keys_off(cursor):
                cursor.execute("INSERT INTO ...")
    
    Args:
        cursor: SQLite cursor object
    
    Example:
        with get_db_cursor(commit=True) as cursor:
            with safe_foreign_keys_off(cursor):
                cursor.execute("INSERT INTO v2ray_keys (...) VALUES (...)")
    """
    with foreign_keys_off(cursor.connection):
        yield

