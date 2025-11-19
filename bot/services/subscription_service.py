"""
Сервис для работы с подписками V2Ray
"""
import uuid
import base64
import time
import logging
from typing import Optional, Dict, Any, List
from app.repositories.subscription_repository import SubscriptionRepository
from app.infra.cache import SimpleCache
from vpn_protocols import V2RayProtocol, ProtocolFactory, normalize_vless_host, add_server_name_to_vless, remove_fragment_from_vless
from utils import get_db_cursor
from app.infra.foreign_keys import safe_foreign_keys_off

logger = logging.getLogger(__name__)

# Кэш для подписок (TTL 5 минут)
_subscription_cache = SimpleCache()
CACHE_TTL = 300  # 5 минут


def validate_subscription_token(token: str) -> bool:
    """Валидация формата токена подписки"""
    if not token or len(token) < 32:
        return False
    try:
        # Проверяем, что это валидный UUID
        uuid.UUID(token)
        return True
    except ValueError:
        # Если не UUID, проверяем длину и формат
        return len(token) >= 32 and token.replace('-', '').replace('_', '').isalnum()


def invalidate_subscription_cache(token: str) -> None:
    """Инвалидировать кэш подписки"""
    cache_key = f"subscription:{token}"
    _subscription_cache.delete(cache_key)


def invalidate_subscriptions_cache_for_server(server_id: int) -> None:
    """
    Инвалидировать кэш всех подписок, содержащих ключи с указанного сервера
    
    Args:
        server_id: ID сервера
    """
    try:
        from utils import get_db_cursor
        
        with get_db_cursor() as cursor:
            # Получаем все уникальные токены подписок, содержащих ключи с этого сервера
            cursor.execute("""
                SELECT DISTINCT s.subscription_token
                FROM subscriptions s
                JOIN v2ray_keys k ON s.id = k.subscription_id
                WHERE k.server_id = ? AND s.is_active = 1
            """, (server_id,))
            
            tokens = cursor.fetchall()
            invalidated_count = 0
            
            for (token,) in tokens:
                invalidate_subscription_cache(token)
                invalidated_count += 1
            
            if invalidated_count > 0:
                logger.info(f"Invalidated cache for {invalidated_count} subscriptions containing keys from server {server_id}")
    except Exception as e:
        logger.error(f"Error invalidating subscription cache for server {server_id}: {e}", exc_info=True)


def invalidate_all_active_subscriptions_cache() -> None:
    """
    Инвалидировать кэш всех активных подписок
    Используется при глобальных изменениях
    """
    try:
        from utils import get_db_cursor
        
        with get_db_cursor() as cursor:
            now = int(time.time())
            cursor.execute("""
                SELECT subscription_token
                FROM subscriptions
                WHERE is_active = 1 AND expires_at > ?
            """, (now,))
            
            tokens = cursor.fetchall()
            invalidated_count = 0
            
            for (token,) in tokens:
                invalidate_subscription_cache(token)
                invalidated_count += 1
            
            if invalidated_count > 0:
                logger.info(f"Invalidated cache for {invalidated_count} active subscriptions")
    except Exception as e:
        logger.error(f"Error invalidating all subscriptions cache: {e}", exc_info=True)


