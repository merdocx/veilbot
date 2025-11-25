#!/usr/bin/env python3
"""
Скрипт для автоматического запуска тестов VPN API
Автоматически находит V2Ray сервер в базе данных и запускает тесты
"""
import asyncio
import sys
import os
import sqlite3
import subprocess

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DATABASE_PATH


def get_v2ray_server():
    """Получить первый активный V2Ray сервер из базы данных"""
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT id, name, api_url, api_key, protocol 
            FROM servers 
            WHERE protocol = 'v2ray' AND active = 1 
            LIMIT 1
        """)
        row = c.fetchone()
        conn.close()
        
        if row:
            return {
                'id': row[0],
                'name': row[1],
                'api_url': row[2],
                'api_key': row[3],
                'protocol': row[4]
            }
        return None
    except Exception as e:
        print(f"Ошибка при получении сервера из БД: {e}")
        return None


def main():
    """Основная функция"""
    print("=" * 60)
    print("АВТОМАТИЧЕСКИЙ ЗАПУСК ТЕСТОВ VPN API")
    print("=" * 60)
    print()
    
    # Получаем сервер из БД
    server = get_v2ray_server()
    
    if not server:
        print("❌ ОШИБКА: Не найден активный V2Ray сервер в базе данных")
        print("   Убедитесь, что в базе данных есть сервер с protocol='v2ray' и active=1")
        sys.exit(1)
    
    print(f"✅ Найден сервер: {server['name']}")
    print(f"   API URL: {server['api_url']}")
    print(f"   Has API Key: {'Да' if server['api_key'] else 'Нет'}")
    print()
    
    if not server['api_key']:
        print("⚠️  ВНИМАНИЕ: API ключ не указан для сервера")
        print("   Тесты могут не работать")
        print()
    
    # Запускаем тесты
    test_script = os.path.join(os.path.dirname(__file__), 'test_vpn_api.py')
    
    if not os.path.exists(test_script):
        print(f"❌ ОШИБКА: Тестовый скрипт не найден: {test_script}")
        sys.exit(1)
    
    # Устанавливаем переменные окружения
    env = os.environ.copy()
    env['VPN_API_URL'] = server['api_url']
    if server['api_key']:
        env['VPN_API_KEY'] = server['api_key']
    
    print("Запускаю тесты...")
    print()
    
    # Запускаем тестовый скрипт
    try:
        result = subprocess.run(
            [sys.executable, test_script],
            env=env,
            cwd=os.path.dirname(__file__)
        )
        sys.exit(result.returncode)
    except Exception as e:
        print(f"❌ ОШИБКА при запуске тестов: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()





