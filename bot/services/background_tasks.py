"""
Модуль для фоновых задач бота
Вынесен из bot.py для улучшения поддерживаемости
"""
import asyncio
import time
import logging
from typing import Optional

from utils import get_db_cursor
from outline import delete_key
from vpn_protocols import format_duration, ProtocolFactory
from bot.utils import format_key_message, format_key_message_unified
from bot.keyboards import get_main_menu
from bot.core import get_bot_instance
from bot.services.key_creation import select_available_server_by_protocol
from app.infra.foreign_keys import safe_foreign_keys_off
from memory_optimizer import optimize_memory, log_memory_usage
from config import ADMIN_ID

logger = logging.getLogger(__name__)

# Глобальная переменная для отслеживания уведомлений о низком количестве ключей
low_key_notified = False


async def auto_delete_expired_keys():
    """Автоматическое удаление истекших ключей с grace period 24 часа"""
    GRACE_PERIOD = 86400  # 24 часа в секундах
    
    while True:
        try:
            with get_db_cursor(commit=True) as cursor:
                now = int(time.time())
                grace_threshold = now - GRACE_PERIOD
                
                # Get expired Outline keys (истекшие более 24 часов назад)
                cursor.execute("""
                    SELECT k.id, k.key_id, s.api_url, s.cert_sha256 
                    FROM keys k 
                    JOIN servers s ON k.server_id = s.id 
                    WHERE k.expiry_at <= ?
                """, (grace_threshold,))
                expired_outline_keys = cursor.fetchall()
                
                # Delete Outline keys from server first, then from database
                outline_deleted = 0
                for key_id_db, key_id_outline, api_url, cert_sha256 in expired_outline_keys:
                    if key_id_outline:
                        success = await asyncio.get_event_loop().run_in_executor(
                            None, delete_key, api_url, cert_sha256, key_id_outline
                        )
                        if not success:
                            logging.warning(f"Failed to delete Outline key {key_id_outline} from server")
                
                # Delete Outline keys from database
                # Используем safe_foreign_keys_off для безопасного удаления
                with safe_foreign_keys_off(cursor):
                    cursor.execute("DELETE FROM keys WHERE expiry_at <= ?", (grace_threshold,))
                    outline_deleted = cursor.rowcount
                
                # Get expired V2Ray keys (истекшие более 24 часов назад)
                cursor.execute("""
                    SELECT k.id, k.v2ray_uuid, s.api_url, s.api_key 
                    FROM v2ray_keys k 
                    JOIN servers s ON k.server_id = s.id 
                    WHERE k.expiry_at <= ?
                """, (grace_threshold,))
                expired_v2ray_keys = cursor.fetchall()
                
                # Delete V2Ray keys from server first, then from database
                v2ray_deleted = 0
                for key_id_db, v2ray_uuid, api_url, api_key in expired_v2ray_keys:
                    if v2ray_uuid and api_url and api_key:
                        try:
                            from vpn_protocols import V2RayProtocol
                            protocol_client = V2RayProtocol(api_url, api_key)
                            await protocol_client.delete_user(v2ray_uuid)
                        except Exception as e:
                            logging.warning(f"Failed to delete V2Ray key {v2ray_uuid} from server: {e}")
                
                # Delete V2Ray keys from database
                try:
                    # Временно отключаем проверку foreign keys для удаления
                    with safe_foreign_keys_off(cursor):
                        cursor.execute("DELETE FROM v2ray_keys WHERE expiry_at <= ?", (grace_threshold,))
                        v2ray_deleted = cursor.rowcount
                except Exception as e:
                    logging.warning(f"Error deleting expired V2Ray keys: {e}")
                    v2ray_deleted = 0
                
                # Log results
                if outline_deleted > 0 or v2ray_deleted > 0:
                    logging.info(f"Deleted expired keys (grace period 24h): {outline_deleted} Outline, {v2ray_deleted} V2Ray")
            
            # Оптимизация памяти после очистки
            try:
                optimize_memory()
                log_memory_usage()
            except Exception as e:
                logging.error(f"Ошибка при оптимизации памяти: {e}")
            
        except Exception as e:
            logging.error(f"Error in auto_delete_expired_keys: {e}")
        
        await asyncio.sleep(600)  # Проверка каждые 10 минут