def update_subscription_configs_remove_fragments() -> None:
    """
    Обновить все конфигурации подписок в БД, удалив фрагменты (email) из VLESS URL.
    Это нужно для применения изменений к существующим подпискам.
    """
    try:
        from utils import get_db_cursor
        
        with get_db_cursor() as cursor:
            # Получаем все конфигурации подписок с фрагментами
            cursor.execute("""
                SELECT v2ray_uuid, client_config
                FROM v2ray_keys
                WHERE subscription_id IS NOT NULL 
                  AND client_config IS NOT NULL 
                  AND client_config LIKE '%#%'
                  AND client_config LIKE 'vless://%'
            """)
            
            configs_to_update = cursor.fetchall()
            updated_count = 0
            
            for v2ray_uuid, client_config in configs_to_update:
                try:
                    # Удаляем фрагмент из конфигурации
                    config_without_fragment = remove_fragment_from_vless(client_config)
                    
                    if config_without_fragment != client_config:
                        cursor.execute("""
                            UPDATE v2ray_keys 
                            SET client_config = ? 
                            WHERE v2ray_uuid = ?
                        """, (config_without_fragment, v2ray_uuid))
                        updated_count += 1
                except Exception as e:
                    logger.error(f"Error updating config for UUID {v2ray_uuid[:8]}...: {e}", exc_info=True)
            
            if updated_count > 0:
                cursor.connection.commit()
                logger.info(f"Updated {updated_count} subscription configs, removed fragments")
            
            # Инвалидируем кэш всех активных подписок
            invalidate_all_active_subscriptions_cache()
            
    except Exception as e:
        logger.error(f"Error updating subscription configs: {e}", exc_info=True)


