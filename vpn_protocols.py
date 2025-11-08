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

    async def get_all_keys(self) -> List[Dict]:
        """Получить все ключи с Outline сервера"""
        try:
            from outline import get_keys
            keys = await asyncio.get_event_loop().run_in_executor(
                None, get_keys, self.api_url, self.cert_sha256
            )
            return keys
        except Exception as e:
            logger.error(f"Error getting Outline keys: {e}")
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
        
        # Настройка SSL контекста для самоподписанных сертификатов
        import ssl
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

        # Общая сессия aiohttp с таймаутами
        self._timeout = aiohttp.ClientTimeout(total=30, connect=5, sock_connect=5, sock_read=25)
        self._connector = aiohttp.TCPConnector(ssl=self.ssl_context, force_close=False, enable_cleanup_closed=True)
        self._session = aiohttp.ClientSession(connector=self._connector, timeout=self._timeout)
    
    async def create_user(self, email: str, level: int = 0) -> Dict:
        """Создать пользователя V2Ray через новый API"""
        try:
            session = self._session
            # Создаем ключ через новый API
            key_data = {
                "name": email
            }
                
            logger.debug(f"Creating V2Ray key with name: {email}")
            logger.debug(f"V2Ray API URL: {self.api_url}/keys")
            logger.debug(f"V2Ray headers present: {list(self.headers.keys())}")
            logger.debug(f"V2Ray key data: {key_data}")
                
            # Сначала проверим статус API сервера
            try:
                async with session.get(f"{self.api_url}/", headers=self.headers) as status_response:
                    logger.debug(f"V2Ray API status check: {status_response.status}")
                    if status_response.status != 200:
                        logger.warning(f"V2Ray API status check failed: {status_response.status}")
            except Exception as status_error:
                logger.warning(f"Could not check V2Ray API status: {status_error}")
                
            async with session.post(
                f"{self.api_url}/keys",
                headers=self.headers,
                json=key_data
            ) as response:
                response_text = await response.text()
                logger.debug(f"V2Ray create response status: {response.status}")
                logger.debug(f"V2Ray create response text: {response_text}")
                
                if response.status == 200:
                    try:
                        result = await response.json()
                        
                        # Проверяем, что результат - это словарь, а не список
                        if isinstance(result, list):
                            if len(result) > 0:
                                # Если это список с одним элементом, берем первый
                                result = result[0]
                            else:
                                # Если API возвращает пустой список, попробуем альтернативный подход
                                print(f"V2Ray API returned empty list, trying alternative approach...")
                                # Попробуем создать ключ с другими параметрами
                                alternative_key_data = {
                                    "name": email,
                                    "email": email
                                }
                                
                                async with session.post(
                                    f"{self.api_url}/keys",
                                    headers=self.headers,
                                    json=alternative_key_data
                                ) as alt_response:
                                    alt_response_text = await alt_response.text()
                                    logger.debug(f"Alternative V2Ray create response status: {alt_response.status}")
                                    logger.debug(f"Alternative V2Ray create response text: {alt_response_text}")
                                    
                                    if alt_response.status == 200:
                                        alt_result = await alt_response.json()
                                        if isinstance(alt_result, list) and len(alt_result) > 0:
                                            result = alt_result[0]
                                        elif isinstance(alt_result, dict):
                                            result = alt_result
                                        else:
                                            raise Exception(f"V2Ray API still returned empty response - {alt_response_text}")
                                    else:
                                        raise Exception(f"V2Ray API alternative request failed: {alt_response.status} - {alt_response_text}")
                        
                        # Валидация ответа сервера
                        if not result.get('id'):
                            raise Exception(f"V2Ray API did not return key id - {response_text}")
                        
                        key_id = result.get('id')
                        uuid_value = result.get('uuid')
                        
                        logger.info(f"Successfully created V2Ray key {key_id} with UUID {uuid_value}")
                        
                        # Извлекаем client_config из ответа API, если он есть
                        client_config = None
                        logger.debug(f"Checking for client_config in API response. Keys: {list(result.keys())}")
                        logger.debug(f"Full API response (sanitized): {str(result).replace(email, 'EMAIL')[:500]}")
                        
                        if result.get('client_config'):
                            client_config = result['client_config']
                            logger.info(f"Found client_config in result.client_config for email {email}")
                            # Извлекаем VLESS URL из client_config
                            if 'vless://' in client_config:
                                lines = client_config.split('\n')
                                for line in lines:
                                    if line.strip().startswith('vless://'):
                                        client_config = line.strip()
                                        # Проверяем наличие SNI и shortid в конфигурации
                                        if 'sni=' in client_config and 'sid=' in client_config:
                                            logger.info(f"client_config contains SNI and shortid: sni and sid found")
                                        else:
                                            logger.warning(f"client_config does not contain SNI or shortid!")
                                        break
                            logger.debug(f"Extracted client_config: {client_config[:100]}...")
                        elif result.get('key') and isinstance(result.get('key'), dict) and result['key'].get('client_config'):
                            client_config = result['key']['client_config']
                            logger.info(f"Found client_config in result.key.client_config for email {email}")
                            if 'vless://' in client_config:
                                lines = client_config.split('\n')
                                for line in lines:
                                    if line.strip().startswith('vless://'):
                                        client_config = line.strip()
                                        # Проверяем наличие SNI и shortid в конфигурации
                                        if 'sni=' in client_config and 'sid=' in client_config:
                                            logger.info(f"client_config contains SNI and shortid: sni and sid found")
                                        else:
                                            logger.warning(f"client_config does not contain SNI or shortid!")
                                        break
                            logger.debug(f"Extracted client_config from key: {client_config[:100]}...")
                        else:
                            logger.warning(f"No client_config found in create_user response for email {email}. Will need to fetch via get_user_config")
                        
                        return {
                            'id': key_id,
                            'uuid': uuid_value,
                            'name': email,
                            'created_at': result.get('created_at'),
                            'is_active': result.get('is_active', True),
                            'port': result.get('port'),  # Добавляем порт из нового API
                            'client_config': client_config  # Добавляем client_config, если он есть
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
            logger.info(f"Attempting to delete V2Ray key {user_id} from {self.api_url}")
            session = self._session
            async with session.delete(
                    f"{self.api_url}/keys/{user_id}",
                    headers=self.headers
                ) as response:
                    logger.debug(f"V2Ray delete response status: {response.status}")
                    response_text = await response.text()
                    logger.debug(f"V2Ray delete response text: {response_text}")
                    
                    if response.status == 200:
                        try:
                            result = await response.json()
                            message = result.get('message', '')
                            if 'deleted successfully' in message.lower():
                                logger.info(f"Successfully deleted V2Ray key {user_id}")
                                return True
                            else:
                                logger.warning(f"Failed to delete V2Ray key {user_id} - unexpected message: {message}")
                                return False
                        except Exception as parse_error:
                            # Если не удалось распарсить JSON, считаем успешным если статус 200
                            logger.info(f"Successfully deleted V2Ray key {user_id} (status 200, parse error: {parse_error})")
                            return True
                    else:
                        logger.warning(f"Failed to delete V2Ray key {user_id} - status {response.status}")
                        return False
        except Exception as e:
            logger.error(f"Error deleting V2Ray key: {e}")
            return False
    
    async def get_user_config(self, user_id: str, server_config: Dict, max_retries: int = 5, retry_delay: float = 1.0) -> str:
        """Получить конфигурацию V2Ray пользователя через новый API с повторными попытками"""
        import asyncio
        
        email = server_config.get('email', 'N/A')
        logger.info(f"[GET_CONFIG] Requesting config for UUID={user_id[:8]}..., email={email}, max_retries={max_retries}")
        
        for attempt in range(max_retries):
            try:
                # Получаем конфигурацию через новый API
                session = self._session
                config_url = f"{self.api_url}/keys/{user_id}/config"
                logger.debug(f"[GET_CONFIG] Attempt {attempt + 1}/{max_retries}: GET {config_url}")
                
                async with session.get(
                        config_url,
                        headers=self.headers
                    ) as response:
                        if response.status == 200:
                            result = await response.json()
                            logger.debug(f"[GET_CONFIG] API response status 200. Keys: {list(result.keys()) if isinstance(result, dict) else 'not a dict'}")
                            
                            # Проверяем новую структуру ответа
                            if result.get('client_config'):
                                # Извлекаем только VLESS URL из client_config
                                client_config = result['client_config']
                                logger.info(f"[GET_CONFIG] Found client_config in API response on attempt {attempt + 1}")
                                # Ищем VLESS URL в конфигурации
                                if 'vless://' in client_config:
                                    # Извлекаем строку, начинающуюся с vless://
                                    lines = client_config.split('\n')
                                    for line in lines:
                                        if line.strip().startswith('vless://'):
                                            config_line = line.strip()
                                            # Проверяем наличие SNI и shortid
                                            if 'sni=' in config_line and 'sid=' in config_line:
                                                logger.info(f"[GET_CONFIG] Successfully retrieved client_config with SNI and shortid on attempt {attempt + 1}")
                                            else:
                                                logger.warning(f"[GET_CONFIG] WARNING: Retrieved client_config without SNI or shortid on attempt {attempt + 1}")
                                            return config_line
                                # Если не нашли VLESS URL, возвращаем всю конфигурацию
                                logger.debug(f"[GET_CONFIG] Successfully retrieved client_config (non-VLESS format) on attempt {attempt + 1}")
                                return client_config
                            
                            # Если client_config не найден, проверяем альтернативную структуру
                            if result.get('key') and result.get('client_config'):
                                client_config = result['client_config']
                                if 'vless://' in client_config:
                                    lines = client_config.split('\n')
                                    for line in lines:
                                        if line.strip().startswith('vless://'):
                                            logger.debug(f"Successfully retrieved client_config from key.client_config on attempt {attempt + 1}")
                                            return line.strip()
                                logger.debug(f"Successfully retrieved client_config from key.client_config (non-VLESS format) on attempt {attempt + 1}")
                                return client_config
                            
                            # Если client_config не найден вообще, пробуем снова или используем fallback
                            if attempt < max_retries - 1:
                                logger.debug(f"API did not return client_config, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                                await asyncio.sleep(retry_delay)
                                continue
                            else:
                                # Если все попытки исчерпаны и client_config не получен, используем fallback
                                domain = server_config.get('domain', 'veil-bird.ru')
                                port = server_config.get('port', 443)
                                email = server_config.get('email', 'VeilBot-V2Ray')
                                
                                logger.warning(f"API did not return client_config after {max_retries} attempts, using fallback")
                                # Используем новый формат VLESS с Reality
                                return f"vless://{user_id}@{domain}:{port}?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email}"
                        
                        # Если статус не 200, пробуем снова или используем fallback
                        if attempt < max_retries - 1:
                            logger.debug(f"API returned status {response.status}, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            # Если все попытки исчерпаны, используем fallback
                            domain = server_config.get('domain', 'veil-bird.ru')
                            port = server_config.get('port', 443)
                            email = server_config.get('email', 'VeilBot-V2Ray')
                            
                            logger.warning(f"API returned status {response.status} after {max_retries} attempts, using fallback")
                            return f"vless://{user_id}@{domain}:{port}?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email}"
                    
            except Exception as e:
                logger.error(f"Error getting V2Ray user config (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    # Fallback к базовой конфигурации после всех попыток
                    logger.error(f"Failed to get client_config after {max_retries} attempts, using fallback")
                    domain = server_config.get('domain', 'veil-bird.ru')
                    port = server_config.get('port', 443)
                    email = server_config.get('email', 'VeilBot-V2Ray')
                    
                    return f"vless://{user_id}@{domain}:{port}?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email}"
        
        # Fallback (на случай, если цикл не вернул значение)
        domain = server_config.get('domain', 'veil-bird.ru')
        port = server_config.get('port', 443)
        email = server_config.get('email', 'VeilBot-V2Ray')
        
        return f"vless://{user_id}@{domain}:{port}?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email}"
    
    async def get_traffic_stats(self) -> List[Dict]:
        """Получить статистику трафика V2Ray через новый API с простым мониторингом"""
        try:
            session = self._session
            # Используем новый эндпоинт для простого мониторинга
            async with session.get(
                    f"{self.api_url}/traffic/simple",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        # Новая структура ответа с простым мониторингом
                        data = result.get('data', {})
                        ports = data.get('ports', {})
                        total_connections = data.get('total_connections', 0)
                        total_bytes = data.get('total_bytes', 0)
                        timestamp = data.get('timestamp')
                        
                        # Преобразуем в формат, совместимый с существующим кодом
                        stats_list = []
                        for port_key, port_data in ports.items():
                            stats_list.append({
                                'uuid': port_data.get('uuid'),
                                'port': port_data.get('port'),
                                'key_name': port_data.get('key_name', 'Unknown'),
                                'uplink_bytes': port_data.get('rx_bytes', 0),
                                'downlink_bytes': port_data.get('tx_bytes', 0),
                                'total_bytes': port_data.get('total_bytes', 0),
                                'uplink_formatted': port_data.get('rx_formatted', '0 B'),
                                'downlink_formatted': port_data.get('tx_formatted', '0 B'),
                                'total_formatted': port_data.get('total_formatted', '0 B'),
                                'uplink_mb': port_data.get('rx_bytes', 0) / (1024 * 1024),
                                'downlink_mb': port_data.get('tx_bytes', 0) / (1024 * 1024),
                                'total_mb': port_data.get('total_bytes', 0) / (1024 * 1024),
                                'connections': port_data.get('connections', 0),
                                'connection_ratio': 0.0,  # Не предоставляется в новом API
                                'connections_count': port_data.get('connections', 0),
                                'timestamp': port_data.get('timestamp'),
                                'source': result.get('source', 'simple_monitor'),
                                'method': 'connection_based_estimation',
                                # Дополнительные поля из новой структуры
                                'traffic_rate': port_data.get('traffic_rate', 0),
                                'interface_traffic': port_data.get('interface_traffic', {}),
                                'connection_details': port_data.get('connection_details', []),
                                'total_connections': total_connections,
                                'total_bytes': total_bytes,
                                'timestamp': timestamp
                            })
                        
                        return stats_list
                    else:
                        # Fallback к устаревшему эндпоинту для совместимости
                        logger.warning(f"New traffic API failed ({response.status}), trying legacy endpoint")
                        return await self._get_legacy_traffic_stats()
        except Exception as e:
            logger.error(f"Error getting V2Ray traffic stats: {e}")
            # Fallback к устаревшему эндпоинту
            return await self._get_legacy_traffic_stats()
    
    async def _get_legacy_traffic_stats(self) -> List[Dict]:
        """Получить статистику трафика через устаревший API (fallback)"""
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/traffic/exact",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        # Устаревшая структура ответа
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
                                # Дополнительные поля из устаревшей структуры
                                'total_keys': total_keys,
                                'active_keys': active_keys,
                                'total_traffic': total_traffic
                            })
                        
                        return stats_list
                    else:
                        logger.error(f"Failed to get V2Ray stats (legacy): {response.status}")
                        return []
        except Exception as e:
            logger.error(f"Error getting V2Ray legacy traffic stats: {e}")
            return []
    
    async def get_key_traffic_stats(self, key_id: str) -> Dict:
        """Получить статистику трафика конкретного ключа через новый API с простым мониторингом"""
        try:
            session = self._session
            # Используем новый эндпоинт для простого мониторинга
            async with session.get(
                    f"{self.api_url}/keys/{key_id}/traffic/simple",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        # Новая структура ответа с простым мониторингом
                        key_info = result.get('key', {})
                        traffic = result.get('traffic', {})
                        
                        return {
                            'uuid': key_info.get('uuid'),
                            'port': traffic.get('port'),
                            'key_name': key_info.get('name'),
                            'uplink_bytes': traffic.get('rx_bytes', 0),
                            'downlink_bytes': traffic.get('tx_bytes', 0),
                            'total_bytes': traffic.get('total_bytes', 0),
                            'uplink_formatted': traffic.get('rx_formatted', '0 B'),
                            'downlink_formatted': traffic.get('tx_formatted', '0 B'),
                            'total_formatted': traffic.get('total_formatted', '0 B'),
                            'uplink_mb': traffic.get('rx_bytes', 0) / (1024 * 1024),
                            'downlink_mb': traffic.get('tx_bytes', 0) / (1024 * 1024),
                            'total_mb': traffic.get('total_bytes', 0) / (1024 * 1024),
                            'connections': traffic.get('connections', 0),
                            'connection_ratio': 0.0,  # Не предоставляется в новом API
                            'connections_count': traffic.get('connections', 0),
                            'timestamp': traffic.get('timestamp'),
                            'source': result.get('source', 'simple_monitor'),
                            'method': 'connection_based_estimation',
                            # Дополнительные поля из новой структуры
                            'traffic_rate': traffic.get('traffic_rate', 0),
                            'interface_traffic': traffic.get('interface_traffic', {}),
                            'connection_details': traffic.get('connection_details', [])
                        }
                    else:
                        # Fallback к устаревшему эндпоинту для совместимости
                        logger.warning(f"New key traffic API failed ({response.status}), trying legacy endpoint")
                        return await self._get_legacy_key_traffic_stats(key_id)
        except Exception as e:
            logger.error(f"Error getting V2Ray key traffic stats: {e}")
            # Fallback к устаревшему эндпоинту
            return await self._get_legacy_key_traffic_stats(key_id)
    
    async def _get_legacy_key_traffic_stats(self, key_id: str) -> Dict:
        """Получить статистику трафика конкретного ключа через устаревший API (fallback)"""
        try:
            session = self._session
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
                        logger.error(f"Failed to get V2Ray key stats (legacy): {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray legacy key traffic stats: {e}")
            return {}
    
    async def reset_key_traffic(self, key_id: str) -> bool:
        """Сбросить статистику трафика ключа через новый API с простым мониторингом"""
        try:
            session = self._session
            # Используем новый эндпоинт для сброса статистики
            async with session.post(
                    f"{self.api_url}/keys/{key_id}/traffic/simple/reset",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        status = result.get('status', '')
                        message = result.get('message', '')
                        if status == 'success' or 'reset successfully' in message.lower():
                            logger.info(f"Successfully reset traffic stats for key {key_id}")
                            return True
                        else:
                            logger.warning(f"Unexpected reset response: {message}")
                    else:
                        # Fallback к устаревшему эндпоинту для совместимости
                        logger.warning(f"New reset API failed ({response.status}), trying legacy endpoint")
                        return await self._reset_legacy_key_traffic(key_id)
                    return False
        except Exception as e:
            logger.error(f"Error resetting V2Ray key traffic: {e}")
            # Fallback к устаревшему эндпоинту
            return await self._reset_legacy_key_traffic(key_id)
    
    async def _reset_legacy_key_traffic(self, key_id: str) -> bool:
        """Сбросить статистику трафика ключа через устаревший API (fallback)"""
        try:
            session = self._session
            async with session.post(
                    f"{self.api_url}/keys/{key_id}/traffic/reset",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        message = result.get('message', '')
                        if 'reset successfully' in message.lower():
                            logger.info(f"Successfully reset traffic stats for key {key_id} (legacy)")
                            return True
                        else:
                            logger.warning(f"Unexpected reset response (legacy): {message}")
                    else:
                        logger.error(f"Failed to reset traffic stats (legacy): {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Error resetting V2Ray key traffic (legacy): {e}")
            return False
    
    async def get_traffic_status(self) -> Dict:
        """Получить статус системы мониторинга трафика"""
        try:
            session = self._session
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
    
    async def get_system_traffic_summary(self) -> Dict:
        """Получить системную сводку трафика"""
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/system/traffic/summary",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        summary = result.get('summary', {})
                        
                        return {
                            'summary': {
                                'total_system_traffic': summary.get('total_system_traffic', 0),
                                'total_system_traffic_formatted': summary.get('total_system_traffic_formatted', '0 B'),
                                'active_ports': summary.get('active_ports', 0),
                                'interface_summary': summary.get('interface_summary', {}),
                                'timestamp': summary.get('timestamp')
                            },
                            'timestamp': result.get('timestamp')
                        }
                    else:
                        logger.error(f"Failed to get system traffic summary: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray system traffic summary: {e}")
            return {}
    
    async def get_system_config_status(self) -> Dict:
        """Получить статус синхронизации конфигурации"""
        try:
            session = self._session
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
            session = self._session
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
            session = self._session
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
            session = self._session
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
    
    async def get_ports_status(self) -> Dict:
        """Получить статус портов через новый API"""
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/system/ports",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        port_assignments = result.get('port_assignments', {})
                        
                        return {
                            'port_assignments': {
                                'used_ports': port_assignments.get('used_ports', {}),
                                'port_assignments': port_assignments.get('port_assignments', {}),
                                'created_at': port_assignments.get('created_at'),
                                'last_updated': port_assignments.get('last_updated')
                            },
                            'used_ports': result.get('used_ports', 0),
                            'available_ports': result.get('available_ports', 0),
                            'max_ports': result.get('max_ports', 0),
                            'port_range': result.get('port_range', ''),
                            'timestamp': result.get('timestamp')
                        }
                    else:
                        logger.error(f"Failed to get ports status: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray ports status: {e}")
            return {}
    
    async def reset_all_ports(self) -> bool:
        """Сбросить все порты (только в экстренных случаях)"""
        try:
            session = self._session
            async with session.post(
                    f"{self.api_url}/system/ports/reset",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        message = result.get('message', '')
                        if 'reset successfully' in message.lower():
                            logger.info("Successfully reset all ports")
                            return True
                        else:
                            logger.warning(f"Unexpected ports reset response: {message}")
                    else:
                        logger.error(f"Failed to reset ports: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Error resetting V2Ray ports: {e}")
            return False
    
    async def get_ports_validation_status(self) -> Dict:
        """Получить статус валидации портов"""
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/system/ports/status",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return {
                            'validation': result.get('validation', {}),
                            'timestamp': result.get('timestamp')
                        }
                    else:
                        logger.error(f"Failed to get ports validation status: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray ports validation status: {e}")
            return {}
    
    async def get_xray_config_status(self) -> Dict:
        """Получить статус конфигурации Xray"""
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/system/xray/config-status",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        config_status = result.get('config_status', {})
                        
                        return {
                            'config_status': {
                                'total_inbounds': config_status.get('total_inbounds', 0),
                                'vless_inbounds': config_status.get('vless_inbounds', 0),
                                'api_inbounds': config_status.get('api_inbounds', 0),
                                'port_assignments': config_status.get('port_assignments', {}),
                                'config_valid': config_status.get('config_valid', False),
                                'timestamp': config_status.get('timestamp')
                            },
                            'timestamp': result.get('timestamp')
                        }
                    else:
                        logger.error(f"Failed to get Xray config status: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray Xray config status: {e}")
            return {}
    
    async def sync_xray_config(self) -> bool:
        """Синхронизировать конфигурацию Xray"""
        try:
            session = self._session
            async with session.post(
                    f"{self.api_url}/system/xray/sync-config",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        message = result.get('message', '')
                        if 'synchronized successfully' in message.lower():
                            logger.info("Successfully synchronized Xray config")
                            return True
                        else:
                            logger.warning(f"Unexpected Xray sync response: {message}")
                    else:
                        logger.error(f"Failed to sync Xray config: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Error syncing V2Ray Xray config: {e}")
            return False
    
    async def validate_xray_sync(self) -> Dict:
        """Проверить соответствие конфигурации Xray с ключами"""
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/system/xray/validate-sync",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        validation = result.get('validation', {})
                        
                        return {
                            'validation': {
                                'synchronized': validation.get('synchronized', False),
                                'key_uuids': validation.get('key_uuids', []),
                                'config_uuids': validation.get('config_uuids', []),
                                'missing_in_config': validation.get('missing_in_config', []),
                                'extra_in_config': validation.get('extra_in_config', []),
                                'total_keys': validation.get('total_keys', 0),
                                'total_config_clients': validation.get('total_config_clients', 0)
                            },
                            'timestamp': result.get('timestamp')
                        }
                    else:
                        logger.error(f"Failed to validate Xray sync: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error validating V2Ray Xray sync: {e}")
            return {}
    
    async def get_all_keys(self) -> List[Dict]:
        """Получить список всех ключей"""
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/keys",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        # Проверяем, что результат - это список
                        if isinstance(result, list):
                            return result
                        else:
                            logger.error(f"Unexpected response format for keys: {type(result)}")
                            return []
                    else:
                        logger.error(f"Failed to get all keys: {response.status}")
                        return []
        except Exception as e:
            logger.error(f"Error getting V2Ray all keys: {e}")
            return []
    
    async def get_key_info(self, key_id: str) -> Dict:
        """Получить информацию о конкретном ключе"""
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/keys/{key_id}",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return {
                            'id': result.get('id'),
                            'name': result.get('name'),
                            'uuid': result.get('uuid'),
                            'created_at': result.get('created_at'),
                            'is_active': result.get('is_active', True),
                            'port': result.get('port')
                        }
                    else:
                        logger.error(f"Failed to get key info: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray key info: {e}")
            return {}

    async def get_traffic_history(self) -> Dict:
        """Получить общий объем трафика для всех ключей с момента создания"""
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/traffic/history",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result
                    else:
                        logger.error(f"Failed to get traffic history: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray traffic history: {e}")
            return {}

    async def get_key_traffic_history(self, key_id: str) -> Dict:
        """Получить общий объем трафика для конкретного ключа с момента создания"""
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/keys/{key_id}/traffic/history",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result
                    else:
                        logger.error(f"Failed to get key traffic history: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray key traffic history: {e}")
            return {}

    async def get_key_usage_bytes(self, key_uuid: str) -> Optional[int]:
        """
        Получить суммарное потребление трафика для ключа V2Ray.
        
        Args:
            key_uuid: UUID ключа (как хранится в таблице v2ray_keys)
        
        Returns:
            Количество байт, израсходованных ключом, или None, если данные недоступны.
        """
        try:
            key_info = await self.get_key_info(key_uuid)
            api_key_id = key_info.get('id') or key_info.get('uuid')
            if not api_key_id:
                logger.warning(f"[V2RAY TRAFFIC] Cannot resolve api_key_id for UUID {key_uuid}")
                return None

            history = await self.get_key_traffic_history(str(api_key_id))
            if not history:
                return None

            data = history.get('data') or {}
            total_traffic = data.get('total_traffic') or {}
            total_bytes = total_traffic.get('total_bytes')

            if isinstance(total_bytes, (int, float)):
                return int(total_bytes)

            logger.debug(f"[V2RAY TRAFFIC] total_bytes not found for UUID {key_uuid}: {total_bytes}")
            return None
        except Exception as e:
            logger.error(f"[V2RAY TRAFFIC] Error getting usage for key {key_uuid}: {e}")
            return None

    async def reset_key_usage(self, key_uuid: str) -> bool:
        """
        Сбросить счётчик трафика для ключа V2Ray.
        
        Args:
            key_uuid: UUID ключа
        
        Returns:
            True, если сброс выполнен успешно.
        """
        try:
            key_info = await self.get_key_info(key_uuid)
            api_key_id = key_info.get('id') or key_info.get('uuid')
            if not api_key_id:
                logger.warning(f"[V2RAY TRAFFIC] Cannot resolve api_key_id for UUID {key_uuid} to reset usage")
                return False

            return await self.reset_key_traffic_history(str(api_key_id))
        except Exception as e:
            logger.error(f"[V2RAY TRAFFIC] Error resetting usage for key {key_uuid}: {e}")
            return False

    async def get_daily_traffic_stats(self, date: str) -> Dict:
        """Получить ежедневную статистику трафика (формат даты: YYYY-MM-DD)"""
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/traffic/daily/{date}",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result
                    else:
                        logger.error(f"Failed to get daily traffic stats: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray daily traffic stats: {e}")
            return {}

    async def reset_key_traffic_history(self, key_id: str) -> bool:
        """Сбросить историю трафика для конкретного ключа"""
        try:
            session = self._session
            async with session.post(
                    f"{self.api_url}/keys/{key_id}/traffic/history/reset",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        message = result.get('message', '')
                        if 'reset successfully' in message.lower():
                            logger.info(f"Successfully reset traffic history for key {key_id}")
                            return True
                        else:
                            logger.warning(f"Unexpected reset response: {message}")
                    else:
                        logger.error(f"Failed to reset key traffic history: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Error resetting V2Ray key traffic history: {e}")
            return False

    async def cleanup_traffic_history(self, days_to_keep: int = 30) -> bool:
        """Очистить старые данные истории трафика"""
        try:
            session = self._session
            async with session.post(
                    f"{self.api_url}/traffic/history/cleanup?days_to_keep={days_to_keep}",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        message = result.get('message', '')
                        if 'cleaned up' in message.lower():
                            logger.info(f"Successfully cleaned up traffic history older than {days_to_keep} days")
                            return True
                        else:
                            logger.warning(f"Unexpected cleanup response: {message}")
                    else:
                        logger.error(f"Failed to cleanup traffic history: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Error cleaning up V2Ray traffic history: {e}")
            return False

    async def get_key_monthly_traffic(self, key_id: str) -> Dict:
        """Получить месячную статистику трафика для конкретного ключа"""
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/keys/{key_id}/traffic/monthly",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result
                    else:
                        logger.error(f"Failed to get key monthly traffic: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray key monthly traffic: {e}")
            return {}

    async def get_monthly_traffic(self) -> Dict:
        """Получить месячную статистику трафика для всех ключей"""
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/traffic/monthly",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result
                    else:
                        logger.error(f"Failed to get monthly traffic: {response.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray monthly traffic: {e}")
            return {}
    
    async def close(self):
        """Закрыть сессию"""
        if hasattr(self, '_session') and not self._session.closed:
            await self._session.close()

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

def get_word_declension(number: int, word_forms: tuple) -> str:
    """
    Правильное склонение для русского языка
    
    Args:
        number: число
        word_forms: кортеж из 3 форм ("год", "года", "лет")
    
    Returns:
        Правильная форма слова
    
    Примеры:
        1, 21, 31 → word_forms[0] (год, день, час)
        2-4, 22-24 → word_forms[1] (года, дня, часа)
        5-20, 25-30 → word_forms[2] (лет, дней, часов)
        11-14 → word_forms[2] (исключение!)
    """
    n = abs(number) % 100
    n1 = number % 10
    
    if n > 10 and n < 20:
        # 11-19 → третья форма (лет, дней, часов)
        return word_forms[2]
    if n1 > 1 and n1 < 5:
        # 2-4, 22-24, 32-34, ... → вторая форма (года, дня, часа)
        return word_forms[1]
    if n1 == 1:
        # 1, 21, 31, 41, ... → первая форма (год, день, час)
        return word_forms[0]
    # Все остальное → третья форма (лет, дней, часов)
    return word_forms[2]

def format_duration(seconds: int) -> str:
    """Форматировать длительность в человекочитаемый вид с правильным склонением"""
    if seconds < 0:
        return "истек"
    
    if seconds < 60:
        return f"{seconds} сек"
    elif seconds < 3600:
        minutes = seconds // 60
        minutes_str = get_word_declension(minutes, ("минута", "минуты", "минут"))
        return f"{minutes} {minutes_str}"
    elif seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        
        hours_str = get_word_declension(hours, ("час", "часа", "часов"))
        minutes_str = get_word_declension(minutes, ("минута", "минуты", "минут"))
        
        if minutes > 0:
            return f"{hours} {hours_str}, {minutes} {minutes_str}"
        else:
            return f"{hours} {hours_str}"
    else:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = ((seconds % 86400) % 3600) // 60
        
        # Если больше года, показываем годы, месяцы, дни, часы, минуты
        if days >= 365:
            years = days // 365
            remaining_days = days % 365
            months = remaining_days // 30
            remaining_days = remaining_days % 30
            
            # Используем функцию правильного склонения
            years_str = get_word_declension(years, ("год", "года", "лет"))
            months_str = get_word_declension(months, ("месяц", "месяца", "месяцев"))
            days_str = get_word_declension(remaining_days, ("день", "дня", "дней"))
            hours_str = get_word_declension(hours, ("час", "часа", "часов"))
            minutes_str = get_word_declension(minutes, ("минута", "минуты", "минут"))
            
            # Формируем результат
            result_parts = []
            if years > 0:
                result_parts.append(f"{years} {years_str}")
            if months > 0:
                result_parts.append(f"{months} {months_str}")
            if remaining_days > 0:
                result_parts.append(f"{remaining_days} {days_str}")
            if hours > 0:
                result_parts.append(f"{hours} {hours_str}")
            if minutes > 0:
                result_parts.append(f"{minutes} {minutes_str}")
            
            return ", ".join(result_parts)
        
        # Если больше месяца, показываем месяцы, дни, часы, минуты
        elif days >= 30:
            months = days // 30
            remaining_days = days % 30
            
            # Используем функцию правильного склонения
            months_str = get_word_declension(months, ("месяц", "месяца", "месяцев"))
            days_str = get_word_declension(remaining_days, ("день", "дня", "дней"))
            hours_str = get_word_declension(hours, ("час", "часа", "часов"))
            minutes_str = get_word_declension(minutes, ("минута", "минуты", "минут"))
            
            # Формируем результат
            result_parts = []
            if months > 0:
                result_parts.append(f"{months} {months_str}")
            if remaining_days > 0:
                result_parts.append(f"{remaining_days} {days_str}")
            if hours > 0:
                result_parts.append(f"{hours} {hours_str}")
            if minutes > 0:
                result_parts.append(f"{minutes} {minutes_str}")
            
            return ", ".join(result_parts)
        
        # Если больше недели, показываем недели, дни, часы, минуты
        elif days >= 7:
            weeks = days // 7
            remaining_days = days % 7
            
            # Используем функцию правильного склонения
            weeks_str = get_word_declension(weeks, ("неделя", "недели", "недель"))
            days_str = get_word_declension(remaining_days, ("день", "дня", "дней"))
            hours_str = get_word_declension(hours, ("час", "часа", "часов"))
            minutes_str = get_word_declension(minutes, ("минута", "минуты", "минут"))
            
            # Формируем результат
            result_parts = []
            if weeks > 0:
                result_parts.append(f"{weeks} {weeks_str}")
            if remaining_days > 0:
                result_parts.append(f"{remaining_days} {days_str}")
            if hours > 0:
                result_parts.append(f"{hours} {hours_str}")
            if minutes > 0:
                result_parts.append(f"{minutes} {minutes_str}")
            
            return ", ".join(result_parts)
        
        # Обычные дни - показываем дни, часы, минуты
        else:
            # Используем функцию правильного склонения
            days_str = get_word_declension(days, ("день", "дня", "дней"))
            hours_str = get_word_declension(hours, ("час", "часа", "часов"))
            minutes_str = get_word_declension(minutes, ("минута", "минуты", "минут"))
            
            # Формируем результат
            result_parts = []
            if days > 0:
                result_parts.append(f"{days} {days_str}")
            if hours > 0:
                result_parts.append(f"{hours} {hours_str}")
            if minutes > 0:
                result_parts.append(f"{minutes} {minutes_str}")
            
            return ", ".join(result_parts) 