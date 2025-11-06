import asyncio
import time
import sqlite3
import re
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from app.logging_config import setup_logging
from aiogram import Bot, Dispatcher, types, executor
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from config import TELEGRAM_BOT_TOKEN, PROTOCOLS, validate_configuration, ADMIN_ID
from db import init_db
from outline import create_key, delete_key
from utils import get_db_cursor
from vpn_protocols import format_duration, ProtocolFactory, get_protocol_instructions
from bot.keyboards import (
    get_main_menu, get_help_keyboard, get_cancel_keyboard,
    get_protocol_selection_menu, get_tariff_menu, get_payment_method_keyboard,
    get_country_menu, get_countries, get_countries_by_protocol, invalidate_menu_cache
)
from bot.utils import format_key_message, format_key_message_unified, format_key_message_with_protocol
from bot.services.key_creation import (
    select_available_server_by_protocol,
    create_new_key_flow_with_protocol,
    wait_for_payment_with_protocol,
    wait_for_crypto_payment
)
from bot.services.key_management import (
    extend_existing_key,
    extend_existing_key_with_fallback,
    delete_old_key_after_success,
    switch_protocol_and_extend,
    change_country_and_extend,
    change_protocol_for_key,
    change_country_for_key,
    reissue_specific_key
)
from bot.services.free_tariff import (
    handle_free_tariff,
    handle_free_tariff_with_protocol,
    check_free_tariff_limit,
    check_free_tariff_limit_by_protocol,
    check_free_tariff_limit_by_protocol_and_country,
    record_free_key_usage
)
from bot.services.tariff_service import (
    get_tariff_by_name_and_price,
    handle_payment_method_selection,
    handle_paid_tariff_with_protocol
)

# Оптимизация памяти
from memory_optimizer import (
    get_payment_service, get_vpn_service, get_security_logger,
    optimize_memory, get_memory_stats, log_memory_usage
)

# Ленивые импорты для тяжелых модулей
PAYMENT_MODULE_AVAILABLE = None  # Будет определено при первом использовании
VPN_PROTOCOLS_AVAILABLE = None   # Будет определено при первом использовании
SECURITY_LOGGER_AVAILABLE = None # Будет определено при первом использовании

# Импорты валидаторов (легкие модули)
from validators import input_validator, db_validator, business_validator, validate_user_input, sanitize_user_input, ValidationError
from bot_error_handler import BotErrorHandler, setup_error_handler
from bot_rate_limiter import rate_limit
from app.infra.foreign_keys import safe_foreign_keys_off

# Security configuration
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin"
}

# Инициализация bot и dp перенесена в bot/main.py
# Эти переменные будут созданы при запуске через bot/main.py
bot = None
dp = None

# Simple state management for email collection
user_states: Dict[int, Dict[str, Any]] = {}  # user_id -> {"state": ..., ...}

# Notification state for key availability перенесена в bot/services/background_tasks.py

# Главное меню (создаем глобальную переменную для обратной совместимости)
main_menu = get_main_menu()
help_keyboard = get_help_keyboard()
cancel_keyboard = get_cancel_keyboard()

def is_valid_email(email: str) -> bool:
    """
    Валидация email адреса
    
    Args:
        email: Email адрес для проверки
    
    Returns:
        True если email валиден, False в противном случае
    """
    return input_validator.validate_email(email)

# Импортируем и регистрируем handlers
from bot.handlers.start import register_start_handler
from bot.handlers.keys import register_keys_handler
from bot.handlers.purchase import register_purchase_handlers
from bot.handlers.renewal import register_renewal_handlers
from bot.handlers.common import register_common_handlers
from bot.handlers.key_management import register_key_management_handlers

# Функции управления ключами определены в bot.py (строки 2576+)
# Они будут переданы в register_key_management_handlers после их определения

# Регистрация handlers перенесена в bot/main.py
# Handlers регистрируются при запуске через bot/main.py

# Регистрация handlers управления ключами и purchase handlers будет выполнена после определения функций
# (функции определены в строках 1724+ для payment, 2484+ для key_management)