async def notify_expiring_keys():
    """Уведомление пользователей об истекающих ключах"""
    bot = get_bot_instance()
    main_menu = get_main_menu()
    
    while True:
        try:
            outline_updates = []  # Список для батчинга обновлений Outline ключей
            v2ray_updates = []  # Список для батчинга обновлений V2Ray ключей
            notifications_to_send = []  # Список уведомлений для отправки
            
            with get_db_cursor() as cursor:
                now = int(time.time())
                one_day = 86400
                one_hour = 3600
                ten_minutes = 600
                
                # Проверяем Outline ключи
                cursor.execute("""
                    SELECT k.id, k.user_id, k.access_url, k.expiry_at, 
                           k.created_at, COALESCE(k.notified, 0) as notified
                    FROM keys k 
                    WHERE k.expiry_at > ?
                """, (now,))
                outline_rows = cursor.fetchall()
                
                for row in outline_rows:
                    key_id_db, user_id, access_url, expiry, created_at, notified = row
                    remaining_time = expiry - now
                    
                    # Пропускаем ключи без created_at (не можем вычислить original_duration)
                    if created_at is None:
                        logging.warning(f"Skipping Outline key {key_id_db} - created_at is None")
                        continue
                    
                    original_duration = expiry - created_at
                    ten_percent_threshold = int(original_duration * 0.1)
                    message = None
                    new_notified = notified
                    key_type = 'outline'

                    # Проверяем уведомления в порядке приоритета (от более ранних к более поздним)
                    # 1 day notification (за 24 часа до истечения, только для ключей длительностью > 1 дня)
                    if original_duration > one_day and one_hour < remaining_time <= one_day and (notified & 4) == 0:
                        time_str = format_duration(remaining_time)
                        message = f"⏳ Ваш ключ истечет через {time_str}:\n`{access_url}`\nПродлите доступ:"
                        new_notified = notified | 4  # Устанавливаем бит для уведомления за день
                    # 1 hour notification (за 1 час до истечения, только для ключей длительностью > 1 часа)
                    # Используем окно: если осталось меньше часа, но больше 10 минут, и уведомление еще не отправлено
                    elif original_duration > one_hour and ten_minutes < remaining_time <= (one_hour + 60) and (notified & 2) == 0:
                        time_str = format_duration(remaining_time)
                        message = f"⏳ Ваш ключ истечет через {time_str}:\n`{access_url}`\nПродлите доступ:"
                        new_notified = notified | 2  # Устанавливаем бит для уведомления за час
                    # 10 minutes notification (за 10 минут до истечения)
                    elif remaining_time > 0 and remaining_time <= ten_minutes and (notified & 8) == 0:
                        time_str = format_duration(remaining_time)
                        message = f"⏳ Ваш ключ истечет через {time_str}:\n`{access_url}`\nПродлите доступ:"
                        new_notified = notified | 8  # Устанавливаем бит для уведомления за 10 минут
                    # 10% notification (когда осталось 10% времени, только если не было других уведомлений)
                    elif remaining_time > 0 and remaining_time <= ten_percent_threshold and (notified & 1) == 0:
                        time_str = format_duration(remaining_time)
                        message = f"⏳ Ваш ключ истечет через {time_str}:\n`{access_url}`\nПродлите доступ:"
                        new_notified = notified | 1  # Устанавливаем бит для уведомления за 10%

                    if message:
                        # Сохраняем уведомление для отправки после коммита
                        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                        keyboard = InlineKeyboardMarkup()
                        keyboard.add(InlineKeyboardButton("🔁 Продлить", callback_data="buy"))
                        notifications_to_send.append((user_id, message, keyboard))
                        # Добавляем обновление в батч
                        outline_updates.append((new_notified, key_id_db))
                
                # Проверяем V2Ray ключи
                cursor.execute("""
                    SELECT k.id, k.user_id, k.client_config, k.expiry_at, 
                           k.created_at, COALESCE(k.notified, 0) as notified
                    FROM v2ray_keys k 
                    WHERE k.expiry_at > ?
                """, (now,))
                v2ray_rows = cursor.fetchall()
                
                for row in v2ray_rows:
                    key_id_db, user_id, client_config, expiry, created_at, notified = row
                    remaining_time = expiry - now
                    
                    # Пропускаем ключи без created_at (не можем вычислить original_duration)
                    if created_at is None:
                        logging.warning(f"Skipping V2Ray key {key_id_db} - created_at is None")
                        continue
                    
                    original_duration = expiry - created_at
                    ten_percent_threshold = int(original_duration * 0.1)
                    message = None
                    new_notified = notified
                    key_type = 'v2ray'
                    
                    # Используем client_config для отображения ключа
                    key_display = client_config if client_config else "V2Ray ключ"

                    # Проверяем уведомления в порядке приоритета (от более ранних к более поздним)
                    # 1 day notification (за 24 часа до истечения, только для ключей длительностью > 1 дня)
                    if original_duration > one_day and one_hour < remaining_time <= one_day and (notified & 4) == 0:
                        time_str = format_duration(remaining_time)
                        message = f"⏳ Ваш ключ истечет через {time_str}:\n`{key_display}`\nПродлите доступ:"
                        new_notified = notified | 4  # Устанавливаем бит для уведомления за день
                    # 1 hour notification (за 1 час до истечения, только для ключей длительностью > 1 часа)
                    # Используем окно: если осталось меньше часа, но больше 10 минут, и уведомление еще не отправлено
                    elif original_duration > one_hour and ten_minutes < remaining_time <= (one_hour + 60) and (notified & 2) == 0:
                        time_str = format_duration(remaining_time)
                        message = f"⏳ Ваш ключ истечет через {time_str}:\n`{key_display}`\nПродлите доступ:"
                        new_notified = notified | 2  # Устанавливаем бит для уведомления за час
                    # 10 minutes notification (за 10 минут до истечения)
                    elif remaining_time > 0 and remaining_time <= ten_minutes and (notified & 8) == 0:
                        time_str = format_duration(remaining_time)
                        message = f"⏳ Ваш ключ истечет через {time_str}:\n`{key_display}`\nПродлите доступ:"
                        new_notified = notified | 8  # Устанавливаем бит для уведомления за 10 минут
                    # 10% notification (когда осталось 10% времени, только если не было других уведомлений)
                    elif remaining_time > 0 and remaining_time <= ten_percent_threshold and (notified & 1) == 0:
                        time_str = format_duration(remaining_time)
                        message = f"⏳ Ваш ключ истечет через {time_str}:\n`{key_display}`\nПродлите доступ:"
                        new_notified = notified | 1  # Устанавливаем бит для уведомления за 10%

                    if message:
                        # Сохраняем уведомление для отправки после коммита
                        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                        keyboard = InlineKeyboardMarkup()
                        keyboard.add(InlineKeyboardButton("🔁 Продлить", callback_data="buy"))
                        notifications_to_send.append((user_id, message, keyboard))
                        # Добавляем обновление в батч
                        v2ray_updates.append((new_notified, key_id_db))
            
            # Отправляем все уведомления
            for user_id, message, keyboard in notifications_to_send:
                try:
                    await bot.send_message(user_id, message, reply_markup=keyboard, disable_web_page_preview=True, parse_mode="Markdown")
                    logging.info(f"Sent expiry notification to user {user_id}")
                except Exception as e:
                    logging.error(f"Error sending expiry notification to user {user_id}: {e}")
            
            # Батчинг обновлений в БД
            if outline_updates:
                with get_db_cursor(commit=True) as cursor:
                    cursor.executemany("UPDATE keys SET notified = ? WHERE id = ?", outline_updates)
                    logging.info(f"Updated {len(outline_updates)} Outline keys with expiry notifications")
            
            if v2ray_updates:
                with get_db_cursor(commit=True) as cursor:
                    cursor.executemany("UPDATE v2ray_keys SET notified = ? WHERE id = ?", v2ray_updates)
                    logging.info(f"Updated {len(v2ray_updates)} V2Ray keys with expiry notifications")
            
        except Exception as e:
            logging.error(f"Error in notify_expiring_keys: {e}", exc_info=True)
        
        await asyncio.sleep(60)  # Проверка каждую минуту


