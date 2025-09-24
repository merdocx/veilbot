#!/usr/bin/env python3
"""
Тест отображения трафика в админке
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

async def test_admin_display():
    """Тестирует отображение трафика в админке"""
    
    print("=== Тест отображения трафика в админке ===")
    
    # Получаем ключи из репозитория
    repo = KeyRepository('/root/veilbot/vpn.db')
    rows = repo.list_keys_unified(limit=10, sort_by='created_at', sort_order='DESC')
    
    print(f"Найдено ключей: {len(rows)}")
    
    # Обрабатываем ключи как в админке
    keys_with_traffic = []
    for key in rows:
        if len(key) > 8 and key[8] == 'v2ray':
            try:
                # Use api_url and api_key from columns (added in repository)
                api_url = key[9] if len(key) > 9 else ''
                api_key = key[10] if len(key) > 10 else ''
                server_config = {'api_url': api_url or '', 'api_key': api_key or ''}
                monthly_traffic = await get_key_monthly_traffic(key[1], 'v2ray', server_config)
                key_with_traffic = list(key) + [monthly_traffic]
                keys_with_traffic.append(key_with_traffic)
                print(f"V2Ray ключ ID {key[0]}: {monthly_traffic}")
            except Exception as e:
                logging.error(f"Error getting traffic for V2Ray key {key[1]}: {e}")
                key_with_traffic = list(key) + ["Error"]
                keys_with_traffic.append(key_with_traffic)
        else:
            key_with_traffic = list(key) + ["N/A"]
            keys_with_traffic.append(key_with_traffic)
    
    print(f"\nОбработано ключей: {len(keys_with_traffic)}")
    
    # Проверяем V2Ray ключи
    v2ray_keys = [k for k in keys_with_traffic if len(k) > 8 and k[8] == 'v2ray']
    print(f"V2Ray ключей: {len(v2ray_keys)}")
    
    for key in v2ray_keys:
        print(f"ID: {key[0]}, Protocol: {key[8]}, Traffic: {key[-1] if len(key) > 12 else 'N/A'}")

if __name__ == "__main__":
    asyncio.run(test_admin_display())
