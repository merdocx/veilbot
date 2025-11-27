import asyncio
import aiohttp
import uuid
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from urllib.parse import urlparse, urlunparse
from outline import create_key as outline_create_key, delete_key as outline_delete_key

logger = logging.getLogger(__name__)


def normalize_vless_host(config: Optional[str], domain: Optional[str], api_url: str) -> str:
    """
    Подменяет хост в VLESS ссылке на указанный домен или host из API URL.
    Это нужно для серверов, где API возвращает дефолтный хост, а подключаться нужно по реальному IP/домену.
    """
    if not config or "vless://" not in config:
        return config or ""

    host_override = (domain or "").strip()
    if not host_override:
        try:
            parsed_api = urlparse(api_url or "")
            host_override = parsed_api.hostname or ""
        except Exception:
            host_override = ""

    if not host_override:
        return config

    try:
        fake_url = config.replace("vless://", "https://", 1)
        parsed = urlparse(fake_url)
        netloc = parsed.netloc
        if "@" not in netloc:
            return config
        userinfo, host_port = netloc.split("@", 1)
        if not userinfo:
            return config

        # IPv6 в [] или обычный хост:порт
        if host_port.startswith("["):
            closing = host_port.find("]")
            if closing != -1:
                port_part = host_port[closing + 1 :]
                new_host_port = f"[{host_override}]{port_part}"
            else:
                new_host_port = host_override
        elif ":" in host_port:
            _, port = host_port.rsplit(":", 1)
            new_host_port = f"{host_override}:{port}"
        else:
            new_host_port = host_override

        new_netloc = f"{userinfo}@{new_host_port}"
        rebuilt = parsed._replace(netloc=new_netloc)
        return urlunparse(rebuilt).replace("https://", "vless://", 1)
    except Exception:
        return config or ""


def remove_fragment_from_vless(config: Optional[str]) -> str:
    """
    Удаляет фрагмент (после #) из VLESS URL.
    
    Args:
        config: VLESS URL
        
    Returns:
        VLESS URL без фрагмента
    """
    if not config or "vless://" not in config:
        return config or ""
    
    try:
        # Разделяем URL на основную часть и фрагмент
        if "#" in config:
            base_url, _ = config.rsplit("#", 1)
            return base_url
        else:
            return config
    except Exception:
        return config or ""