async def check_key_availability():
    """Проверка доступности ключей и уведомление админа при низком количестве"""
    global low_key_notified
    bot = get_bot_instance()
    
    while True:
        try:
            with get_db_cursor() as cursor:
                # Calculate total key capacity
                cursor.execute("SELECT SUM(max_keys) FROM servers WHERE active = 1")
                total_capacity = cursor.fetchone()[0] or 0

                # Count active keys
                now = int(time.time())
                cursor.execute("SELECT COUNT(*) FROM keys WHERE expiry_at > ?", (now,))
                active_keys = cursor.fetchone()[0] or 0

                free_keys = total_capacity - active_keys

                if free_keys < 6:
                    if not low_key_notified:
                        await bot.send_message(
                            ADMIN_ID,
                            f"⚠️ **Внимание:** Осталось мало свободных ключей: *{free_keys}*.",
                            parse_mode="Markdown"
                        )
                        low_key_notified = True
                else:
                    if low_key_notified:
                        await bot.send_message(
                            ADMIN_ID,
                            f"✅ **Статус:** Количество свободных ключей восстановлено: *{free_keys}*.",
                            parse_mode="Markdown"
                        )
                    low_key_notified = False
        except Exception as e:
            logging.error(f"Error in check_key_availability: {e}")

        await asyncio.sleep(300)  # Проверка каждые 5 минут


