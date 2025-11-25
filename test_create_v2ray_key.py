#!/usr/bin/env python3
"""Тестовый скрипт для создания ключа v2ray через API"""

import asyncio
import sys
import os
import time
from typing import Optional

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.infra.sqlite_utils import get_db_cursor
from vpn_protocols import V2RayProtocol


async def create_test_key():
    """Создание тестового ключа v2ray через API"""
    
    print("=" * 80)
    print("ТЕСТ: СОЗДАНИЕ КЛЮЧА V2RAY ЧЕРЕЗ API")
    print("=" * 80)
    print()
    
    # Получаем первый активный V2Ray сервер из БД
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT id, name, api_url, api_key, domain, protocol, country
            FROM servers
            WHERE protocol = 'v2ray' AND active = 1
            LIMIT 1
        """)
        row = cursor.fetchone()
    
    if not row:
        print("❌ ОШИБКА: Не найден активный V2Ray сервер в базе данных")
        return
    
    server_info = dict(row)
    
    print("📋 ИНФОРМАЦИЯ О СЕРВЕРЕ:")
    print(f"   ID: {server_info['id']}")
    print(f"   Название: {server_info['name']}")
    print(f"   Страна: {server_info.get('country', 'N/A')}")
    print(f"   Домен: {server_info.get('domain', 'N/A')}")
    print(f"   API URL: {server_info['api_url']}")
    
    api_key = server_info.get('api_key')
    if not api_key:
        print("❌ ОШИБКА: API ключ не указан для сервера")
        return
    
    print(f"   API Key: {api_key[:10]}...{api_key[-5:] if len(api_key) > 15 else ''}")
    print()
    
    # Создаем протокол клиент
    protocol = V2RayProtocol(server_info['api_url'], api_key)
    
    try:
        # Генерируем уникальное имя для тестового ключа
        test_email = f"test_{int(time.time())}@veilbot.com"
        test_name = f"Test Key {int(time.time())}"
        
        print("=" * 80)
        print("СОЗДАНИЕ КЛЮЧА")
        print("=" * 80)
        print(f"📧 Email: {test_email}")
        print(f"📝 Название: {test_name}")
        print()
        
        # Создаем ключ через API
        print("🔄 Отправка запроса к API...")
        user_data = await protocol.create_user(test_email, name=test_name)
        
        if not user_data:
            print("❌ ОШИБКА: Не получены данные о созданном ключе")
            return
        
        if not user_data.get('uuid'):
            print("❌ ОШИБКА: В ответе отсутствует UUID ключа")
            print(f"   Ответ API: {user_data}")
            return
        
        # Выводим результат
        print("✅ КЛЮЧ УСПЕШНО СОЗДАН!")
        print()
        print("📦 ДАННЫЕ КЛЮЧА:")
        print(f"   ID: {user_data.get('id', 'N/A')}")
        print(f"   UUID: {user_data.get('uuid', 'N/A')}")
        print(f"   Название: {user_data.get('name', 'N/A')}")
        print(f"   Порт: {user_data.get('port', 'N/A')}")
        print(f"   Short ID: {user_data.get('short_id', 'N/A')}")
        print(f"   SNI: {user_data.get('sni', 'N/A')}")
        print(f"   Создан: {user_data.get('created_at', 'N/A')}")
        print(f"   Активен: {user_data.get('is_active', 'N/A')}")
        
        # Показываем конфигурацию клиента, если есть
        client_config = user_data.get('client_config')
        if client_config:
            print()
            print("🔗 КОНФИГУРАЦИЯ КЛИЕНТА:")
            if isinstance(client_config, str):
                # Если это VLESS URL, показываем его
                if 'vless://' in client_config:
                    print(f"   VLESS URL: {client_config}")
                else:
                    print(f"   Конфигурация:\n{client_config}")
            else:
                print(f"   {client_config}")
        
        print()
        print("=" * 80)
        print("ТЕСТ ЗАВЕРШЕН УСПЕШНО")
        print("=" * 80)
        
    except Exception as e:
        print()
        print("❌ ОШИБКА ПРИ СОЗДАНИИ КЛЮЧА:")
        print(f"   {str(e)}")
        import traceback
        print()
        print("Детали ошибки:")
        for line in traceback.format_exc().split('\n'):
            if line.strip():
                print(f"   {line}")
    finally:
        # Закрываем соединение
        await protocol.close()


if __name__ == "__main__":
    asyncio.run(create_test_key())