# Функции для передачи в purchase handlers
# ВАЖНО: handle_invite_friend перенесена в bot/handlers/common.py
# Оставлена здесь для обратной совместимости (используется в purchase handlers)
async def handle_invite_friend(message: types.Message) -> None:
    """Функция для обратной совместимости - делегирует в common.py"""
    from bot.handlers.common import handle_invite_friend as common_handle_invite_friend
    await common_handle_invite_friend(message)

# Функция get_tariff_by_name_and_price перенесена в bot/services/tariff_service.py

# Регистрация purchase handlers будет выполнена после определения функций (см. строку ~3327)

# Обработчики покупки вынесены в bot/handlers/purchase.py

@dp.message_handler(lambda m: m.text == "🔙 Назад")
async def back_to_main(message: types.Message) -> None:
    # Clear any existing state
    user_id = message.from_user.id
    if user_id in user_states:
        del user_states[user_id]
    await message.answer("Главное меню:", reply_markup=main_menu)

# --- Обработчик кнопки 'Пригласить друга' перенесен в bot/handlers/common.py ---
# Регистрируется через register_common_handlers()

# Обработчики purchase handlers вынесены в bot/handlers/purchase.py
# (handle_buy_menu, handle_protocol_selection, handle_cancel,
#  handle_payment_method_after_country, handle_payment_method_input,
#  handle_email_input, handle_country_selection, handle_protocol_country_selection,
#  handle_tariff_selection_with_country)

@dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "reactivation_country_selection")
async def handle_reactivation_country_selection(message: types.Message) -> None:
    """Обработчик выбора страны при реактивации истекшего ключа"""
    user_id = message.from_user.id
    text = message.text or ""
    
    # Проверяем, что это кнопка "Отмена"
    if text == "🔙 Отмена":
        user_states.pop(user_id, None)
        await message.answer("Покупка отменена.", reply_markup=main_menu)
        return
    
    # Получаем сохраненное состояние
    state = user_states.get(user_id, {})
    tariff = state.get("tariff")
    email = state.get("email")
    protocol = state.get("protocol", "outline")
    last_country = state.get("last_country")
    
    if not tariff:
        await message.answer("Ошибка: данные тарифа не найдены. Попробуйте еще раз.", reply_markup=main_menu)
        user_states.pop(user_id, None)
        return
    
    # Извлекаем название страны из текста
    selected_country = text
    if text.startswith("🔄 ") and "(как раньше)" in text:
        # Убираем "🔄 " и " (как раньше)"
        selected_country = text[2:].replace(" (как раньше)", "")
    
    # Проверяем, что страна доступна для выбранного протокола
    countries = get_countries_by_protocol(protocol)
    if selected_country not in countries:
        await message.answer(
            f"Пожалуйста, выберите страну из списка для {PROTOCOLS[protocol]['name']}:",
            reply_markup=get_country_menu(countries)
        )
        return
    
    # Очищаем состояние и создаем ключ с выбранной страной
    user_states.pop(user_id, None)
    
    # Создаем ключ через существующую функцию
    with get_db_cursor(commit=True) as cursor:
        await create_new_key_flow_with_protocol(cursor, message, user_id, tariff, email, selected_country, protocol)

# Обработчик country_change_selection вынесен в bot/handlers/key_management.py

# Обработчики purchase (waiting_country, protocol_selected, waiting_tariff) вынесены в bot/handlers/purchase.py

# Функция get_tariff_by_name_and_price перенесена в bot/services/tariff_service.py

# Функции работы с бесплатными тарифами перенесены в bot/services/free_tariff.py
# Импортируем их оттуда (см. импорты выше)

# Функции управления ключами перенесены в bot/services/key_management.py
# Импортируем их оттуда (см. импорты выше)
# Удалены функции:
# - check_server_availability() (~17 строк)
# - find_alternative_server() (~22 строки)
# - extend_existing_key_with_fallback() (~190 строк)
# - extend_existing_key() (~19 строк)
# - switch_protocol_and_extend() (~209 строк)
# - change_country_and_extend() (~173 строки)
# - delete_old_key_after_success() (~51 строка)
# - change_protocol_for_key() (~144 строки)
# - change_country_for_key() (~164 строки)
# - reissue_specific_key() (~257 строк)
# Функции управления ключами перенесены в bot/services/key_management.py
# Старые версии функций удалены

