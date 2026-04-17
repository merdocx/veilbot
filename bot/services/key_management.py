"""
Модуль для управления ключами (продление, перевыпуск, смена протокола/страны)
Вынесен из bot.py для улучшения поддерживаемости
"""
import asyncio
import time
import logging
import sqlite3
from typing import Optional, Dict, Any, Tuple
from aiogram import types

from app.infra.sqlite_utils import get_db_cursor
from bot.services.subscription_server_groups import user_has_active_paid_subscription
from vpn_protocols import format_duration, ProtocolFactory, normalize_vless_host
from bot.utils import format_key_message_unified
from bot.keyboards import get_main_menu
from bot.core import get_bot_instance
from app.infra.foreign_keys import safe_foreign_keys_off
from config import PROTOCOLS

logger = logging.getLogger(__name__)

_COLUMN_CACHE: Dict[str, set[str]] = {}


# ============================================================================
# Вспомогательные функции
# ============================================================================

def check_server_availability(api_url: str, cert_sha256: str, protocol: str = 'v2ray') -> bool:
    """
    Проверяет доступность VPN сервера (V2Ray API).
    """
    try:
        import requests
        response = requests.get(f"{api_url}/", verify=False, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logging.warning(f"Server availability check failed: {e}")
        return False


def find_alternative_server(
    cursor: sqlite3.Cursor, 
    country: Optional[str], 
    protocol: str, 
    exclude_server_id: Optional[int] = None
) -> Optional[Tuple]:
    """
    Находит альтернативный сервер той же страны и протокола
    
    Args:
        cursor: Курсор базы данных
        country: Страна сервера (опционально)
        protocol: Протокол VPN (v2ray)
        exclude_server_id: ID сервера, который нужно исключить из поиска
    
    Returns:
        Tuple с данными сервера (id, name, api_url, cert_sha256, domain, api_key, v2ray_path) 
        или None, если альтернативный сервер не найден
    """
    if country:
        cursor.execute("""
            SELECT id, name, api_url, cert_sha256, domain, api_key, v2ray_path 
            FROM servers 
            WHERE active = 1 AND country = ? AND protocol = ? AND id != ?
            ORDER BY RANDOM() LIMIT 1
        """, (country, protocol, exclude_server_id or 0))
    else:
        cursor.execute("""
            SELECT id, name, api_url, cert_sha256, domain, api_key, v2ray_path 
            FROM servers 
            WHERE active = 1 AND protocol = ? AND id != ?
            ORDER BY RANDOM() LIMIT 1
        """, (protocol, exclude_server_id or 0))
    
    row = cursor.fetchone()
    if not row:
        return None
    
    return row


def _table_has_column(cursor: sqlite3.Cursor, table_name: str, column_name: str) -> bool:
    columns = _COLUMN_CACHE.get(table_name)
    if columns is None:
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = {row[1] for row in cursor.fetchall()}
        _COLUMN_CACHE[table_name] = columns
    return column_name in columns


def _resolve_v2ray_usage_metadata(
    cursor: sqlite3.Cursor,
    key_id: Optional[int],
    fallback_usage: Any = 0,
    fallback_over_limit_at: Optional[int] = None,
    fallback_over_limit_notified: Any = 0,
) -> tuple[int, Optional[int], int]:
    """
    Возвращает актуальные значения трафика для V2Ray ключа из базы данных.

    Args:
        cursor: активный курсор SQLite.
        key_id: ID записи ключа в таблице v2ray_keys.
        fallback_usage: значение трафика, полученное ранее (по умолчанию 0).
        fallback_over_limit_at: время превышения лимита из внешних данных (legacy, не используется).
        fallback_over_limit_notified: флаг уведомления о превышении из внешних данных (legacy, не используется).

    Returns:
        Кортеж (traffic_usage_bytes, None, 0) - возвращает только usage, остальное для обратной совместимости.
    
    ВАЖНО: traffic_over_limit_at и traffic_over_limit_notified удалены из v2ray_keys.
    Для подписок используются аналогичные поля в subscriptions.
    """
    usage = fallback_usage or 0

    if not key_id:
        return int(usage or 0), None, 0

    try:
        # ВАЖНО: traffic_over_limit_at и traffic_over_limit_notified удалены из v2ray_keys
        cursor.execute(
            """
            SELECT traffic_usage_bytes
            FROM v2ray_keys
            WHERE id = ?
            """,
            (key_id,),
        )
        row = cursor.fetchone()
    except sqlite3.OperationalError as exc:  # noqa: BLE001
        logger.warning("Failed to read V2Ray usage metadata for key %s: %s", key_id, exc)
        return int(usage or 0), None, 0

    if row and row[0] is not None:
        usage = row[0]

    # Возвращаем None и 0 для удаленных полей (для обратной совместимости сигнатуры)
    return int(usage or 0), None, 0


# ============================================================================
# Функции продления ключей
# ============================================================================

def extend_existing_key(
    cursor: sqlite3.Cursor, 
    existing_key: Tuple, 
    duration: int, 
    email: Optional[str] = None, 
    tariff_id: Optional[int] = None
) -> None:
    """
    Продлевает существующий ключ на указанное время
    
    Если ключ истёк, продление начинается от текущего времени.
    Если ключ ещё активен, время добавляется к текущему сроку действия.
    
    Args:
        cursor: Курсор базы данных
        existing_key: Tuple с данными ключа (id, expiry_at, ...)
        duration: Продолжительность продления в секундах
        email: Email пользователя (опционально, для обновления)
        tariff_id: ID тарифа (опционально, для обновления)
    """
    now = int(time.time())
    if tariff_id:
        try:
            cursor.execute("SELECT traffic_limit_mb FROM tariffs WHERE id = ?", (tariff_id,))
            row = cursor.fetchone()
            if row and row[0] is not None:
                int(row[0])
        except Exception as e:  # noqa: BLE001
            logging.warning(f"Failed to fetch traffic_limit_mb for tariff {tariff_id}: {e}")
    # Получаем subscription_id для обновления подписки
    cursor.execute("SELECT subscription_id FROM v2ray_keys WHERE id = ?", (existing_key[0],))
    sub_row = cursor.fetchone()
    if not sub_row or not sub_row[0]:
        raise ValueError(f"Key {existing_key[0]} does not have subscription_id")
    
    subscription_id = sub_row[0]
    
    # Пытаемся получить текущий срок подписки (если таблица subscriptions есть)
    current_expires = None
    try:
        cursor.execute("SELECT expires_at FROM subscriptions WHERE id = ?", (subscription_id,))
        sub_expires_row = cursor.fetchone()
        if sub_expires_row:
            current_expires = sub_expires_row[0]
    except sqlite3.OperationalError as exc:  # noqa: BLE001
        if "no such table: subscriptions" in str(exc):
            current_expires = None
        else:
            raise
    
    # Логика продления:
    # - Если есть подписка, используем срок подписки (current_expires)
    # - Если подписки нет, используем срок ключа (existing_key[1])
    # - Если срок истёк, продлеваем от текущего времени
    # - Если не истёк, продлеваем от текущего срока
    
    if current_expires is not None:
        # Используем срок подписки, а не ключа
        if current_expires <= now:
            new_expiry = now + duration
            logging.info(
                "Extending expired subscription %s (key %s): was expired at %s, new expiry: %s",
                subscription_id,
                existing_key[0],
                current_expires,
                new_expiry,
            )
        else:
            new_expiry = current_expires + duration
            logging.info(
                "Extending active subscription %s (key %s): current expires at %s, new expiry: %s",
                subscription_id,
                existing_key[0],
                current_expires,
                new_expiry,
            )
    else:
        # Fallback на срок ключа, если подписки нет (старая логика для совместимости)
        if existing_key[1] <= now:
            new_expiry = now + duration
            logging.info(
                "Extending expired key %s (no subscription): was expired at %s, new expiry: %s",
                existing_key[0],
                existing_key[1],
                new_expiry,
            )
        else:
            new_expiry = existing_key[1] + duration
            logging.info(
                "Extending active key %s (no subscription): current expires at %s, new expiry: %s",
                existing_key[0],
                existing_key[1],
                new_expiry,
            )
    
    # Обновляем подписку, если таблица есть
    try:
        cursor.execute(
            """
            UPDATE subscriptions 
            SET expires_at = ?, last_updated_at = strftime('%s','now'), purchase_notification_sent = 0
            WHERE id = ?
            """,
            (new_expiry, subscription_id),
        )
    except sqlite3.OperationalError as exc:  # noqa: BLE001
        if "no such table: subscriptions" not in str(exc):
            raise
    
    # ВАЖНО: expiry_at удалено из таблицы v2ray_keys - срок действия берется из subscriptions
    # Обновление expiry_at здесь не требуется, так как срок действия управляется через подписку
    
    # Обновляем email и tariff_id если нужно
    if email and tariff_id:
        cursor.execute("UPDATE v2ray_keys SET email = ?, tariff_id = ? WHERE id = ?", (email, tariff_id, existing_key[0]))
    elif email:
        cursor.execute("UPDATE v2ray_keys SET email = ? WHERE id = ?", (email, existing_key[0]))
    elif tariff_id:
        cursor.execute("UPDATE v2ray_keys SET tariff_id = ? WHERE id = ?", (tariff_id, existing_key[0]))


async def extend_existing_key_with_fallback(
    cursor: sqlite3.Cursor, 
    existing_key: Tuple, 
    duration: int, 
    email: Optional[str] = None, 
    tariff_id: Optional[int] = None, 
    protocol: str = 'v2ray'
) -> None:
    """
    Продлевает существующий ключ с fallback на альтернативный сервер
    
    Если текущий сервер недоступен, пытается использовать альтернативный сервер
    той же страны и протокола. Если ключ истёк, продление начинается от текущего времени.
    
    Args:
        cursor: Курсор базы данных
        existing_key: Tuple с данными ключа (id, expiry_at, server_id, ...)
        duration: Продолжительность продления в секундах
        email: Email пользователя (опционально, для обновления)
        tariff_id: ID тарифа (опционально, для обновления)
        protocol: Протокол VPN (v2ray)
    """
    now = int(time.time())
    tariff_limit_mb: Optional[int] = None
    if tariff_id:
        try:
            cursor.execute("SELECT traffic_limit_mb FROM tariffs WHERE id = ?", (tariff_id,))
            row = cursor.fetchone()
            if row and row[0] is not None:
                tariff_limit_mb = int(row[0])
        except Exception as e:  # noqa: BLE001
            logging.warning(f"Failed to fetch traffic_limit_mb for tariff {tariff_id}: {e}")
    # Если ключ истёк, продляем от текущего времени, иначе от старого expiry_at
    if existing_key[1] <= now:
        new_expiry = now + duration
        logging.info(f"Extending expired key {existing_key[0]} ({protocol}): was expired at {existing_key[1]}, new expiry: {new_expiry}")
    else:
        new_expiry = existing_key[1] + duration
    
    # Получаем информацию о текущем сервере (V2Ray)
    server_id = existing_key[5]  # server_id из запроса
    cursor.execute("SELECT api_url, api_key, country, active FROM servers WHERE id = ?", (server_id,))
    server_data = cursor.fetchone()
    
    if not server_data:
        logging.error(f"Server {server_id} not found for V2Ray key {existing_key[0]}")
        return False
    
    api_url, api_key, country, is_active = server_data
    cert_sha256 = None  # Для V2Ray не используется

    # Если сервер неактивен, пытаемся найти альтернативный
    if not is_active:
        logging.warning(f"Server {server_id} is not active, looking for alternative for renewal")
        alternative_server = find_alternative_server(cursor, country, protocol, server_id)
        
        if not alternative_server:
            logging.error(f"No alternative active server found for country={country}, protocol={protocol}")
            return False
        
        alt_server_id, alt_name, alt_api_url, alt_cert_sha256, alt_domain, alt_api_key, alt_v2ray_path = alternative_server
        
        if not check_server_availability(alt_api_url, None, protocol):
            logging.error(f"Alternative server {alt_server_id} is not available")
            return False
        
        try:
            from vpn_protocols import V2RayProtocol
            v2ray_client = V2RayProtocol(alt_api_url, alt_api_key)
            user_data = await v2ray_client.create_user(email or f"user_{existing_key[0]}@veilbot.com")
            
            if not user_data or not user_data.get('uuid'):
                logging.error(f"Failed to create V2Ray key on alternative server {alt_server_id}")
                return False
            
            update_parts = [
                ("server_id = ?", alt_server_id),
                ("v2ray_uuid = ?", user_data['uuid']),
                ("email = ?", email or ''),
                ("tariff_id = ?", tariff_id or 0),
            ]
            if tariff_limit_mb is not None and _table_has_column(cursor, "v2ray_keys", "traffic_limit_mb"):
                update_parts.append(("traffic_limit_mb = ?", tariff_limit_mb))
            sql = "UPDATE v2ray_keys SET " + ", ".join(part for part, _ in update_parts) + " WHERE id = ?"
            params = [value for _, value in update_parts]
            params.append(existing_key[0])
            cursor.execute(sql, tuple(params))
            
            logging.info(f"V2Ray key {existing_key[0]} moved to alternative server {alt_server_id} ({alt_name})")
            return True
        except Exception as e:
            logging.error(f"Error creating V2Ray key on alternative server: {e}")
            return False
    
    # Если сервер активен, проверяем его доступность через API
    if check_server_availability(api_url, cert_sha256, protocol):
        update_parts = []
        if email is not None:
            update_parts.append(("email = ?", email))
        if tariff_id is not None:
            update_parts.append(("tariff_id = ?", tariff_id))
        if tariff_limit_mb is not None and _table_has_column(cursor, "v2ray_keys", "traffic_limit_mb"):
            update_parts.append(("traffic_limit_mb = ?", tariff_limit_mb))
        if update_parts:
            sql = "UPDATE v2ray_keys SET " + ", ".join(part for part, _ in update_parts) + " WHERE id = ?"
            params = [value for _, value in update_parts]
            params.append(existing_key[0])
            cursor.execute(sql, tuple(params))
        return True
    else:
        logging.warning(f"Server {server_id} is not available, looking for alternative")
        alternative_server = find_alternative_server(cursor, country, protocol, server_id)
        
        if not alternative_server:
            logging.error(f"No alternative server found for country={country}, protocol={protocol}")
            return False
        
        alt_server_id, alt_name, alt_api_url, alt_cert_sha256, alt_domain, alt_api_key, alt_v2ray_path = alternative_server
        
        if not check_server_availability(alt_api_url, alt_cert_sha256, protocol):
            logging.error(f"Alternative server {alt_server_id} is also not available")
            return False
        
        try:
            from vpn_protocols import V2RayProtocol
            v2ray_client = V2RayProtocol(alt_api_url, alt_api_key)
            user_data = await v2ray_client.create_user(email or f"user_{existing_key[0]}@veilbot.com")
            
            if not user_data or not user_data.get('uuid'):
                logging.error(f"Failed to create V2Ray key on alternative server {alt_server_id}")
                return False
            
            cursor.execute("""
                UPDATE v2ray_keys 
                SET server_id = ?, v2ray_uuid = ?, email = ?, tariff_id = ?
                WHERE id = ?
            """, (alt_server_id, user_data['uuid'], email or '', tariff_id or 0, existing_key[0]))
            
            logging.info(f"V2Ray key {existing_key[0]} moved to alternative server {alt_server_id} ({alt_name})")
            return True
                
        except Exception as e:
            logging.error(f"Error creating key on alternative server: {e}")
            return False


# ============================================================================
# Функции удаления ключей
# ============================================================================

async def delete_old_key_after_success(
    cursor: sqlite3.Cursor, 
    old_key_data: Dict[str, Any]
) -> None:
    """
    Удаляет старый ключ после успешного создания нового (V2Ray).
    
    Args:
        cursor: Курсор базы данных
        old_key_data: Словарь с данными старого ключа (type, server_id, v2ray_uuid, db_id).
    """
    try:
        key_type = old_key_data.get('type')
        logging.debug(f"[DELETE OLD KEY] type={key_type}, db_id={old_key_data.get('db_id')}, v2ray_uuid={old_key_data.get('v2ray_uuid')}, key_id={old_key_data.get('key_id')}")
        
        if key_type == "v2ray":
            # Удаляем старый ключ из V2Ray сервера
            server_id = old_key_data.get('server_id')
            v2ray_uuid = old_key_data.get('v2ray_uuid')
            db_id = old_key_data.get('db_id')
            
            if server_id and v2ray_uuid:
                cursor.execute("SELECT api_url, api_key FROM servers WHERE id = ?", (server_id,))
                old_server_data = cursor.fetchone()
                if old_server_data:
                    old_api_url, old_api_key = old_server_data
                    if old_api_url and old_api_key:
                        server_config = {'api_url': old_api_url, 'api_key': old_api_key}
                        protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                        try:
                            await protocol_client.delete_user(v2ray_uuid)
                            logging.info(f"Удален старый V2Ray ключ {v2ray_uuid} с сервера {server_id}")
                        except Exception as e:
                            logging.warning(f"Не удалось удалить старый V2Ray ключ {v2ray_uuid} с сервера {server_id}: {e}")
                    else:
                        logging.warning(f"[DELETE OLD KEY] API URL или API ключ не найдены для сервера {server_id}")
                else:
                    logging.warning(f"[DELETE OLD KEY] Сервер {server_id} не найден в базе")
            else:
                logging.warning(f"[DELETE OLD KEY] Недостаточно данных для удаления V2Ray ключа: server_id={server_id}, v2ray_uuid={v2ray_uuid}")
            
            # Удаляем старый ключ из базы
            if db_id:
                with safe_foreign_keys_off(cursor):
                    cursor.execute("DELETE FROM v2ray_keys WHERE id = ?", (db_id,))
                    logging.info(f"Удален старый V2Ray ключ {db_id} из базы")
                    try:
                        cursor.execute("DELETE FROM v2ray_usage_snapshots WHERE key_id = ?", (db_id,))
                    except sqlite3.OperationalError:
                        logging.debug("[DELETE OLD KEY] v2ray_usage_snapshots table not present while deleting key %s", db_id)
            else:
                logging.warning(f"[DELETE OLD KEY] DB ID не найден для удаления V2Ray ключа из базы")
        else:
            logging.error(f"[DELETE OLD KEY] Неизвестный тип ключа: {key_type}, old_key_data: {old_key_data}")
            
    except Exception as e:
        logging.error(f"Ошибка при удалении старого ключа: {e}", exc_info=True)


# ============================================================================
# Функции смены протокола и страны
# ============================================================================

async def switch_protocol_and_extend(
    cursor: sqlite3.Cursor, 
    message: types.Message, 
    user_id: int, 
    old_key_data: Dict[str, Any], 
    new_protocol: str, 
    new_country: str, 
    additional_duration: int, 
    email: str, 
    tariff: Dict[str, Any]
) -> None:
    """
    Меняет протокол (и возможно страну) с продлением времени
    
    Создает новый ключ с новым протоколом и страной, сохраняя оставшееся время
    старого ключа и добавляя новое купленное время. Старый ключ удаляется.
    
    Args:
        cursor: Курсор базы данных
        message: Telegram сообщение для отправки уведомлений пользователю
        user_id: ID пользователя
        old_key_data: Словарь с данными старого ключа
        new_protocol: Новый протокол ('v2ray')
        new_country: Новая страна сервера
        additional_duration: Дополнительное время в секундах (продление)
        email: Email пользователя
        tariff: Словарь с данными тарифа
    """
    get_bot_instance()
    main_menu = get_main_menu()
    
    now = int(time.time())
    old_protocol = old_key_data['protocol']
    old_country = old_key_data['country']
    
    # Если страна не указана, используем текущую страну старого ключа
    target_country = new_country or old_country
    
    # Считаем оставшееся время старого ключа
    remaining = max(0, old_key_data['expiry_at'] - now)
    
    # Общее время = оставшееся + новое купленное
    total_duration = remaining + additional_duration
    now + total_duration
    
    old_server_id = old_key_data['server_id']
    old_email = old_key_data.get('email') or email
    if old_key_data.get('type') == 'v2ray':
        usage_resolved, over_limit_resolved, notified_resolved = _resolve_v2ray_usage_metadata(
            cursor,
            old_key_data.get('id') or old_key_data.get('db_id'),
            old_key_data.get('traffic_usage_bytes'),
            old_key_data.get('traffic_over_limit_at'),
            old_key_data.get('traffic_over_limit_notified'),
        )
        # ВАЖНО: traffic_over_limit_at и traffic_over_limit_notified удалены из v2ray_keys
        old_key_data['traffic_usage_bytes'] = usage_resolved
        # over_limit_resolved и notified_resolved не используются, но сохраняем сигнатуру функции
    traffic_limit_mb = 0
    try:
        traffic_limit_mb = int(old_key_data.get('traffic_limit_mb') or 0)
    except (TypeError, ValueError):
        traffic_limit_mb = 0
    if isinstance(tariff, dict):
        try:
            tariff_limit = int(tariff.get('traffic_limit_mb') or 0)
        except (TypeError, ValueError):
            tariff_limit = 0
        if traffic_limit_mb == 0 and tariff_limit:
            traffic_limit_mb = tariff_limit
        tariff['traffic_limit_mb'] = tariff_limit
    if traffic_limit_mb == 0 and isinstance(tariff, dict) and tariff.get('id'):
        try:
            cursor.execute("SELECT traffic_limit_mb FROM tariffs WHERE id = ?", (tariff['id'],))
            row = cursor.fetchone()
            if row and row[0]:
                traffic_limit_mb = int(row[0])
        except Exception as e:  # noqa: BLE001
            logging.warning(f"Failed to fetch traffic_limit_mb for tariff {tariff.get('id')}: {e}")
    
    old_key_data['traffic_limit_mb'] = traffic_limit_mb
    
    logging.info(f"User {user_id}: switching protocol {old_protocol}→{new_protocol}, country {old_country}→{target_country}, remaining={remaining}s, adding={additional_duration}s")
    
    # Ищем сервер в целевой стране с новым протоколом с учетом access_level
    cursor.execute("""
        SELECT id, api_url, cert_sha256, domain, v2ray_path, api_key, COALESCE(access_level, 'all') as access_level FROM servers 
        WHERE active = 1 AND country = ? AND protocol = ?
    """, (target_country, new_protocol))
    servers_raw = cursor.fetchall()
    
    # Фильтруем серверы по доступности для пользователя
    from app.repositories.user_repository import UserRepository
    user_repo = UserRepository()
    is_vip = user_repo.is_user_vip(user_id)
    now_ts = int(time.time())
    
    has_active_paid_subscription = user_has_active_paid_subscription(cursor, user_id, now_ts)
    
    servers = []
    for server in servers_raw:
        server_access_level = server[6] if len(server) > 6 else 'all'
        if server_access_level == 'all':
            servers.append(server[:6])  # Без access_level
        elif server_access_level == 'vip' and is_vip:
            servers.append(server[:6])
        elif server_access_level == 'paid' and (is_vip or has_active_paid_subscription):
            servers.append(server[:6])
    
    if not servers:
        logging.error(f"No servers found for protocol={new_protocol}, country={target_country}")
        await message.answer(f"❌ Нет доступных серверов {PROTOCOLS[new_protocol]['name']} в стране {target_country}.", reply_markup=main_menu)
        return False
    
    # Берём первый подходящий сервер
    new_server_id, api_url, cert_sha256, domain, v2ray_path, api_key = servers[0]
    
    # Сохраняем данные старого ключа для удаления
    old_key_for_deletion = {
        'type': old_key_data['type'],
        'server_id': old_server_id,
        'key_id': old_key_data.get('key_id'),
        'v2ray_uuid': old_key_data.get('v2ray_uuid'),
        'db_id': old_key_data['id']
    }
    
    try:
        if new_protocol != "v2ray":
            await message.answer(
                "❌ Смена на этот протокол недоступна. Поддерживается только V2Ray (VLESS).",
                reply_markup=main_menu,
            )
            return False
        from vpn_protocols import V2RayProtocol
        v2ray_client = V2RayProtocol(api_url, api_key)
        user_data = await v2ray_client.create_user(old_email or f"user_{user_id}@veilbot.com")
        
        if not user_data or not user_data.get('uuid'):
            logging.error(f"Failed to create V2Ray key on server {new_server_id}")
            await message.answer("❌ Ошибка при создании V2Ray ключа на новом сервере.", reply_markup=main_menu)
            return False
        
        config = None
        if user_data.get('client_config'):
            config = user_data['client_config']
            if 'vless://' in config:
                lines = config.split('\n')
                for line in lines:
                    if line.strip().startswith('vless://'):
                        config = line.strip()
                        break
            access_url = config
            logging.info(f"Using client_config from create_user response for protocol switch")
        else:
            try:
                config = await v2ray_client.get_user_config(user_data['uuid'], {
                    'domain': domain,
                    'port': 443,
                    'path': v2ray_path or '/v2ray',
                    'email': old_email or f"user_{user_id}@veilbot.com"
                })
                if 'vless://' in config:
                    lines = config.split('\n')
                    for line in lines:
                        if line.strip().startswith('vless://'):
                            config = line.strip()
                            break
                access_url = config
            except Exception as e:
                logging.error(f"Failed to get user config for UUID {user_data['uuid'][:8]}...: {e}")
                raise Exception(f"Failed to get V2Ray config for user {user_data['uuid'][:8]}...: {e}. Cannot use fallback with hardcoded short ID as servers generate unique short IDs.")
        
        cursor.execute("SELECT subscription_id FROM v2ray_keys WHERE id = ?", (old_key_data.get('id') or old_key_data.get('db_id'),))
        subscription_row = cursor.fetchone()
        subscription_id = subscription_row[0] if subscription_row else None
        
        usage_bytes_new = old_key_data.get('traffic_usage_bytes', 0)
        cursor.execute(
            "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, created_at, email, tariff_id, client_config, "
            "traffic_usage_bytes, subscription_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_server_id,
                user_id,
                user_data['uuid'],
                now,
                old_email,
                tariff['id'],
                config,
                usage_bytes_new,
                subscription_id,
            ),
        )
        await delete_old_key_after_success(cursor, old_key_for_deletion)
        
        time_remaining_str = format_duration(remaining)
        time_added_str = format_duration(additional_duration)
        time_total_str = format_duration(total_duration)
        
        if old_country != target_country:
            msg = (
                f"🌍🔄 *Смена протокола и страны*\n\n"
                f"Ваш ключ перенесён:\n"
                f"• С *{PROTOCOLS[old_protocol]['name']}* на *{PROTOCOLS[new_protocol]['name']}*\n"
                f"• Из *{old_country}* в *{target_country}*\n\n"
                f"⏰ Оставшееся время: {time_remaining_str}\n"
                f"➕ Добавлено: {time_added_str}\n"
                f"📅 Итого: {time_total_str}\n\n"
                f"{format_key_message_unified(access_url, new_protocol, tariff)}"
            )
        else:
            msg = (
                f"🔄 *Смена протокола и продление*\n\n"
                f"Ваш ключ перенесён с *{PROTOCOLS[old_protocol]['name']}* на *{PROTOCOLS[new_protocol]['name']}*\n"
                f"Страна: *{target_country}*\n\n"
                f"⏰ Оставшееся время: {time_remaining_str}\n"
                f"➕ Добавлено: {time_added_str}\n"
                f"📅 Итого: {time_total_str}\n\n"
                f"{format_key_message_unified(access_url, new_protocol, tariff)}"
            )
        await message.answer(msg, reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
        
        cursor.connection.commit()
        
        logging.info(f"Successfully switched protocol for user {user_id}: {old_protocol}→{new_protocol}, {old_country}→{target_country}, total={total_duration}s")
        return True
        
    except Exception as e:
        logging.error(f"Error in switch_protocol_and_extend: {e}")
        await message.answer("❌ Произошла ошибка при смене протокола. Попробуйте позже.", reply_markup=main_menu)
        return False


async def change_country_and_extend(
    cursor: sqlite3.Cursor, 
    message: types.Message, 
    user_id: int, 
    key_data: Dict[str, Any], 
    new_country: str, 
    additional_duration: int, 
    email: str, 
    tariff: Dict[str, Any],
    reset_usage: bool = False,
) -> None:
    """
    Меняет страну для ключа и добавляет новое время (при покупке нового тарифа)
    
    Создает новый ключ на сервере в новой стране, сохраняя оставшееся время
    старого ключа и добавляя новое купленное время. Старый ключ удаляется.
    
    Args:
        cursor: Курсор базы данных
        message: Telegram сообщение для отправки уведомлений пользователю
        user_id: ID пользователя
        key_data: Словарь с данными текущего ключа
        new_country: Новая страна сервера
        additional_duration: Дополнительное время в секундах (продление)
        email: Email пользователя
        tariff: Словарь с данными тарифа
        reset_usage: Сбрасывать ли учёт трафика после переноса (используется при продлении)
    """
    get_bot_instance()
    main_menu = get_main_menu()
    
    now = int(time.time())
    protocol = key_data['protocol']
    traffic_limit_mb = 0
    try:
        traffic_limit_mb = int(key_data.get('traffic_limit_mb') or 0)
    except (TypeError, ValueError):
        traffic_limit_mb = 0
    if isinstance(tariff, dict):
        try:
            tariff_limit = int(tariff.get('traffic_limit_mb') or 0)
        except (TypeError, ValueError):
            tariff_limit = 0
        if traffic_limit_mb == 0 and tariff_limit:
            traffic_limit_mb = tariff_limit
        tariff['traffic_limit_mb'] = tariff_limit
    if traffic_limit_mb == 0 and tariff and isinstance(tariff, dict) and tariff.get('id'):
        try:
            cursor.execute("SELECT traffic_limit_mb FROM tariffs WHERE id = ?", (tariff['id'],))
            row = cursor.fetchone()
            if row and row[0]:
                traffic_limit_mb = int(row[0])
        except Exception as e:  # noqa: BLE001
            logging.warning(f"Failed to fetch traffic_limit_mb for tariff {tariff.get('id')}: {e}")
    
    key_data['traffic_limit_mb'] = traffic_limit_mb
    
    # Считаем оставшееся время старого ключа
    remaining = max(0, key_data['expiry_at'] - now)
    
    # Общее время = оставшееся + новое купленное
    total_duration = remaining + additional_duration
    now + total_duration
    
    old_server_id = key_data['server_id']
    old_country = key_data['country']
    old_email = key_data['email'] or email
    
    # Ищем сервер в новой стране с тем же протоколом с учетом access_level
    cursor.execute("""
        SELECT id, api_url, cert_sha256, domain, v2ray_path, api_key, COALESCE(access_level, 'all') as access_level FROM servers 
        WHERE active = 1 AND country = ? AND protocol = ?
    """, (new_country, protocol))
    servers_raw = cursor.fetchall()
    
    # Фильтруем серверы по доступности для пользователя
    from app.repositories.user_repository import UserRepository
    user_repo = UserRepository()
    is_vip = user_repo.is_user_vip(user_id)
    now_ts = int(time.time())
    
    has_active_paid_subscription = user_has_active_paid_subscription(cursor, user_id, now_ts)
    
    servers = []
    for server in servers_raw:
        server_access_level = server[6] if len(server) > 6 else 'all'
        if server_access_level == 'all':
            servers.append(server[:6])  # Без access_level
        elif server_access_level == 'vip' and is_vip:
            servers.append(server[:6])
        elif server_access_level == 'paid' and (is_vip or has_active_paid_subscription):
            servers.append(server[:6])
    
    if not servers:
        logging.error(f"No servers found for country={new_country}, protocol={protocol}")
        await message.answer(f"❌ Нет доступных серверов {PROTOCOLS[protocol]['name']} в стране {new_country}.", reply_markup=main_menu)
        return False
    
    # Берём первый подходящий сервер
    new_server_id, api_url, cert_sha256, domain, v2ray_path, api_key = servers[0]
    
    # Сохраняем данные старого ключа для удаления
    # ВАЖНО: traffic_over_limit_at и traffic_over_limit_notified удалены из v2ray_keys
    usage_default = key_data.get('traffic_usage_bytes')
    if key_data['type'] == 'v2ray':
        usage_default, _, _ = _resolve_v2ray_usage_metadata(
            cursor,
            key_data.get('id'),
            usage_default,
            None,  # fallback_over_limit_at (не используется)
            0,     # fallback_over_limit_notified (не используется)
        )
    old_key_data = {
        'type': key_data['type'],
        'server_id': old_server_id,
        'key_id': key_data.get('key_id'),
        'v2ray_uuid': key_data.get('v2ray_uuid'),
        'db_id': key_data['id'],
        'traffic_usage_bytes': usage_default or 0,
    }
    
    try:
        if protocol != "v2ray":
            await message.answer(
                "❌ Смена страны для этого протокола недоступна. Поддерживается только V2Ray (VLESS).",
                reply_markup=main_menu,
            )
            return False
        from vpn_protocols import V2RayProtocol
        v2ray_client = V2RayProtocol(api_url, api_key)
        access_url = None
        new_key_id: Optional[int] = None
        try:
            user_data = await v2ray_client.create_user(old_email or f"user_{user_id}@veilbot.com")
            
            if not user_data or not user_data.get('uuid'):
                logging.error(f"Failed to create V2Ray key on server {new_server_id}")
                await message.answer("❌ Ошибка при создании V2Ray ключа на новом сервере.", reply_markup=main_menu)
                return False
            
            config = None
            if user_data.get('client_config'):
                config = user_data['client_config']
                if 'vless://' in config:
                    lines = config.split('\n')
                    for line in lines:
                        if line.strip().startswith('vless://'):
                            config = line.strip()
                            break
                access_url = config
                logging.info(f"Using client_config from create_user response for country change")
            else:
                try:
                    config = await v2ray_client.get_user_config(user_data['uuid'], {
                        'domain': domain,
                        'port': 443,
                        'path': v2ray_path or '/v2ray',
                        'email': old_email or f"user_{user_id}@veilbot.com"
                    })
                    if 'vless://' in config:
                        lines = config.split('\n')
                        for line in lines:
                            if line.strip().startswith('vless://'):
                                config = line.strip()
                                break
                    access_url = config
                except Exception as e:
                    logging.error(f"Failed to get user config for UUID {user_data['uuid'][:8]}...: {e}")
                    raise Exception(f"Failed to get V2Ray config for user {user_data['uuid'][:8]}...: {e}. Cannot use fallback with hardcoded short ID as servers generate unique short IDs.")
            
            if reset_usage:
                usage_bytes_new = 0
            else:
                usage_bytes_new = usage_default or 0

            cursor.execute("SELECT subscription_id FROM v2ray_keys WHERE id = ?", (key_data['id'],))
            subscription_row = cursor.fetchone()
            subscription_id = subscription_row[0] if subscription_row else None
            
            cursor.execute(
                "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, created_at, email, tariff_id, client_config, "
                "traffic_usage_bytes, subscription_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_server_id,
                    user_id,
                    user_data['uuid'],
                    now,
                    old_email,
                    tariff['id'],
                    config,
                    usage_bytes_new,
                    subscription_id,
                ),
            )
            new_key_id = cursor.lastrowid

            if reset_usage and user_data.get('uuid'):
                try:
                    await v2ray_client.reset_key_usage(user_data['uuid'])
                except Exception as reset_error:
                    logging.error(f"Failed to reset V2Ray usage after renewal for UUID {user_data['uuid']}: {reset_error}")
                if new_key_id:
                    try:
                        cursor.execute(
                            """
                            UPDATE v2ray_keys
                            SET traffic_usage_bytes = 0
                            WHERE id = ?
                            """,
                            (new_key_id,),
                        )
                    except Exception as reset_db_error:
                        logging.error(f"Failed to reset V2Ray usage flags in DB for key {new_key_id}: {reset_db_error}")
        finally:
            try:
                await v2ray_client.close()
            except Exception as close_error:
                logging.warning(f"Error closing V2Ray client after country change: {close_error}")

        await delete_old_key_after_success(cursor, old_key_data)
        
        time_remaining_str = format_duration(remaining)
        time_added_str = format_duration(additional_duration)
        time_total_str = format_duration(total_duration)
        
        msg = (
            f"🌍 *Смена страны и продление*\n\n"
            f"Ваш ключ перенесён из *{old_country}* в *{new_country}*\n\n"
            f"⏰ Оставшееся время: {time_remaining_str}\n"
            f"➕ Добавлено: {time_added_str}\n"
            f"📅 Итого: {time_total_str}\n\n"
            f"{format_key_message_unified(access_url, protocol, tariff)}"
        )
        await message.answer(msg, reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
        
        # Админ-уведомления о действиях с ключами отключены:
        # админ получает только платежные уведомления о подписке.
        
        # Commit транзакции
        cursor.connection.commit()
        
        logging.info(f"Successfully changed country and extended for user {user_id}: {old_country} -> {new_country}, +{additional_duration}s")
        return True
        
    except Exception as e:
        logging.error(f"Error in change_country_and_extend: {e}")
        await message.answer("❌ Произошла ошибка при смене страны. Попробуйте позже.", reply_markup=main_menu)
        return False


# ============================================================================
# Функции смены протокола и страны для конкретного ключа
# ============================================================================

async def change_protocol_for_key(
    message: types.Message, 
    user_id: int, 
    key_data: Dict[str, Any]
) -> None:
    """
    Меняет протокол для конкретного ключа
    
    Позволяет пользователю сменить протокол VPN (V2Ray) для существующего ключа,
    сохраняя оставшееся время действия. Создает новый ключ с новым протоколом и удаляет старый.
    
    Args:
        message: Telegram сообщение для отправки уведомлений пользователю
        user_id: ID пользователя
        key_data: Словарь с данными ключа, должен содержать:
            - id: ID ключа в базе данных
            - type: Тип ключа ('v2ray')
            - protocol: Текущий протокол
            - country: Страна сервера
            - expiry_at: Время истечения ключа
            - server_id: ID сервера
    """
    get_bot_instance()
    main_menu = get_main_menu()
    
    await message.answer(
        "Смена протокола недоступна: поддерживается только V2Ray (VLESS).",
        reply_markup=main_menu,
    )


async def change_country_for_key(
    message: types.Message, 
    user_id: int, 
    key_data: Dict[str, Any], 
    new_country: str
) -> None:
    """
    Меняет страну для конкретного ключа
    
    Позволяет пользователю сменить страну сервера для существующего ключа,
    сохраняя оставшееся время действия и протокол. Создает новый ключ на сервере
    в новой стране и удаляет старый.
    
    Args:
        message: Telegram сообщение для отправки уведомлений пользователю
        user_id: ID пользователя
        key_data: Словарь с данными ключа, должен содержать:
            - id: ID ключа в базе данных
            - type: Тип ключа ('v2ray')
            - protocol: Протокол VPN
            - country: Текущая страна сервера
            - expiry_at: Время истечения ключа
            - server_id: ID сервера
        new_country: Новая страна сервера
    """
    bot = get_bot_instance()
    main_menu = get_main_menu()
    
    logging.info(f"[COUNTRY CHANGE] Starting country change for user {user_id}, key_data keys: {list(key_data.keys())}, new_country: {new_country}")
    
    now = int(time.time())
    
    with get_db_cursor(commit=False) as cursor:
        # Получаем тариф
        tariff_id = key_data.get('tariff_id')
        if not tariff_id:
            logging.error(f"[COUNTRY CHANGE] tariff_id not found in key_data: {key_data}")
            await message.answer("Ошибка: тариф не найден в данных ключа.", reply_markup=main_menu)
            return
        
        tariff_row = _fetch_tariff_row_with_limit(cursor, tariff_id)
        if not tariff_row:
            logging.error(f"[COUNTRY CHANGE] Tariff {tariff_id} not found in database")
            await message.answer("Ошибка: тариф не найден в базе данных.", reply_markup=main_menu)
            return
        tariff = {
            'id': tariff_id,
            'name': tariff_row[0],
            'duration_sec': tariff_row[1],
            'price_rub': tariff_row[2],
            'traffic_limit_mb': tariff_row[3],
        }
        
        # Считаем оставшееся время
        remaining = key_data['expiry_at'] - now
        if remaining <= 0:
            await message.answer("Срок действия ключа истёк.", reply_markup=main_menu)
            return
        
        old_server_id = key_data['server_id']
        old_country = key_data['country']
        old_email = key_data.get('email')
        protocol = key_data['protocol']
        
        # Ищем сервер той же страны с тем же протоколом с учетом access_level
        cursor.execute("""
            SELECT id, api_url, cert_sha256, domain, v2ray_path, api_key, COALESCE(access_level, 'all') as access_level FROM servers 
            WHERE active = 1 AND country = ? AND protocol = ?
        """, (new_country, protocol))
        servers_raw = cursor.fetchall()
        
        # Фильтруем серверы по доступности для пользователя
        from app.repositories.user_repository import UserRepository
        user_repo = UserRepository()
        is_vip = user_repo.is_user_vip(user_id)
        now_ts = int(time.time())
        
        has_active_paid_subscription = user_has_active_paid_subscription(cursor, user_id, now_ts)
        
        servers = []
        for server in servers_raw:
            server_access_level = server[6] if len(server) > 6 else 'all'
            if server_access_level == 'all':
                servers.append(server[:6])  # Без access_level
            elif server_access_level == 'vip' and is_vip:
                servers.append(server[:6])
            elif server_access_level == 'paid' and (is_vip or has_active_paid_subscription):
                servers.append(server[:6])
        
        if not servers:
            await message.answer(f"Нет серверов {PROTOCOLS[protocol]['name']} в стране {new_country}.", reply_markup=main_menu)
            return
        
        # Берём первый подходящий сервер
        new_server_id, api_url, cert_sha256, domain, v2ray_path, api_key = servers[0]
        
        # ВАЖНО: traffic_over_limit_at и traffic_over_limit_notified удалены из v2ray_keys
        usage_default = key_data.get('traffic_usage_bytes')
        if (key_data.get('type') or protocol) == 'v2ray':
            usage_default, _, _ = _resolve_v2ray_usage_metadata(
                cursor,
                key_data.get('id'),
                usage_default,
                None,  # fallback_over_limit_at (не используется)
                0,     # fallback_over_limit_notified (не используется)
            )

        # Сохраняем данные старого ключа для удаления после успешного создания нового
        old_key_data = {
            'type': key_data.get('type') or protocol,  # Используем type из key_data или протокол
            'server_id': old_server_id,
            'key_id': key_data.get('key_id'),
            'v2ray_uuid': key_data.get('v2ray_uuid'),
            'db_id': key_data.get('id'),
            'traffic_usage_bytes': usage_default or 0,
        }
        
        if not old_key_data['db_id']:
            logging.error(f"[COUNTRY CHANGE] Key ID not found in key_data: {key_data}")
            await message.answer("Ошибка: ID ключа не найден.", reply_markup=main_menu)
            return
        
        logging.info(f"[COUNTRY CHANGE] Old key data: type={old_key_data['type']}, protocol={protocol}, key_data_type={key_data.get('type')}, db_id={old_key_data['db_id']}, v2ray_uuid={old_key_data.get('v2ray_uuid')}, key_id={old_key_data.get('key_id')}")
        
        if protocol != "v2ray":
            await message.answer(
                "Смена страны для этого протокола недоступна. Поддерживается только V2Ray (VLESS).",
                reply_markup=main_menu,
            )
            return
        # Создаём новый V2Ray ключ
        server_config = {'api_url': api_url, 'api_key': api_key}
        protocol_client = ProtocolFactory.create_protocol(protocol, server_config)
        
        try:
            user_data = await protocol_client.create_user(old_email or f"user_{user_id}@veilbot.com")
            
            if not user_data or not user_data.get('uuid'):
                raise Exception(f"Failed to create V2Ray user - invalid response from server")
            
            config = None
            old_email_val = old_email or f"user_{user_id}@veilbot.com"
            logging.info(f"[COUNTRY CHANGE] Processing country change for email: {old_email_val}, new UUID: {user_data.get('uuid')}")
            
            if user_data.get('client_config'):
                config = user_data['client_config']
                logging.info(f"[COUNTRY CHANGE] Using client_config from create_user response for email {old_email_val}")
            else:
                logging.warning(f"[COUNTRY CHANGE] client_config not in create_user response for email {old_email_val}, fetching via get_user_config")
                await asyncio.sleep(1.0)
                config = await protocol_client.get_user_config(user_data['uuid'], {
                    'domain': domain,
                    'port': 443,
                    'path': v2ray_path or '/v2ray',
                    'email': old_email_val
                }, max_retries=5, retry_delay=1.5)
            
            config = normalize_vless_host(config, domain, api_url)
            
            old_uuid = old_key_data.get('v2ray_uuid')
            if old_uuid:
                try:
                    cursor.execute("SELECT api_url, api_key FROM servers WHERE id = ?", (old_server_id,))
                    old_server_data = cursor.fetchone()
                    if old_server_data:
                        old_api_url, old_api_key = old_server_data
                        old_server_config = {'api_url': old_api_url, 'api_key': old_api_key}
                        old_protocol_client = ProtocolFactory.create_protocol(protocol, old_server_config)
                        await old_protocol_client.delete_user(old_uuid)
                        logging.info(f"Удален старый V2Ray ключ {old_uuid} с сервера {old_server_id}")
                except Exception as e:
                    logging.warning(f"Не удалось удалить старый V2Ray ключ (возможно уже удален): {e}")
            
            with safe_foreign_keys_off(cursor):
                usage_bytes_new = usage_default or 0
                cursor.execute("SELECT subscription_id FROM v2ray_keys WHERE id = ?", (key_data['id'],))
                subscription_row = cursor.fetchone()
                subscription_id = subscription_row[0] if subscription_row else None
                
                cursor.execute(
                    "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, created_at, email, tariff_id, client_config, "
                    "traffic_usage_bytes, subscription_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_server_id,
                        user_id,
                        user_data['uuid'],
                        now,
                        old_email,
                        tariff_id,
                        config,
                        usage_bytes_new,
                        subscription_id,
                    ),
                )
            
            with safe_foreign_keys_off(cursor):
                cursor.execute("DELETE FROM v2ray_keys WHERE id = ?", (old_key_data['db_id'],))
                logging.info(f"Удален старый V2Ray ключ {old_key_data['db_id']} из базы")
            
            country_text = (
                "🌍 *Смена страны*\n\n"
                f"Ваш ключ перенесён из *{old_country}* в *{new_country}*.\n\n"
                f"⏰ Оставшееся время: {format_duration(remaining)}\n\n"
            )
            await message.answer(
                country_text + format_key_message_unified(config, protocol, tariff, remaining),
                reply_markup=main_menu,
                disable_web_page_preview=True,
                parse_mode="Markdown",
            )
            
        except Exception as e:
            logging.error(f"[COUNTRY CHANGE] Ошибка при создании нового V2Ray ключа: {e}", exc_info=True)
            
            try:
                if 'user_data' in locals() and user_data and user_data.get('uuid'):
                    await protocol_client.delete_user(user_data['uuid'])
                    logging.info(f"[COUNTRY CHANGE] Deleted V2Ray user {user_data['uuid']} from server due to error")
            except Exception as cleanup_error:
                logging.error(f"[COUNTRY CHANGE] Error cleaning up V2Ray user after error: {cleanup_error}")
            
            error_msg = "Ошибка при создании нового ключа."
            if "401" in str(e):
                error_msg = "Ошибка авторизации на сервере V2Ray. Обратитесь к администратору."
            elif "404" in str(e):
                error_msg = "Сервер V2Ray недоступен. Попробуйте позже."
            await message.answer(error_msg, reply_markup=main_menu)
            return
        
        # Админ-уведомления о действиях с ключами отключены:
        # админ получает только платежные уведомления о подписке.
        
        # Ручной commit транзакции
        cursor.connection.commit()


