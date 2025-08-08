#!/usr/bin/env python3
"""
Тестовый скрипт для проверки логики ограничения бесплатных ключей
"""

import sqlite3
import time
from bot import check_free_tariff_limit_by_protocol_and_country, record_free_key_usage

def test_free_key_limit():
    """Тестирует логику ограничения бесплатных ключей"""
    
    conn = sqlite3.connect("vpn.db")
    cursor = conn.cursor()
    
    try:
        print("🧪 Тестирование логики ограничения бесплатных ключей")
        print("=" * 60)
        
        # Тестовый пользователь
        test_user_id = 999999
        
        print(f"👤 Тестируем для пользователя {test_user_id}")
        
        # Проверяем, что пользователь может получить бесплатный ключ
        can_get_free = check_free_tariff_limit_by_protocol_and_country(cursor, test_user_id, "outline")
        print(f"🔍 Может получить бесплатный Outline ключ: {not can_get_free}")
        
        # Записываем использование бесплатного ключа
        print("📝 Записываем использование бесплатного ключа...")
        record_free_key_usage(cursor, test_user_id, "outline", "Russia")
        
        # Проверяем снова
        can_get_free = check_free_tariff_limit_by_protocol_and_country(cursor, test_user_id, "outline")
        print(f"🔍 Может получить бесплатный Outline ключ после записи: {not can_get_free}")
        
        # Проверяем для другой страны (должен быть заблокирован, так как уже получал для этого протокола)
        can_get_free_other_country = check_free_tariff_limit_by_protocol_and_country(cursor, test_user_id, "outline", "Germany")
        print(f"🔍 Может получить бесплатный Outline ключ для Германии: {not can_get_free_other_country}")
        
        # Проверяем для другого протокола
        can_get_free_v2ray = check_free_tariff_limit_by_protocol_and_country(cursor, test_user_id, "v2ray")
        print(f"🔍 Может получить бесплатный V2Ray ключ: {not can_get_free_v2ray}")
        
        # Записываем использование V2Ray ключа
        print("📝 Записываем использование бесплатного V2Ray ключа...")
        record_free_key_usage(cursor, test_user_id, "v2ray", "Russia")
        
        # Проверяем снова
        can_get_free_v2ray = check_free_tariff_limit_by_protocol_and_country(cursor, test_user_id, "v2ray")
        print(f"🔍 Может получить бесплатный V2Ray ключ после записи: {not can_get_free_v2ray}")
        
        # Показываем содержимое таблицы
        print("\n📊 Содержимое таблицы free_key_usage:")
        cursor.execute("SELECT user_id, protocol, country, created_at FROM free_key_usage WHERE user_id = ?", (test_user_id,))
        records = cursor.fetchall()
        for record in records:
            print(f"  - User: {record[0]}, Protocol: {record[1]}, Country: {record[2]}, Created: {record[3]}")
        
        conn.commit()
        
        print("\n✅ Тест завершен успешно!")
        
    except Exception as e:
        print(f"❌ Ошибка при тестировании: {e}")
        conn.rollback()
    finally:
        conn.close()

def test_existing_users():
    """Тестирует существующих пользователей"""
    
    conn = sqlite3.connect("vpn.db")
    cursor = conn.cursor()
    
    try:
        print("\n🔍 Тестирование существующих пользователей")
        print("=" * 60)
        
        # Получаем несколько существующих пользователей
        cursor.execute("SELECT DISTINCT user_id FROM free_key_usage LIMIT 3")
        existing_users = cursor.fetchall()
        
        for user_id, in existing_users:
            print(f"\n👤 Пользователь {user_id}:")
            
            # Проверяем для Outline
            can_get_outline = check_free_tariff_limit_by_protocol_and_country(cursor, user_id, "outline")
            print(f"  - Может получить бесплатный Outline ключ: {not can_get_outline}")
            
            # Проверяем для V2Ray
            can_get_v2ray = check_free_tariff_limit_by_protocol_and_country(cursor, user_id, "v2ray")
            print(f"  - Может получить бесплатный V2Ray ключ: {not can_get_v2ray}")
        
    except Exception as e:
        print(f"❌ Ошибка при тестировании существующих пользователей: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    test_free_key_limit()
    test_existing_users()
