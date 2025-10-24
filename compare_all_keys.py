#!/usr/bin/env python3
"""
Скрипт для сравнения всех ключей на серверах и в базе данных
"""

import sqlite3
import requests
import json
import logging
from typing import Dict, List, Tuple, Set
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class KeyComparator:
    def __init__(self, db_path: str = "vpn.db"):
        self.db_path = db_path
        
    def get_servers_from_db(self) -> List[Tuple]:
        """Получить список серверов из базы данных"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, api_url, cert_sha256, protocol, domain, api_key 
            FROM servers WHERE active = 1
        """)
        servers = cursor.fetchall()
        conn.close()
        return servers
    
    def get_outline_keys_from_server(self, api_url: str, cert_sha256: str) -> List[Dict]:
        """Получить ключи с Outline сервера"""
        try:
            response = requests.get(
                f"{api_url}/access-keys",
                verify=False,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            
            if isinstance(data, dict) and 'accessKeys' in data:
                return data['accessKeys']
            elif isinstance(data, list):
                return data
            else:
                logger.warning(f"Неожиданный формат ответа от {api_url}: {type(data)}")
                return []
        except Exception as e:
            logger.error(f"Ошибка при получении ключей с Outline сервера {api_url}: {e}")
            return []
    
    def get_v2ray_keys_from_server(self, api_url: str, api_key: str) -> List[Dict]:
        """Получить ключи с V2Ray сервера"""
        try:
            headers = {
                "Content-Type": "application/json",
                "X-API-Key": api_key
            }
            
            response = requests.get(
                f"{api_url}/keys",
                headers=headers,
                verify=False,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and 'keys' in data:
                return data['keys']
            else:
                logger.warning(f"Неожиданный формат ответа от V2Ray сервера {api_url}: {type(data)}")
                return []
        except Exception as e:
            logger.error(f"Ошибка при получении ключей с V2Ray сервера {api_url}: {e}")
            return []
    
    def get_outline_keys_from_db(self, server_id: int) -> List[Dict]:
        """Получить Outline ключи из базы данных для конкретного сервера"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT key_id, access_url, user_id, email, created_at, expiry_at
            FROM keys WHERE server_id = ?
        """, (server_id,))
        
        keys = []
        for row in cursor.fetchall():
            keys.append({
                'id': row[0],
                'accessUrl': row[1],
                'user_id': row[2],
                'email': row[3],
                'created_at': row[4],
                'expiry_at': row[5]
            })
        conn.close()
        return keys
    
    def get_v2ray_keys_from_db(self, server_id: int) -> List[Dict]:
        """Получить V2Ray ключи из базы данных для конкретного сервера"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT v2ray_uuid, user_id, email, created_at, expiry_at
            FROM v2ray_keys WHERE server_id = ?
        """, (server_id,))
        
        keys = []
        for row in cursor.fetchall():
            keys.append({
                'id': row[0],
                'user_id': row[1],
                'email': row[2],
                'created_at': row[3],
                'expiry_at': row[4]
            })
        conn.close()
        return keys
    
    def compare_outline_server(self, server_id: int, name: str, api_url: str, cert_sha256: str) -> Dict:
        """Сравнить Outline ключи между сервером и БД"""
        logger.info(f"Сравнение Outline сервера {name} (ID: {server_id})")
        
        # Получаем ключи с сервера
        server_keys = self.get_outline_keys_from_server(api_url, cert_sha256)
        server_key_ids = {key.get('id') for key in server_keys if key.get('id')}
        
        # Получаем ключи из БД
        db_keys = self.get_outline_keys_from_db(server_id)
        db_key_ids = {key.get('id') for key in db_keys if key.get('id')}
        
        # Находим различия
        only_on_server = server_key_ids - db_key_ids
        only_in_db = db_key_ids - server_key_ids
        common_keys = server_key_ids & db_key_ids
        
        result = {
            'server_name': name,
            'server_id': server_id,
            'protocol': 'outline',
            'server_keys_count': len(server_keys),
            'db_keys_count': len(db_keys),
            'only_on_server': list(only_on_server),
            'only_in_db': list(only_in_db),
            'common_keys': list(common_keys),
            'is_synced': len(only_on_server) == 0 and len(only_in_db) == 0,
            'server_keys': server_keys,
            'db_keys': db_keys
        }
        
        return result
    
    def compare_v2ray_server(self, server_id: int, name: str, api_url: str, api_key: str) -> Dict:
        """Сравнить V2Ray ключи между сервером и БД"""
        logger.info(f"Сравнение V2Ray сервера {name} (ID: {server_id})")
        
        # Получаем ключи с сервера
        server_keys = self.get_v2ray_keys_from_server(api_url, api_key)
        # Для V2Ray сервер возвращает 'uuid', а в БД хранится как 'id'
        server_key_ids = {key.get('uuid') for key in server_keys if key.get('uuid')}
        
        # Получаем ключи из БД
        db_keys = self.get_v2ray_keys_from_db(server_id)
        db_key_ids = {key.get('id') for key in db_keys if key.get('id')}
        
        # Находим различия
        only_on_server = server_key_ids - db_key_ids
        only_in_db = db_key_ids - server_key_ids
        common_keys = server_key_ids & db_key_ids
        
        result = {
            'server_name': name,
            'server_id': server_id,
            'protocol': 'v2ray',
            'server_keys_count': len(server_keys),
            'db_keys_count': len(db_keys),
            'only_on_server': list(only_on_server),
            'only_in_db': list(only_in_db),
            'common_keys': list(common_keys),
            'is_synced': len(only_on_server) == 0 and len(only_in_db) == 0,
            'server_keys': server_keys,
            'db_keys': db_keys
        }
        
        return result
    
    def compare_all_servers(self) -> Dict:
        """Сравнить ключи для всех серверов"""
        logger.info("Начинаем сравнение всех серверов...")
        
        servers = self.get_servers_from_db()
        results = {}
        synced_count = 0
        
        for server in servers:
            server_id, name, api_url, cert_sha256, protocol, domain, api_key = server
            
            if protocol == 'outline':
                result = self.compare_outline_server(server_id, name, api_url, cert_sha256)
                results[f"outline_{server_id}"] = result
                
            elif protocol == 'v2ray':
                result = self.compare_v2ray_server(server_id, name, api_url, api_key)
                results[f"v2ray_{server_id}"] = result
            
            if result['is_synced']:
                synced_count += 1
        
        summary = {
            'total_servers': len(servers),
            'synced_servers': synced_count,
            'results': results
        }
        
        logger.info(f"Сравнение завершено: {synced_count}/{len(servers)} серверов синхронизированы")
        
        return summary