async def reissue_specific_key(
    message: types.Message, 
    user_id: int, 
    key_data: Dict[str, Any]
) -> None:
    """
    Перевыпускает конкретный ключ
    
    Создает новый ключ с теми же параметрами (протокол, страна, срок действия),
    что и старый ключ. Используется для решения проблем с доступом к VPN.
    Старый ключ удаляется после успешного создания нового.
    
    Args:
        message: Telegram сообщение для отправки уведомлений пользователю
        user_id: ID пользователя
        key_data: Словарь с данными ключа, должен содержать:
            - id: ID ключа в базе данных
            - type: Тип ключа ('v2ray')
            - protocol: Протокол VPN
            - country: Страна сервера
            - expiry_at: Время истечения ключа
            - server_id: ID сервера
            - email: Email пользователя (опционально)
    """
    bot = get_bot_instance()
    main_menu = get_main_menu()
    
    now = int(time.time())
    
    with get_db_cursor(commit=True) as cursor:
        # Получаем тариф
        cursor.execute("SELECT name, duration_sec, price_rub, traffic_limit_mb FROM tariffs WHERE id = ?", (key_data['tariff_id'],))
        tariff_row = cursor.fetchone()
        if not tariff_row:
            await message.answer("Ошибка: тариф не найден.", reply_markup=main_menu)
            return
        tariff = {
            'id': key_data['tariff_id'],
            'name': tariff_row[0],
            'duration_sec': tariff_row[1],
            'price_rub': tariff_row[2],
            'traffic_limit_mb': tariff_row[3],
        }
        
        # Считаем оставшееся время
        remaining = key_data['expiry_at'] - now
        if remaining <= 0:
            await message.answer("Срок действия ключа истёк.", reply_markup=main_menu)
            return
        
        old_server_id = key_data['server_id']
        country = key_data['country']
        old_email = key_data['email']
        protocol = key_data['protocol']
        try:
            traffic_limit_mb = int(key_data.get('traffic_limit_mb') or 0)
        except (TypeError, ValueError):
            traffic_limit_mb = 0
        try:
            tariff_limit = int(tariff.get('traffic_limit_mb') or 0)
        except (TypeError, ValueError):
            tariff_limit = 0
        if traffic_limit_mb == 0 and tariff_limit:
            traffic_limit_mb = tariff_limit
        if traffic_limit_mb == 0:
            traffic_limit_mb = tariff_limit
        tariff['traffic_limit_mb'] = tariff_limit
        
        # Ищем другие серверы той же страны и протокола с учетом access_level
        cursor.execute("""
            SELECT id, api_url, cert_sha256, domain, v2ray_path, api_key, max_keys, COALESCE(access_level, 'all') as access_level FROM servers 
            WHERE active = 1 AND country = ? AND protocol = ? AND id != ?
        """, (country, protocol, old_server_id))
        other_servers_raw = cursor.fetchall()
        
        # Проверяем доступность других серверов для пользователя
        available_servers = []
        from app.repositories.user_repository import UserRepository
        user_repo = UserRepository()
        is_vip = user_repo.is_user_vip(user_id)
        
        has_active_paid_subscription = user_has_active_paid_subscription(cursor, user_id, now)
        
        for server in other_servers_raw:
            server_id, api_url, cert_sha256, domain, v2ray_path, api_key, max_keys, server_access_level = server
            
            # Проверяем доступность по access_level
            if server_access_level == 'all':
                pass  # Доступен всем
            elif server_access_level == 'vip' and not is_vip:
                logging.debug(f"Server {server_id} is VIP-only, user {user_id} is not VIP, skipping")
                continue
            elif server_access_level == 'paid' and not (is_vip or has_active_paid_subscription):
                logging.debug(f"Server {server_id} is paid-only, user {user_id} has no paid subscription/VIP, skipping")
                continue
            
            # Проверяем емкость сервера
            cursor.execute("""
                SELECT COUNT(*) FROM v2ray_keys k
                JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.server_id = ? AND sub.expires_at > ?
            """, (server_id, now))
            
            current_keys = cursor.fetchone()[0]
            if current_keys < max_keys:
                available_servers.append(server)
        
        # Выбираем сервер для перевыпуска
        if available_servers:
            # Есть другие доступные серверы - используем первый
            new_server_id, api_url, cert_sha256, domain, v2ray_path, api_key, max_keys = available_servers[0]
            logging.info(f"Найден другой сервер {new_server_id} для перевыпуска ключа")
        else:
            # Нет других серверов - проверяем, можно ли перевыпустить на том же сервере
            # НО только если текущий сервер доступен пользователю по access_level
            cursor.execute("SELECT max_keys, COALESCE(access_level, 'all') as access_level FROM servers WHERE id = ?", (old_server_id,))
            server_info = cursor.fetchone()
            if not server_info:
                await message.answer(f"Сервер не найден. Обратитесь к администратору.", reply_markup=main_menu)
                return
            
            max_keys, server_access_level = server_info
            
            # Проверяем доступность текущего сервера для пользователя
            from app.repositories.user_repository import UserRepository
            user_repo = UserRepository()
            is_vip = user_repo.is_user_vip(user_id)
            
            has_active_paid_subscription = user_has_active_paid_subscription(cursor, user_id, now)
            
            server_accessible = False
            if server_access_level == 'all':
                server_accessible = True
            elif server_access_level == 'vip' and is_vip:
                server_accessible = True
            elif server_access_level == 'paid' and (is_vip or has_active_paid_subscription):
                server_accessible = True
            
            if not server_accessible:
                await message.answer(
                    f"Нет доступных серверов {PROTOCOLS[protocol]['name']} в вашей стране для перевыпуска ключа. "
                    f"Текущий сервер недоступен для вашего уровня доступа.",
                    reply_markup=main_menu
                )
                return
            
            cursor.execute("""
                SELECT COUNT(*) FROM v2ray_keys k
                JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.server_id = ? AND sub.expires_at > ?
            """, (old_server_id, now))
            
            current_keys = cursor.fetchone()[0]
            
            if current_keys < max_keys:
                new_server_id = old_server_id
                # Получаем данные текущего сервера
                cursor.execute("SELECT api_url, cert_sha256, domain, v2ray_path, api_key FROM servers WHERE id = ?", (old_server_id,))
                server_data = cursor.fetchone()
                api_url, cert_sha256, domain, v2ray_path, api_key = server_data
                logging.info(f"Других серверов нет, перевыпускаем на том же сервере {old_server_id}")
            else:
                await message.answer(f"Нет доступных серверов {PROTOCOLS[protocol]['name']} в вашей стране для перевыпуска ключа.", reply_markup=main_menu)
                return
        
        if key_data['type'] != "v2ray":
            await message.answer(
                "Перевыпуск доступен только для V2Ray (VLESS) ключей.",
                reply_markup=main_menu,
            )
            return
        server_config = {'api_url': api_url, 'api_key': api_key}
        protocol_client = ProtocolFactory.create_protocol(protocol, server_config)
        
        try:
            # Создаем пользователя на новом сервере (ВАЖНО: делаем это до удаления старого)
            user_data = await protocol_client.create_user(old_email or f"user_{user_id}@veilbot.com")
            
            # Валидация: проверяем, что пользователь действительно создан
            if not user_data or not user_data.get('uuid'):
                raise Exception(f"Failed to create V2Ray user - invalid response from server")
            
            # ИСПРАВЛЕНИЕ: Используем client_config из ответа create_user, если он есть
            config = None
            old_email_val = old_email or f"user_{user_id}@veilbot.com"
            logging.info(f"[REISSUE] Processing reissue for email: {old_email_val}, new UUID: {user_data.get('uuid')}")

            if user_data.get('client_config'):
                config = user_data['client_config']
                logging.info(f"[REISSUE] Using client_config from create_user response for email {old_email_val}")
                # Проверяем SNI и shortid в конфигурации
                if 'sni=' in config and 'sid=' in config:
                    # Извлекаем SNI и shortid для логирования
                    try:
                        import urllib.parse
                        if '?' in config:
                            params_str = config.split('?')[1].split('#')[0]
                            params = urllib.parse.parse_qs(params_str)
                            sni = params.get('sni', ['N/A'])[0]
                            sid = params.get('sid', ['N/A'])[0]
                            logging.info(f"[REISSUE] client_config SNI={sni}, shortid={sid} for email {old_email_val}")
                    except Exception as e:
                        logging.debug(f"[REISSUE] Could not parse SNI/sid from config: {e}")
                else:
                    logging.warning(f"[REISSUE] WARNING: client_config does not contain SNI or shortid for email {old_email_val}")
            else:
                # Если client_config нет в ответе, запрашиваем через get_user_config
                # Увеличиваем количество попыток и задержку, так как API может генерировать конфигурацию асинхронно
                logging.warning(f"[REISSUE] client_config not in create_user response for email {old_email_val}, fetching via get_user_config with extended retries")
                # Ждем немного перед первым запросом, чтобы дать API время сгенерировать конфигурацию
                await asyncio.sleep(1.0)
                config = await protocol_client.get_user_config(user_data['uuid'], {
                    'domain': domain,
                    'port': 443,
                    'path': v2ray_path or '/v2ray',
                    'email': old_email_val
                }, max_retries=5, retry_delay=1.5)
                # Проверяем, что полученная конфигурация содержит SNI и shortid
                if config and 'sni=' in config and 'sid=' in config:
                    try:
                        import urllib.parse
                        if '?' in config:
                            params_str = config.split('?')[1].split('#')[0]
                            params = urllib.parse.parse_qs(params_str)
                            sni = params.get('sni', ['N/A'])[0]
                            sid = params.get('sid', ['N/A'])[0]
                            logging.info(f"[REISSUE] get_user_config returned SNI={sni}, shortid={sid} for email {old_email_val}")
                    except Exception as e:
                        logging.debug(f"[REISSUE] Could not parse SNI/sid from get_user_config result: {e}")
                else:
                    logging.warning(f"[REISSUE] WARNING: get_user_config returned config without SNI or shortid for email {old_email_val}")

            # Удаляем старый ключ из V2Ray сервера
            old_uuid = key_data['v2ray_uuid']
            try:
                # Получаем данные старого сервера для правильного API ключа
                cursor.execute("SELECT api_url, api_key FROM servers WHERE id = ?", (old_server_id,))
                old_server_data = cursor.fetchone()
                if old_server_data:
                    old_api_url, old_api_key = old_server_data
                    old_server_config = {'api_url': old_api_url, 'api_key': old_api_key}
                    old_protocol_client = ProtocolFactory.create_protocol(protocol, old_server_config)
                    await old_protocol_client.delete_user(old_uuid)
            except Exception as e:
                logging.warning(f"Не удалось удалить старый V2Ray ключ (возможно уже удален): {e}")

            # ВАЖНО: traffic_over_limit_at и traffic_over_limit_notified удалены из v2ray_keys
            # traffic_limit_mb не устанавливается - лимит берется из подписки
            # Добавляем новый ключ с client_config в базу данных (до удаления старого)
            # Временно отключаем foreign key проверку для INSERT
            usage_bytes_new, _, _ = _resolve_v2ray_usage_metadata(
                cursor,
                key_data.get('id'),
                key_data.get('traffic_usage_bytes'),
                None,  # fallback_over_limit_at (не используется)
                0,     # fallback_over_limit_notified (не используется)
            )
            # ВАЖНО: expiry_at удалено из таблицы v2ray_keys - срок действия берется из subscriptions
            # Получаем subscription_id из старого ключа перед удалением
            cursor.execute("SELECT subscription_id FROM v2ray_keys WHERE id = ?", (key_data['id'],))
            subscription_row = cursor.fetchone()
            subscription_id = subscription_row[0] if subscription_row else None

            with safe_foreign_keys_off(cursor):
                cursor.execute(
                    "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, email, created_at, tariff_id, client_config, "
                    "traffic_usage_bytes, subscription_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_server_id,
                        user_id,
                        user_data['uuid'],
                        old_email or f"user_{user_id}@veilbot.com",
                        now,
                        key_data['tariff_id'],
                        config,
                        usage_bytes_new,
                        subscription_id,
                    ),
                )

            # Удаляем старый ключ из базы после успешного создания нового
            with safe_foreign_keys_off(cursor):
                cursor.execute("DELETE FROM v2ray_keys WHERE id = ?", (key_data['id'],))

            # Используем сохраненный config для отправки пользователю
            reissue_text = (
                "🔄 Ваш ключ перевыпущен. Пожалуйста, заново настройте его в приложении.\n\n"
                + format_key_message_unified(config, protocol, None, remaining)
            )
            await message.answer(reissue_text, reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")

        except Exception as e:
            logging.error(f"Ошибка при перевыпуске V2Ray ключа: {e}")
            
            try:
                if 'user_data' in locals() and user_data and user_data.get('uuid'):
                    await protocol_client.delete_user(user_data['uuid'])
                    logging.info(f"Deleted V2Ray user {user_data['uuid']} from server due to error")
            except Exception as cleanup_error:
                logging.error(f"Failed to cleanup V2Ray user after error: {cleanup_error}")
            
            await message.answer("Ошибка при создании нового V2Ray ключа.", reply_markup=main_menu)
            return
        
        # Админ-уведомления о действиях с ключами отключены:
        # админ получает только платежные уведомления о подписке.


def _fetch_tariff_row_with_limit(cursor: sqlite3.Cursor, tariff_id: int) -> Optional[tuple]:
    try:
        cursor.execute(
            "SELECT name, duration_sec, price_rub, traffic_limit_mb FROM tariffs WHERE id = ?",
            (tariff_id,),
        )
        return cursor.fetchone()
    except sqlite3.OperationalError as exc:
        if "traffic_limit_mb" in str(exc):
            cursor.execute(
                "SELECT name, duration_sec, price_rub FROM tariffs WHERE id = ?",
                (tariff_id,),
            )
            row = cursor.fetchone()
            if row:
                return (*row, 0)
            return None
        raise

