#!/usr/bin/env python3
"""
Скрипт для поиска пользователей без ключа на указанном сервере
"""
import sqlite3
import sys
from app.infra.sqlite_utils import get_db_cursor
from config import DATABASE_PATH

def find_users_without_key_on_server(server_id: int):
    """Найти пользователей без ключа на указанном сервере"""
    
    with get_db_cursor() as cursor:
        # Сначала проверим, существует ли сервер
        cursor.execute("""
            SELECT id, name, protocol, active, country
            FROM servers
            WHERE id = ?
        """, (server_id,))
        server = cursor.fetchone()
        
        if not server:
            print(f"❌ Сервер с ID {server_id} не найден")
            return
        
        server_id_db, server_name, protocol, active, country = server
        print(f"📡 Сервер #{server_id}: {server_name}")
        print(f"   Протокол: {protocol}")
        print(f"   Статус: {'активен' if active else 'неактивен'}")
        print(f"   Страна: {country or 'не указана'}")
        print()
        
        # Определяем, какая таблица ключей используется
        if protocol == 'v2ray':
            keys_table = 'v2ray_keys'
            key_id_field = 'v2ray_uuid'
        else:
            keys_table = 'keys'
            key_id_field = 'key_id'
        
        # Найти пользователей с активными подписками
        now = int(__import__('time').time())
        
        # Получаем всех пользователей с активными подписками
        cursor.execute("""
            SELECT DISTINCT 
                s.user_id,
                s.id as subscription_id,
                s.subscription_token,
                s.expires_at,
                u.username,
                u.first_name,
                u.last_name
            FROM subscriptions s
            LEFT JOIN users u ON u.user_id = s.user_id
            WHERE s.is_active = 1 AND s.expires_at > ?
            ORDER BY s.user_id
        """, (now,))
        
        all_subscriptions = cursor.fetchall()
        print(f"📋 Всего активных подписок: {len(all_subscriptions)}")
        print()
        
        # Найти пользователей с подписками, но без ключа на этом сервере
        users_without_key = []
        
        # Также собираем всех пользователей с ключами на этом сервере для статистики
        if protocol == 'v2ray':
            cursor.execute("""
                SELECT DISTINCT user_id, subscription_id
                FROM v2ray_keys
                WHERE server_id = ?
            """, (server_id,))
            keys_data = cursor.fetchall()
            users_with_keys = {(row[0], row[1]) for row in keys_data}
            # Отдельные ключи (без подписки)
            users_with_standalone_keys = {row[0] for row in keys_data if row[1] is None}
        else:
            cursor.execute("""
                SELECT DISTINCT user_id
                FROM keys
                WHERE server_id = ?
            """, (server_id,))
            users_with_keys_set = {row[0] for row in cursor.fetchall()}
            users_with_keys = {(uid, None) for uid in users_with_keys_set}
            users_with_standalone_keys = users_with_keys_set
        
        print(f"📊 Пользователей с ключами на сервере #{server_id}: {len(users_with_keys)}")
        print()
        
        for sub in all_subscriptions:
            user_id, sub_id, token, expires_at, username, first_name, last_name = sub
            
            # Проверяем, есть ли ключ на этом сервере для этой подписки
            has_key = (user_id, sub_id) in users_with_keys
            
            if not has_key:
                # Проверяем, есть ли отдельный ключ (не подписка)
                has_standalone = user_id in users_with_standalone_keys
                
                users_without_key.append({
                    'user_id': user_id,
                    'subscription_id': sub_id,
                    'token': token,
                    'expires_at': expires_at,
                    'username': username,
                    'first_name': first_name,
                    'last_name': last_name,
                    'has_standalone_key': has_standalone
                })
        
        print(f"🔍 Пользователи с подписками БЕЗ ключа на сервере #{server_id}: {len(users_without_key)}")
        print()
        
        if users_without_key:
            print(f"{'User ID':<12} {'Username':<20} {'Имя':<25} {'Подписка ID':<12} {'Отд. ключ':<10} {'Истекает':<20}")
            print("=" * 120)
            
            for user in users_without_key:
                username = user['username'] or '—'
                first_name = user['first_name'] or ''
                last_name = user['last_name'] or ''
                name = f"{first_name} {last_name}".strip() or '—'
                if len(name) > 24:
                    name = name[:21] + '...'
                
                from datetime import datetime
                expires_str = datetime.fromtimestamp(user['expires_at']).strftime("%d.%m.%Y %H:%M")
                has_standalone = "✅" if user['has_standalone_key'] else "❌"
                
                print(f"{user['user_id']:<12} {username:<20} {name:<25} {user['subscription_id']:<12} {has_standalone:<10} {expires_str:<20}")
            
            print()
            print(f"Всего: {len(users_without_key)} пользователей с подписками, но без ключа на сервере #{server_id}")
            
            # Дополнительная информация
            print()
            print("💡 Примечание:")
            print("   - Пользователи должны иметь ключи на всех активных серверах, доступных для подписки")
            print("   - Если у пользователя есть отдельный ключ (не подписка), это указано в колонке 'Отд. ключ'")
        else:
            print("✅ У всех пользователей с подписками есть ключ на этом сервере")

if __name__ == "__main__":
    server_id = int(sys.argv[1]) if len(sys.argv) > 1 else 18
    find_users_without_key_on_server(server_id)

