import asyncio
import aiohttp
import uuid
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from outline import create_key as outline_create_key, delete_key as outline_delete_key

logger = logging.getLogger(__name__)

class VPNProtocol(ABC):
    """Абстрактный класс для VPN протоколов"""
    
    @abstractmethod
    async def create_user(self, email: str, level: int = 0) -> Dict:
        """Создать пользователя"""
        pass
    
    @abstractmethod
    async def delete_user(self, user_id: str) -> bool:
        """Удалить пользователя"""
        pass
    
    @abstractmethod
    async def get_user_config(self, user_id: str, server_config: Dict) -> str:
        """Получить конфигурацию пользователя"""
        pass
    
    @abstractmethod
    async def get_traffic_stats(self) -> List[Dict]:
        """Получить статистику трафика"""
        pass

class OutlineProtocol(VPNProtocol):
    """Реализация для Outline VPN"""
    
    def __init__(self, api_url: str, cert_sha256: str):
        self.api_url = api_url
        self.cert_sha256 = cert_sha256
    
    async def create_user(self, email: str, level: int = 0) -> Dict:
        """Создать пользователя Outline"""
        try:
            # Используем существующую функцию outline.py
            key_data = await asyncio.get_event_loop().run_in_executor(
                None, outline_create_key, self.api_url, self.cert_sha256
            )
            
            if key_data:
                return {
                    'id': key_data['id'],
                    'accessUrl': key_data['accessUrl'],
                    'name': email,
                    'password': '',
                    'port': key_data.get('port', 443),
                    'method': 'chacha20-ietf-poly1305'
                }
            else:
                raise Exception("Failed to create Outline key")
                
        except Exception as e:
            logger.error(f"Error creating Outline user: {e}")
            raise
    
    async def delete_user(self, user_id: str) -> bool:
        """Удалить пользователя Outline"""
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, outline_delete_key, self.api_url, self.cert_sha256, user_id
            )
            return result
        except Exception as e:
            logger.error(f"Error deleting Outline user: {e}")
            return False
    
    async def get_user_config(self, user_id: str, server_config: Dict) -> str:
        """Получить конфигурацию Outline пользователя"""
        # Для Outline возвращаем accessUrl как есть
        return server_config.get('accessUrl', '')
    
    async def get_traffic_stats(self) -> List[Dict]:
        """Получить статистику трафика Outline"""
        # Outline не предоставляет детальную статистику через API
        # Возвращаем пустой список
        return []

