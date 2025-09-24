#!/usr/bin/env python3
"""
Тест подсчета трафика в админке
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.repositories.key_repository import KeyRepository
from admin.admin_routes import get_key_monthly_traffic
import logging

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

async def test_admin_traffic():
    """Тестирует подсчет трафика в админке"""
    
    print("=== Тест подсчета трафика в админке ===")
    
    # Получаем ключи из репозитория
    repo = KeyRepository('/root/veilbot/vpn.db')
    rows = repo.list_keys_unified(limit=5, sort_by='created_at', sort_order='DESC')
    
    print(f"Найдено ключей: {len(rows)}")
    
    for i, key in enumerate(rows):
        print(f"\n--- Ключ {i+1} ---")
        print(f"ID: {key[0]}")
        print(f"Key ID/UUID: {key[1]}")
        print(f"Access URL: {key[2][:50]}...")
        print(f"Protocol: {key[8] if len(key) > 8 else 'N/A'}")
        print(f"API URL: {key[9] if len(key) > 9 else 'N/A'}")
        print(f"API Key: {key[10][:20] if len(key) > 10 and key[10] else 'N/A'}...")
        
        if len(key) > 8 and key[8] == 'v2ray':
            try:
                # Используем те же параметры, что и в админке
                api_url = key[9] if len(key) > 9 else ''
                api_key = key[10] if len(key) > 10 else ''
                server_config = {'api_url': api_url or '', 'api_key': api_key or ''}
                
                print(f"Вызываем get_key_monthly_traffic с UUID: {key[1]}")
                monthly_traffic = await get_key_monthly_traffic(key[1], 'v2ray', server_config)
                print(f"Результат: {monthly_traffic}")
                
            except Exception as e:
                print(f"Ошибка: {e}")
                import traceback
                traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_admin_traffic())