async def create_new_key_flow(
    cursor: sqlite3.Cursor, 
    message: types.Message, 
    user_id: int, 
    tariff: Dict[str, Any], 
    email: Optional[str] = None, 
    country: Optional[str] = None
) -> None:
    """
    Создает новый VPN ключ или продлевает существующий (старая версия без протоколов)
    
    Если у пользователя есть активный или недавно истекший ключ (в пределах grace period 24 часа),
    ключ продлевается. Иначе создается новый ключ.
    
    Args:
        cursor: Курсор базы данных
        message: Telegram сообщение для отправки уведомлений пользователю
        user_id: ID пользователя
        tariff: Словарь с данными тарифа (name, price_rub, duration_sec, id)
        email: Email пользователя (опционально)
        country: Страна сервера (опционально)
    """
    now = int(time.time())
    GRACE_PERIOD = 86400  # 24 часа в секундах
    grace_threshold = now - GRACE_PERIOD
    
    # Проверяем наличие активного или недавно истекшего ключа (в пределах grace period)
    cursor.execute("SELECT id, expiry_at, access_url FROM keys WHERE user_id = ? AND expiry_at > ? ORDER BY expiry_at DESC LIMIT 1", (user_id, grace_threshold))
    existing_key = cursor.fetchone()
    if existing_key:
        # Используем функцию из key_management.py
        from bot.services.key_management import extend_existing_key
        extend_existing_key(cursor, existing_key, tariff['duration_sec'], email, tariff['id'])
        was_expired = existing_key[1] <= now
        if was_expired:
            await message.answer(f"✅ Ваш истекший ключ восстановлен и продлён на {format_duration(tariff['duration_sec'])}!\n\n{format_key_message(existing_key[2])}", reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
        else:
            await message.answer(f"Ваш ключ продлён на {format_duration(tariff['duration_sec'])}!\n\n{format_key_message(existing_key[2])}", reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
        # Уведомление админу
        admin_msg = (
            f"🔑 *Продление ключа*\n"
            f"Пользователь: `{user_id}`\n"
            f"Тариф: *{tariff.get('name', 'Неизвестно')}*\n"
            f"Ключ: `{existing_key[2]}`\n"
        )
        if email:
            admin_msg += f"Email: `{email}`\n"
        try:
            await bot.send_message(ADMIN_ID, admin_msg, disable_web_page_preview=True, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Failed to send admin notification: {e}")
        return
    # Если нет активного ключа — создаём новый
    server = select_available_server(cursor, country)
    if not server:
        await message.answer("Нет доступных серверов.", reply_markup=main_menu)
        return
    key = await asyncio.get_event_loop().run_in_executor(None, create_key, server['api_url'], server['cert_sha256'])
    if not key:
        await message.answer("Ошибка при создании ключа.", reply_markup=main_menu)
        return
    expiry = now + tariff['duration_sec']
    cursor.execute(
        "INSERT INTO keys (server_id, user_id, access_url, expiry_at, key_id, created_at, email, tariff_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (server['id'], user_id, key["accessUrl"], expiry, key["id"], now, email, tariff['id'])
    )
    
    # Если это бесплатный тариф, записываем использование
    if tariff['price_rub'] == 0:
        record_free_key_usage(cursor, user_id, "outline", country)
    
    await message.answer(format_key_message(key["accessUrl"]), reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
    # Admin notification as before
    admin_msg = (
        f"🔑 *Покупка ключа*\n"
        f"Пользователь: `{user_id}`\n"
        f"Тариф: *{tariff.get('name', 'Неизвестно')}*\n"
        f"Ключ: `{key['accessUrl']}`\n"
    )
    if email:
        admin_msg += f"Email: `{email}`\n"
    try:
        await bot.send_message(ADMIN_ID, admin_msg, disable_web_page_preview=True, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Failed to send admin notification: {e}")

# Функции switch_protocol_and_extend и change_country_and_extend перенесены в bot/services/key_management.py

# Функция create_new_key_flow_with_protocol перенесена в bot/services/key_creation.py
# Старая реализация удалена (было ~527 строк)
# Функция select_available_server_by_protocol перенесена в bot/services/key_creation.py

# Функция handle_free_tariff_with_protocol перенесена в bot/services/free_tariff.py

# Функции handle_payment_method_selection и handle_paid_tariff_with_protocol 
# перенесены в bot/services/tariff_service.py

async def create_payment_with_email_and_protocol(
    message: types.Message, 
    user_id: int, 
    tariff: Dict[str, Any], 
    email: Optional[str] = None, 
    country: Optional[str] = None, 
    protocol: str = "outline", 
    payment_method: str = "yookassa", 
    for_renewal: bool = False
) -> None:
    """
    Создание платежа с поддержкой протоколов и способов оплаты
    
    Создает платеж через YooKassa или CryptoBot в зависимости от выбранного способа оплаты.
    После успешной оплаты автоматически создается VPN ключ.
    
    Args:
        message: Telegram сообщение для отправки уведомлений пользователю
        user_id: ID пользователя
        tariff: Словарь с данными тарифа (name, price_rub, duration_sec, id, price_crypto_usd)
        email: Email пользователя (опционально)
        country: Страна сервера (опционально)
        protocol: Протокол VPN ('outline' или 'v2ray')
        payment_method: Способ оплаты ('yookassa' или 'cryptobot')
        for_renewal: Если True, при выборе сервера не проверяется available_for_purchase (только active)
    """
    logging.debug(f"create_payment_with_email_and_protocol: user_id={user_id}, email={email}, tariff={tariff}, country={country}, protocol={protocol}, payment_method={payment_method}, for_renewal={for_renewal}")
    
    # Если выбран CryptoBot, создаем криптоплатеж
    if payment_method == "cryptobot":
        if not tariff.get('price_crypto_usd'):
            await message.answer(
                "❌ Крипто-оплата недоступна для этого тарифа. Пожалуйста, выберите другой способ оплаты.",
                reply_markup=main_menu
            )
            return
        
        try:
            payment_service = get_payment_service()
            if not payment_service or not payment_service.cryptobot_service:
                await message.answer(
                    "❌ Сервис крипто-платежей временно недоступен. Пожалуйста, используйте другой способ оплаты.",
                    reply_markup=main_menu
                )
                return
            
            # Создаем криптоплатеж
            invoice_id, payment_url = await payment_service.create_crypto_payment(
                user_id=user_id,
                tariff_id=tariff['id'],
                amount_usd=float(tariff['price_crypto_usd']),
                email=email or f"user_{user_id}@veilbot.com",
                country=country,
                protocol=protocol,
                description=f"VPN тариф {tariff['name']}",
                asset="USDT",
                network="TRC20"
            )
            
            if not invoice_id or not payment_url:
                await message.answer(
                    "❌ Ошибка при создании платежа. Попробуйте еще раз или выберите другой способ оплаты.",
                    reply_markup=main_menu
                )
                return
            
            # Создаем inline клавиатуру для оплаты
            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton("₿ Оплатить USDT", url=payment_url))
            keyboard.add(InlineKeyboardButton("🔙 Отмена", callback_data="cancel_payment"))
            
            display_email = email if email else f"user_{user_id}@veilbot.com"
            
            await message.answer(
                f"₿ *Оплата криптовалютой (USDT)*\n\n"
        f"📦 Тариф: *{tariff['name']}*\n"
                f"💰 Сумма: *${tariff['price_crypto_usd']:.2f} USDT*\n"
                f"📧 Email: `{display_email}`\n\n"
                f"{PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}\n\n"
                "Нажмите кнопку ниже для оплаты через CryptoBot:\n"
                "⚠️ Инвойс действителен 1 час",
                reply_markup=keyboard,
        parse_mode="Markdown"
    )

            # Запускаем ожидание платежа (для CryptoBot это будет проверка через webhook или периодическую проверку)
            with get_db_cursor() as cursor:
                server = select_available_server_by_protocol(cursor, country, protocol, for_renewal=for_renewal)
                if server:
                    asyncio.create_task(wait_for_crypto_payment(message, invoice_id, server, user_id, tariff, country, protocol, for_renewal=for_renewal))
            
            return
            
        except Exception as e:
            logging.error(f"Error creating crypto payment: {e}")
            await message.answer(
                "❌ Ошибка при создании криптоплатежа. Попробуйте еще раз или выберите другой способ оплаты.",
                reply_markup=main_menu
            )
            return
    
    # Ленивая инициализация платежного модуля
    global PAYMENT_MODULE_AVAILABLE
    if PAYMENT_MODULE_AVAILABLE is None:
        try:
            payment_service = get_payment_service()
            PAYMENT_MODULE_AVAILABLE = payment_service is not None
            if PAYMENT_MODULE_AVAILABLE:
                # Проверяем, что YooKassa сервис доступен
                if hasattr(payment_service, 'yookassa_service') and payment_service.yookassa_service:
                    logging.info("Платежный сервис инициализирован (lazy loading) - YooKassa доступен")
                else:
                    logging.warning("Платежный сервис инициализирован, но YooKassa недоступен")
                    PAYMENT_MODULE_AVAILABLE = False
            else:
                logging.warning("Платежный сервис недоступен")
        except Exception as e:
            PAYMENT_MODULE_AVAILABLE = False
            logging.error(f"Ошибка инициализации платежного сервиса: {e}", exc_info=True)
    
    # Логирование попытки создания платежа
    try:
            security_logger = get_security_logger()
            if security_logger:
                ip_addr = None
                try:
                    ip_addr = str(message.from_user.id) if getattr(message, 'from_user', None) else None
                except Exception:
                    ip_addr = None
                security_logger.log_payment_attempt(
                    user_id=user_id,
                    amount=tariff.get('price_rub', 0) * 100,  # Конвертируем в копейки
                    protocol=protocol,
                    country=country,
                    email=email,
                    success=True,
                    ip_address=ip_addr,
                    user_agent="Telegram Bot"
                )
    except Exception as e:
        logging.error(f"Error logging payment attempt: {e}")
    
    # Используем новый платежный модуль
    if PAYMENT_MODULE_AVAILABLE:
        try:
            # Используем lazy loading для legacy adapter
            from payments.adapters.legacy_adapter import create_payment_with_email_and_protocol_legacy
            result = await create_payment_with_email_and_protocol_legacy(message, user_id, tariff, email, country, protocol)
            
            if result and result != (None, None):
                # Новый модуль создал платеж
                payment_id, payment_url = result
                logging.debug(f"New payment module created payment: {payment_id}")
                
                # Логирование успешного создания платежа
                try:
                    security_logger = get_security_logger()
                    if security_logger:
                        security_logger.log_payment_success(
                            user_id=user_id,
                            payment_id=payment_id,
                            amount=tariff.get('price_rub', 0) * 100,
                            protocol=protocol,
                            country=country,
                            ip_address=getattr(message, 'from_user', {}).get('id', None),
                            user_agent="Telegram Bot"
                        )
                except Exception as e:
                    logging.error(f"Error logging payment success: {e}")
                
                # Выбираем сервер с учетом протокола
                with get_db_cursor() as cursor:
                    server = select_available_server_by_protocol(cursor, country, protocol, for_renewal=for_renewal)
                    if not server:
                        await message.answer(f"Нет доступных серверов {PROTOCOLS[protocol]['name']} в выбранной стране.", reply_markup=main_menu)
                        return
                
                # Создаем inline клавиатуру для оплаты
                keyboard = InlineKeyboardMarkup()
                keyboard.add(InlineKeyboardButton("💳 Оплатить", url=payment_url))
                keyboard.add(InlineKeyboardButton("🔙 Отмена", callback_data="cancel_payment"))
                
                # Определяем email для отображения
                display_email = email if email else f"user_{user_id}@veilbot.com"
                
                await message.answer(
                    f"💳 *Оплата {PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}*\n\n"
                    f"📦 Тариф: *{tariff['name']}*\n"
                    f"💰 Сумма: *{tariff['price_rub']}₽*\n"
                    f"📧 Email: `{display_email}`\n\n"
                    "Нажмите кнопку ниже для оплаты:",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
                
                # Запускаем ожидание платежа
                asyncio.create_task(wait_for_payment_with_protocol(message, payment_id, server, user_id, tariff, country, protocol, for_renewal=for_renewal))
                return
            else:
                logging.debug("New payment module failed to create payment")
                
                # Логирование неудачного создания платежа
                try:
                    security_logger = get_security_logger()
                    if security_logger:
                        security_logger.log_payment_failure(
                            user_id=user_id,
                            amount=tariff.get('price_rub', 0) * 100,
                            protocol=protocol,
                            error="Payment creation failed",
                            country=country,
                            ip_address=getattr(message, 'from_user', {}).get('id', None),
                            user_agent="Telegram Bot"
                        )
                except Exception as log_e:
                    logging.error(f"Error logging payment failure: {log_e}")
                
                await message.answer("Ошибка при создании платежа.", reply_markup=main_menu)
                return
                
        except Exception as e:
            logging.warning(f"Ошибка в новом платежном модуле: {e}")
            
            # Логирование ошибки в платежном модуле
            try:
                security_logger = get_security_logger()
                if security_logger:
                    security_logger.log_payment_failure(
                        user_id=user_id,
                        amount=tariff.get('price_rub', 0) * 100,
                        protocol=protocol,
                        error=str(e),
                        country=country,
                        ip_address=getattr(message, 'from_user', {}).get('id', None),
                        user_agent="Telegram Bot"
                    )
            except Exception as log_e:
                logging.error(f"Error logging payment module error: {log_e}")
            
            await message.answer("Ошибка при создании платежа.", reply_markup=main_menu)
            return
    else:
        # Если новый модуль недоступен
        logging.warning("Новый платежный модуль недоступен")
        await message.answer("Платежная система временно недоступна.", reply_markup=main_menu)
        return

def select_available_server(
    cursor: sqlite3.Cursor, 
    country: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Выбор доступного сервера для покупки
    
    Выбирает сервер, который активен, доступен для покупки и имеет свободные слоты для ключей.
    
    Args:
        cursor: Курсор базы данных
        country: Страна сервера (опционально, если указана, выбирается сервер из этой страны)
    
    Returns:
        Словарь с данными сервера {'id': int, 'api_url': str, 'cert_sha256': str} 
        или None, если доступный сервер не найден
    """
    now = int(time.time())
    if country:
        servers = cursor.execute("SELECT id, api_url, cert_sha256, max_keys FROM servers WHERE active = 1 AND available_for_purchase = 1 AND country = ?", (country,)).fetchall()
    else:
        servers = cursor.execute("SELECT id, api_url, cert_sha256, max_keys FROM servers WHERE active = 1 AND available_for_purchase = 1").fetchall()
    for s_id, api_url, cert_sha256, max_keys in servers:
        cursor.execute("SELECT COUNT(*) FROM keys WHERE server_id = ? AND expiry_at > ?", (s_id, now))
        active_keys = cursor.fetchone()[0]
        if active_keys < max_keys:
            return {"id": s_id, "api_url": api_url, "cert_sha256": cert_sha256}
    return None


# Функции wait_for_payment_with_protocol и wait_for_crypto_payment перенесены в bot/services/key_creation.py
# Старые реализации удалены (было ~70 и ~75 строк соответственно)

# Фоновые задачи перенесены в bot/services/background_tasks.py
# Импортируем их оттуда
from bot.services.background_tasks import (
    auto_delete_expired_keys,
    notify_expiring_keys,
    check_key_availability,
    process_pending_paid_payments
)

# Старые реализации фоновых задач удалены (было ~350 строк)
# Удалены функции:
# - auto_delete_expired_keys() (~75 строк)
# - notify_expiring_keys() (~70 строк)
# - check_key_availability() (~35 строк)
# - process_pending_paid_payments() (~170 строк)

# Обработчик renewal (callback_buy_button) вынесен в bot/handlers/renewal.py

# --- Country selection helpers ---

# Обработчики help/support/broadcast перенесены в bot/handlers/common.py

# Handlers управления ключами вынесены в bot/handlers/key_management.py

async def show_key_selection_menu(
    message: types.Message, 
    user_id: int, 
    keys: List[Dict[str, Any]]
) -> None:
    """
    Показывает меню выбора ключа для перевыпуска
    
    Отображает список доступных ключей пользователя с возможностью выбора
    конкретного ключа для перевыпуска.
    
    Args:
        message: Telegram сообщение для отправки меню
        user_id: ID пользователя
        keys: Список словарей с данными ключей, каждый должен содержать:
            - id: ID ключа в базе данных
            - type: Тип ключа ('outline' или 'v2ray')
            - protocol: Протокол VPN
            - country: Страна сервера
            - expiry_at: Время истечения ключа
            - tariff_id: ID тарифа
    """
    
    # Создаем клавиатуру для выбора ключа
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    for i, key in enumerate(keys):
        # Получаем информацию о тарифе
        with get_db_cursor() as cursor:
            cursor.execute("SELECT name FROM tariffs WHERE id = ?", (key['tariff_id'],))
            tariff_result = cursor.fetchone()
            tariff_name = tariff_result[0] if tariff_result else "Неизвестно"
        
        # Форматируем время истечения
        expiry_time = time.strftime('%d.%m.%Y %H:%M', time.localtime(key['expiry_at']))
        
        # Создаем текст кнопки
        protocol_icon = PROTOCOLS[key['protocol']]['icon']
        button_text = f"{protocol_icon} {key['country']} - {tariff_name} (до {expiry_time})"
        
        keyboard.add(InlineKeyboardButton(
            text=button_text,
            callback_data=f"reissue_key_{key['type']}_{key['id']}"
        ))
    
    keyboard.add(InlineKeyboardButton("🔙 Отмена", callback_data="cancel_reissue"))
    
    await message.answer(
        "Выберите ключ для перевыпуска:",
        reply_markup=keyboard
    )

async def show_protocol_change_menu(
    message: types.Message, 
    user_id: int, 
    keys: List[Dict[str, Any]]
) -> None:
    """
    Показывает меню выбора ключа для смены протокола
    
    Отображает список доступных ключей пользователя с возможностью выбора
    конкретного ключа для смены протокола VPN (Outline ↔ V2Ray).
    
    Args:
        message: Telegram сообщение для отправки меню
        user_id: ID пользователя
        keys: Список словарей с данными ключей, каждый должен содержать:
            - id: ID ключа в базе данных
            - type: Тип ключа ('outline' или 'v2ray')
            - protocol: Протокол VPN
            - country: Страна сервера
            - expiry_at: Время истечения ключа
            - tariff_id: ID тарифа
    """
    
    # Создаем клавиатуру для выбора ключа
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    for i, key in enumerate(keys):
        # Получаем информацию о тарифе
        with get_db_cursor() as cursor:
            cursor.execute("SELECT name FROM tariffs WHERE id = ?", (key['tariff_id'],))
            tariff_result = cursor.fetchone()
            tariff_name = tariff_result[0] if tariff_result else "Неизвестно"
        
        # Форматируем время истечения
        expiry_time = time.strftime('%d.%m.%Y %H:%M', time.localtime(key['expiry_at']))
        
        # Создаем текст кнопки
        protocol_icon = PROTOCOLS[key['protocol']]['icon']
        button_text = f"{protocol_icon} {key['country']} - {tariff_name} (до {expiry_time})"
        
        keyboard.add(InlineKeyboardButton(
            text=button_text,
            callback_data=f"change_protocol_{key['type']}_{key['id']}"
        ))
    
    keyboard.add(InlineKeyboardButton("🔙 Отмена", callback_data="cancel_protocol_change"))
    
    await message.answer(
        "Выберите ключ для смены протокола:",
        reply_markup=keyboard
    )

# Функции delete_old_key_after_success, change_protocol_for_key, change_country_for_key и reissue_specific_key перенесены в bot/services/key_management.py
# Старые версии функций удалены

async def show_key_selection_for_country_change(
    message: types.Message, 
    user_id: int, 
    all_keys: List[Dict[str, Any]]
) -> None:
    """
    Показывает меню выбора ключа для смены страны
    
    Отображает список доступных ключей пользователя с возможностью выбора
    конкретного ключа для смены страны сервера.
    
    Args:
        message: Telegram сообщение для отправки меню
        user_id: ID пользователя
        all_keys: Список словарей с данными ключей, каждый должен содержать:
            - id: ID ключа в базе данных
            - type: Тип ключа ('outline' или 'v2ray')
            - protocol: Протокол VPN
            - country: Страна сервера
            - expiry_at: Время истечения ключа
    """
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    for key in all_keys:
        protocol_name = PROTOCOLS[key['protocol']]['name']
        country_name = key['country']
        expiry_date = time.strftime('%d.%m.%Y', time.localtime(key['expiry_at']))
        
        button_text = f"{PROTOCOLS[key['protocol']]['icon']} {protocol_name} ({country_name}) - до {expiry_date}"
        callback_data = f"change_country_{key['type']}_{key['id']}"
        
        keyboard.add(InlineKeyboardButton(button_text, callback_data=callback_data))
    
    keyboard.add(InlineKeyboardButton("🔙 Отмена", callback_data="cancel_country_change"))
    
    await message.answer(
        "Выберите ключ для смены страны:",
        reply_markup=keyboard
    )

async def show_country_change_menu(
    message: types.Message, 
    user_id: int, 
    key_data: Dict[str, Any], 
    user_states_dict: Optional[Dict[int, Dict[str, Any]]] = None
) -> None:
    """
    Показывает меню выбора страны для смены
    
    Отображает список доступных стран для выбранного протокола VPN,
    позволяя пользователю выбрать новую страну для своего ключа.
    
    Args:
        message: Telegram сообщение для отправки меню
        user_id: ID пользователя
        key_data: Словарь с данными ключа, должен содержать:
            - protocol: Протокол VPN ('outline' или 'v2ray')
            - country: Текущая страна сервера
        user_states_dict: Словарь состояний пользователей (опционально,
            если не указан, используется глобальный user_states)
    """
    try:
        # Используем переданный user_states или глобальный
        if user_states_dict is None:
            user_states_dict = user_states
        
        protocol = key_data.get('protocol')
        current_country = key_data.get('country')
        
        if not protocol or not current_country:
            logging.error(f"[COUNTRY CHANGE MENU] Missing protocol or country in key_data: {key_data}")
            await message.answer("Ошибка: неполные данные ключа.", reply_markup=help_keyboard)
            return
        
        # Получаем доступные страны для того же протокола
        available_countries = get_countries_by_protocol(protocol)
        
        # Исключаем текущую страну
        available_countries = [country for country in available_countries if country != current_country]
        
        if not available_countries:
            await message.answer(
                f"К сожалению, для протокола {PROTOCOLS[protocol]['name']} нет других доступных стран.",
                reply_markup=help_keyboard
            )
            return
        
        # Сохраняем данные ключа в состоянии пользователя
        user_states_dict[user_id] = {
            'state': 'country_change_selection',
            'key_data': key_data
        }
        
        # Создаем клавиатуру с доступными странами
        keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
        for country in available_countries:
            keyboard.add(KeyboardButton(f"🌍 {country}"))
        keyboard.add(KeyboardButton("🔙 Назад"))
        
        await message.answer(
            f"Текущая страна: {current_country}\n\n"
            f"Выберите новую страну для протокола {PROTOCOLS[protocol]['name']}:",
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"[COUNTRY CHANGE MENU] Error: {e}", exc_info=True)
        await message.answer("Ошибка при отображении меню смены страны.", reply_markup=help_keyboard)

# Функции change_country_for_key и reissue_specific_key перенесены в bot/services/key_management.py
# Старые версии функций удалены

# Callback handlers управления ключами вынесены в bot/handlers/key_management.py

# Функции broadcast_message, handle_broadcast_command, handle_confirm_broadcast, 
# handle_cancel_broadcast перенесены в bot/handlers/common.py

# Регистрация handlers перенесена в bot/main.py
# Точка входа перенесена в bot/main.py