def main():
    """Основная функция"""
    print("=== Сравнение всех ключей на серверах и в базе данных ===")
    print(f"Время запуска: {datetime.now()}")
    
    comparator = KeyComparator()
    
    # Запускаем сравнение
    print("\nСравнение ключей...")
    results = comparator.compare_all_servers()
    
    # Выводим результаты
    print("\n=== РЕЗУЛЬТАТЫ СРАВНЕНИЯ ===")
    print(f"Проверено серверов: {results['total_servers']}")
    print(f"Синхронизировано: {results['synced_servers']}")
    
    print("\nДетали по серверам:")
    for key, result in results['results'].items():
        status = "✅" if result['is_synced'] else "❌"
        print(f"\n{status} {result['server_name']} ({result['protocol']})")
        print(f"  Ключей на сервере: {result['server_keys_count']}")
        print(f"  Ключей в БД: {result['db_keys_count']}")
        print(f"  Только на сервере: {len(result['only_on_server'])}")
        print(f"  Только в БД: {len(result['only_in_db'])}")
        print(f"  Общие: {len(result['common_keys'])}")
        
        if result['only_on_server']:
            print(f"  Ключи только на сервере: {result['only_on_server']}")
        if result['only_in_db']:
            print(f"  Ключи только в БД: {result['only_in_db']}")
    
    if results['synced_servers'] == results['total_servers']:
        print("\n🎉 Все серверы синхронизированы!")
    else:
        print(f"\n⚠️  {results['total_servers'] - results['synced_servers']} серверов требуют синхронизации.")
    
    print(f"\nВремя завершения: {datetime.now()}")

if __name__ == "__main__":
    main()

