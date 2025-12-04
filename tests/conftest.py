"""
Конфигурация pytest для VeilBot тестов
"""
import pytest
import sqlite3
import tempfile
import os
from typing import Generator
from unittest.mock import MagicMock, Mock, AsyncMock


@pytest.fixture
def temp_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Создает временную базу данных для тестов
    
    Yields:
        sqlite3.Connection: Временное соединение с БД
    """
    # Создаем временный файл БД
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # Создаем базовую структуру таблиц
    cursor = conn.cursor()
    
    # Таблица users
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            created_at INTEGER,
            last_active_at INTEGER,
            blocked INTEGER DEFAULT 0
        )
    """)
    
    # Таблица servers
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            api_url TEXT,
            cert_sha256 TEXT,
            domain TEXT,
            api_key TEXT,
            v2ray_path TEXT,
            country TEXT,
            protocol TEXT DEFAULT 'outline',
            active INTEGER DEFAULT 1,
            available_for_purchase INTEGER DEFAULT 1,
            max_keys INTEGER DEFAULT 100
        )
    """)
    
    # Таблица keys
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER,
            user_id INTEGER,
            access_url TEXT,
            expiry_at INTEGER,
            key_id TEXT,
            created_at INTEGER,
            email TEXT,
            tariff_id INTEGER,
            protocol TEXT,
            FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
        )
    """)
    
    # Таблица v2ray_keys
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS v2ray_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER,
            user_id INTEGER,
            v2ray_uuid TEXT,
            expiry_at INTEGER,
            created_at INTEGER,
            email TEXT,
            tariff_id INTEGER,
            FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
        )
    """)
    
    # Таблица tariffs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tariffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            price_rub REAL,
            duration_sec INTEGER,
            price_crypto_usd REAL
        )
    """)
    
    # Таблица free_key_usage
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS free_key_usage (
            user_id INTEGER,
            protocol TEXT,
            country TEXT,
            created_at INTEGER,
            PRIMARY KEY (user_id, protocol, country)
        )
    """)
    
    conn.commit()
    
    yield conn
    
    # Очистка
    conn.close()
    os.unlink(db_path)


@pytest.fixture
def mock_cursor(temp_db: sqlite3.Connection) -> sqlite3.Cursor:
    """
    Создает курсор для тестов
    
    Args:
        temp_db: Временная БД
    
    Returns:
        sqlite3.Cursor: Курсор БД
    """
    return temp_db.cursor()


@pytest.fixture
def mock_message() -> MagicMock:
    """
    Создает мок Telegram сообщения
    
    Returns:
        MagicMock: Мок aiogram.types.Message
    """
    message = MagicMock()
    message.from_user.id = 12345
    message.from_user.username = "test_user"
    message.from_user.first_name = "Test"
    message.text = "Test message"
    message.answer = AsyncMock(return_value=None)
    message.answer_photo = AsyncMock(return_value=None)
    return message


@pytest.fixture
def mock_bot() -> MagicMock:
    """
    Создает мок Telegram бота
    
    Returns:
        MagicMock: Мок aiogram.Bot
    """
    bot = MagicMock()
    bot_me = MagicMock()
    bot_me.username = "test_bot"
    bot.get_me = AsyncMock(return_value=bot_me)
    bot.send_message = AsyncMock(return_value=None)
    return bot

