#!/usr/bin/env python3
"""
Тест подсчета трафика для V2Ray ключей
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from vpn_protocols import V2RayProtocol
import logging

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

async def test_v2ray_traffic():
    """Тестирует получение трафика для V2Ray ключей"""
    
    # Данные сервера из базы
    api_url = "https://veil-bird.ru/api"
    api_key = "QBDMqDzCRh17NIGUsKDtWtoUmvwRVvSHHp4W8OCMcOM="
    key_uuid = "48e4c686-2320-49e5-8997-a5478860114c"
    
    print("=== Тест V2Ray трафика ===")
    print(f"API URL: {api_url}")
    print(f"API Key: {api_key[:20]}...")
    print(f"Key UUID: {key_uuid}")
    
    try:
        # Создаем экземпляр V2Ray протокола
        v2ray = V2RayProtocol(api_url, api_key)
        
        # Тест 1: Проверка доступности API
        print("\n1. Проверка доступности API...")
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{api_url}/", verify_ssl=False) as response:
                    print(f"   Статус: {response.status}")
                    if response.status == 200:
                        print("   ✅ API доступен")
                    else:
                        print("   ❌ API недоступен")
        except Exception as e:
            print(f"   ❌ Ошибка подключения: {e}")
        
        # Тест 2: Получение месячного трафика
        print("\n2. Получение месячного трафика...")
        monthly_traffic = await v2ray.get_monthly_traffic()
        print(f"   Результат: {monthly_traffic}")
        
        if monthly_traffic and monthly_traffic.get('data'):
            data = monthly_traffic.get('data', {})
            keys = data.get('keys', [])
            print(f"   Найдено ключей: {len(keys)}")
            
            # Ищем наш ключ
            for key in keys:
                if key.get('key_uuid') == key_uuid:
                    monthly_traffic_data = key.get('monthly_traffic', {})
                    total_bytes = monthly_traffic_data.get('total_bytes', 0)
                    print(f"   ✅ Найден ключ: {total_bytes} байт")
                    break
            else:
                print(f"   ❌ Ключ {key_uuid} не найден в месячных данных")
        else:
            print("   ❌ Нет данных о месячном трафике")
        
        # Тест 3: Получение общего трафика
        print("\n3. Получение общего трафика...")
        traffic_history = await v2ray.get_traffic_history()
        print(f"   Результат: {traffic_history}")
        
        if traffic_history and traffic_history.get('data'):
            data = traffic_history.get('data', {})
            keys = data.get('keys', [])
            print(f"   Найдено ключей: {len(keys)}")
            
            # Ищем наш ключ
            for key in keys:
                if key.get('key_uuid') == key_uuid:
                    total_traffic = key.get('total_traffic', {})
                    total_bytes = total_traffic.get('total_bytes', 0)
                    print(f"   ✅ Найден ключ: {total_bytes} байт")
                    break
            else:
                print(f"   ❌ Ключ {key_uuid} не найден в общих данных")
        else:
            print("   ❌ Нет данных об общем трафике")
        
        # Тест 4: Получение трафика конкретного ключа
        print("\n4. Получение трафика конкретного ключа...")
        key_traffic = await v2ray.get_key_traffic_history(key_uuid)
        print(f"   Результат: {key_traffic}")
        
        # Закрываем сессию
        await v2ray._session.close()
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_v2ray_traffic())
