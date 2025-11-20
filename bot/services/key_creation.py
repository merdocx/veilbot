"""
Модуль для создания новых VPN ключей
Вынесен из bot.py для улучшения поддерживаемости
"""
import asyncio
import time
import logging
import sqlite3
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, Callable
from aiogram import types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

from config import PROTOCOLS, ADMIN_ID
from utils import get_db_cursor
from vpn_protocols import format_duration, ProtocolFactory
from bot.keyboards import get_main_menu, get_countries_by_protocol
from bot.utils import format_key_message_unified, safe_send_message
from bot.core import get_bot_instance
from memory_optimizer import get_vpn_service, get_security_logger

logger = logging.getLogger(__name__)

# Глобальная переменная для lazy loading VPN протоколов
VPN_PROTOCOLS_AVAILABLE = None

COLUMN_CACHE: Dict[str, set[str]] = {}


def _get_table_columns(cursor: sqlite3.Cursor, table_name: str) -> set[str]:
    columns = COLUMN_CACHE.get(table_name)
    if columns is None:
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = {row[1] for row in cursor.fetchall()}
        COLUMN_CACHE[table_name] = columns
    return columns


def _table_has_column(cursor: sqlite3.Cursor, table_name: str, column_name: str) -> bool:
    return column_name in _get_table_columns(cursor, table_name)


def _insert_with_schema(cursor: sqlite3.Cursor, table_name: str, values: Dict[str, Any]) -> None:
    columns = _get_table_columns(cursor, table_name)
    insert_columns = [col for col in values.keys() if col in columns]
    if not insert_columns:
        raise ValueError(f"No matching columns to insert into {table_name}")
    placeholders = ", ".join(["?"] * len(insert_columns))
    cursor.execute(
        f"INSERT INTO {table_name} ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(values[col] for col in insert_columns),
    )


def select_available_server_by_protocol(
    cursor: sqlite3.Cursor, 
    country: Optional[str] = None, 
    protocol: str = 'outline', 
    for_renewal: bool = False
) -> Optional[Tuple]:
    """
    Выбор сервера с учетом протокола
    
    Args:
        cursor: Курсор базы данных
        country: Страна сервера
        protocol: Протокол (outline или v2ray)
        for_renewal: Если True, проверяет только active, не проверяет available_for_purchase (для продления)
    
    Returns:
        Tuple с данными сервера или None
    """
    if for_renewal:
        # Для продления проверяем только active
        if country:
            cursor.execute("""
                SELECT id, name, api_url, cert_sha256, domain, api_key, v2ray_path 
                FROM servers 
                WHERE active = 1 AND country = ? AND protocol = ?
                ORDER BY RANDOM() LIMIT 1
            """, (country, protocol))
        else:
            cursor.execute("""
                SELECT id, name, api_url, cert_sha256, domain, api_key, v2ray_path 
                FROM servers 
                WHERE active = 1 AND protocol = ?
                ORDER BY RANDOM() LIMIT 1
            """, (protocol,))
    else:
        # Для покупки проверяем и active, и available_for_purchase
        if country:
            cursor.execute("""
                SELECT id, name, api_url, cert_sha256, domain, api_key, v2ray_path 
                FROM servers 
                WHERE active = 1 AND available_for_purchase = 1 AND country = ? AND protocol = ?
                ORDER BY RANDOM() LIMIT 1
            """, (country, protocol))
        else:
            cursor.execute("""
                SELECT id, name, api_url, cert_sha256, domain, api_key, v2ray_path 
                FROM servers 
                WHERE active = 1 AND available_for_purchase = 1 AND protocol = ?
                ORDER BY RANDOM() LIMIT 1
            """, (protocol,))
    
    row = cursor.fetchone()
    if not row:
        return None
    
    return row


def _execute_with_limit_fallback(
    cursor: sqlite3.Cursor,
    sql_with_limit: str,
    sql_without_limit: str,
    params: tuple,
) -> None:
    try:
        cursor.execute(sql_with_limit, params)
    except sqlite3.OperationalError as exc:
        if "traffic_limit_mb" in str(exc):
            cursor.execute(sql_without_limit, params)
        else:
            raise


