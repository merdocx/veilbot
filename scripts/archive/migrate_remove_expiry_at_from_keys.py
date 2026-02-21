#!/usr/bin/env python3
"""
Миграция: Удаление поля expiry_at из таблиц keys и v2ray_keys
Все ключи теперь получают срок действия из subscriptions.expires_at через JOIN
"""
import sqlite3
import sys
import os
from pathlib import Path

# Добавляем корневую директорию проекта в путь (scripts/archive -> scripts -> корень)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.settings import settings

DB_PATH = settings.DATABASE_PATH


def migrate():
    """Выполнить миграцию"""
    print("=" * 80)
    print("Миграция: Удаление expiry_at из keys и v2ray_keys")
    print("=" * 80)
    
    # Проверяем, что все ключи имеют subscription_id
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Проверяем keys
    cursor.execute("SELECT COUNT(*) FROM keys WHERE subscription_id IS NULL")
    outline_standalone = cursor.fetchone()[0]
    
    # Проверяем v2ray_keys
    cursor.execute("SELECT COUNT(*) FROM v2ray_keys WHERE subscription_id IS NULL")
    v2ray_standalone = cursor.fetchone()[0]
    
    if outline_standalone > 0 or v2ray_standalone > 0:
        print(f"⚠️  ВНИМАНИЕ: Найдено standalone ключей без subscription_id:")
        print(f"   Outline: {outline_standalone}")
        print(f"   V2Ray: {v2ray_standalone}")
        print("   Миграция продолжится, но эти ключи не будут работать корректно!")
        response = input("Продолжить миграцию? (yes/no): ")
        if response.lower() != 'yes':
            print("Миграция отменена")
            conn.close()
            return False
    
    print("\n1. Создание резервной копии...")
    backup_path = f"{DB_PATH}.before_remove_expiry_at_backup"
    backup_conn = sqlite3.connect(backup_path)
    conn.backup(backup_conn)
    backup_conn.close()
    print(f"   ✓ Резервная копия создана: {backup_path}")
    
    try:
        # Отключаем foreign keys для безопасной миграции
        cursor.execute("PRAGMA foreign_keys = OFF")
        
        print("\n2. Пересоздание таблицы keys без expiry_at...")
        # Получаем текущую структуру
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='keys'")
        old_sql = cursor.fetchone()[0]
        
        # Создаем временную таблицу с новой структурой
        cursor.execute("""
            CREATE TABLE keys_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER,
                user_id INTEGER,
                access_url TEXT,
                traffic_limit_mb INTEGER,
                notified INTEGER DEFAULT 0,
                key_id TEXT,
                created_at INTEGER,
                email TEXT,
                tariff_id INTEGER,
                protocol TEXT DEFAULT 'outline',
                subscription_id INTEGER,
                FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE
            )
        """)
        
        # Копируем данные (без expiry_at)
        cursor.execute("""
            INSERT INTO keys_new 
            (id, server_id, user_id, access_url, traffic_limit_mb, notified, key_id, 
             created_at, email, tariff_id, protocol, subscription_id)
            SELECT 
                id, server_id, user_id, access_url, traffic_limit_mb, notified, key_id,
                created_at, email, tariff_id, protocol, subscription_id
            FROM keys
        """)
        
        # Удаляем старую таблицу и переименовываем новую
        cursor.execute("DROP TABLE keys")
        cursor.execute("ALTER TABLE keys_new RENAME TO keys")
        print("   ✓ Таблица keys пересоздана")
        
        print("\n3. Пересоздание таблицы v2ray_keys без expiry_at...")
        # Создаем временную таблицу с новой структурой
        cursor.execute("""
            CREATE TABLE v2ray_keys_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER,
                user_id INTEGER,
                v2ray_uuid TEXT UNIQUE,
                email TEXT,
                level INTEGER DEFAULT 0,
                created_at INTEGER,
                tariff_id INTEGER,
                client_config TEXT DEFAULT NULL,
                notified INTEGER DEFAULT 0,
                traffic_limit_mb INTEGER DEFAULT 0,
                traffic_usage_bytes INTEGER DEFAULT 0,
                subscription_id INTEGER,
                FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
            )
        """)
        
        # Копируем данные (без expiry_at)
        cursor.execute("""
            INSERT INTO v2ray_keys_new 
            (id, server_id, user_id, v2ray_uuid, email, level, created_at, tariff_id,
             client_config, notified, traffic_limit_mb, traffic_usage_bytes, subscription_id)
            SELECT 
                id, server_id, user_id, v2ray_uuid, email, level, created_at, tariff_id,
                client_config, notified, traffic_limit_mb, traffic_usage_bytes, subscription_id
            FROM v2ray_keys
        """)
        
        # Удаляем старую таблицу и переименовываем новую
        cursor.execute("DROP TABLE v2ray_keys")
        cursor.execute("ALTER TABLE v2ray_keys_new RENAME TO v2ray_keys")
        print("   ✓ Таблица v2ray_keys пересоздана")
        
        # Восстанавливаем foreign keys
        cursor.execute("PRAGMA foreign_keys = ON")
        
        # Коммитим изменения
        conn.commit()
        
        # Проверяем целостность
        print("\n4. Проверка целостности...")
        cursor.execute("PRAGMA integrity_check")
        integrity = cursor.fetchone()[0]
        if integrity == 'ok':
            print("   ✓ Целостность БД проверена")
        else:
            print(f"   ⚠️  Проблемы с целостностью: {integrity}")
        
        print("\n" + "=" * 80)
        print("✅ Миграция успешно завершена!")
        print(f"Резервная копия: {backup_path}")
        print("=" * 80)
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"\n❌ Ошибка при миграции: {e}")
        import traceback
        traceback.print_exc()
        print(f"\n⚠️  Восстановите БД из резервной копии: {backup_path}")
        conn.rollback()
        conn.close()
        return False


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)


