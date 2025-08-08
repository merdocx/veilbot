#!/usr/bin/env python3
"""
Специальный тест для пользователя 6358556135
"""

import sqlite3
import time
from bot import check_free_tariff_limit_by_protocol_and_country

def test_user_6358556135():
    """Тестирует пользователя 6358556135"""
    
    conn = sqlite3.connect("vpn.db")
    cursor = conn.cursor()
    
    try:
        print("🧪 Тестирование пользователя 6358556135")
        print("=" * 50)
        
        user_id = 6358556135
        
        # Проверяем записи в free_key_usage
        cursor.execute("SELECT * FROM free_key_usage WHERE user_id = ?", (user_id,))
        usage_records = cursor.fetchall()
        print(f"📊 Записей в free_key_usage: {len(usage_records)}")
        for record in usage_records:
            print(f"  - ID: {record[0]}, User: {record[1]}, Protocol: {record[2]}, Country: {record[3]}, Created: {record[4]}")
        
        # Проверяем активные ключи
        now = int(time.time())
        cursor.execute("""
            SELECT k.id, k.created_at, t.name, t.price_rub, s.country 
            FROM keys k 
            JOIN tariffs t ON k.tariff_id = t.id 
            LEFT JOIN servers s ON k.server_id = s.id 
            WHERE k.user_id = ? AND k.expiry_at > ?
        """, (user_id, now))
        active_keys = cursor.fetchall()
        print(f"📊 Активных ключей: {len(active_keys)}")
        for key in active_keys:
            print(f"  - ID: {key[0]}, Created: {key[1]}, Tariff: {key[2]}, Price: {key[3]}, Country: {key[4]}")
        
        # Тестируем функцию проверки
        print("\n🔍 Тестирование функции проверки:")
        
        # Outline без указания страны
        result = check_free_tariff_limit_by_protocol_and_country(cursor, user_id, "outline")
        print(f"  - Outline (без страны): может получить = {not result}")
        
        # Outline с указанием страны
        result = check_free_tariff_limit_by_protocol_and_country(cursor, user_id, "outline", "Нидерланды")
        print(f"  - Outline (Нидерланды): может получить = {not result}")
        
        # Outline с другой страной
        result = check_free_tariff_limit_by_protocol_and_country(cursor, user_id, "outline", "Германия")
        print(f"  - Outline (Германия): может получить = {not result}")
        
        # V2Ray без указания страны
        result = check_free_tariff_limit_by_protocol_and_country(cursor, user_id, "v2ray")
        print(f"  - V2Ray (без страны): может получить = {not result}")
        
        # V2Ray с указанием страны
        result = check_free_tariff_limit_by_protocol_and_country(cursor, user_id, "v2ray", "Нидерланды")
        print(f"  - V2Ray (Нидерланды): может получить = {not result}")
        
        print("\n✅ Тест завершен!")
        
    except Exception as e:
        print(f"❌ Ошибка при тестировании: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    test_user_6358556135()
