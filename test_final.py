#!/usr/bin/env python3
"""
Финальный тест подсчета трафика для V2Ray ключей
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.repositories.key_repository import KeyRepository
from admin.admin_routes import get_key_monthly_traffic
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_final():
    """Финальный тест подсчета трафика"""
    
    print("=== Финальный тест подсчета трафика ===")
    
    # Получаем ключи из репозитория
    repo = KeyRepository('/root/veilbot/vpn.db')
    rows = repo.list_keys_unified(limit=10, sort_by='created_at', sort_order='DESC')
    
    print(f"Найдено ключей: {len(rows)}")
    
    # Находим V2Ray ключ
    v2ray_key = None
    for key in rows:
        if len(key) > 8 and key[8] == 'v2ray':
            v2ray_key = key
            break
    
    if not v2ray_key:
        print("V2Ray ключ не найден")
        return
    
    print(f"\nV2Ray ключ найден:")
    print(f"  ID: {v2ray_key[0]}")
    print(f"  UUID: {v2ray_key[1]}")
    print(f"  Protocol: {v2ray_key[8]}")
    print(f"  API URL (index 9): '{v2ray_key[9] if len(v2ray_key) > 9 else 'N/A'}'")
    print(f"  API Key (index 10): '{v2ray_key[10][:30] if len(v2ray_key) > 10 and v2ray_key[10] else 'N/A'}...'")
    
    # Тестируем подсчет трафика
    try:
        api_url = v2ray_key[9] if len(v2ray_key) > 9 else ''
        api_key = v2ray_key[10] if len(v2ray_key) > 10 else ''
        server_config = {'api_url': api_url or '', 'api_key': api_key or ''}
        
        print(f"\nВызываем get_key_monthly_traffic:")
        print(f"  UUID: {v2ray_key[1]}")
        print(f"  API URL: '{api_url}'")
        print(f"  API Key: '{api_key[:20] if api_key else 'N/A'}...'")
        
        monthly_traffic = await get_key_monthly_traffic(v2ray_key[1], 'v2ray', server_config)
        print(f"  Результат: {monthly_traffic}")
        
    except Exception as e:
        print(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_final())