def add_server_name_to_vless(config: Optional[str], server_name: Optional[str]) -> str:
    """
    Добавляет или обновляет название сервера в фрагменте VLESS URL.
    
    Args:
        config: VLESS URL
        server_name: Название сервера из админки
        
    Returns:
        VLESS URL с обновленным фрагментом
    """
    if not config or "vless://" not in config:
        return config or ""
    
    if not server_name:
        return config
    
    try:
        # Разделяем URL на основную часть и фрагмент
        if "#" in config:
            base_url, _ = config.rsplit("#", 1)
        else:
            base_url = config
        
        # URL-кодируем название сервера для фрагмента
        from urllib.parse import quote
        encoded_name = quote(server_name, safe="")
        
        return f"{base_url}#{encoded_name}"
    except Exception:
        return config or ""


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
        base_url = api_url.strip()
        logger.debug(f"[V2RayProtocol.__init__] Input api_url: {api_url!r}")
        parsed = urlparse(base_url)
        logger.debug(f"[V2RayProtocol.__init__] Parsed: scheme={parsed.scheme!r}, netloc={parsed.netloc!r}, path={parsed.path!r}")
        
        # Проверяем, что URL содержит протокол
        if not parsed.scheme:
            # Если протокол отсутствует, добавляем https://
            if base_url.startswith('/'):
                # Если начинается с /, это относительный путь - это ошибка
                raise ValueError(f"Invalid API URL format (missing protocol and starts with /): {api_url}")
            # Пытаемся добавить https://
            base_url = f"https://{base_url}"
            parsed = urlparse(base_url)
            logger.debug(f"[V2RayProtocol.__init__] Added https://, new parsed: scheme={parsed.scheme!r}, netloc={parsed.netloc!r}, path={parsed.path!r}")
        
        path = (parsed.path or "").rstrip('/')
        segments = [seg for seg in path.split('/') if seg]
        if not segments:
            path = "/api"
        elif "api" not in segments:
            path = f"{path}/api" if path else "/api"
        # Гарантируем ведущий слэш
        if not path.startswith("/"):
            path = f"/{path}"
        normalized = parsed._replace(path=path)
        self.api_url = urlunparse(normalized).rstrip('/')
        logger.info(f"[V2RayProtocol.__init__] Final self.api_url: {self.api_url}")
        
        # Финальная проверка, что URL валидный
        if not self.api_url.startswith(('http://', 'https://')):
            raise ValueError(f"Failed to construct valid API URL from: {api_url}. Result: {self.api_url}")
        # API требует аутентификации
        self.headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        if api_key:
            self.headers['Authorization'] = f'Bearer {api_key}'
        
        # Настройка SSL контекста для самоподписанных сертификатов
        import ssl
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

        # Общая сессия aiohttp с таймаутами
        self._timeout = aiohttp.ClientTimeout(total=30, connect=5, sock_connect=5, sock_read=25)
        self._connector = aiohttp.TCPConnector(ssl=self.ssl_context, force_close=False, enable_cleanup_closed=True)
        self._session = aiohttp.ClientSession(connector=self._connector, timeout=self._timeout)
    
    async def create_user(self, email: str, level: int = 0, name: Optional[str] = None) -> Dict:
        """Создать пользователя V2Ray через новый API
        
        Args:
            email: Email для ключа (используется как fallback для name)
            level: Уровень доступа (не используется в новом API)
            name: Название ключа (если не указано, используется email)
        """
        try:
            session = self._session
            # Создаем ключ через новый API
            # Используем переданное название или email как fallback
            key_name = name if name else email
            key_data = {
                "name": key_name
            }
                
            logger.debug(f"Creating V2Ray key with name: {key_name} (email: {email})")
            logger.debug(f"V2Ray API URL: {self.api_url}/keys")
            logger.debug(f"V2Ray headers present: {list(self.headers.keys())}")
            logger.debug(f"V2Ray key data: {key_data}")
                
            async with session.post(
                f"{self.api_url}/keys",
                headers=self.headers,
                json=key_data
            ) as response:
                response_text = await response.text()
                logger.debug(f"V2Ray create response status: {response.status}")
                logger.debug(f"V2Ray create response text: {response_text}")
                
                if response.status in (200, 201):
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
                                    
                                    if alt_response.status in (200, 201):
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
                        # API возвращает key_id (integer) или id (string) для обратной совместимости
                        key_id = result.get('key_id') or result.get('id')
                        if not key_id:
                            raise Exception(f"V2Ray API did not return key_id or id - {response_text}")
                        
                        uuid_value = result.get('uuid')
                        
                        logger.info(f"Successfully created V2Ray key {key_id} with UUID {uuid_value}")
                        
                        # Извлекаем все параметры из ответа API (API версия 2.3.7)
                        # ВАЖНО: Сохраняем все параметры из ответа, не генерируем самостоятельно!
                        short_id = result.get('short_id')
                        sni = result.get('sni')
                        port = result.get('port')
                        
                        if short_id:
                            logger.info(f"Key {key_id} has short_id from API: {short_id}")
                        if sni:
                            logger.info(f"Key {key_id} has SNI from API: {sni}")
                        if port:
                            logger.info(f"Key {key_id} has port from API: {port}")
                        
                        # РЕКОМЕНДУЕТСЯ: Получить готовый VLESS URL через /api/keys/{key_id}/link
                        # Согласно API документации, это лучший способ, так как:
                        # - Все параметры гарантированно правильные
                        # - Short ID совпадает с БД
                        # - Public key правильный
                        # - SNI правильный
                        client_config = None
                        
                        # Сначала пробуем получить готовый URL через эндпоинт link
                        try:
                            logger.info(f"Fetching ready VLESS URL via GET /api/keys/{key_id}/link")
                            config_url = f"{self.api_url}/keys/{key_id}/link"
                            
                            async with session.get(
                                    config_url,
                                    headers=self.headers
                                ) as config_response:
                                if config_response.status == 200:
                                    config_result = await config_response.json()
                                    
                                    # Извлекаем vless_link из ответа API
                                    client_config = config_result.get('vless_link') or config_result.get('client_config') or config_result.get('vless_url')
                                    
                                    if client_config:
                                        # Извлекаем VLESS URL из многострочного формата, если нужно
                                        if 'vless://' in client_config:
                                            lines = client_config.split('\n')
                                            for line in lines:
                                                if line.strip().startswith('vless://'):
                                                    client_config = line.strip()
                                                    break

                                        # Проверяем наличие ключевых параметров
                                        if 'sni=' in client_config and 'sid=' in client_config:
                                            logger.info(f"✅ Got ready VLESS URL with SNI and short_id from /api/keys/{key_id}/link")
                                        else:
                                            logger.warning(f"⚠️  VLESS URL from /api/keys/{key_id}/link missing SNI or short_id")

                                        logger.info(f"✅ Successfully obtained ready VLESS URL via /api/keys/{key_id}/link")
                                    else:
                                        logger.warning(f"⚠️  /api/keys/{key_id}/link returned empty vless_link")
                                else:
                                    logger.warning(f"⚠️  Failed to get link via /api/keys/{key_id}/link: status {config_response.status}")
                        except Exception as config_error:
                            logger.warning(f"⚠️  Error getting link via /api/keys/{key_id}/link: {config_error}")
                        
                        # Если не получилось получить через config эндпоинт, пробуем синхронизацию и повтор
                        if not client_config:
                            logger.info(f"Config not obtained, trying sync and retry...")
                            try:
                                sync_success = await self.sync_xray_config()
                                if sync_success:
                                    logger.info(f"Sync successful, retrying link fetch...")
                                    # Повторная попытка после синхронизации
                                    async with session.get(
                                            f"{self.api_url}/keys/{key_id}/link",
                                            headers=self.headers
                                        ) as retry_response:
                                        if retry_response.status == 200:
                                            retry_result = await retry_response.json()
                                            client_config = retry_result.get('vless_link') or retry_result.get('client_config') or retry_result.get('vless_url')
                                            
                                            if client_config and 'vless://' in client_config:
                                                lines = client_config.split('\n')
                                                for line in lines:
                                                    if line.strip().startswith('vless://'):
                                                        client_config = line.strip()
                                                        if 'sni=' in client_config and 'sid=' in client_config:
                                                            logger.info(f"✅ Got ready VLESS URL after sync and retry")
                                                        break
                            except Exception as retry_error:
                                logger.warning(f"Error during sync and retry: {retry_error}")
                        
                        # Если все еще нет client_config, пробуем извлечь из ответа создания (fallback)
                        if not client_config:
                            logger.warning(f"No vless_link obtained via /api/keys/{key_id}/link, trying fallback from create response")
                            vless_url = result.get('vless_url')
                            if not vless_url and isinstance(result.get('key'), dict):
                                vless_url = result['key'].get('vless_url')
                            if isinstance(vless_url, str) and vless_url.strip():
                                client_config = vless_url.strip()
                            
                            if result.get('client_config'):
                                client_config = result['client_config']
                                if 'vless://' in client_config:
                                    lines = client_config.split('\n')
                                    for line in lines:
                                        if line.strip().startswith('vless://'):
                                            client_config = line.strip()
                                            break
                            elif result.get('key') and isinstance(result.get('key'), dict) and result['key'].get('client_config'):
                                client_config = result['key']['client_config']
                                if 'vless://' in client_config:
                                    lines = client_config.split('\n')
                                    for line in lines:
                                        if line.strip().startswith('vless://'):
                                            client_config = line.strip()
                                            break
                        
                        # Если все еще нет, используем get_user_config как последний fallback
                        if not client_config:
                            logger.warning(f"No client_config found anywhere, using get_user_config as last resort")
                            try:
                                # Используем get_user_config с параметрами из ответа API
                                server_config = {
                                    'domain': None,  # Будет получен из конфигурации
                                    'port': port,
                                    'email': email
                                }
                                client_config = await self.get_user_config(uuid_value, server_config, max_retries=3, retry_delay=1.0)
                            except Exception as fallback_error:
                                logger.error(f"Failed to get config via get_user_config fallback: {fallback_error}")
                                # Не прерываем выполнение - ключ создан, просто нет конфигурации
                        
                        if client_config and isinstance(client_config, str):
                            client_config = client_config.strip()
                        
                        # Вызываем синхронизацию для гарантии применения ключа
                        # Согласно документации API, ключ автоматически применяется при создании,
                        # но дополнительная синхронизация гарантирует применение
                        try:
                            sync_success = await self.sync_xray_config()
                            if sync_success:
                                logger.info(f"Successfully synchronized Xray config via HandlerService API after creating key {key_id} (UUID: {uuid_value})")
                            else:
                                logger.warning(f"Failed to synchronize Xray config after creating key {key_id}, but key was created and should be applied automatically via HandlerService API")
                        except Exception as sync_error:
                            # Не прерываем выполнение, если синхронизация не удалась
                            logger.error(f"Error syncing Xray config after creating key {key_id}: {sync_error}")
                        
                        # API возвращает key_id (integer) согласно документации
                        # Сохраняем для обратной совместимости и как id, и как key_id
                        return {
                            'id': key_id,
                            'key_id': key_id,  # Добавляем key_id для соответствия документации
                            'uuid': uuid_value,
                            'name': email,
                            'created_at': result.get('created_at'),
                            'is_active': result.get('is_active', True),
                            'port': port,  # Порт из ответа API
                            'short_id': short_id,  # Short ID из ответа API (НЕ генерируем самостоятельно!)
                            'sni': sni,  # SNI из ответа API
                            'client_config': client_config  # Готовый VLESS URL из /api/keys/{key_id}/link (РЕКОМЕНДУЕТСЯ)
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
                config_url = f"{self.api_url}/keys/{user_id}/link"
                logger.debug(f"[GET_CONFIG] Attempt {attempt + 1}/{max_retries}: GET {config_url}")
                logger.debug(f"[GET_CONFIG] self.api_url = {self.api_url}")
                
                # Проверяем, что URL содержит протокол
                if not config_url.startswith(('http://', 'https://')):
                    error_msg = f"Invalid URL format (missing protocol): {config_url}. self.api_url = {self.api_url}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
                
                async with session.get(
                        config_url,
                        headers=self.headers
                    ) as response:
                        if response.status == 200:
                            result = await response.json()
                            logger.debug(f"[GET_CONFIG] API response status 200. Keys: {list(result.keys()) if isinstance(result, dict) else 'not a dict'}")
                            
                            # API возвращает vless_link в поле vless_link
                            vless_url = None
                            if isinstance(result, dict):
                                vless_url = result.get('vless_link') or result.get('vless_url')
                                if not vless_url and isinstance(result.get('key'), dict):
                                    vless_url = result['key'].get('vless_link') or result['key'].get('vless_url')
                            if isinstance(vless_url, str) and vless_url.strip():
                                logger.info(f"[GET_CONFIG] Using vless_link from API response on attempt {attempt + 1}")
                                return vless_url.strip()
                            
                            # Проверяем альтернативную структуру ответа (для обратной совместимости)
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
                            
                            # Если client_config не найден вообще, пробуем снова или выбрасываем исключение
                            if attempt < max_retries - 1:
                                logger.debug(f"API did not return client_config, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                                await asyncio.sleep(retry_delay)
                                continue
                            else:
                                # Если все попытки исчерпаны и client_config не получен, выбрасываем исключение
                                # Не используем fallback с хардкодом short id, так как каждый сервер генерирует уникальные short id
                                logger.error(f"API did not return client_config after {max_retries} attempts for user {user_id}")
                                raise Exception(f"Failed to get client_config from V2Ray API after {max_retries} attempts. Server may be generating unique short IDs that must be retrieved from API.")
                        
                        # Если статус не 200, пробуем снова или выбрасываем исключение
                        if attempt < max_retries - 1:
                            logger.debug(f"API returned status {response.status}, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            # Если все попытки исчерпаны, выбрасываем исключение
                            # Не используем fallback с хардкодом short id
                            logger.error(f"API returned status {response.status} after {max_retries} attempts for user {user_id}")
                            raise Exception(f"V2Ray API returned status {response.status} after {max_retries} attempts. Cannot use fallback with hardcoded short ID as servers generate unique short IDs.")
                    
            except Exception as e:
                import traceback
                error_details = str(e)
                error_type = type(e).__name__
                # Логируем на уровне ERROR для видимости в production логах
                logger.error(f"Error getting V2Ray user config (attempt {attempt + 1}/{max_retries}): {error_type}: {error_details}")
                logger.error(f"Full traceback: {traceback.format_exc()}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    # Выбрасываем исключение вместо использования fallback с хардкодом short id
                    logger.error(f"Failed to get client_config after {max_retries} attempts for user {user_id}")
                    raise Exception(f"Failed to get client_config from V2Ray API after {max_retries} attempts: {error_type}: {error_details}. Cannot use fallback with hardcoded short ID as servers generate unique short IDs.")
        
        # Если цикл не вернул значение, выбрасываем исключение
        raise Exception(f"Failed to get client_config from V2Ray API for user {user_id}. No valid response received after {max_retries} attempts.")
    
    async def get_traffic_stats(self) -> List[Dict]:
        """Получить статистику трафика V2Ray через новый API"""
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/traffic",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        # Новая структура ответа согласно документации
                        # Может быть формат с data.ports или прямая структура
                        if 'data' in result:
                            data = result.get('data', {})
                            ports = data.get('ports', {})
                            total_connections = data.get('total_connections', 0)
                            total_bytes = data.get('total_bytes', 0)
                            timestamp = data.get('timestamp')
                        else:
                            # Прямой формат ответа
                            ports = result.get('ports', {})
                            total_connections = result.get('total_connections', 0)
                            total_bytes = result.get('total_bytes', 0)
                            timestamp = result.get('timestamp')
                        
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
                                'source': result.get('source', 'traffic_monitor'),
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
                        logger.error(f"Failed to get traffic stats: {response.status}")
                        response_text = await response.text()
                        logger.error(f"Response: {response_text}")
                        return []
        except Exception as e:
            logger.error(f"Error getting V2Ray traffic stats: {e}")
            return []
    
    async def get_key_traffic_stats(self, key_id: str) -> Dict:
        """Получить статистику трафика конкретного ключа через новый API
        
        Согласно документации API, ответ имеет формат:
        {
            "key_id": 1,
            "upload": 1024000,
            "download": 2048000,
            "total": 3072000,
            "last_updated": 1703520000
        }
        """
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/keys/{key_id}/traffic",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        # Формат ответа API согласно документации
                        key_id_from_api = result.get('key_id')
                        upload_bytes = result.get('upload', 0)
                        download_bytes = result.get('download', 0)
                        total_bytes = result.get('total', 0)
                        last_updated = result.get('last_updated')
                        
                        # Обратная совместимость со старым форматом
                        if not total_bytes and result.get('total_bytes'):
                            total_bytes = result.get('total_bytes', 0)
                        if not upload_bytes and result.get('uplink_bytes'):
                            upload_bytes = result.get('uplink_bytes', 0)
                        if not download_bytes and result.get('downlink_bytes'):
                            download_bytes = result.get('downlink_bytes', 0)
                        if not last_updated and result.get('timestamp'):
                            last_updated = result.get('timestamp')
                        
                        # Преобразуем в целые числа
                        upload_bytes_int = int(upload_bytes) if upload_bytes else 0
                        download_bytes_int = int(download_bytes) if download_bytes else 0
                        total_bytes_int = int(total_bytes) if total_bytes else 0
                        
                        return {
                            'uuid': result.get('key_uuid') or result.get('uuid'),
                            'key_id': key_id_from_api or key_id,
                            'key_name': result.get('key_name', 'Unknown'),
                            'total_bytes': total_bytes_int,
                            'uplink_bytes': upload_bytes_int,
                            'downlink_bytes': download_bytes_int,
                            'total_formatted': self._format_bytes(total_bytes_int),
                            'uplink_formatted': self._format_bytes(upload_bytes_int),
                            'downlink_formatted': self._format_bytes(download_bytes_int),
                            'total_mb': total_bytes_int / (1024 * 1024),
                            'uplink_mb': upload_bytes_int / (1024 * 1024),
                            'downlink_mb': download_bytes_int / (1024 * 1024),
                            'connections': result.get('connections', 0),
                            'connection_ratio': 0.0,
                            'connections_count': result.get('connections', 0),
                            'timestamp': last_updated,
                            'last_updated': last_updated,
                            'status': result.get('status', 'success'),
                            'source': result.get('source', 'api'),
                            'method': result.get('method', 'cumulative'),
                        }
                    else:
                        logger.error(f"Failed to get key traffic stats: {response.status}")
                        response_text = await response.text()
                        logger.error(f"Response: {response_text}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting V2Ray key traffic stats: {e}")
            return {}
    
    def _format_bytes(self, num_bytes: int) -> str:
        """Форматировать байты в человекочитаемый формат"""
        if num_bytes <= 0:
            return "0 B"
        units = ["B", "KB", "MB", "GB", "TB", "PB"]
        import math
        idx = min(int(math.log(num_bytes, 1024)), len(units) - 1)
        normalized = num_bytes / (1024 ** idx)
        return f"{normalized:.2f} {units[idx]}"
    
    async def reset_key_traffic(self, key_id: str) -> bool:
        """Сбросить статистику трафика ключа через новый API
        
        Согласно документации API, ответ имеет формат:
        {
            "success": true,
            "message": "Traffic reset successfully for key 1",
            "key_id": 1,
            "previous_upload": 1024000,
            "previous_download": 2048000,
            "previous_total": 3072000
        }
        """
        try:
            session = self._session
            async with session.post(
                    f"{self.api_url}/keys/{key_id}/traffic/reset",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        # Проверяем формат ответа согласно документации
                        success = result.get('success', False)
                        message = result.get('message', '')
                        
                        if success or 'reset successfully' in message.lower():
                            logger.info(f"Successfully reset traffic stats for key {key_id}")
                            # Логируем предыдущие значения для отладки
                            if 'previous_total' in result:
                                prev_total = result.get('previous_total', 0)
                                logger.debug(f"Previous traffic for key {key_id}: {prev_total} bytes ({prev_total / (1024*1024):.2f} MB)")
                            return True
                        else:
                            logger.warning(f"Unexpected reset response: {message}")
                            return False
                    else:
                        logger.error(f"Failed to reset traffic stats: {response.status}")
                        response_text = await response.text()
                        logger.error(f"Response: {response_text}")
                        return False
        except Exception as e:
            logger.error(f"Error resetting V2Ray key traffic: {e}")
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
        """Синхронизировать конфигурацию Xray через HandlerService API
        
        Согласно документации API, этот метод применяет изменения через Xray HandlerService API
        без перезапуска сервиса, обеспечивая нулевое время простоя и мгновенную активацию ключей.
        """
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
                            logger.info("Successfully synchronized Xray config via HandlerService API")
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
        """Получить список всех ключей
        
        Согласно документации API, ответ может быть:
        - Список ключей напрямую
        - Объект с полями 'keys' (массив) и 'total' (число)
        """
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/keys",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        # Проверяем формат ответа
                        if isinstance(result, list):
                            # Прямой список ключей
                            return result
                        elif isinstance(result, dict) and 'keys' in result:
                            # Объект с полем 'keys'
                            return result.get('keys', [])
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
        """Получить информацию о конкретном ключе
        
        Согласно документации API, ответ включает:
        - key_id, name, uuid, short_id, created_at, is_active
        """
        try:
            session = self._session
            async with session.get(
                    f"{self.api_url}/keys/{key_id}",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        # API возвращает key_id (integer) или id (string) для обратной совместимости
                        api_key_id = result.get('key_id') or result.get('id')
                        return {
                            'id': api_key_id,
                            'key_id': api_key_id,
                            'name': result.get('name'),
                            'uuid': result.get('uuid'),
                            'created_at': result.get('created_at'),
                            'is_active': result.get('is_active', True),
                            'port': result.get('port'),
                            'short_id': result.get('short_id'),
                            'sni': result.get('sni')
                        }
                    else:
                        logger.error(f"Failed to get key info: {response.status}")
                        response_text = await response.text()
                        logger.error(f"Response: {response_text}")
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

    async def get_key_usage_bytes(self, key_id: str) -> Optional[int]:
        """
        Получить суммарное потребление трафика для ключа V2Ray через новый API.
        
        Args:
            key_id: ID или UUID ключа (как возвращается из API)
        
        Returns:
            Количество байт, израсходованных ключом, или None, если данные недоступны.
        """
        try:
            # Используем новый эндпоинт GET /api/keys/{key_id}/traffic
            stats = await self.get_key_traffic_stats(key_id)
            if not stats:
                return None
            
            total_bytes = stats.get('total_bytes')
            if isinstance(total_bytes, (int, float)) and total_bytes >= 0:
                return int(total_bytes)
            
            logger.debug(f"[V2RAY TRAFFIC] total_bytes not found or invalid for key {key_id}: {total_bytes}")
            return None
        except Exception as e:
            logger.error(f"[V2RAY TRAFFIC] Error getting usage for key {key_id}: {e}")
            return None

    async def reset_key_usage(self, key_id: str) -> bool:
        """
        Сбросить счётчик трафика для ключа V2Ray через новый API.
        
        Args:
            key_id: ID или UUID ключа (как возвращается из API)
        
        Returns:
            True, если сброс выполнен успешно.
        """
        try:
            # Используем новый эндпоинт POST /api/keys/{key_id}/traffic/reset
            return await self.reset_key_traffic(key_id)
        except Exception as e:
            logger.error(f"[V2RAY TRAFFIC] Error resetting usage for key {key_id}: {e}")
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