async def create_new_key_flow_with_protocol(
    cursor: sqlite3.Cursor, 
    message: Optional[types.Message], 
    user_id: int, 
    tariff: Dict[str, Any], 
    email: Optional[str] = None, 
    country: Optional[str] = None, 
    protocol: str = "outline", 
    for_renewal: bool = False,
    user_states: Optional[Dict[int, Dict[str, Any]]] = None,
    extend_existing_key_with_fallback: Optional[Callable] = None,
    change_country_and_extend: Optional[Callable] = None,
    switch_protocol_and_extend: Optional[Callable] = None,
    record_free_key_usage: Optional[Callable] = None
) -> None:
    """
    Создание нового ключа с поддержкой протоколов
    
    Args:
        cursor: Курсор базы данных
        message: Сообщение от пользователя (может быть None для webhook)
        user_id: ID пользователя
        tariff: Словарь с данными тарифа
        email: Email пользователя
        country: Страна сервера
        protocol: Протокол (outline или v2ray)
        for_renewal: Если True, при выборе сервера не проверяется available_for_purchase (только active)
        user_states: Словарь состояний пользователей (для очистки состояния)
        extend_existing_key_with_fallback: Функция продления ключа (импортируется из bot.py)
        change_country_and_extend: Функция смены страны (импортируется из bot.py)
        switch_protocol_and_extend: Функция смены протокола (импортируется из bot.py)
        record_free_key_usage: Функция записи использования бесплатного тарифа (импортируется из bot.py)
    """
    # Импортируем функции из соответствующих модулей для избежания циклических зависимостей (lazy import)
    if extend_existing_key_with_fallback is None or change_country_and_extend is None or switch_protocol_and_extend is None or record_free_key_usage is None or user_states is None:
        # Lazy import для избежания циклических зависимостей
        import importlib
        
        # Импортируем функции управления ключами из bot/services/key_management.py
        if extend_existing_key_with_fallback is None or change_country_and_extend is None or switch_protocol_and_extend is None:
            try:
                key_management_module = importlib.import_module('bot.services.key_management')
                if extend_existing_key_with_fallback is None:
                    extend_existing_key_with_fallback = getattr(key_management_module, 'extend_existing_key_with_fallback', None)
                if change_country_and_extend is None:
                    change_country_and_extend = getattr(key_management_module, 'change_country_and_extend', None)
                if switch_protocol_and_extend is None:
                    switch_protocol_and_extend = getattr(key_management_module, 'switch_protocol_and_extend', None)
            except Exception as e:
                logging.error(f"Error importing key_management functions: {e}", exc_info=True)
        
        # Импортируем record_free_key_usage из free_tariff.py
        if record_free_key_usage is None:
            try:
                from bot.services.free_tariff import record_free_key_usage as free_tariff_record
                record_free_key_usage = free_tariff_record
            except Exception as e:
                logging.error(f"Error importing record_free_key_usage from free_tariff: {e}", exc_info=True)
        
        # Импортируем user_states из bot.py
        if user_states is None:
            try:
                bot_module = importlib.import_module('bot')
                user_states = getattr(bot_module, 'user_states', {})
            except Exception as e:
                logging.error(f"Error importing user_states from bot module: {e}", exc_info=True)
        
        # Проверяем, что все необходимые функции загружены
        if extend_existing_key_with_fallback is None:
            logging.error("extend_existing_key_with_fallback is None after import")
        if change_country_and_extend is None:
            logging.error("change_country_and_extend is None after import")
        if switch_protocol_and_extend is None:
            logging.error("switch_protocol_and_extend is None after import")
    
    bot = get_bot_instance()
    main_menu = get_main_menu()
    
    now = int(time.time())
    traffic_limit_mb = 0
    if isinstance(tariff, dict):
        raw_limit = tariff.get('traffic_limit_mb')
        if raw_limit is None and tariff.get('id') is not None:
            try:
                cursor.execute("SELECT traffic_limit_mb FROM tariffs WHERE id = ?", (tariff['id'],))
                row = cursor.fetchone()
                if row is not None:
                    raw_limit = row[0]
            except Exception as e:  # noqa: BLE001
                logging.warning("Failed to fetch traffic_limit_mb for tariff %s: %s", tariff.get('id'), e)
        try:
            traffic_limit_mb = int(raw_limit or 0)
        except (TypeError, ValueError):
            traffic_limit_mb = 0
        if traffic_limit_mb == 0:
            logging.warning("[TRAFFIC LIMIT] Tariff %s provided zero traffic_limit_mb", tariff.get('id'))
        tariff['traffic_limit_mb'] = traffic_limit_mb
    GRACE_PERIOD = 86400  # 24 часа в секундах
    grace_threshold = now - GRACE_PERIOD
    
    # Проверяем наличие активного или недавно истекшего ключа (в пределах grace period)
    if protocol == "outline":
        _execute_with_limit_fallback(
            cursor,
            "SELECT k.id, k.expiry_at, k.access_url, s.country, k.server_id, k.key_id, k.tariff_id, k.email, "
            "COALESCE(k.traffic_limit_mb, 0) "
            "FROM keys k JOIN servers s ON k.server_id = s.id "
            "WHERE k.user_id = ? AND k.expiry_at > ? ORDER BY k.expiry_at DESC LIMIT 1",
            "SELECT k.id, k.expiry_at, k.access_url, s.country, k.server_id, k.key_id, k.tariff_id, k.email, 0 AS traffic_limit_mb "
            "FROM keys k JOIN servers s ON k.server_id = s.id "
            "WHERE k.user_id = ? AND k.expiry_at > ? ORDER BY k.expiry_at DESC LIMIT 1",
            (user_id, grace_threshold),
        )
        existing_key = cursor.fetchone()
        if existing_key:
            # Проверяем, отличается ли запрошенная страна от текущей
            current_country = existing_key[3]  # s.country
            
            if country and country != current_country:
                # Запрошена другая страна - запускаем логику смены страны с продлением
                logging.info(f"User {user_id} requested different country: current={current_country}, requested={country}. Running country change logic.")
                
                # Формируем key_data для функции смены страны
                key_data = {
                    'id': existing_key[0],
                    'expiry_at': existing_key[1],
                    'access_url': existing_key[2],
                    'country': current_country,
                    'server_id': existing_key[4],
                    'key_id': existing_key[5],
                    'tariff_id': existing_key[6] or tariff['id'],
                    'email': existing_key[7] or email,
                    'protocol': protocol,
                    'type': 'outline',
                    'traffic_limit_mb': existing_key[8] if len(existing_key) > 8 else traffic_limit_mb,
                }
                
                # Вызываем функцию смены страны с продлением
                if change_country_and_extend is None:
                    logging.error("change_country_and_extend is None, cannot change country for Outline key")
                    success = False
                else:
                    success = await change_country_and_extend(
                        cursor,
                        message,
                        user_id,
                        key_data,
                        country,
                        tariff['duration_sec'],
                        email,
                        tariff,
                        reset_usage=for_renewal,
                    )
                
                if success:
                    user_states.pop(user_id, None)
                    return
                else:
                    # Если не удалось сменить страну, создаем новый ключ
                    logging.warning(f"Failed to change country for key {existing_key[0]}, creating new key for user {user_id}")
                    # Продолжаем выполнение для создания нового ключа
            else:
                # Та же страна или страна не указана - продлеваем как обычно
                if extend_existing_key_with_fallback is None:
                    logging.error("extend_existing_key_with_fallback is None, cannot extend Outline key")
                    # Продолжаем создание нового ключа
                    success = False
                else:
                    success = await extend_existing_key_with_fallback(cursor, existing_key, tariff['duration_sec'], email, tariff['id'], protocol)
            
                if success:
                    # Получаем обновленную информацию о ключе
                    cursor.execute("SELECT access_url FROM keys WHERE id = ?", (existing_key[0],))
                    updated_key = cursor.fetchone()
                    access_url = updated_key[0] if updated_key else existing_key[2]
                    
                    # Очищаем состояние пользователя
                    user_states.pop(user_id, None)
                    
                    # Проверяем, был ли ключ истекшим
                    was_expired = existing_key[1] <= now
                    if was_expired:
                        msg_text = f"✅ Ваш истекший ключ восстановлен и продлён на {format_duration(tariff['duration_sec'])}!\n\n{format_key_message_unified(access_url, protocol, tariff)}"
                    else:
                        msg_text = f"Ваш ключ продлён на {format_duration(tariff['duration_sec'])}!\n\n{format_key_message_unified(access_url, protocol, tariff)}"
                    
                    # Отправляем сообщение пользователю
                    notification_sent = False
                    if message:
                        try:
                            await message.answer(msg_text, reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                            notification_sent = True
                        except Exception as e:
                            logging.error(f"Failed to send Outline renewal notification via message.answer to user {user_id}: {e}")
                            # Пробуем через safe_send_message как fallback
                            result = await safe_send_message(bot, user_id, msg_text, reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                            notification_sent = result is not None
                    else:
                        # Если message=None (например, из webhook), отправляем напрямую через bot
                        result = await safe_send_message(bot, user_id, msg_text, reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                        notification_sent = result is not None
                    
                    if notification_sent:
                        logging.info(f"Outline renewal notification sent successfully to user {user_id}")
                    else:
                        logging.warning(f"Failed to send Outline renewal notification to user {user_id}")
                    
                    return
                else:
                    # Если не удалось продлить, создаем новый ключ
                    logging.warning(f"Failed to extend key {existing_key[0]}, creating new key for user {user_id}")
                    # Продолжаем выполнение для создания нового ключа
    else:  # v2ray
        cursor.execute(
            "SELECT k.id, k.expiry_at, k.v2ray_uuid, s.domain, s.v2ray_path, k.server_id, s.country, "
            "k.tariff_id, k.email, COALESCE(k.traffic_limit_mb, 0), COALESCE(k.traffic_usage_bytes, 0), "
            "k.traffic_over_limit_at, COALESCE(k.traffic_over_limit_notified, 0) "
            "FROM v2ray_keys k JOIN servers s ON k.server_id = s.id "
            "WHERE k.user_id = ? AND k.expiry_at > ? ORDER BY k.expiry_at DESC LIMIT 1",
            (user_id, grace_threshold),
        )
        existing_key = cursor.fetchone()
        if existing_key:
            # Проверяем, отличается ли запрошенная страна от текущей
            current_country = existing_key[6]  # s.country
            
            if country and country != current_country:
                # Запрошена другая страна - запускаем логику смены страны с продлением
                logging.info(f"User {user_id} requested different country for V2Ray: current={current_country}, requested={country}. Running country change logic.")
                
                # Формируем key_data для функции смены страны
                key_data = {
                    'id': existing_key[0],
                    'expiry_at': existing_key[1],
                    'v2ray_uuid': existing_key[2],
                    'domain': existing_key[3],
                    'v2ray_path': existing_key[4],
                    'country': current_country,
                    'server_id': existing_key[5],
                    'tariff_id': existing_key[7] or tariff['id'],
                    'email': existing_key[8] or email,
                    'protocol': protocol,
                    'type': 'v2ray',
                    'traffic_limit_mb': existing_key[9] if len(existing_key) > 9 else 0,
                    'traffic_usage_bytes': existing_key[10] if len(existing_key) > 10 else 0,
                    'traffic_over_limit_at': existing_key[11] if len(existing_key) > 11 else None,
                    'traffic_over_limit_notified': existing_key[12] if len(existing_key) > 12 else 0,
                }
                
                # Вызываем функцию смены страны с продлением
                success = await change_country_and_extend(cursor, message, user_id, key_data, country, tariff['duration_sec'], email, tariff, reset_usage=for_renewal)
                success = await change_country_and_extend(cursor, message, user_id, key_data, country, tariff['duration_sec'], email, tariff, reset_usage=for_renewal)
                
                if success:
                    user_states.pop(user_id, None)
                    return
                else:
                    # Если не удалось сменить страну, создаем новый ключ
                    logging.warning(f"Failed to change country for V2Ray key {existing_key[0]}, creating new key for user {user_id}")
                    # Продолжаем выполнение для создания нового ключа
            else:
                # Та же страна или страна не указана - продлеваем как обычно
                if extend_existing_key_with_fallback is None:
                    logging.error("extend_existing_key_with_fallback is None, cannot extend V2Ray key")
                    # Продолжаем создание нового ключа
                    success = False
                else:
                    success = await extend_existing_key_with_fallback(cursor, existing_key, tariff['duration_sec'], email, tariff['id'], protocol)
            
                if success:
                    # Получаем обновленную информацию о ключе
                    cursor.execute("SELECT k.v2ray_uuid, s.domain, s.v2ray_path, s.api_url, s.api_key, k.email FROM v2ray_keys k JOIN servers s ON k.server_id = s.id WHERE k.id = ?", (existing_key[0],))
                    updated_key = cursor.fetchone()
                    
                    if updated_key:
                        v2ray_uuid, domain, path, api_url, api_key, key_email = updated_key
                        # Получаем реальную конфигурацию с сервера (как в "мои ключи")
                        config = None
                        protocol_client = None
                        try:
                            if api_url and api_key:
                                server_config = {'api_url': api_url, 'api_key': api_key}
                                protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                                config = await protocol_client.get_user_config(v2ray_uuid, {
                                    'domain': domain,
                                    'port': 443,
                                    'path': path or '/v2ray',
                                    'email': key_email or email or f"user_{user_id}@veilbot.com"
                                })
                                if for_renewal:
                                    try:
                                        await protocol_client.reset_key_usage(v2ray_uuid)
                                    except Exception as reset_error:
                                        logging.error(f"Error resetting V2Ray usage for {v2ray_uuid}: {reset_error}")
                            else:
                                # Fallback к хардкодной конфигурации если нет данных сервера
                                config = f"vless://{v2ray_uuid}@{domain}:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email or 'VeilBot-V2Ray'}"
                        except Exception as e:
                            logging.error(f"Error getting V2Ray config for {v2ray_uuid} during extension: {e}")
                            # Fallback к хардкодной конфигурации при ошибке
                            config = f"vless://{v2ray_uuid}@{domain}:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email or 'VeilBot-V2Ray'}"
                        finally:
                            if protocol_client:
                                try:
                                    await protocol_client.close()
                                except Exception as close_error:
                                    logging.warning(f"Error closing V2Ray protocol client after renewal: {close_error}")
                        if for_renewal:
                            try:
                                cursor.execute(
                                    """
                                    UPDATE v2ray_keys
                                    SET traffic_usage_bytes = 0,
                                        traffic_over_limit_at = NULL,
                                        traffic_over_limit_notified = 0
                                    WHERE id = ?
                                    """,
                                    (existing_key[0],),
                                )
                            except Exception as reset_db_error:
                                logging.error(f"Failed to reset traffic counters in DB for key {existing_key[0]}: {reset_db_error}")
                    else:
                        # Fallback к старой конфигурации
                        v2ray_uuid = existing_key[2]
                        domain = existing_key[3]
                        path = existing_key[4] or '/v2ray'
                        config = f"vless://{v2ray_uuid}@{domain}:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email or 'VeilBot-V2Ray'}"
                        if for_renewal:
                            try:
                                cursor.execute(
                                    """
                                    UPDATE v2ray_keys
                                    SET traffic_usage_bytes = 0,
                                        traffic_over_limit_at = NULL,
                                        traffic_over_limit_notified = 0
                                    WHERE id = ?
                                    """,
                                    (existing_key[0],),
                                )
                            except Exception as reset_db_error:
                                logging.error(f"Failed to reset V2Ray usage flags in DB for key {existing_key[0]}: {reset_db_error}")
                    
                    # Очищаем состояние пользователя
                    user_states.pop(user_id, None)
                    
                    # Проверяем, был ли ключ истекшим
                    was_expired = existing_key[1] <= now
                    if was_expired:
                        msg_text = f"✅ Ваш истекший ключ восстановлен и продлён на {format_duration(tariff['duration_sec'])}!\n\n{format_key_message_unified(config, protocol, tariff)}"
                    else:
                        msg_text = f"Ваш ключ продлён на {format_duration(tariff['duration_sec'])}!\n\n{format_key_message_unified(config, protocol, tariff)}"
                    
                    # Отправляем сообщение пользователю
                    notification_sent = False
                    if message:
                        try:
                            await message.answer(msg_text, reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                            notification_sent = True
                        except Exception as e:
                            logging.error(f"Failed to send V2Ray renewal notification via message.answer to user {user_id}: {e}")
                            # Пробуем через safe_send_message как fallback
                            result = await safe_send_message(bot, user_id, msg_text, reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                            notification_sent = result is not None
                    else:
                        # Если message=None (например, из webhook), отправляем напрямую через bot
                        result = await safe_send_message(bot, user_id, msg_text, reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                        notification_sent = result is not None
                    
                    if notification_sent:
                        logging.info(f"V2Ray renewal notification sent successfully to user {user_id}")
                    else:
                        logging.warning(f"Failed to send V2Ray renewal notification to user {user_id}")
                    
                    return
                else:
                    # Если не удалось продлить, создаем новый ключ
                    logging.warning(f"Failed to extend V2Ray key {existing_key[0]}, creating new key for user {user_id}")
                    # Продолжаем выполнение для создания нового ключа
    
    # НОВАЯ ЛОГИКА: Если нет ключа запрошенного протокола, проверяем противоположный протокол
    # Это важно: проверяем только если НЕ нашли ключ запрошенного протокола выше
    if protocol == "outline":
        # Проверяем, есть ли V2Ray ключ
        _execute_with_limit_fallback(
            cursor,
            "SELECT k.id, k.expiry_at, k.v2ray_uuid, s.domain, s.v2ray_path, k.server_id, s.country, "
            "k.tariff_id, k.email, COALESCE(k.traffic_limit_mb, 0), COALESCE(k.traffic_usage_bytes, 0), "
            "k.traffic_over_limit_at, COALESCE(k.traffic_over_limit_notified, 0) "
            "FROM v2ray_keys k JOIN servers s ON k.server_id = s.id "
            "WHERE k.user_id = ? AND k.expiry_at > ? ORDER BY k.expiry_at DESC LIMIT 1",
            "SELECT k.id, k.expiry_at, k.v2ray_uuid, s.domain, s.v2ray_path, k.server_id, s.country, "
            "k.tariff_id, k.email, 0 AS traffic_limit_mb, 0 AS traffic_usage_bytes, "
            "NULL AS traffic_over_limit_at, 0 AS traffic_over_limit_notified "
            "FROM v2ray_keys k JOIN servers s ON k.server_id = s.id "
            "WHERE k.user_id = ? AND k.expiry_at > ? ORDER BY k.expiry_at DESC LIMIT 1",
            (user_id, grace_threshold),
        )
        opposite_key = cursor.fetchone()
        
        if opposite_key:
            # Нашли V2Ray ключ, хотя покупается Outline
            logging.info(f"User {user_id}: found V2Ray key while purchasing Outline. Switching protocol.")
            
            old_key_data = {
                'id': opposite_key[0],
                'expiry_at': opposite_key[1],
                'v2ray_uuid': opposite_key[2],
                'domain': opposite_key[3],
                'v2ray_path': opposite_key[4],
                'server_id': opposite_key[5],
                'country': opposite_key[6],
                'tariff_id': opposite_key[7],
                'email': opposite_key[8],
                'protocol': 'v2ray',
                'type': 'v2ray',
                'traffic_limit_mb': opposite_key[9] if len(opposite_key) > 9 else 0,
                'traffic_usage_bytes': opposite_key[10] if len(opposite_key) > 10 else 0,
                'traffic_over_limit_at': opposite_key[11] if len(opposite_key) > 11 else None,
                'traffic_over_limit_notified': opposite_key[12] if len(opposite_key) > 12 else 0,
            }
            
            # Вызываем функцию смены протокола
            if switch_protocol_and_extend is None:
                logging.error("switch_protocol_and_extend is None, cannot switch protocol")
                success = False
            else:
                success = await switch_protocol_and_extend(cursor, message, user_id, old_key_data, protocol, country, tariff['duration_sec'], email, tariff)
            
            if success:
                user_states.pop(user_id, None)
                return
            else:
                logging.warning(f"Failed to switch protocol from v2ray to outline for user {user_id}, creating new key")
                # Продолжаем создание нового ключа
    
    else:  # protocol == "v2ray"
        # Проверяем, есть ли Outline ключ
        _execute_with_limit_fallback(
            cursor,
            "SELECT k.id, k.expiry_at, k.access_url, s.country, k.server_id, k.key_id, k.tariff_id, k.email FROM keys k JOIN servers s ON k.server_id = s.id WHERE k.user_id = ? AND k.expiry_at > ? ORDER BY k.expiry_at DESC LIMIT 1",
            "SELECT k.id, k.expiry_at, k.access_url, s.country, k.server_id, k.key_id, k.tariff_id, k.email, 0 AS traffic_limit_mb "
            "FROM keys k JOIN servers s ON k.server_id = s.id "
            "WHERE k.user_id = ? AND k.expiry_at > ? ORDER BY k.expiry_at DESC LIMIT 1",
            (user_id, grace_threshold),
        )
        opposite_key = cursor.fetchone()
        
        if opposite_key:
            # Нашли Outline ключ, хотя покупается V2Ray
            logging.info(f"User {user_id}: found Outline key while purchasing V2Ray. Switching protocol.")
            
            old_key_data = {
                'id': opposite_key[0],
                'expiry_at': opposite_key[1],
                'access_url': opposite_key[2],
                'country': opposite_key[3],
                'server_id': opposite_key[4],
                'key_id': opposite_key[5],
                'tariff_id': opposite_key[6],
                'email': opposite_key[7],
                'protocol': 'outline',
                'type': 'outline'
            }
            
            # Вызываем функцию смены протокола
            success = await switch_protocol_and_extend(cursor, message, user_id, old_key_data, protocol, country, tariff['duration_sec'], email, tariff)
            
            if success:
                user_states.pop(user_id, None)
                return
            else:
                logging.warning(f"Failed to switch protocol from outline to v2ray for user {user_id}, creating new key")
                # Продолжаем создание нового ключа
    
    # Если нет активного ключа — проверяем историю и спрашиваем про страну
    if country is None:
        # Ищем последний сервер пользователя (даже если ключ уже удалён)
        last_country = None
        if protocol == "outline":
            cursor.execute("""
                SELECT s.country 
                FROM keys k 
                JOIN servers s ON k.server_id = s.id 
                WHERE k.user_id = ? 
                ORDER BY k.created_at DESC 
                LIMIT 1
            """, (user_id,))
        else:  # v2ray
            cursor.execute("""
                SELECT s.country 
                FROM v2ray_keys k 
                JOIN servers s ON k.server_id = s.id 
                WHERE k.user_id = ? 
                ORDER BY k.created_at DESC 
                LIMIT 1
            """, (user_id,))
        
        country_row = cursor.fetchone()
        if country_row:
            last_country = country_row[0]
            
        # Если у пользователя была история - спрашиваем про выбор страны
        if last_country:
            # Сохраняем состояние для продолжения после выбора страны
            user_states[user_id] = {
                'state': 'reactivation_country_selection',
                'tariff': tariff,
                'email': email,
                'protocol': protocol,
                'last_country': last_country
            }
            
            # Получаем доступные страны для протокола
            countries = get_countries_by_protocol(protocol)
            
            # Создаем клавиатуру с выбором стран
            keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
            
            # Добавляем кнопку для выбора прежней страны
            if last_country in countries:
                keyboard.add(KeyboardButton(f"🔄 {last_country} (как раньше)"))
            
            # Добавляем остальные страны
            for country_name in countries:
                if country_name != last_country:
                    keyboard.add(KeyboardButton(country_name))
            
            keyboard.add(KeyboardButton("🔙 Отмена"))
            
            if message:
                await message.answer(
                    f"⚠️ Ваш предыдущий ключ истёк более 24 часов назад и был удалён.\n\n"
                    f"Последний сервер был в стране: **{last_country}**\n\n"
                    f"Выберите страну для нового ключа {PROTOCOLS[protocol]['name']}:",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            else:
                # Если message=None (например, из webhook), отправляем напрямую через bot
                await safe_send_message(
                    bot,
                    user_id,
                    f"⚠️ Ваш предыдущий ключ истёк более 24 часов назад и был удалён.\n\n"
                    f"Последний сервер был в стране: **{last_country}**\n\n"
                    f"Выберите страну для нового ключа {PROTOCOLS[protocol]['name']}:",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            return
    
    # Если нет активного ключа — создаём новый
    server = select_available_server_by_protocol(cursor, country, protocol, for_renewal=for_renewal)
    if not server:
        if message:
            await message.answer(f"Нет доступных серверов {PROTOCOLS[protocol]['name']} в выбранной стране.", reply_markup=main_menu)
        else:
            await safe_send_message(bot, user_id, f"Нет доступных серверов {PROTOCOLS[protocol]['name']} в выбранной стране.", reply_markup=main_menu)
        return
    
    try:
        # Отправляем сообщение о начале создания ключа
        loading_msg = None
        if message:
            loading_msg = await message.answer(
                f"🔄 Создаю ключ {PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}...\n"
                f"Пожалуйста, подождите.",
                reply_markup=None
            )
        
        # Создаем протокол-клиент с lazy loading
        server_config = {
            'api_url': server[2],
            'cert_sha256': server[3],
            'api_key': server[5],
            'domain': server[4],
            'path': server[6]
        }
        
        # Ленивая инициализация VPN протоколов
        global VPN_PROTOCOLS_AVAILABLE
        if VPN_PROTOCOLS_AVAILABLE is None:
            try:
                vpn_service = get_vpn_service()
                VPN_PROTOCOLS_AVAILABLE = vpn_service is not None
                if VPN_PROTOCOLS_AVAILABLE:
                    logging.info("VPN протоколы инициализированы (lazy loading)")
                else:
                    logging.warning("VPN протоколы недоступны")
            except Exception as e:
                VPN_PROTOCOLS_AVAILABLE = False
                logging.warning(f"Ошибка инициализации VPN протоколов: {e}")
        
        if VPN_PROTOCOLS_AVAILABLE:
            protocol_client = ProtocolFactory.create_protocol(protocol, server_config)
        else:
            raise Exception("VPN протоколы недоступны")
        
        # Создаем пользователя на сервере (ВАЖНО: делаем это до сохранения в БД)
        user_data = await protocol_client.create_user(email or f"user_{user_id}@veilbot.com")
        
        # Валидация: проверяем, что пользователь действительно создан
        if not user_data or not user_data.get('uuid' if protocol == 'v2ray' else 'id'):
            raise Exception(f"Failed to create {protocol} user - invalid response from server")
        
        # Сохраняем в соответствующую таблицу
        expiry = now + tariff['duration_sec']
        
        if protocol == 'outline':
            _insert_with_schema(
                cursor,
                "keys",
                {
                    "server_id": server[0],
                    "user_id": user_id,
                    "access_url": user_data['accessUrl'],
                    "expiry_at": expiry,
                    "traffic_limit_mb": traffic_limit_mb,
                    "notified": 0,
                    "key_id": user_data['id'],
                    "created_at": now,
                    "email": email,
                    "tariff_id": tariff['id'],
                    "protocol": protocol,
                },
            )
            
            config = user_data['accessUrl']
            
            # Логирование создания Outline ключа
            try:
                security_logger = get_security_logger()
                if security_logger:
                    security_logger.log_key_creation(
                        user_id=user_id,
                        key_id=user_data['id'],
                        protocol=protocol,
                        server_id=server[0],
                        tariff_id=tariff['id'],
                        ip_address=getattr(message, 'from_user', {}).get('id', None) if message else None,
                        user_agent="Telegram Bot"
                    )
            except Exception as e:
                logging.error(f"Error logging key creation: {e}")
                
        else:  # v2ray
            # ИСПРАВЛЕНИЕ: Используем client_config из ответа create_user, если он есть
            config = None
            if user_data.get('client_config'):
                config = user_data['client_config']
                # Извлекаем VLESS URL, если конфигурация многострочная
                if 'vless://' in config:
                    lines = config.split('\n')
                    for line in lines:
                        if line.strip().startswith('vless://'):
                            config = line.strip()
                            break
                logging.info(f"Using client_config from create_user response for new key")
            else:
                # Если client_config нет в ответе, запрашиваем через get_user_config
                logging.debug(f"client_config not in create_user response, fetching via get_user_config")
                config = await protocol_client.get_user_config(user_data['uuid'], {
                    'domain': server[4],
                    'port': 443,
                    'path': server[6] or '/v2ray',
                    'email': email or f"user_{user_id}@veilbot.com"
                })
                # Извлекаем VLESS URL, если конфигурация многострочная
                if 'vless://' in config:
                    lines = config.split('\n')
                    for line in lines:
                        if line.strip().startswith('vless://'):
                            config = line.strip()
                            break
            
            # ИСПРАВЛЕНИЕ: Проверяем и создаем пользователя в таблице users, если его нет
            # Это необходимо для соблюдения foreign key constraint
            from app.infra.foreign_keys import safe_foreign_keys_off
            
            # Проверяем существование пользователя и создаем его, если нужно
            # Все операции выполняем в одном контексте с отключенными foreign keys
            with safe_foreign_keys_off(cursor):
                cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
                if not cursor.fetchone():
                    logging.info(f"User {user_id} not found in users table, creating...")
                    # Получаем данные пользователя из message, если доступны
                    username = None
                    first_name = None
                    last_name = None
                    if message and hasattr(message, 'from_user') and message.from_user:
                        username = message.from_user.username
                        first_name = message.from_user.first_name
                        last_name = message.from_user.last_name
                    
                    cursor.execute("""
                        INSERT OR REPLACE INTO users 
                        (user_id, username, first_name, last_name, created_at, last_active_at, blocked)
                        VALUES (?, ?, ?, ?, ?, ?, 0)
                    """, (user_id, username, first_name, last_name, now, now))
                    logging.info(f"User {user_id} created in users table")
                
                # Сохраняем ключ с конфигурацией в БД
                # Выполняем в том же контексте с отключенными foreign keys
                values_map = {
                    "server_id": server[0],
                    "user_id": user_id,
                    "v2ray_uuid": user_data['uuid'],
                    "email": email or f"user_{user_id}@veilbot.com",
                    "created_at": now,
                    "expiry_at": expiry,
                    "tariff_id": tariff['id'],
                    "client_config": config,
                    "traffic_limit_mb": traffic_limit_mb,
                    "traffic_usage_bytes": 0,
                    "traffic_over_limit_at": None,
                    "traffic_over_limit_notified": 0,
                }
                _insert_with_schema(cursor, "v2ray_keys", values_map)
                new_key_id = cursor.lastrowid
                if (traffic_limit_mb or 0) <= 0 and tariff.get('id'):
                    try:
                        cursor.execute(
                            """
                            UPDATE v2ray_keys
                            SET traffic_limit_mb = COALESCE(
                                (SELECT traffic_limit_mb FROM tariffs WHERE id = ?),
                                0
                            )
                            WHERE id = ?
                            """,
                            (tariff['id'], new_key_id),
                        )
                        cursor.execute(
                            """
                            SELECT traffic_limit_mb FROM v2ray_keys WHERE id = ?
                            """,
                            (new_key_id,),
                        )
                        row_limit = cursor.fetchone()
                        if row_limit and (row_limit[0] or 0) > 0:
                            traffic_limit_mb = row_limit[0]
                    except Exception as e:  # noqa: BLE001
                        logging.warning("Failed to backfill traffic_limit_mb for new v2ray key %s: %s", new_key_id, e)
            
            # Логирование создания V2Ray ключа
            try:
                security_logger = get_security_logger()
                if security_logger:
                    security_logger.log_key_creation(
                        user_id=user_id,
                        key_id=user_data['uuid'],
                        protocol=protocol,
                        server_id=server[0],
                        tariff_id=tariff['id'],
                        ip_address=getattr(message, 'from_user', {}).get('id', None) if message else None,
                        user_agent="Telegram Bot"
                    )
            except Exception as e:
                logging.error(f"Error logging key creation: {e}")
        
        # Удаляем сообщение о загрузке
        try:
            await loading_msg.delete()
        except:
            pass
        
        # Если это бесплатный тариф, записываем использование
        if tariff['price_rub'] == 0:
            record_free_key_usage(cursor, user_id, protocol, country)
        
        # Очищаем состояние пользователя
        user_states.pop(user_id, None)
        
        # Отправляем пользователю
        # Получаем актуальное главное меню (на случай, если оно изменилось)
        current_main_menu = get_main_menu()
        
        key_message = format_key_message_unified(config, protocol, tariff)
        notification_sent = False
        
        if message:
            try:
                await message.answer(
                    key_message,
                    reply_markup=current_main_menu,
                    disable_web_page_preview=True,
                    parse_mode="Markdown"
                )
                notification_sent = True
            except Exception as e:
                logging.error(f"Failed to send key creation notification via message.answer to user {user_id}: {e}")
                # Пробуем через safe_send_message как fallback
                result = await safe_send_message(
                    bot,
                    user_id,
                    key_message,
                    reply_markup=current_main_menu,
                    disable_web_page_preview=True,
                    parse_mode="Markdown"
                )
                notification_sent = result is not None
        else:
            # Если message=None (например, из webhook), отправляем напрямую через bot
            result = await safe_send_message(
                bot,
                user_id,
                key_message,
                reply_markup=current_main_menu,
                disable_web_page_preview=True,
                parse_mode="Markdown"
            )
            notification_sent = result is not None
        
        if notification_sent:
            logging.info(f"Key creation notification sent successfully to user {user_id} for {protocol} key")
        else:
            logging.warning(f"Failed to send key creation notification to user {user_id} for {protocol} key")
        
        # Уведомление админу
        admin_msg = (
            f"🔑 *Покупка ключа {PROTOCOLS[protocol]['icon']}*\n"
            f"Пользователь: `{user_id}`\n"
            f"Протокол: *{PROTOCOLS[protocol]['name']}*\n"
            f"Тариф: *{tariff.get('name', 'Неизвестно')}*\n"
            f"Ключ: `{config}`\n"
        )
        if email:
            admin_msg += f"Email: `{email}`\n"
        await safe_send_message(
            bot,
            ADMIN_ID,
            admin_msg,
            disable_web_page_preview=True,
            parse_mode="Markdown",
            mark_blocked=False,
        )
            
    except Exception as e:
        # При ошибке пытаемся удалить созданного пользователя с сервера
        logging.error(f"Failed to create {protocol} key: {e}")
        
        # Логирование ошибки создания ключа
        try:
            security_logger = get_security_logger()
            if security_logger:
                security_logger.log_suspicious_activity(
                    user_id=user_id,
                    activity_type="key_creation_failed",
                    details=f"Failed to create {protocol} key: {str(e)}",
                    ip_address=getattr(message, 'from_user', {}).get('id', None) if message else None,
                    user_agent="Telegram Bot"
                )
        except Exception as log_e:
            logging.error(f"Error logging key creation failure: {log_e}")
        
        try:
            if 'user_data' in locals() and user_data:
                if protocol == 'v2ray' and user_data.get('uuid'):
                    await protocol_client.delete_user(user_data['uuid'])
                    logging.info(f"Deleted V2Ray user {user_data['uuid']} from server due to error")
                elif protocol == 'outline' and user_data.get('id'):
                    # Для Outline используем существующую функцию
                    from outline import delete_key
                    delete_key(server[2], server[3], user_data['id'])
                    logging.info(f"Deleted Outline key {user_data['id']} from server due to error")
        except Exception as cleanup_error:
            logging.error(f"Failed to cleanup {protocol} user after error: {cleanup_error}")
        
        # Удаляем сообщение о загрузке
        try:
            await loading_msg.delete()
        except:
            pass
        
        # Очищаем состояние пользователя
        user_states.pop(user_id, None)
        
        # Отправляем сообщение об ошибке пользователю
        if message:
            await message.answer(
                f"❌ Ошибка при создании ключа {PROTOCOLS[protocol]['icon']}.\n"
                f"Попробуйте позже или обратитесь к администратору.",
                reply_markup=main_menu
            )
        else:
            await safe_send_message(
                bot,
                user_id,
                f"❌ Ошибка при создании ключа {PROTOCOLS[protocol]['icon']}.\n"
                f"Попробуйте позже или обратитесь к администратору.",
                reply_markup=main_menu
            )
        return


async def wait_for_payment_with_protocol(
    message: Optional[types.Message], 
    payment_id: str, 
    server: Tuple, 
    user_id: int, 
    tariff: Dict[str, Any], 
    country: Optional[str] = None, 
    protocol: str = "outline", 
    for_renewal: bool = False,
    extend_existing_key: Optional[Callable] = None
) -> None:
    """
    Ожидание платежа с поддержкой протоколов
    
    Args:
        message: Сообщение от пользователя
        payment_id: ID платежа
        server: Данные сервера (tuple)
        user_id: ID пользователя
        tariff: Словарь с данными тарифа
        country: Страна сервера
        protocol: Протокол (outline или v2ray)
        for_renewal: Если True, при выборе сервера не проверяется available_for_purchase (только active)
        extend_existing_key: Функция продления ключа (импортируется из bot.py)
    """
    # Импортируем необходимые зависимости (lazy import для избежания циклических зависимостей)
    import importlib
    bot_module = importlib.import_module('bot')
    # PAYMENT_MODULE_AVAILABLE может быть None при первом импорте, используем getattr
    PAYMENT_MODULE_AVAILABLE = getattr(bot_module, 'PAYMENT_MODULE_AVAILABLE', None)
    # Если None, проверяем доступность платежного сервиса
    if PAYMENT_MODULE_AVAILABLE is None:
        try:
            from memory_optimizer import get_payment_service
            payment_service = get_payment_service()
            PAYMENT_MODULE_AVAILABLE = payment_service is not None
        except Exception:
            PAYMENT_MODULE_AVAILABLE = False
    
    # Получаем user_states и main_menu с fallback
    user_states = getattr(bot_module, 'user_states', {})
    main_menu = getattr(bot_module, 'main_menu', None)
    if main_menu is None:
        # Если main_menu не найден, получаем через функцию
        try:
            from bot.keyboards import get_main_menu
            main_menu = get_main_menu()
        except Exception:
            # Если и это не работает, создаем пустое меню
            from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
            main_menu = ReplyKeyboardMarkup(resize_keyboard=True)
            main_menu.add(KeyboardButton("🔑 Мои ключи"))
            main_menu.add(KeyboardButton("💳 Купить доступ"))
    
    from memory_optimizer import get_payment_service
    bot = get_bot_instance()
    
    if extend_existing_key is None:
        extend_existing_key = getattr(bot_module, 'extend_existing_key', None)
    
    # Используем новый платежный модуль
    if PAYMENT_MODULE_AVAILABLE:
        try:
            from payments.adapters.legacy_adapter import wait_for_payment_with_protocol_legacy
            from memory_optimizer import get_payment_service
            
            # Проверяем, что платежный сервис доступен
            payment_service = get_payment_service()
            if not payment_service:
                logging.error("Payment service is not available")
                if message:
                    await message.answer("Платежная система временно недоступна. Обратитесь в поддержку.", reply_markup=main_menu)
                return
            
            # Проверяем статус платежа в БД ПЕРЕД ожиданием
            # Если платеж уже оплачен, сразу создаем подписку/ключ
            payment_already_paid = False
            with get_db_cursor() as check_cursor:
                check_cursor.execute("SELECT status FROM payments WHERE payment_id = ?", (payment_id,))
                status_row = check_cursor.fetchone()
                if status_row:
                    payment_status = (status_row[0] or "").lower()
                    if payment_status == "paid":
                        payment_already_paid = True
                        logging.info(f"Payment {payment_id} already paid in DB, skipping wait and creating subscription/key immediately")
            
            if payment_already_paid:
                # Платеж уже оплачен, сразу создаем подписку/ключ
                success = True
            else:
                # Ожидаем оплату платежа
                success = await wait_for_payment_with_protocol_legacy(message, payment_id, protocol)
            
            if success:
                logging.debug(f"New payment module confirmed payment success: {payment_id}")
                
                # Если есть сообщение с кнопкой "Отмена", редактируем его, убирая кнопки
                # Проверяем, что message - это Message объект (не CallbackQuery)
                if message and hasattr(message, 'edit_text'):
                    try:
                        await message.edit_text(
                            "✅ Платеж успешно обработан! Ваш ключ отправлен отдельным сообщением.",
                            reply_markup=None
                        )
                    except Exception as e:
                        logging.debug(f"Could not edit payment message: {e}")
                
                # Создаем ключ после успешного платежа
                with get_db_cursor(commit=True) as cursor:
                    cursor.execute(
                        "SELECT status, user_id, email, tariff_id, country, protocol FROM payments WHERE payment_id = ?",
                        (payment_id,),
                    )
                    payment_data = cursor.fetchone()
                    
                    if payment_data:
                        payment_status = (payment_data[0] or "").lower()
                        if payment_status == "completed":
                            logging.info(f"Payment {payment_id} already completed, skipping key issuance")
                            return
                        
                        if payment_status != "paid":
                            cursor.execute("UPDATE payments SET status = 'paid' WHERE payment_id = ?", (payment_id,))
                        
                        payment_user_id = payment_data[1]
                        email = payment_data[2]
                        payment_tariff_id = payment_data[3]
                        payment_country = payment_data[4]
                        payment_protocol = payment_data[5]
                        
                        # Используем user_id из платежа, если он не был передан или отличается
                        if not user_id or user_id != payment_user_id:
                            user_id = payment_user_id
                            logging.info(f"Using user_id from payment: {user_id}")
                        
                        # Если tariff не передан, получаем его из платежа
                        if not tariff and payment_tariff_id:
                            cursor.execute("SELECT id, name, duration_sec, price_rub, traffic_limit_mb FROM tariffs WHERE id = ?", (payment_tariff_id,))
                            tariff_row = cursor.fetchone()
                            if tariff_row:
                                tariff = {
                                    'id': tariff_row[0],
                                    'name': tariff_row[1],
                                    'duration_sec': tariff_row[2],
                                    'price_rub': tariff_row[3],
                                    'traffic_limit_mb': tariff_row[4],
                                }
                        
                        # Используем данные из платежа, если они не переданы
                        if not country and payment_country:
                            country = payment_country
                        if not protocol and payment_protocol:
                            protocol = payment_protocol
                    else:
                        email = None
                        logging.error(f"Payment {payment_id} not found in database")
                    
                    if not user_id:
                        logging.error(f"Could not get user_id for payment {payment_id}")
                        if message:
                            await message.answer("Ошибка: не удалось получить данные пользователя. Обратитесь в поддержку.", reply_markup=main_menu)
                        return
                    
                    if not tariff:
                        logging.error(f"Could not get tariff for payment {payment_id}")
                        if message:
                            await message.answer("Ошибка: не удалось получить данные тарифа. Обратитесь в поддержку.", reply_markup=main_menu)
                        return
                    
                    # Проверяем метаданные платежа для определения типа ключа
                    cursor.execute("SELECT metadata FROM payments WHERE payment_id = ?", (payment_id,))
                    metadata_row = cursor.fetchone()
                    payment_metadata = {}
                    if metadata_row and metadata_row[0]:
                        import json
                        try:
                            payment_metadata = json.loads(metadata_row[0]) if isinstance(metadata_row[0], str) else metadata_row[0]
                        except (json.JSONDecodeError, TypeError):
                            payment_metadata = {}
                    
                    key_type = payment_metadata.get('key_type') if payment_metadata else None
                    is_subscription = key_type == 'subscription' and protocol == 'v2ray'
                    
                    if is_subscription:
                        # Используем единый сервис для обработки подписки
                        # Это предотвращает дублирование уведомлений и обеспечивает единообразную обработку
                        from payments.services.subscription_purchase_service import SubscriptionPurchaseService
                        
                        # Проверяем, не обработан ли уже платеж через SubscriptionPurchaseService
                        # (может быть обработан через webhook или фоновую задачу)
                        cursor.execute("SELECT status FROM payments WHERE payment_id = ?", (payment_id,))
                        status_row = cursor.fetchone()
                        if status_row and (status_row[0] or "").lower() == "completed":
                            logging.info(f"Payment {payment_id} already completed, skipping subscription processing in wait_for_payment_with_protocol")
                            return
                        
                        # Используем SubscriptionPurchaseService для обработки подписки
                        # Он сам проверит статус платежа и защитит от повторной обработки
                        try:
                            subscription_service = SubscriptionPurchaseService()
                            success, error_msg = await subscription_service.process_subscription_purchase(payment_id)
                            
                            if success:
                                logging.info(
                                    f"Subscription purchase processed successfully via wait_for_payment_with_protocol "
                                    f"for payment {payment_id}, user {user_id}"
                                )
                                # Платеж уже помечен как completed в SubscriptionPurchaseService
                                # Уведомление уже отправлено, не отправляем повторно
                            else:
                                # КРИТИЧНО: Проверяем, действительно ли подписка не создана перед отправкой ошибки
                                # Если подписка уже создана, но уведомление не отправлено - это не критическая ошибка,
                                # фоновая задача повторит отправку уведомления
                                subscription_exists = False
                                with get_db_cursor() as check_cursor:
                                    check_cursor.execute(
                                        """
                                        SELECT id FROM subscriptions 
                                        WHERE user_id = ? AND is_active = 1 
                                        ORDER BY created_at DESC LIMIT 1
                                        """,
                                        (user_id,)
                                    )
                                    if check_cursor.fetchone():
                                        subscription_exists = True
                                
                                if subscription_exists:
                                    logging.warning(
                                        f"Subscription purchase failed via wait_for_payment_with_protocol "
                                        f"for payment {payment_id}, user {user_id}: {error_msg}, "
                                        f"but subscription exists. Will retry notification via background task."
                                    )
                                    # Подписка создана, но уведомление не отправлено - фоновая задача повторит
                                    # НЕ отправляем сообщение об ошибке пользователю
                                else:
                                    logging.error(
                                        f"Failed to process subscription purchase via wait_for_payment_with_protocol "
                                        f"for payment {payment_id}, user {user_id}: {error_msg}"
                                    )
                                    if message:
                                        try:
                                            await message.answer(
                                                "❌ Произошла ошибка при создании подписки. Обратитесь в поддержку.",
                                                reply_markup=main_menu
                                            )
                                        except Exception:
                                            pass
                                # НЕ помечаем платеж как completed при ошибке, чтобы повторить попытку
                        except Exception as exc:
                            # КРИТИЧНО: Проверяем, действительно ли подписка не создана перед отправкой ошибки
                            subscription_exists = False
                            try:
                                with get_db_cursor() as check_cursor:
                                    check_cursor.execute(
                                        """
                                        SELECT id FROM subscriptions 
                                        WHERE user_id = ? AND is_active = 1 
                                        ORDER BY created_at DESC LIMIT 1
                                        """,
                                        (user_id,)
                                    )
                                    if check_cursor.fetchone():
                                        subscription_exists = True
                            except Exception:
                                pass
                            
                            if subscription_exists:
                                logging.warning(
                                    f"Exception processing subscription purchase via wait_for_payment_with_protocol "
                                    f"for payment {payment_id}, user {user_id}: {exc}, "
                                    f"but subscription exists. Will retry notification via background task.",
                                    exc_info=True
                                )
                                # Подписка создана, но произошла ошибка - фоновая задача повторит
                                # НЕ отправляем сообщение об ошибке пользователю
                            else:
                                logging.error(
                                    f"Exception processing subscription purchase via wait_for_payment_with_protocol "
                                    f"for payment {payment_id}, user {user_id}: {exc}",
                                    exc_info=True
                                )
                                if message:
                                    try:
                                        await message.answer(
                                            "❌ Произошла ошибка при создании подписки. Обратитесь в поддержку.",
                                            reply_markup=main_menu
                                        )
                                    except Exception:
                                        pass
                            # НЕ помечаем платеж как completed при ошибке, чтобы повторить попытку
                    else:
                        # Логика продления: если for_renewal=True, функция create_new_key_flow_with_protocol
                        # автоматически проверит наличие существующего ключа и продлит его
                        # Если ключа нет, создаст новый (как при обычной покупке)
                        logging.info(f"Creating/extending key for payment {payment_id}, user_id={user_id}, for_renewal={for_renewal}")
                        await create_new_key_flow_with_protocol(cursor, None, user_id, tariff, email, country, protocol, for_renewal=for_renewal)
                        
                        # После успешной выдачи/продления ключа помечаем платеж как закрытый
                        # ИСПРАВЛЕНО: Используем payment_repo для атомарного обновления статуса
                        try:
                            from payments.repositories.payment_repository import PaymentRepository
                            from payments.models.payment import PaymentStatus
                            payment_repo = PaymentRepository()
                            payment = await payment_repo.get_by_payment_id(payment_id)
                            if payment:
                                payment.mark_as_completed()
                                await payment_repo.update(payment)
                                logging.info(f"Payment {payment_id} marked as completed after key creation/renewal")
                            else:
                                # Fallback на старый способ, если payment_repo недоступен
                                cursor.execute("UPDATE payments SET status = 'completed' WHERE payment_id = ?", (payment_id,))
                                logging.info(f"Payment {payment_id} marked as completed (fallback method)")
                        except Exception as e:
                            logging.error(f"Error marking payment {payment_id} as completed: {e}")
                            # Fallback на старый способ при ошибке
                            cursor.execute("UPDATE payments SET status = 'completed' WHERE payment_id = ?", (payment_id,))
                            logging.info(f"Payment {payment_id} marked as completed (fallback after error)")
                    # --- Реферальный бонус ---
                    cursor.execute("SELECT referrer_id, bonus_issued FROM referrals WHERE referred_id = ?", (user_id,))
                    ref_row = cursor.fetchone()
                    bonus_duration = 30 * 24 * 3600  # 1 месяц

                    # Бонус начисляем только за платные тарифы
                    tariff_price = 0
                    if isinstance(tariff, dict):
                        tariff_price = int(tariff.get('price_rub') or 0)

                    cursor.execute(
                        "SELECT 1 FROM payments WHERE user_id = ? AND amount > 0 AND status IN ('paid', 'completed') LIMIT 1",
                        (user_id,),
                    )
                    has_paid_payment = cursor.fetchone() is not None

                    if ref_row and ref_row[0] and not ref_row[1] and has_paid_payment and tariff_price > 0:
                        referrer_id = ref_row[0]
                        # Проверяем, есть ли у пригласившего активный ключ
                        now = int(time.time())
                        cursor.execute("SELECT id, expiry_at FROM keys WHERE user_id = ? AND expiry_at > ? ORDER BY expiry_at DESC LIMIT 1", (referrer_id, now))
                        key = cursor.fetchone()
                        if key and extend_existing_key:
                            try:
                                extend_existing_key(cursor, key, bonus_duration)
                                await safe_send_message(bot, referrer_id, "🎉 Ваш ключ продлён на месяц за приглашённого друга!")
                            except Exception as e:
                                logging.error(f"Error extending referrer key: {e}")
                                # Если не удалось продлить, выдаем новый ключ
                                cursor.execute("SELECT * FROM tariffs WHERE duration_sec >= ? ORDER BY duration_sec ASC LIMIT 1", (bonus_duration,))
                                bonus_tariff = cursor.fetchone()
                                if bonus_tariff:
                                    bonus_tariff_dict = {"id": bonus_tariff[0], "name": bonus_tariff[1], "price_rub": bonus_tariff[4], "duration_sec": bonus_tariff[2]}
                                    await create_new_key_flow_with_protocol(cursor, message, referrer_id, bonus_tariff_dict, None, None, protocol)
                                    await safe_send_message(bot, referrer_id, "🎉 Вам выдан бесплатный месяц за приглашённого друга!")
                        elif key:
                            logging.warning(f"extend_existing_key is None, cannot extend referrer key for user {referrer_id}")
                        else:
                            # Выдаём новый ключ на месяц
                            cursor.execute("SELECT * FROM tariffs WHERE duration_sec >= ? ORDER BY duration_sec ASC LIMIT 1", (bonus_duration,))
                            bonus_tariff = cursor.fetchone()
                            if bonus_tariff:
                                bonus_tariff_dict = {"id": bonus_tariff[0], "name": bonus_tariff[1], "price_rub": bonus_tariff[4], "duration_sec": bonus_tariff[2]}
                                await create_new_key_flow_with_protocol(cursor, message, referrer_id, bonus_tariff_dict, None, None, protocol)
                                await safe_send_message(bot, referrer_id, "🎉 Вам выдан бесплатный месяц за приглашённого друга!")
                        cursor.execute("UPDATE referrals SET bonus_issued = 1 WHERE referred_id = ?", (user_id,))
                return
            else:
                logging.debug(f"New payment module timeout or failed: {payment_id}")
                if message:
                    await message.answer("Время ожидания платежа истекло. Попробуйте создать платеж заново.", reply_markup=main_menu)
                return
                
        except Exception as e:
            logging.warning(f"Ошибка в новом платежном модуле: {e}")
            if message:
                await message.answer("Ошибка при проверке платежа. Обратитесь в поддержку.", reply_markup=main_menu)
            return
    else:
        # Если новый модуль недоступен
        logging.warning("Новый платежный модуль недоступен")
        if message:
            await message.answer("Платежная система временно недоступна.", reply_markup=main_menu)
        return


async def wait_for_crypto_payment(
    message: Optional[types.Message], 
    invoice_id: str, 
    server: Tuple, 
    user_id: int, 
    tariff: Dict[str, Any], 
    country: Optional[str] = None, 
    protocol: str = "outline", 
    for_renewal: bool = False,
    extend_existing_key: Optional[Callable] = None
) -> None:
    """
    Ожидание криптоплатежа через CryptoBot
    
    Args:
        message: Сообщение от пользователя
        invoice_id: ID инвойса
        server: Данные сервера (tuple)
        user_id: ID пользователя
        tariff: Словарь с данными тарифа
        country: Страна сервера
        protocol: Протокол (outline или v2ray)
        for_renewal: Если True, при выборе сервера не проверяется available_for_purchase (только active)
        extend_existing_key: Функция продления ключа (импортируется из bot.py)
    """
    # Импортируем необходимые зависимости (lazy import для избежания циклических зависимостей)
    import importlib
    bot_module = importlib.import_module('bot')
    
    # Получаем user_states и main_menu с fallback
    user_states = getattr(bot_module, 'user_states', {})
    main_menu = getattr(bot_module, 'main_menu', None)
    if main_menu is None:
        # Если main_menu не найден, получаем через функцию
        try:
            from bot.keyboards import get_main_menu
            main_menu = get_main_menu()
        except Exception:
            # Если и это не работает, создаем пустое меню
            from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
            main_menu = ReplyKeyboardMarkup(resize_keyboard=True)
            main_menu.add(KeyboardButton("🔑 Мои ключи"))
            main_menu.add(KeyboardButton("💳 Купить доступ"))
    
    from memory_optimizer import get_payment_service
    bot = get_bot_instance()
    
    if extend_existing_key is None:
        extend_existing_key = getattr(bot_module, 'extend_existing_key', None)
    
    try:
        payment_service = get_payment_service()
        if not payment_service or not payment_service.cryptobot_service:
            logging.error("CryptoBot service not available")
            return
        
        # Проверяем статус инвойса периодически (каждые 10 секунд, максимум 1 час)
        max_checks = 360  # 1 час = 3600 секунд / 10 секунд
        check_interval = 10
        
        for check_num in range(max_checks):
            is_paid = await payment_service.cryptobot_service.is_invoice_paid(int(invoice_id))
            
            if is_paid:
                logging.info(f"CryptoBot payment confirmed: {invoice_id}")
                
                # Если есть сообщение с кнопкой "Отмена", редактируем его, убирая кнопки
                # Проверяем, что message - это Message объект (не CallbackQuery)
                if message and hasattr(message, 'edit_text'):
                    try:
                        await message.edit_text(
                            "✅ Платеж успешно обработан! Ваш ключ отправлен отдельным сообщением.",
                            reply_markup=None
                        )
                    except Exception as e:
                        logging.debug(f"Could not edit crypto payment message: {e}")
                
                # Обновляем статус платежа в БД
                with get_db_cursor(commit=True) as cursor:
                    cursor.execute(
                        "SELECT status, user_id, email, tariff_id, country, protocol FROM payments WHERE payment_id = ?",
                        (str(invoice_id),),
                    )
                    payment_data = cursor.fetchone()
                    
                    if payment_data:
                        payment_status = (payment_data[0] or "").lower()
                        if payment_status == "completed":
                            logging.info(f"Crypto payment {invoice_id} already completed, skipping key issuance")
                            return
                        
                        if payment_status != "paid":
                            cursor.execute("UPDATE payments SET status = 'paid' WHERE payment_id = ?", (str(invoice_id),))
                        
                        payment_user_id = payment_data[1]
                        email = payment_data[2] if payment_data[2] else None
                        payment_tariff_id = payment_data[3]
                        payment_country = payment_data[4]
                        payment_protocol = payment_data[5]
                        
                        # Используем user_id из платежа, если он не был передан или отличается
                        if not user_id or user_id != payment_user_id:
                            user_id = payment_user_id
                            logging.info(f"Using user_id from crypto payment: {user_id}")
                        
                        # Если tariff не передан, получаем его из платежа
                        if not tariff and payment_tariff_id:
                            cursor.execute("SELECT id, name, duration_sec, price_rub, traffic_limit_mb FROM tariffs WHERE id = ?", (payment_tariff_id,))
                            tariff_row = cursor.fetchone()
                            if tariff_row:
                                tariff = {
                                    'id': tariff_row[0],
                                    'name': tariff_row[1],
                                    'duration_sec': tariff_row[2],
                                    'price_rub': tariff_row[3],
                                    'traffic_limit_mb': tariff_row[4],
                                }
                        
                        # Используем данные из платежа, если они не переданы
                        if not country and payment_country:
                            country = payment_country
                        if not protocol and payment_protocol:
                            protocol = payment_protocol
                    else:
                        email = None
                        logging.error(f"Crypto payment {invoice_id} not found in database")
                    
                    if not user_id:
                        logging.error(f"Could not get user_id for crypto payment {invoice_id}")
                        if message:
                            await message.answer("Ошибка: не удалось получить данные пользователя. Обратитесь в поддержку.", reply_markup=main_menu)
                        return
                    
                    if not tariff:
                        logging.error(f"Could not get tariff for crypto payment {invoice_id}")
                        if message:
                            await message.answer("Ошибка: не удалось получить данные тарифа. Обратитесь в поддержку.", reply_markup=main_menu)
                        return
                    
                    # Проверяем метаданные платежа для определения типа ключа
                    cursor.execute("SELECT metadata FROM payments WHERE payment_id = ?", (str(invoice_id),))
                    metadata_row = cursor.fetchone()
                    payment_metadata = {}
                    if metadata_row and metadata_row[0]:
                        import json
                        try:
                            payment_metadata = json.loads(metadata_row[0]) if isinstance(metadata_row[0], str) else metadata_row[0]
                        except (json.JSONDecodeError, TypeError):
                            payment_metadata = {}
                    
                    key_type = payment_metadata.get('key_type') if payment_metadata else None
                    is_subscription = key_type == 'subscription' and protocol == 'v2ray'
                    
                    if is_subscription:
                        # Используем единый сервис для обработки подписки
                        # Это предотвращает дублирование уведомлений и обеспечивает единообразную обработку
                        from payments.services.subscription_purchase_service import SubscriptionPurchaseService
                        
                        # Проверяем, не обработан ли уже платеж через SubscriptionPurchaseService
                        # (может быть обработан через фоновую задачу)
                        cursor.execute("SELECT status FROM payments WHERE payment_id = ?", (str(invoice_id),))
                        status_row = cursor.fetchone()
                        if status_row and (status_row[0] or "").lower() == "completed":
                            logging.info(f"Crypto payment {invoice_id} already completed, skipping subscription processing in wait_for_crypto_payment")
                            return
                        
                        # Используем SubscriptionPurchaseService для обработки подписки
                        # Он сам проверит статус платежа и защитит от повторной обработки
                        try:
                            subscription_service = SubscriptionPurchaseService()
                            success, error_msg = await subscription_service.process_subscription_purchase(str(invoice_id))
                            
                            if success:
                                logging.info(
                                    f"Subscription purchase processed successfully via wait_for_crypto_payment "
                                    f"for crypto payment {invoice_id}, user {user_id}"
                                )
                                # Платеж уже помечен как completed в SubscriptionPurchaseService
                                # Уведомление уже отправлено, не отправляем повторно
                            else:
                                logging.error(
                                    f"Failed to process subscription purchase via wait_for_crypto_payment "
                                    f"for crypto payment {invoice_id}, user {user_id}: {error_msg}"
                                )
                                if message:
                                    try:
                                        await message.answer(
                                            "❌ Произошла ошибка при создании подписки. Обратитесь в поддержку.",
                                            reply_markup=main_menu
                                        )
                                    except Exception:
                                        pass
                                # НЕ помечаем платеж как completed при ошибке, чтобы повторить попытку
                        except Exception as exc:
                            logging.error(
                                f"Exception processing subscription purchase via wait_for_crypto_payment "
                                f"for crypto payment {invoice_id}, user {user_id}: {exc}",
                                exc_info=True
                            )
                            if message:
                                try:
                                    await message.answer(
                                        "❌ Произошла ошибка при создании подписки. Обратитесь в поддержку.",
                                        reply_markup=main_menu
                                    )
                                except Exception:
                                    pass
                            # НЕ помечаем платеж как completed при ошибке, чтобы повторить попытку
                    else:
                        # Логика продления: если for_renewal=True, функция create_new_key_flow_with_protocol
                        # автоматически проверит наличие существующего ключа и продлит его
                        # Если ключа нет, создаст новый (как при обычной покупке)
                        logging.info(f"Creating/extending key for crypto payment {invoice_id}, user_id={user_id}, for_renewal={for_renewal}")
                        await create_new_key_flow_with_protocol(cursor, None, user_id, tariff, email, country, protocol, for_renewal=for_renewal)
                        
                        # После успешной выдачи/продления ключа помечаем платеж как закрытый
                        # Это предотвратит повторную выдачу ключа по этому платежу
                        cursor.execute("UPDATE payments SET status = 'completed' WHERE payment_id = ?", (str(invoice_id),))
                        logging.info(f"Crypto payment {invoice_id} marked as completed after key creation/renewal")
                    
                    # Реферальный бонус (та же логика что и для обычных платежей)
                    cursor.execute("SELECT referrer_id, bonus_issued FROM referrals WHERE referred_id = ?", (user_id,))
                    ref_row = cursor.fetchone()
                    bonus_duration = 30 * 24 * 3600  # 1 месяц
                    if ref_row and ref_row[0] and not ref_row[1]:
                        referrer_id = ref_row[0]
                        now = int(time.time())
                        cursor.execute("SELECT id, expiry_at FROM keys WHERE user_id = ? AND expiry_at > ? ORDER BY expiry_at DESC LIMIT 1", (referrer_id, now))
                        key = cursor.fetchone()
                        if key and extend_existing_key:
                            try:
                                extend_existing_key(cursor, key, bonus_duration)
                                await safe_send_message(bot, referrer_id, "🎉 Ваш ключ продлён на месяц за приглашённого друга!")
                            except Exception as e:
                                logging.error(f"Error extending referrer key: {e}")
                                # Если не удалось продлить, выдаем новый ключ
                                cursor.execute("SELECT * FROM tariffs WHERE duration_sec >= ? ORDER BY duration_sec ASC LIMIT 1", (bonus_duration,))
                                bonus_tariff = cursor.fetchone()
                                if bonus_tariff:
                                    bonus_tariff_dict = {"id": bonus_tariff[0], "name": bonus_tariff[1], "price_rub": bonus_tariff[4], "duration_sec": bonus_tariff[2]}
                                    await create_new_key_flow_with_protocol(cursor, message, referrer_id, bonus_tariff_dict, None, None, protocol)
                                    await safe_send_message(bot, referrer_id, "🎉 Вам выдан бесплатный месяц за приглашённого друга!")
                        elif key:
                            logging.warning(f"extend_existing_key is None, cannot extend referrer key for user {referrer_id}")
                        else:
                            cursor.execute("SELECT * FROM tariffs WHERE duration_sec >= ? ORDER BY duration_sec ASC LIMIT 1", (bonus_duration,))
                            bonus_tariff = cursor.fetchone()
                            if bonus_tariff:
                                bonus_tariff_dict = {"id": bonus_tariff[0], "name": bonus_tariff[1], "price_rub": bonus_tariff[4], "duration_sec": bonus_tariff[2]}
                                await create_new_key_flow_with_protocol(cursor, message, referrer_id, bonus_tariff_dict, None, None, protocol)
                                await safe_send_message(bot, referrer_id, "🎉 Вам выдан бесплатный месяц за приглашённого друга!")
                        cursor.execute("UPDATE referrals SET bonus_issued = 1 WHERE referred_id = ?", (user_id,))
                
                return
            
            # Проверяем, не истек ли инвойс
            invoice_info = await payment_service.cryptobot_service.get_invoice(int(invoice_id))
            if invoice_info and invoice_info.get("status") == "expired":
                if message:
                    await message.answer("⏰ Время оплаты истекло. Создайте новый платеж.", reply_markup=main_menu)
                return
            
            await asyncio.sleep(check_interval)
        
        # Если цикл завершился, значит таймаут
        if message:
            await message.answer("⏰ Время ожидания платежа истекло. Попробуйте создать платеж заново.", reply_markup=main_menu)
        
    except Exception as e:
        logging.error(f"Error waiting for crypto payment: {e}")
        if message:
            await message.answer("Ошибка при проверке платежа. Обратитесь в поддержку.", reply_markup=main_menu)