class V2RayProtocol(VPNProtocol):
    """Реализация для V2Ray VLESS с новым API"""
    
    def __init__(self, api_url: str, api_key: str = None):
        self.api_url = api_url.rstrip('/')
        # API требует аутентификации
        self.headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        if api_key:
            self.headers['X-API-Key'] = api_key
    
    async def create_user(self, email: str, level: int = 0) -> Dict:
        """Создать пользователя V2Ray через новый API"""
        try:
            async with aiohttp.ClientSession() as session:
                # Создаем ключ через новый API
                key_data = {
                    "name": email
                }
                
                print(f"Creating V2Ray key with name: {email}")
                
                async with session.post(
                    f"{self.api_url}/keys",
                    headers=self.headers,
                    json=key_data
                ) as response:
                    response_text = await response.text()
                    print(f"V2Ray create response status: {response.status}")
                    print(f"V2Ray create response text: {response_text}")
                    
                    if response.status == 200:
                        try:
                            result = await response.json()
                            
                            # Валидация ответа сервера
                            if not result.get('id'):
                                raise Exception(f"V2Ray API did not return key id - {response_text}")
                            
                            key_id = result.get('id')
                            uuid_value = result.get('uuid')
                            
                            print(f"Successfully created V2Ray key {key_id} with UUID {uuid_value}")
                            
                            return {
                                'id': key_id,
                                'uuid': uuid_value,
                                'name': email,
                                'created_at': result.get('created_at'),
                                'is_active': result.get('is_active', True)
                            }
                        except Exception as parse_error:
                            raise Exception(f"Failed to parse V2Ray API response: {parse_error} - Response: {response_text}")
                    else:
                        raise Exception(f"V2Ray API error: {response.status} - {response_text}")
                        
        except Exception as e:
            logger.error(f"Error creating V2Ray user: {e}")
            raise
    
    async def delete_user(self, user_id: str) -> bool:
        """Удалить пользователя V2Ray через новый API"""
        try:
            print(f"Attempting to delete V2Ray key {user_id} from {self.api_url}")
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{self.api_url}/keys/{user_id}",
                    headers=self.headers
                ) as response:
                    print(f"V2Ray delete response status: {response.status}")
                    response_text = await response.text()
                    print(f"V2Ray delete response text: {response_text}")
                    
                    if response.status == 200:
                        try:
                            result = await response.json()
                            message = result.get('message', '')
                            if 'deleted successfully' in message.lower():
                                print(f"Successfully deleted V2Ray key {user_id}")
                                return True
                            else:
                                print(f"Failed to delete V2Ray key {user_id} - unexpected message: {message}")
                                return False
                        except Exception as parse_error:
                            # Если не удалось распарсить JSON, считаем успешным если статус 200
                            print(f"Successfully deleted V2Ray key {user_id} (status 200, parse error: {parse_error})")
                            return True
                    else:
                        print(f"Failed to delete V2Ray key {user_id} - status {response.status}")
                        return False
        except Exception as e:
            print(f"Error deleting V2Ray key {user_id}: {e}")
            logger.error(f"Error deleting V2Ray key: {e}")
            return False
    
    async def get_user_config(self, user_id: str, server_config: Dict) -> str:
        """Получить конфигурацию V2Ray пользователя через новый API"""
        try:
            # Получаем конфигурацию через новый API
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}/keys/{user_id}/config",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get('client_config'):
                            # Извлекаем только VLESS URL из client_config
                            client_config = result['client_config']
                            # Ищем VLESS URL в конфигурации
                            if 'vless://' in client_config:
                                # Извлекаем строку, начинающуюся с vless://
                                lines = client_config.split('\n')
                                for line in lines:
                                    if line.strip().startswith('vless://'):
                                        return line.strip()
                            # Если не нашли VLESS URL, возвращаем всю конфигурацию
                            return client_config
                    
                    # Если API не работает, используем fallback
                    domain = server_config.get('domain', 'veil-bird.ru')
                    port = server_config.get('port', 443)
                    email = server_config.get('email', 'VeilBot-V2Ray')
                    
                    # Используем новый формат VLESS с Reality
                    return f"vless://{user_id}@{domain}:{port}?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email}"
                    
        except Exception as e:
            logger.error(f"Error getting V2Ray user config: {e}")
            # Fallback к базовой конфигурации
            domain = server_config.get('domain', 'veil-bird.ru')
            port = server_config.get('port', 443)
            email = server_config.get('email', 'VeilBot-V2Ray')
            
            return f"vless://{user_id}@{domain}:{port}?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email}"
    
    async def get_traffic_stats(self) -> List[Dict]:
        """Получить статистику трафика V2Ray через новый API"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}/traffic/exact",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        # Новая структура ответа
                        total_keys = result.get('total_keys', 0)
                        active_keys = result.get('active_keys', 0)
                        total_traffic = result.get('total_traffic', '0 B')
                        
                        traffic_stats = result.get('traffic_stats', {})
                        keys_stats = traffic_stats.get('keys_stats', [])
                        
                        # Преобразуем в формат, совместимый с существующим кодом
                        stats_list = []
                        for key_stat in keys_stats:
                            stats_list.append({
                                'uuid': key_stat.get('uuid'),
                                'uplink_bytes': key_stat.get('uplink_bytes', 0),
                                'downlink_bytes': key_stat.get('downlink_bytes', 0),
                                'total_bytes': key_stat.get('total_bytes', 0),
                                'uplink_formatted': key_stat.get('uplink_formatted', '0 B'),
                                'downlink_formatted': key_stat.get('downlink_formatted', '0 B'),
                                'total_formatted': key_stat.get('total_formatted', '0 B'),
                                'uplink_mb': key_stat.get('uplink_mb', 0),
                                'downlink_mb': key_stat.get('downlink_mb', 0),
                                'total_mb': key_stat.get('total_mb', 0),
                                'connections': key_stat.get('connections', 0),
                                'connection_ratio': key_stat.get('connection_ratio', 0.0),
                                'connections_count': key_stat.get('connections_count', 0),
                                'timestamp': key_stat.get('timestamp'),
                                'source': key_stat.get('source', 'alternative_monitor'),
                                'method': key_stat.get('method', 'network_distribution'),
                                # Дополнительные поля из новой структуры
                                'total_keys': total_keys,
                                'active_keys': active_keys,
                                'total_traffic': total_traffic
                            })
                        
                        return stats_list
                    else:
                        logger.error(f"Failed to get V2Ray stats: {response.status}")
                        return []
        except Exception as e:
            logger.error(f"Error getting V2Ray traffic stats: {e}")
            return []
    
    async def get_key_traffic_stats(self, key_id: str) -> Dict:
        """Получить статистику трафика конкретного ключа"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}/keys/{key_id}/traffic/exact",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        traffic_bytes = result.get('traffic_bytes', {})
                        
                        return {
                            'uuid': traffic_bytes.get('uuid'),
                            'uplink_bytes': traffic_bytes.get('uplink_bytes', 0),
                            'downlink_bytes': traffic_bytes.get('downlink_bytes', 0),
                            'total_bytes': traffic_bytes.get('total_bytes', 0),
                            'uplink_formatted': traffic_bytes.get('uplink_formatted', '0 B'),
                            'downlink_formatted': traffic_bytes.get('downlink_formatted', '0 B'),
                            'total_formatted': traffic_bytes.get('total_formatted', '0 B'),
                            'uplink_mb': traffic_bytes.get('uplink_mb', 0),
                            'downlink_mb': traffic_bytes.get('downlink_mb', 0),
                            'total_mb': traffic_bytes.get('total_mb', 0),
                            'connections': traffic_bytes.get('connections', 0),
                            'connection_ratio': traffic_bytes.get('connection_ratio', 0.0),
                            'connections_count': traffic_bytes.get('connections_count', 0),
                            'timestamp': traffic_bytes.get('timestamp'),
                            'source': traffic_bytes.get('source', 'alternative_monitor'),
                            'method': traffic_bytes.get('method', 'network_distribution')
                        }
                    else:
                        logger.error(f"Failed to get V2Ray key stats: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray key traffic stats: {e}")
            return {}
    
    async def reset_key_traffic(self, key_id: str) -> bool:
        """Сбросить статистику трафика ключа"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/keys/{key_id}/traffic/reset",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        message = result.get('message', '')
                        if 'reset successfully' in message.lower():
                            logger.info(f"Successfully reset traffic stats for key {key_id}")
                            return True
                        else:
                            logger.warning(f"Unexpected reset response: {message}")
                    else:
                        logger.error(f"Failed to reset traffic stats: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Error resetting V2Ray key traffic: {e}")
            return False
    
    async def get_traffic_status(self) -> Dict:
        """Получить статус системы мониторинга трафика"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}/traffic/status",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return {
                            'total_keys': result.get('total_keys', 0),
                            'active_keys': result.get('active_keys', 0),
                            'precise_monitor_available': result.get('precise_monitor_available', False),
                            'traffic_stats': result.get('traffic_stats', [])
                        }
                    else:
                        logger.error(f"Failed to get traffic status: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray traffic status: {e}")
            return {}
    
    async def get_system_config_status(self) -> Dict:
        """Получить статус синхронизации конфигурации"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}/system/config-status",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return {
                            'synchronized': result.get('synchronized', False),
                            'keys_json_count': result.get('keys_json_count', 0),
                            'config_json_count': result.get('config_json_count', 0),
                            'keys_json_uuids': result.get('keys_json_uuids', []),
                            'config_json_uuids': result.get('config_json_uuids', []),
                            'timestamp': result.get('timestamp')
                        }
                    else:
                        logger.error(f"Failed to get config status: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray config status: {e}")
            return {}
    
    async def sync_system_config(self) -> bool:
        """Принудительная синхронизация конфигурации"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/system/sync-config",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        message = result.get('message', '')
                        if 'synchronized successfully' in message.lower():
                            logger.info("Successfully synchronized system config")
                            return True
                        else:
                            logger.warning(f"Unexpected sync response: {message}")
                    else:
                        logger.error(f"Failed to sync config: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Error syncing V2Ray system config: {e}")
            return False
    
    async def verify_reality_settings(self) -> bool:
        """Проверить и обновить настройки Reality протокола"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/system/verify-reality",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        message = result.get('message', '')
                        if 'verified and updated successfully' in message.lower():
                            logger.info("Successfully verified and updated Reality settings")
                            return True
                        else:
                            logger.warning(f"Unexpected verify response: {message}")
                    else:
                        logger.error(f"Failed to verify Reality settings: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Error verifying V2Ray Reality settings: {e}")
            return False
    
    async def get_api_status(self) -> Dict:
        """Получить статус API"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}/",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return {
                            'message': result.get('message', ''),
                            'version': result.get('version', ''),
                            'status': result.get('status', '')
                        }
                    else:
                        logger.error(f"Failed to get API status: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray API status: {e}")
            return {}

