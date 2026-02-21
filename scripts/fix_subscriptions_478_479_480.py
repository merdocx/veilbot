#!/usr/bin/env python3
"""
Скрипт для диагностики и исправления подписок 478, 479, 480
Проблема: ключи не создались, подписки неактивны
"""
import sqlite3
import os
from datetime import datetime

db_path = '/root/veilbot/vpn.db'

def check_subscriptions():
    """Проверяет подписки и выводит диагностическую информацию"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("=" * 60)
    print("ДИАГНОСТИКА ПОДПИСОК 478, 479, 480")
    print("=" * 60)
    
    for sub_id in [478, 479, 480]:
        print(f"\n=== Подписка {sub_id} ===")
        
        # Информация о подписке
        cursor.execute("""
            SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, traffic_limit_mb
            FROM subscriptions
            WHERE id = ?
        """, (sub_id,))
        sub = cursor.fetchone()
        
        if not sub:
            print(f"❌ Подписка {sub_id} не найдена")
            continue
        
        sub_id_db, user_id, token, created_at, expires_at, tariff_id, is_active, traffic_limit_mb = sub
        print(f"User ID: {user_id}")
        print(f"Is Active: {is_active}")
        print(f"Created: {datetime.fromtimestamp(created_at) if created_at else None}")
        print(f"Expires: {datetime.fromtimestamp(expires_at) if expires_at else None}")
        print(f"Traffic Limit MB: {traffic_limit_mb}")
        
        # Ключи
        cursor.execute("SELECT COUNT(*) FROM v2ray_keys WHERE subscription_id = ?", (sub_id,))
        key_count = cursor.fetchone()[0]
        print(f"V2Ray Keys: {key_count}")
        
        # Тариф
        if tariff_id:
            cursor.execute("SELECT name, price_rub FROM tariffs WHERE id = ?", (tariff_id,))
            tariff = cursor.fetchone()
            if tariff:
                print(f"Tariff: {tariff[0]}, Price: {tariff[1]} RUB")
        
        # Серверы
        cursor.execute("""
            SELECT id, name, api_url, domain, COALESCE(access_level, 'all') as access_level
            FROM servers
            WHERE protocol = 'v2ray' AND active = 1
        """)
        servers = cursor.fetchall()
        print(f"Active V2Ray Servers: {len(servers)}")
        for server in servers:
            print(f"  - Server {server[0]}: {server[1]}, Access Level: {server[4]}")
        
        # Пользователь
        cursor.execute("SELECT user_id, is_vip FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        if user:
            print(f"User VIP: {user[1]}")
    
    conn.close()

def activate_subscriptions():
    """Активирует подписки (если ключи уже есть)"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("\n" + "=" * 60)
    print("ПОПЫТКА АКТИВАЦИИ ПОДПИСОК")
    print("=" * 60)
    
    for sub_id in [478, 479, 480]:
        cursor.execute("SELECT COUNT(*) FROM v2ray_keys WHERE subscription_id = ?", (sub_id,))
        key_count = cursor.fetchone()[0]
        
        if key_count > 0:
            cursor.execute("UPDATE subscriptions SET is_active = 1 WHERE id = ?", (sub_id,))
            conn.commit()
            print(f"✓ Подписка {sub_id} активирована ({key_count} ключей)")
        else:
            print(f"✗ Подписка {sub_id} не активирована (нет ключей)")
    
    conn.close()

if __name__ == "__main__":
    check_subscriptions()
    print("\n" + "=" * 60)
    print("РЕКОМЕНДАЦИЯ:")
    print("=" * 60)
    print("Подписки были деактивированы автоматически, т.к. не удалось создать ключи.")
    print("Вероятные причины:")
    print("1. Ошибка подключения к V2Ray API серверу")
    print("2. Неверный API ключ")
    print("3. Сервер недоступен во время создания подписки")
    print("\nДля исправления необходимо:")
    print("1. Проверить доступность V2Ray API сервера")
    print("2. Проверить логи бота на момент создания подписок")
    print("3. Вручную создать ключи через SubscriptionService.create_subscription()")
    print("\nПопытка активации подписок с существующими ключами:")
    activate_subscriptions()