async def process_pending_paid_payments():
    """Обработка оплаченных платежей без созданных ключей"""
    # Используем новый платежный модуль если доступен
    try:
        from memory_optimizer import get_payment_service
        payment_service = get_payment_service()
        if payment_service:
            # Используем новый модуль через legacy adapter
            try:
                from payments.adapters.legacy_adapter import process_pending_paid_payments_legacy
                await process_pending_paid_payments_legacy()
                return
            except Exception as e:
                logging.warning(f"Ошибка в новом платежном модуле, используем старый: {e}")
    except Exception as e:
        logging.warning(f"Не удалось загрузить платежный модуль: {e}")
    
    # Fallback на старый код
    bot = get_bot_instance()
    main_menu = get_main_menu()
    
    # Lazy import для избежания циклических зависимостей
    from outline import create_key
    from bot.utils import format_key_message
    
    while True:
        try:
            with get_db_cursor(commit=True) as cursor:
                # Проверяем оплаченные платежи, у которых нет активных ключей (Outline или V2Ray)
                # И которые не были отозваны
                cursor.execute('''
                    SELECT p.id, p.user_id, p.tariff_id, p.email, p.protocol, p.country 
                    FROM payments p
                    WHERE p.status="paid" AND p.revoked = 0 
                    AND p.user_id NOT IN (
                        SELECT user_id FROM keys WHERE expiry_at > ?
                        UNION
                        SELECT user_id FROM v2ray_keys WHERE expiry_at > ?
                    )
                ''', (int(time.time()), int(time.time())))
                payments = cursor.fetchall()
                
                for payment_id, user_id, tariff_id, email, protocol, country in payments:
                    # Получаем тариф
                    cursor.execute('SELECT name, duration_sec, price_rub FROM tariffs WHERE id=?', (tariff_id,))
                    tariff_row = cursor.fetchone()
                    if not tariff_row:
                        logging.error(f"[AUTO-ISSUE] Не найден тариф id={tariff_id} для user_id={user_id}")
                        continue
                    tariff = {'id': tariff_id, 'name': tariff_row[0], 'duration_sec': tariff_row[1], 'price_rub': tariff_row[2]}
                    
                    # Определяем протокол (если не указан, используем outline по умолчанию)
                    if not protocol:
                        protocol = "outline"
                    
                    # Выбираем сервер с местами для указанного протокола и страны
                    server = select_available_server_by_protocol(cursor, country, protocol)
                    if not server:
                        logging.error(f"[AUTO-ISSUE] Нет доступных серверов {protocol} для user_id={user_id}, тариф={tariff}, страна={country}")
                        continue
                    
                    # Преобразуем server tuple в словарь для удобства
                    # select_available_server_by_protocol возвращает: id, name, api_url, cert_sha256, domain, api_key, v2ray_path
                    server_dict = {
                        'id': server[0] if len(server) > 0 else None,
                        'name': server[1] if len(server) > 1 else None,
                        'api_url': server[2] if len(server) > 2 else None,
                        'cert_sha256': server[3] if len(server) > 3 else None,
                        'domain': server[4] if len(server) > 4 else None,
                        'api_key': server[5] if len(server) > 5 else None,
                        'v2ray_path': server[6] if len(server) > 6 else None,
                    }
                    
                    # Создаём ключ в зависимости от протокола
                    if protocol == "outline":
                        try:
                            key = await asyncio.get_event_loop().run_in_executor(None, create_key, server_dict['api_url'], server_dict['cert_sha256'])
                        except Exception as e:
                            logging.error(f"[AUTO-ISSUE] Ошибка при создании Outline ключа для user_id={user_id}: {e}")
                            continue
                        if not key:
                            logging.error(f"[AUTO-ISSUE] Не удалось создать Outline ключ для user_id={user_id}, тариф={tariff}")
                            continue
                        
                        now = int(time.time())
                        expiry = now + tariff['duration_sec']
                        cursor.execute(
                            "INSERT INTO keys (server_id, user_id, access_url, expiry_at, key_id, created_at, email, tariff_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (server_dict['id'], user_id, key["accessUrl"], expiry, key["id"], now, email, tariff_id)
                        )
                        
                        # Если это бесплатный тариф, записываем использование
                        if tariff['price_rub'] == 0:
                            # Lazy import для избежания циклических зависимостей
                            import importlib
                            bot_module = importlib.import_module('bot')
                            record_free_key_usage = getattr(bot_module, 'record_free_key_usage', None)
                            if record_free_key_usage:
                                record_free_key_usage(cursor, user_id, protocol, country)
                        
                        # Уведомляем пользователя
                        try:
                            await bot.send_message(user_id, format_key_message(key["accessUrl"]), reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                        except Exception as e:
                            logging.error(f"[AUTO-ISSUE] Не удалось отправить Outline ключ user_id={user_id}: {e}")
                    
                    elif protocol == "v2ray":
                        try:
                            server_config = {'api_url': server_dict['api_url'], 'api_key': server_dict.get('api_key')}
                            protocol_client = ProtocolFactory.create_protocol(protocol, server_config)
                            
                            # Создаем пользователя на сервере (ВАЖНО: делаем это до сохранения в БД)
                            user_data = await protocol_client.create_user(email or f"user_{user_id}@veilbot.com")
                            
                            # Проверяем, что user_data - это словарь
                            if not isinstance(user_data, dict):
                                logging.error(f"[AUTO-ISSUE] Invalid user_data type: {type(user_data)}, expected dict for user_id={user_id}")
                                continue
                            
                            # Валидация: проверяем, что пользователь действительно создан
                            if not user_data or not user_data.get('uuid'):
                                raise Exception(f"Failed to create V2Ray user - invalid response from server")
                            
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
                                logging.info(f"Using client_config from create_user response for auto-issued key")
                            else:
                                # Если client_config нет в ответе, запрашиваем через get_user_config
                                logging.debug(f"client_config not in create_user response, fetching via get_user_config")
                                config = await protocol_client.get_user_config(user_data['uuid'], {
                                    'domain': server_dict.get('domain') or 'veil-bot.ru',
                                    'port': 443,
                                    'path': server_dict.get('v2ray_path') or '/v2ray',
                                    'email': email or f"user_{user_id}@veilbot.com"
                                })
                                # Извлекаем VLESS URL, если конфигурация многострочная
                                if 'vless://' in config:
                                    lines = config.split('\n')
                                    for line in lines:
                                        if line.strip().startswith('vless://'):
                                            config = line.strip()
                                            break
                            
                            # Проверяем и создаем пользователя в таблице users, если его нет
                            cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
                            if not cursor.fetchone():
                                logging.info(f"[AUTO-ISSUE] User {user_id} not found in users table, creating...")
                                with safe_foreign_keys_off(cursor):
                                    cursor.execute("""
                                        INSERT OR REPLACE INTO users 
                                        (user_id, username, first_name, last_name, created_at, last_active_at, blocked)
                                        VALUES (?, ?, ?, ?, ?, ?, 0)
                                    """, (user_id, None, None, None, now, now))
                                logging.info(f"[AUTO-ISSUE] User {user_id} created in users table")
                            
                            now = int(time.time())
                            expiry = now + tariff['duration_sec']
                            with safe_foreign_keys_off(cursor):
                                cursor.execute(
                                    "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id, client_config) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                    (server_dict['id'], user_id, user_data['uuid'], email or f"user_{user_id}@veilbot.com", now, expiry, tariff_id, config)
                                )
                            
                            # Если это бесплатный тариф, записываем использование
                            if tariff['price_rub'] == 0:
                                # Lazy import для избежания циклических зависимостей
                                import importlib
                                bot_module = importlib.import_module('bot')
                                record_free_key_usage = getattr(bot_module, 'record_free_key_usage', None)
                                if record_free_key_usage:
                                    record_free_key_usage(cursor, user_id, protocol, country)
                            
                            # Уведомляем пользователя
                            try:
                                await bot.send_message(user_id, format_key_message_unified(config, protocol, tariff), reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                            except Exception as e:
                                logging.error(f"[AUTO-ISSUE] Не удалось отправить V2Ray ключ user_id={user_id}: {e}")
                                
                        except Exception as e:
                            logging.error(f"[AUTO-ISSUE] Ошибка при создании V2Ray ключа для user_id={user_id}: {e}")
                            
                            # При ошибке пытаемся удалить созданного пользователя с сервера
                            try:
                                if 'user_data' in locals() and user_data and user_data.get('uuid'):
                                    if 'protocol_client' in locals():
                                        await protocol_client.delete_user(user_data['uuid'])
                                        logging.info(f"[AUTO-ISSUE] Deleted V2Ray user {user_data['uuid']} from server due to error")
                            except Exception as cleanup_error:
                                logging.error(f"[AUTO-ISSUE] Failed to cleanup V2Ray user after error: {cleanup_error}")
                            
                            continue
                    
                    logging.info(f"[AUTO-ISSUE] Успешно создан ключ {protocol} для user_id={user_id}, payment_id={payment_id}")
                    
        except Exception as e:
            logging.error(f"[AUTO-ISSUE] Общая ошибка фоновой задачи: {e}")
        await asyncio.sleep(300)  # Проверка каждые 5 минут