class ProtocolFactory:
    """Фабрика для создания протоколов"""
    
    @staticmethod
    def create_protocol(protocol_type: str, server_config: Dict) -> VPNProtocol:
        """Создать экземпляр протокола по типу"""
        if protocol_type == 'outline':
            return OutlineProtocol(
                server_config['api_url'], 
                server_config['cert_sha256']
            )
        elif protocol_type == 'v2ray':
            return V2RayProtocol(
                server_config['api_url'],
                server_config.get('api_key')
            )
        else:
            raise ValueError(f"Неизвестный протокол: {protocol_type}")

def get_protocol_instructions(protocol: str) -> str:
    """Получить инструкции по подключению для протокола"""
    if protocol == 'outline':
        return (
            "1. Установите Outline:\n"
            "   • [App Store](https://apps.apple.com/app/outline-app/id1356177741)\n"
            "   • [Google Play](https://play.google.com/store/apps/details?id=org.outline.android.client)\n"
            "2. Откройте приложение и нажмите «Добавить сервер»\n"
            "3. Вставьте ключ выше"
        )
    else:  # v2ray
        return (
            "1. Установите V2Ray клиент:\n"
            "   • [App Store](https://apps.apple.com/ru/app/v2raytun/id6476628951)\n"
            "   • [Google Play](https://play.google.com/store/apps/details?id=com.v2raytun.android)\n"
            "2. Откройте приложение и нажмите «+»\n"
            "3. Выберите «Импорт из буфера обмена»\n"
            "4. Вставьте ключ выше"
        )

def format_duration(seconds: int) -> str:
    """Форматировать длительность в человекочитаемый вид"""
    if seconds < 60:
        return f"{seconds} секунд"
    elif seconds < 3600:
        return f"{seconds // 60} минут"
    elif seconds < 86400:
        return f"{seconds // 3600} часов"
    else:
        return f"{seconds // 86400} дней" 