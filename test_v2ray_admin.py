#!/usr/bin/env python3
"""
Тест подсчета трафика для конкретного V2Ray ключа в админке
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from admin.admin_routes import get_key_monthly_traffic
import logging

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

async def test_v2ray_admin():
    """Тестирует подсчет трафика для V2Ray ключа в админке"""
    
    print("=== Тест V2Ray трафика в админке ===")
    
    # Данные V2Ray ключа из базы
    key_uuid = "***REMOVED***"
    api_url = "https://veil-bird.ru/api"
    api_key = "***REMOVED***"
    server_config = {'api_url': api_url, 'api_key': api_key}
    
    print(f"Key UUID: {key_uuid}")
    print(f"API URL: {api_url}")
    print(f"API Key: {api_key[:20]}...")
    
    try:
        print("\nВызываем get_key_monthly_traffic...")
        monthly_traffic = await get_key_monthly_traffic(key_uuid, 'v2ray', server_config)
        print(f"Результат: {monthly_traffic}")
        
    except Exception as e:
        print(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_v2ray_admin())
