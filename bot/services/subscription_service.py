"""
Сервис для работы с подписками V2Ray
"""
import uuid
import base64
import time
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from app.repositories.subscription_repository import SubscriptionRepository
from app.infra.cache import SimpleCache
from vpn_protocols import (
    V2RayProtocol,
    ProtocolFactory,
    normalize_vless_host,
    add_server_name_to_vless,
    remove_fragment_from_vless,
)
from app.infra.sqlite_utils import get_db_cursor
from app.infra.foreign_keys import safe_foreign_keys_off
from config import SUPPORT_USERNAME

try:
    from app.settings import settings as _app_settings

    SUBSCRIPTION_DISPLAY_NAME = getattr(_app_settings, "SUBSCRIPTION_DISPLAY_NAME", "Vee VPN")
except Exception:  # pragma: no cover - fallback for startup issues/tests
    SUBSCRIPTION_DISPLAY_NAME = "Vee VPN"

logger = logging.getLogger(__name__)

BYTES_IN_GB = 1024 ** 3

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
        from app.infra.sqlite_utils import get_db_cursor
        
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
        from app.infra.sqlite_utils import get_db_cursor
        
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
        from app.infra.sqlite_utils import get_db_cursor
        
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


def _format_traffic_label(usage_bytes: int, limit_bytes: Optional[int]) -> str:
    """Форматирует метку трафика для комментария в подписке"""
    usage_gb = usage_bytes / BYTES_IN_GB
    if limit_bytes and limit_bytes > 0:
        limit_gb = limit_bytes / BYTES_IN_GB
        # Используем 2 знака после запятой для большей точности
        # Если меньше 1 GB, показываем в MB для избежания округления до 0 GB в v2raytun
        # v2raytun округляет значения < 0.5 GB до 0 GB при парсинге из комментария # Traffic:
        if usage_gb < 1.0:
            usage_mb = usage_bytes / (1024 * 1024)
            return f"{usage_mb:.2f} MB / {limit_gb:.1f} GB"
        return f"{usage_gb:.2f}/{limit_gb:.1f} GB"
    if usage_gb < 1.0:
        usage_mb = usage_bytes / (1024 * 1024)
        return f"{usage_mb:.2f} MB / unlimited"
    return f"{usage_gb:.2f}/unlimited GB"


def _normalize_support_username() -> Optional[str]:
    if not SUPPORT_USERNAME:
        return None
    username = SUPPORT_USERNAME.strip()
    if not username:
        return None
    return f"@{username.lstrip('@')}"