class SubscriptionService:
    def __init__(self, db_path: Optional[str] = None):
        self.repository = SubscriptionRepository(db_path)

    async def generate_subscription_content(self, token: str) -> Optional[str]:
        """
        Сгенерировать содержимое подписки (base64-кодированный список VLESS URL)
        
        Args:
            token: Токен подписки
            
        Returns:
            Base64-кодированная строка с VLESS URL или None при ошибке
        """
        # Проверка кэша
        cache_key = f"subscription:{token}"
        cached = _subscription_cache.get(cache_key)
        if cached:
            logger.debug(f"Subscription cache hit for token {token[:8]}...")
            return cached

        # Валидация токена
        if not validate_subscription_token(token):
            logger.warning(f"Invalid subscription token format: {token[:8]}...")
            return None

        # Получение подписки из БД
        subscription = await self.repository.get_subscription_by_token_async(token)
        if not subscription:
            logger.warning(f"Subscription not found for token {token[:8]}...")
            return None

        (
            subscription_id,
            user_id,
            subscription_token,
            created_at,
            expires_at,
            tariff_id,
            is_active,
            last_updated_at,
            notified,
        ) = subscription

        # Проверка активности и срока действия
        now = int(time.time())
        if not is_active:
            logger.warning(f"Subscription {subscription_id} is not active")
            return None

        if expires_at <= now:
            logger.warning(f"Subscription {subscription_id} has expired")
            return None

        # Получение ключей подписки
        keys = await self.repository.get_subscription_keys_async(subscription_id, user_id, now)
        if not keys:
            logger.warning(f"No active keys found for subscription {subscription_id}")
            return None

        # Сбор VLESS URL
        vless_urls = []
        keys_to_update = []

        for (
            v2ray_uuid,
            client_config,
            domain,
            api_url,
            api_key,
            country,
            server_name,
        ) in keys:
            config = None

            # Используем сохраненную конфигурацию, если она есть и содержит vless://
            if client_config and 'vless://' in client_config:
                # Нормализуем сохраненную конфигурацию
                config = normalize_vless_host(
                    client_config,
                    domain,
                    api_url or ''
                )
                # Удаляем фрагмент (email) из сохраненной конфигурации, если он есть
                # Название сервера будет добавлено динамически
                config = remove_fragment_from_vless(config)
            else:
                # Получаем конфигурацию через API
                try:
                    server_config = {
                        'api_url': api_url,
                        'api_key': api_key,
                        'domain': domain,
                    }
                    protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                    fetched_config = await protocol_client.get_user_config(
                        v2ray_uuid,
                        {
                            'domain': domain,
                            'port': 443,
                            'email': f"user_{user_id}@veilbot.com",
                        },
                    )

                    # Извлекаем VLESS URL из конфигурации и нормализуем
                    if 'vless://' in fetched_config:
                        lines = fetched_config.split('\n')
                        for line in lines:
                            if line.strip().startswith('vless://'):
                                config = normalize_vless_host(
                                    line.strip(),
                                    domain,
                                    api_url or ''
                                )
                                break
                        else:
                            config = normalize_vless_host(
                                fetched_config.strip(),
                                domain,
                                api_url or ''
                            )
                    else:
                        config = fetched_config.strip()

                    # Удаляем фрагмент (email) из конфигурации, полученной из API
                    # V2Ray API может возвращать конфигурацию с email в фрагменте
                    if config:
                        config = remove_fragment_from_vless(config)
                        # Сохраняем конфигурацию без фрагмента в БД
                        config_for_db = config
                        keys_to_update.append((config_for_db, v2ray_uuid))

                except Exception as e:
                    logger.error(
                        f"Failed to get config for key {v2ray_uuid[:8]}...: {e}",
                        exc_info=True,
                    )
                    continue

            # Добавляем название сервера из админки в фрагмент VLESS URL
            # Это делается для всех конфигураций (и сохраненных, и полученных из API)
            if config and config.startswith('vless://'):
                if not server_name:
                    logger.warning(
                        f"Server name is empty for server_id (uuid={v2ray_uuid[:8]}...), "
                        f"country={country}, domain={domain}"
                    )
                else:
                    logger.debug(
                        f"Adding server name '{server_name}' to config for UUID {v2ray_uuid[:8]}..."
                    )
                config_before = config
                config = add_server_name_to_vless(config, server_name)
                if config != config_before:
                    logger.debug(
                        f"Updated config fragment: '{config_before.split('#')[-1] if '#' in config_before else 'none'}' -> '{config.split('#')[-1] if '#' in config else 'none'}'"
                    )
                vless_urls.append(config)

        # Обновление конфигураций в БД
        if keys_to_update:
            with get_db_cursor(commit=True) as cursor:
                for config, v2ray_uuid in keys_to_update:
                    cursor.execute(
                        "UPDATE v2ray_keys SET client_config = ? WHERE v2ray_uuid = ?",
                        (config, v2ray_uuid),
                    )

        if not vless_urls:
            logger.warning(f"No valid VLESS URLs found for subscription {subscription_id}")
            return None

        # Объединение и кодирование
        subscription_content = '\n'.join(vless_urls)
        encoded_content = base64.b64encode(subscription_content.encode('utf-8')).decode('utf-8')

        # Кэширование
        _subscription_cache.set(cache_key, encoded_content, ttl=CACHE_TTL)

        # Обновление last_updated_at
        await self.repository.update_subscription_last_updated_async(subscription_id)

        logger.info(
            f"Generated subscription content for subscription {subscription_id} "
            f"with {len(vless_urls)} servers"
        )

        return encoded_content

    async def create_subscription(
        self,
        user_id: int,
        tariff_id: int,
        duration_sec: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Создать новую подписку для пользователя
        
        Args:
            user_id: ID пользователя
            tariff_id: ID тарифа
            duration_sec: Длительность в секундах
            
        Returns:
            Словарь с данными подписки или None при ошибке
        """
        now = int(time.time())
        expires_at = now + duration_sec

        # Проверка наличия активной подписки
        existing = await self.repository.get_active_subscription_async(user_id)
        if existing:
            # Продлеваем существующую подписку
            existing_id = existing[0]
            existing_expires_at = existing[4]
            # ПРИБАВЛЯЕМ срок к текущей дате истечения, а не берем максимум
            # Это гарантирует, что пользователь получит полный оплаченный срок
            new_expires_at = existing_expires_at + duration_sec
            await self.repository.extend_subscription_async(existing_id, new_expires_at)
            
            # Продлеваем все ключи подписки на серверах
            now = int(time.time())
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(
                    """
                    UPDATE v2ray_keys 
                    SET expiry_at = ? 
                    WHERE subscription_id = ? AND expiry_at > ?
                    """,
                    (new_expires_at, existing_id, now)
                )
                keys_extended = cursor.rowcount
                logger.info(
                    f"Extended {keys_extended} keys for subscription {existing_id} "
                    f"to {new_expires_at}"
                )
            
            logger.info(
                f"Extended existing subscription {existing_id} for user {user_id}: "
                f"{existing_expires_at} -> {new_expires_at} (+{duration_sec} sec)"
            )
            return {
                'id': existing_id,
                'token': existing[2],
                'expires_at': new_expires_at,
                'extended': True,
            }

        # Генерация уникального токена
        max_attempts = 10
        for _ in range(max_attempts):
            subscription_token = str(uuid.uuid4())
            # Проверяем уникальность
            existing_sub = await self.repository.get_subscription_by_token_async(subscription_token)
            if not existing_sub:
                break
        else:
            logger.error(f"Failed to generate unique subscription token after {max_attempts} attempts")
            return None

        # Создание подписки в БД
        try:
            subscription_id = await self.repository.create_subscription_async(
                user_id=user_id,
                subscription_token=subscription_token,
                expires_at=expires_at,
                tariff_id=tariff_id,
            )

            # Создание ключей на всех активных V2Ray серверах
            with get_db_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, name, api_url, api_key, domain, v2ray_path
                    FROM servers
                    WHERE protocol = 'v2ray' AND active = 1
                    ORDER BY id
                    """,
                )
                servers = cursor.fetchall()

            created_keys = 0
            failed_servers = []

            for server_id, server_name, api_url, api_key, domain, v2ray_path in servers:
                try:
                    # Генерация email для ключа
                    key_email = f"{user_id}_subscription_{subscription_id}@veilbot.com"

                    # Создание ключа через V2Ray API с названием сервера
                    server_config = {
                        'api_url': api_url,
                        'api_key': api_key,
                        'domain': domain,
                    }
                    protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                    # Передаем название сервера вместо email для name в V2Ray API
                    user_data = await protocol_client.create_user(key_email, name=server_name)

                    if not user_data or not user_data.get('uuid'):
                        raise Exception("Failed to create user on V2Ray server")

                    v2ray_uuid = user_data['uuid']

                    # Получение client_config
                    client_config = await protocol_client.get_user_config(
                        v2ray_uuid,
                        {
                            'domain': domain,
                            'port': 443,
                            'email': key_email,
                        },
                    )

                    # Извлекаем VLESS URL из конфигурации
                    if 'vless://' in client_config:
                        lines = client_config.split('\n')
                        for line in lines:
                            if line.strip().startswith('vless://'):
                                client_config = line.strip()
                                break

                    # Сохранение ключа в БД
                    # Временно отключаем проверку внешних ключей из-за несоответствия структуры users(id) vs users(user_id)
                    with get_db_cursor(commit=True) as cursor:
                        # Отключаем проверку внешних ключей для этой операции
                        cursor.connection.execute("PRAGMA foreign_keys = OFF")
                        try:
                            cursor.execute(
                                """
                                INSERT INTO v2ray_keys 
                                (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id, client_config, subscription_id)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    server_id,
                                    user_id,
                                    v2ray_uuid,
                                    key_email,
                                    now,
                                    expires_at,
                                    tariff_id,
                                    client_config,
                                    subscription_id,
                                ),
                            )
                        finally:
                            # Включаем обратно проверку внешних ключей
                            cursor.connection.execute("PRAGMA foreign_keys = ON")

                    created_keys += 1
                    logger.info(
                        f"Created key for subscription {subscription_id} on server {server_id}"
                    )

                except Exception as e:
                    logger.error(
                        f"Failed to create key for subscription {subscription_id} "
                        f"on server {server_id}: {e}",
                        exc_info=True,
                    )
                    failed_servers.append(server_id)

            logger.info(
                f"Created subscription {subscription_id} for user {user_id}: "
                f"{created_keys} keys created, {len(failed_servers)} failed"
            )

            return {
                'id': subscription_id,
                'token': subscription_token,
                'expires_at': expires_at,
                'created_keys': created_keys,
                'failed_servers': failed_servers,
            }

        except Exception as e:
            logger.error(f"Failed to create subscription for user {user_id}: {e}", exc_info=True)
            return None

