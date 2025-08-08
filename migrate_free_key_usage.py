#!/usr/bin/env python3
"""
Скрипт для миграции существующих данных о бесплатных ключах
в новую таблицу free_key_usage
"""

import sqlite3
import time
from db import migrate_add_free_key_usage

def migrate_existing_free_keys():
    """Мигрирует существующие данные о бесплатных ключах в таблицу free_key_usage"""
    
    # Создаем таблицу если её нет
    migrate_add_free_key_usage()
    
    conn = sqlite3.connect("vpn.db")
    cursor = conn.cursor()
    
    try:
        print("🔄 Начинаю миграцию данных о бесплатных ключах...")
        
        # Получаем все бесплатные ключи из таблицы keys (Outline)
        cursor.execute("""
            SELECT DISTINCT k.user_id, s.country, k.created_at
            FROM keys k
            JOIN tariffs t ON k.tariff_id = t.id
            LEFT JOIN servers s ON k.server_id = s.id
            WHERE t.price_rub = 0
            ORDER BY k.user_id, k.created_at
        """)
        outline_free_keys = cursor.fetchall()
        
        # Получаем все бесплатные ключи из таблицы v2ray_keys
        cursor.execute("""
            SELECT DISTINCT k.user_id, s.country, k.created_at
            FROM v2ray_keys k
            JOIN tariffs t ON k.tariff_id = t.id
            LEFT JOIN servers s ON k.server_id = s.id
            WHERE t.price_rub = 0
            ORDER BY k.user_id, k.created_at
        """)
        v2ray_free_keys = cursor.fetchall()
        
        print(f"📊 Найдено {len(outline_free_keys)} бесплатных Outline ключей")
        print(f"📊 Найдено {len(v2ray_free_keys)} бесплатных V2Ray ключей")
        
        # Добавляем записи для Outline ключей
        outline_count = 0
        for user_id, country, created_at in outline_free_keys:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO free_key_usage (user_id, protocol, country, created_at)
                    VALUES (?, ?, ?, ?)
                """, (user_id, "outline", country, created_at))
                if cursor.rowcount > 0:
                    outline_count += 1
            except Exception as e:
                print(f"❌ Ошибка при добавлении Outline записи для user_id={user_id}: {e}")
        
        # Добавляем записи для V2Ray ключей
        v2ray_count = 0
        for user_id, country, created_at in v2ray_free_keys:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO free_key_usage (user_id, protocol, country, created_at)
                    VALUES (?, ?, ?, ?)
                """, (user_id, "v2ray", country, created_at))
                if cursor.rowcount > 0:
                    v2ray_count += 1
            except Exception as e:
                print(f"❌ Ошибка при добавлении V2Ray записи для user_id={user_id}: {e}")
        
        conn.commit()
        
        print(f"✅ Миграция завершена!")
        print(f"📈 Добавлено {outline_count} записей для Outline ключей")
        print(f"📈 Добавлено {v2ray_count} записей для V2Ray ключей")
        
        # Показываем статистику
        cursor.execute("SELECT COUNT(*) FROM free_key_usage")
        total_records = cursor.fetchone()[0]
        print(f"📊 Всего записей в таблице free_key_usage: {total_records}")
        
        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM free_key_usage")
        unique_users = cursor.fetchone()[0]
        print(f"👥 Уникальных пользователей: {unique_users}")
        
    except Exception as e:
        print(f"❌ Ошибка при миграции: {e}")
        conn.rollback()
    finally:
        conn.close()

def verify_migration():
    """Проверяет корректность миграции"""
    
    conn = sqlite3.connect("vpn.db")
    cursor = conn.cursor()
    
    try:
        print("\n🔍 Проверка корректности миграции...")
        
        # Проверяем, что все пользователи с бесплатными ключами есть в новой таблице
        cursor.execute("""
            SELECT COUNT(DISTINCT k.user_id) as users_with_free_keys
            FROM (
                SELECT user_id FROM keys k
                JOIN tariffs t ON k.tariff_id = t.id
                WHERE t.price_rub = 0
                UNION
                SELECT user_id FROM v2ray_keys k
                JOIN tariffs t ON k.tariff_id = t.id
                WHERE t.price_rub = 0
            ) k
        """)
        users_with_free_keys = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM free_key_usage")
        users_in_new_table = cursor.fetchone()[0]
        
        print(f"👥 Пользователей с бесплатными ключами: {users_with_free_keys}")
        print(f"👥 Пользователей в новой таблице: {users_in_new_table}")
        
        if users_with_free_keys == users_in_new_table:
            print("✅ Миграция прошла успешно!")
        else:
            print("⚠️ Возможны проблемы с миграцией")
            
    except Exception as e:
        print(f"❌ Ошибка при проверке: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    print("🚀 Запуск миграции данных о бесплатных ключах")
    print("=" * 50)
    
    migrate_existing_free_keys()
    verify_migration()
    
    print("\n" + "=" * 50)
    print("✅ Миграция завершена!")

