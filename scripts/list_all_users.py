#!/usr/bin/env python3
"""
Вывод списка всех user_id из базы
"""
import sys
import os

_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root_dir)

from app.infra.sqlite_utils import open_connection
from app.settings import settings

def list_all_users(limit: int = 50):
    """Показать список всех user_id"""
    db_path = settings.DATABASE_PATH
    
    print(f"Список user_id в базе данных (первые {limit}):\n")
    
    with open_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # Объединенный список user_id из всех таблиц
        cursor.execute("""
            SELECT DISTINCT user_id FROM (
                SELECT user_id FROM users
                UNION
                SELECT user_id FROM keys
                UNION
                SELECT user_id FROM v2ray_keys
                UNION
                SELECT user_id FROM subscriptions
                UNION
                SELECT user_id FROM payments
                UNION
                SELECT referrer_id as user_id FROM referrals
                UNION
                SELECT referred_id as user_id FROM referrals
            )
            ORDER BY user_id
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        if rows:
            print(f"Найдено {len(rows)} уникальных user_id:\n")
            for row in rows:
                print(f"  {row[0]}")
            
            # Показываем минимальный и максимальный ID
            cursor.execute("""
                SELECT MIN(user_id), MAX(user_id), COUNT(DISTINCT user_id) FROM (
                    SELECT user_id FROM users
                    UNION
                    SELECT user_id FROM keys
                    UNION
                    SELECT user_id FROM v2ray_keys
                    UNION
                    SELECT user_id FROM subscriptions
                    UNION
                    SELECT user_id FROM payments
                    UNION
                    SELECT referrer_id as user_id FROM referrals
                    UNION
                    SELECT referred_id as user_id FROM referrals
                )
            """)
            min_id, max_id, total = cursor.fetchone()
            print(f"\nМинимальный ID: {min_id}")
            print(f"Максимальный ID: {max_id}")
            print(f"Всего уникальных пользователей: {total}")
        else:
            print("База данных пуста - не найдено ни одного user_id")

if __name__ == "__main__":
    limit = 50
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
        except:
            pass
    
    try:
        list_all_users(limit)
    except Exception as e:
        print(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