class SubscriptionService:
    def __init__(self, db_path: Optional[str] = None):
        self.repository = SubscriptionRepository(db_path)

    async def generate_subscription_content(self, token: str) -> Optional[str]:
        package = await self.generate_subscription_package(token)
        if not package:
            return None
        return package["content"]

    async def generate_subscription_package(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Сгенерировать содержимое подписки (base64-кодированный список VLESS URL)
        
        Args:
            token: Токен подписки
            
        Returns:
            Base64-кодированная строка с VLESS URL или None при ошибке
        """
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
        
        # Проверка лимита трафика подписки
        tariff_name: Optional[str] = None
        traffic_limit_bytes: Optional[int] = None
        traffic_usage_bytes: int = 0

        # Вычисляем использованный трафик динамически, суммируя трафик всех ключей подписки
        traffic_usage_bytes = self.repository.get_subscription_traffic_sum(subscription_id)

        # Берём эффективный лимит через репозиторий, чтобы логика совпадала с админкой
        traffic_limit_bytes = self.repository.get_subscription_traffic_limit(subscription_id)

        with get_db_cursor(commit=True) as cursor:
            # Тариф сейчас нужен только для отображения имени тарифа в метаданных
            cursor.execute(
                """
                SELECT t.name
                FROM subscriptions s
                LEFT JOIN tariffs t ON s.tariff_id = t.id
                WHERE s.id = ?
                """,
                (subscription_id,),
            )
            limits_row = cursor.fetchone()
            if limits_row:
                tariff_name = limits_row[0]

            if traffic_limit_bytes and traffic_usage_bytes > traffic_limit_bytes:
                cursor.execute(
                    """
                    SELECT traffic_over_limit_at
                    FROM subscriptions
                    WHERE id = ?
                    """,
                    (subscription_id,),
                )
                over_limit_row = cursor.fetchone()
                if over_limit_row and over_limit_row[0]:
                    grace_end = over_limit_row[0] + 86400  # 24 часа
                    if now > grace_end:
                        logger.warning(
                            "Subscription %s disabled due to traffic limit (%s > %s)",
                            subscription_id,
                            traffic_usage_bytes,
                            traffic_limit_bytes,
                        )
                        return None

        # Получение ключей подписки
        keys = await self.repository.get_subscription_keys_async(subscription_id, user_id, now)
        if not keys:
            logger.warning(f"No active keys found for subscription {subscription_id}")
            return None

        # Сбор VLESS URL
        vless_urls = []
        keys_to_update = []
        first_server_name: Optional[str] = None

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
                if not first_server_name and server_name:
                    first_server_name = server_name
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

        expiry_dt = datetime.fromtimestamp(expires_at)
        expiry_label = expiry_dt.strftime("%d.%m.%Y %H:%M")
        tariff_display = tariff_name or "Subscription"
        traffic_label = _format_traffic_label(traffic_usage_bytes, traffic_limit_bytes)
        support_contact = _normalize_support_username()
        subscription_title = SUBSCRIPTION_DISPLAY_NAME

        # ТЕСТ: Выводим всю доступную информацию о подписке в комментариях
        from vpn_protocols import format_duration
        
        header_lines = []
        
        # 1. Информация о времени
        header_lines.append(f"# Active until: {expiry_label}")
        
        if created_at:
            created_dt = datetime.fromtimestamp(created_at)
            created_label = created_dt.strftime("%d.%m.%Y %H:%M")
            header_lines.append(f"# Created: {created_label}")
        
        # Вычисляем остаток времени
        remaining_time = expires_at - now
        if remaining_time > 0:
            remaining_duration = format_duration(remaining_time)
            header_lines.append(f"# Remaining: {remaining_duration}")
        
        # Процент использования времени подписки
        if created_at and expires_at > created_at:
            total_lifetime = expires_at - created_at
            used_lifetime = now - created_at
            lifetime_percent = min(100.0, max(0.0, (used_lifetime / total_lifetime) * 100)) if total_lifetime > 0 else 0
            header_lines.append(f"# Lifetime: {lifetime_percent:.1f}%")
        
        if last_updated_at:
            updated_dt = datetime.fromtimestamp(last_updated_at)
            updated_label = updated_dt.strftime("%d.%m.%Y %H:%M")
            header_lines.append(f"# Last updated: {updated_label}")
        
        # 2. Информация о трафике
        header_lines.append(f"# Traffic: {traffic_label}")
        
        # Остаток трафика
        if traffic_limit_bytes and traffic_limit_bytes > 0:
            remaining_bytes = max(0, traffic_limit_bytes - traffic_usage_bytes)
            remaining_gb = remaining_bytes / BYTES_IN_GB
            if remaining_gb < 0.01:
                remaining_mb = remaining_bytes / (1024 * 1024)
                traffic_remaining_label = f"{remaining_mb:.2f} MB"
            else:
                traffic_remaining_label = f"{remaining_gb:.2f} GB"
            header_lines.append(f"# Traffic remaining: {traffic_remaining_label}")
            
            # Процент использования трафика
            traffic_usage_percent = min(100.0, max(0.0, (traffic_usage_bytes / traffic_limit_bytes) * 100)) if traffic_limit_bytes > 0 else 0
            header_lines.append(f"# Traffic usage: {traffic_usage_percent:.2f}%")
        
        # 3. Информация о тарифе
        if tariff_name:
            header_lines.append(f"# Plan: {tariff_name}")
        
        # 4. Информация о серверах
        server_count = len(vless_urls)
        if server_count > 0:
            header_lines.append(f"# Servers: {server_count}")
            
            # Собираем уникальные страны из ключей
            unique_countries = set()
            for (v2ray_uuid, client_config, domain, api_url, api_key, country, server_name) in keys:
                if country:
                    unique_countries.add(country)
            
            if unique_countries:
                countries_list = ", ".join(sorted(unique_countries))
                header_lines.append(f"# Locations: {countries_list}")
        
        # 5. Статус подписки
        status = "Активна" if (is_active and expires_at > now) else "Истекла"
        header_lines.append(f"# Status: {status}")
        
        # 6. Дополнительная информация
        header_lines.append(f"# Subscription ID: {subscription_id}")
        
        # 7. Контакты поддержки
        if support_contact:
            header_lines.append(f"# Support: {support_contact}")

        # Заголовок подписки содержит все метаданные; названия серверов оставляем как в админке.

        subscription_content = '\n'.join(header_lines + vless_urls)
        encoded_content = base64.b64encode(subscription_content.encode('utf-8')).decode('utf-8')

        # Кэширование
        package = {
            "content": encoded_content,
            "metadata": {
                "tariff_name": tariff_name,
                "expires_at": expires_at,
                "traffic_usage_bytes": traffic_usage_bytes,
                "traffic_limit_bytes": traffic_limit_bytes,
                "subscription_title": subscription_title,
                "traffic_label": traffic_label,
                "support_contact": support_contact,
            },
        }
        _subscription_cache.set(cache_key, package, ttl=CACHE_TTL)

        # Обновление last_updated_at
        await self.repository.update_subscription_last_updated_async(subscription_id)

        logger.info(
            f"Generated subscription content for subscription {subscription_id} "
            f"with {len(vless_urls)} servers"
        )

        return package

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
            
            # Продлеваем все ключи подписки на серверах (v2ray и outline)
            # ВАЖНО: Продлеваем ВСЕ ключи подписки, даже если они истекли
            # Это гарантирует, что при продлении подписки все ключи будут активны
            with get_db_cursor(commit=True) as cursor:
                # Продлеваем V2Ray ключи
                cursor.execute(
                    """
                    UPDATE v2ray_keys 
                    SET expiry_at = ? 
                    WHERE subscription_id = ?
                    """,
                    (new_expires_at, existing_id)
                )
                v2ray_keys_extended = cursor.rowcount
                
                # Продлеваем Outline ключи
                cursor.execute(
                    """
                    UPDATE keys 
                    SET expiry_at = ? 
                    WHERE subscription_id = ?
                    """,
                    (new_expires_at, existing_id)
                )
                outline_keys_extended = cursor.rowcount
                
                keys_extended = v2ray_keys_extended + outline_keys_extended
                logger.info(
                    f"Extended {v2ray_keys_extended} V2Ray keys and {outline_keys_extended} Outline keys "
                    f"for subscription {existing_id} to {new_expires_at}"
                )
            
            # Если ключей нет, создаем их на всех активных серверах
            created_keys = 0
            failed_servers = []
            if keys_extended == 0:
                logger.info(
                    f"No keys found for subscription {existing_id}, creating keys on all active servers"
                )
                with get_db_cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT id, name, api_url, api_key, domain, v2ray_path, protocol, cert_sha256
                        FROM servers
                        WHERE active = 1 AND (protocol = 'v2ray' OR protocol = 'outline')
                        ORDER BY protocol, id
                        """,
                    )
                    servers = cursor.fetchall()

                for server_id, server_name, api_url, api_key, domain, v2ray_path, protocol, cert_sha256 in servers:
                    protocol_client = None
                    v2ray_uuid = None
                    outline_key_id = None
                    try:
                        # Генерация email для ключа
                        key_email = f"{user_id}_subscription_{existing_id}@veilbot.com"

                        if protocol == 'v2ray':
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
                            # ИСПРАВЛЕНИЕ: Используем client_config из ответа create_user, если он есть
                            client_config = user_data.get('client_config')
                            if not client_config:
                                # Если client_config нет в ответе, запрашиваем через get_user_config
                                logger.debug(f"client_config not in create_user response, fetching via get_user_config")
                                client_config = await protocol_client.get_user_config(
                                    v2ray_uuid,
                                    {
                                        'domain': domain,
                                        'port': 443,
                                        'email': key_email,
                                    },
                                )

                            # Извлекаем VLESS URL из конфигурации
                            if client_config and 'vless://' in client_config:
                                lines = client_config.split('\n')
                                for line in lines:
                                    if line.strip().startswith('vless://'):
                                        client_config = line.strip()
                                        break

                            # Сохранение V2Ray ключа в БД
                            with get_db_cursor(commit=True) as cursor:
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
                                            new_expires_at,
                                            tariff_id,
                                            client_config,
                                            existing_id,
                                        ),
                                    )
                                finally:
                                    cursor.connection.execute("PRAGMA foreign_keys = ON")
                        
                        elif protocol == 'outline':
                            # Создание ключа через Outline API
                            server_config = {
                                'api_url': api_url,
                                'cert_sha256': cert_sha256,
                            }
                            protocol_client = ProtocolFactory.create_protocol('outline', server_config)
                            user_data = await protocol_client.create_user(key_email)

                            if not user_data or not user_data.get('id'):
                                raise Exception("Failed to create user on Outline server")

                            outline_key_id = user_data['id']
                            access_url = user_data['accessUrl']

                            # Сохранение Outline ключа в БД
                            with get_db_cursor(commit=True) as cursor:
                                cursor.connection.execute("PRAGMA foreign_keys = OFF")
                                try:
                                    cursor.execute(
                                        """
                                        INSERT INTO keys 
                                        (server_id, user_id, access_url, expiry_at, traffic_limit_mb, notified, key_id, created_at, email, tariff_id, protocol, subscription_id)
                                        VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                                        """,
                                        (
                                            server_id,
                                            user_id,
                                            access_url,
                                            new_expires_at,
                                            0,  # traffic_limit_mb
                                            outline_key_id,
                                            now,
                                            key_email,
                                            tariff_id,
                                            'outline',
                                            existing_id,
                                        ),
                                    )
                                finally:
                                    cursor.connection.execute("PRAGMA foreign_keys = ON")
                        else:
                            logger.warning(
                                f"Unknown protocol {protocol} for server {server_id}, skipping"
                            )
                            continue

                        created_keys += 1
                        logger.info(
                            f"Created {protocol} key for subscription {existing_id} on server {server_id}"
                        )
                        if protocol_client:
                            await protocol_client.close()

                    except Exception as e:
                        logger.error(
                            f"Failed to create {protocol} key for subscription {existing_id} "
                            f"on server {server_id}: {e}",
                            exc_info=True,
                        )
                        # Если ключ был создан на сервере, но не сохранен в БД - пытаемся удалить его с сервера
                        if protocol_client:
                            try:
                                if protocol == 'v2ray' and v2ray_uuid:
                                    await protocol_client.delete_user(v2ray_uuid)
                                elif protocol == 'outline' and outline_key_id:
                                    await protocol_client.delete_user(outline_key_id)
                                await protocol_client.close()
                            except Exception as cleanup_error:
                                logger.error(f"Failed to cleanup orphaned key: {cleanup_error}")
                        failed_servers.append(server_id)
            
            logger.info(
                f"Extended existing subscription {existing_id} for user {user_id}: "
                f"{existing_expires_at} -> {new_expires_at} (+{duration_sec} sec), "
                f"extended {keys_extended} keys, created {created_keys} new keys"
            )
            return {
                'id': existing_id,
                'token': existing[2],
                'expires_at': new_expires_at,
                'extended': True,
                'created_keys': created_keys,
                'failed_servers': failed_servers,
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
                protocol_client = None
                v2ray_uuid = None
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
                    # ИСПРАВЛЕНИЕ: Используем client_config из ответа create_user, если он есть
                    client_config = user_data.get('client_config')
                    if not client_config:
                        # Если client_config нет в ответе, запрашиваем через get_user_config
                        logger.debug(f"client_config not in create_user response, fetching via get_user_config")
                        client_config = await protocol_client.get_user_config(
                            v2ray_uuid,
                            {
                                'domain': domain,
                                'port': 443,
                                'email': key_email,
                            },
                        )

                    # Извлекаем VLESS URL из конфигурации
                    if client_config and 'vless://' in client_config:
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
                    await protocol_client.close()

                except Exception as e:
                    logger.error(
                        f"Failed to create key for subscription {subscription_id} "
                        f"on server {server_id}: {e}",
                        exc_info=True,
                    )
                    # Если ключ был создан на сервере, но не сохранен в БД - пытаемся удалить его с сервера
                    if v2ray_uuid and protocol_client:
                        try:
                            await protocol_client.delete_user(v2ray_uuid)
                            logger.info(f"Cleaned up orphaned key on server {server_id}")
                        except Exception as cleanup_error:
                            logger.error(f"Failed to cleanup orphaned key: {cleanup_error}")
                    elif protocol_client:
                        try:
                            await protocol_client.close()
                        except Exception:
                            pass
                    failed_servers.append(server_id)

            # Проверяем, были ли созданы ключи
            if created_keys == 0:
                error_msg = f"Failed to create any keys for subscription {subscription_id}"
                logger.error(error_msg)
                # Деактивируем подписку, если не удалось создать ни одного ключа
                await self.repository.deactivate_subscription_async(subscription_id)
                return None

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

