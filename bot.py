import asyncio
import time
import sqlite3
import re
import logging
from datetime import datetime
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

# Security configuration
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin"
}

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is not set in config.py")

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot)

# Явная проверка конфигурации при старте
config_validation = validate_configuration()
if not config_validation['is_valid']:
    for err in config_validation['errors']:
        logging.error(f"Config error: {err}")
    raise RuntimeError("Invalid configuration. Check environment variables.")
for warn in config_validation['warnings']:
    logging.warning(f"Config warning: {warn}")

# Инициализация будет выполнена при первом использовании через lazy loading
logging.info("🚀 VeilBot запущен с оптимизацией памяти")

# Simple state management for email collection
user_states = {}  # user_id -> {"state": ..., ...}

# Notification state for key availability
low_key_notified = False

# Главное меню (создаем глобальную переменную для обратной совместимости)
main_menu = get_main_menu()
help_keyboard = get_help_keyboard()
cancel_keyboard = get_cancel_keyboard()

def is_valid_email(email: str) -> bool:
    """Валидация email с использованием нового валидатора"""
    return input_validator.validate_email(email)

@dp.message_handler(commands=["start"])
async def handle_start(message: types.Message):
    args = message.get_args()
    user_id = message.from_user.id
    
    # Save or update user in users table
    with get_db_cursor(commit=True) as cursor:
        now = int(time.time())
        username = message.from_user.username
        first_name = message.from_user.first_name
        last_name = message.from_user.last_name
        
        cursor.execute("""
            INSERT OR REPLACE INTO users 
            (user_id, username, first_name, last_name, created_at, last_active_at, blocked)
            VALUES (?, ?, ?, ?, 
                COALESCE((SELECT created_at FROM users WHERE user_id = ?), ?), 
                ?, 0)
        """, (user_id, username, first_name, last_name, user_id, now, now))
    
    if args and args.isdigit() and int(args) != user_id:
        referrer_id = int(args)
        with get_db_cursor(commit=True) as cursor:
            cursor.execute("SELECT 1 FROM referrals WHERE referred_id = ?", (user_id,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?, ?, ?)",
                    (referrer_id, user_id, int(time.time()))
                )
    # Clear any existing state
    if user_id in user_states:
        del user_states[user_id]
    await message.answer("Нажмите «Купить доступ» для получения доступа", reply_markup=main_menu)

@dp.message_handler(lambda m: m.text == "Купить доступ")
@rate_limit("buy")
async def handle_buy_menu(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_states:
        del user_states[user_id]
    
    # Проверяем наличие доступных протоколов
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT protocol FROM servers 
            WHERE active = 1 AND available_for_purchase = 1
        """)
        available_protocols = [row[0] for row in cursor.fetchall()]
    
    if len(available_protocols) == 0:
        await message.answer(
            "❌ К сожалению, сейчас нет доступных серверов для покупки. "
            "Пожалуйста, попробуйте позже.",
            reply_markup=main_menu
        )
        return
    
    # Если доступен только один протокол - автоматически выбираем его
    if len(available_protocols) == 1:
        protocol = available_protocols[0]
        user_states[user_id] = {
            'state': 'protocol_selected',
            'protocol': protocol
        }
        
        # Получаем страны для этого протокола
        countries = get_countries_by_protocol(protocol)
        
        if not countries:
            await message.answer(
                f"К сожалению, для протокола {PROTOCOLS[protocol]['name']} пока нет доступных серверов.",
                reply_markup=main_menu
            )
            return
        
        # Если доступна только одна страна - автоматически выбираем её
        if len(countries) == 1:
            country = countries[0]
            user_states[user_id] = {
                "state": "waiting_payment_method_after_country",
                "country": country,
                "protocol": protocol
            }
            
            msg = f"💳 *Выберите способ оплаты*\n\n"
            msg += f"{PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}\n"
            msg += f"🌍 Страна: *{country}*\n"
            
            await message.answer(
                msg,
                reply_markup=get_payment_method_keyboard(),
                parse_mode="Markdown"
            )
            return
        
        # Если несколько стран - показываем выбор
        await message.answer(
            "Доступные страны:",
            reply_markup=get_country_menu(countries)
        )
        return
    
    # Если несколько протоколов - показываем выбор
    try:
        await message.answer(
            "Выберите VPN протокол:",
            reply_markup=get_protocol_selection_menu()
        )
    except Exception as e:
        logging.error(f"Error showing protocol selection: {e}")
        await message.answer("❌ Не удалось отобразить выбор протокола. Попробуйте ещё раз.", reply_markup=main_menu)

@dp.message_handler(lambda m: m.text in [f"{PROTOCOLS['outline']['icon']} {PROTOCOLS['outline']['name']}", 
                                        f"{PROTOCOLS['v2ray']['icon']} {PROTOCOLS['v2ray']['name']}"])
async def handle_protocol_selection(message: types.Message):
    """Обработка выбора протокола"""
    user_id = message.from_user.id
    text = message.text or ""
    protocol = 'outline' if ('Outline' in text or 'Outline VPN' in text) else ('v2ray' if 'V2Ray' in text or 'VLESS' in text else 'outline')
    
    # Сохраняем выбор протокола в состоянии пользователя
    user_states[user_id] = {
        'state': 'protocol_selected',
        'protocol': protocol
    }
    
    # Получаем страны только для выбранного протокола
    countries = get_countries_by_protocol(protocol)
    
    if not countries:
        await message.answer(
            f"К сожалению, для протокола {PROTOCOLS[protocol]['name']} пока нет доступных серверов.\n"
            "Попробуйте выбрать другой протокол.",
            reply_markup=get_protocol_selection_menu()
        )
        return
    
    # Если доступна только одна страна - автоматически выбираем её
    if len(countries) == 1:
        country = countries[0]
        user_states[user_id] = {
            "state": "waiting_payment_method_after_country",
            "country": country,
            "protocol": protocol
        }
        
        msg = f"💳 *Выберите способ оплаты*\n\n"
        msg += f"{PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}\n"
        msg += f"🌍 Страна: *{country}*\n"
        
        await message.answer(
            msg,
            reply_markup=get_payment_method_keyboard(),
            parse_mode="Markdown"
        )
        return
    
    # Если несколько стран - показываем выбор
    await message.answer(
        "Доступные страны:",
        reply_markup=get_country_menu(countries)
    )


@dp.message_handler(lambda m: m.text == "🔙 Отмена")
async def handle_cancel(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_states:
        del user_states[user_id]
    await message.answer("Операция отменена. Выберите протокол:", reply_markup=get_protocol_selection_menu())

@dp.message_handler(lambda m: m.text == "Мои ключи")
@rate_limit("keys")
async def handle_my_keys_btn(message: types.Message):
    user_id = message.from_user.id
    now = int(time.time())
    
    all_keys = []
    keys_to_update = []  # Список ключей, для которых нужно обновить конфигурацию в БД
    
    with get_db_cursor() as cursor:
        # Получаем Outline ключи с информацией о стране
        cursor.execute("""
            SELECT k.access_url, k.expiry_at, k.protocol, s.country
            FROM keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.expiry_at > ?
        """, (user_id, now))
        outline_keys = cursor.fetchall()
        
        # Получаем V2Ray ключи с информацией о стране и сервере, включая сохраненную конфигурацию
        cursor.execute("""
            SELECT k.v2ray_uuid, k.expiry_at, s.domain, s.v2ray_path, s.country, k.email, s.api_url, s.api_key, k.client_config
            FROM v2ray_keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.expiry_at > ?
        """, (user_id, now))
        v2ray_keys = cursor.fetchall()
    
    # Добавляем Outline ключи
    for access_url, exp, protocol, country in outline_keys:
        all_keys.append({
            'type': 'outline',
            'config': access_url,
            'expiry': exp,
            'protocol': protocol or 'outline',
            'country': country
        })
    
    # Добавляем V2Ray ключи
    for v2ray_uuid, exp, domain, path, country, email, api_url, api_key, saved_config in v2ray_keys:
        # Используем сохраненную конфигурацию из БД, если она есть
        if saved_config:
            # Извлекаем VLESS URL из сохраненной конфигурации, если она многострочная
            if 'vless://' in saved_config:
                lines = saved_config.split('\n')
                for line in lines:
                    if line.strip().startswith('vless://'):
                        config = line.strip()
                        break
                else:
                    config = saved_config.strip()
            else:
                config = saved_config.strip()
            logging.debug(f"Using saved client_config from DB for UUID {v2ray_uuid[:8]}...")
        else:
            # Если сохраненной конфигурации нет, запрашиваем с сервера (fallback)
            try:
                if api_url and api_key:
                    server_config = {'api_url': api_url, 'api_key': api_key}
                    protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                    config = await protocol_client.get_user_config(v2ray_uuid, {
                        'domain': domain,
                        'port': 443,
                        'path': path or '/v2ray',
                        'email': email or f"user_{user_id}@veilbot.com"
                    })
                    # Извлекаем VLESS URL, если конфигурация многострочная
                    if 'vless://' in config:
                        lines = config.split('\n')
                        for line in lines:
                            if line.strip().startswith('vless://'):
                                config = line.strip()
                                break
                    # Сохраняем для обновления в БД
                    keys_to_update.append((config, v2ray_uuid))
                    logging.info(f"Retrieved client_config from server for UUID {v2ray_uuid[:8]}..., will save to DB")
                else:
                    # Fallback к старому формату если нет данных сервера
                    config = f"vless://{v2ray_uuid}@{domain}:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email or 'VeilBot-V2Ray'}"
            except Exception as e:
                logging.error(f"Error getting V2Ray config for {v2ray_uuid}: {e}")
                # Fallback к старому формату при ошибке
                config = f"vless://{v2ray_uuid}@{domain}:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email or 'VeilBot-V2Ray'}"
        
        all_keys.append({
            'type': 'v2ray',
            'config': config,
            'expiry': exp,
            'protocol': 'v2ray',
            'country': country
        })
    
    # Обновляем конфигурации в БД, если нужно
    if keys_to_update:
        with get_db_cursor(commit=True) as cursor:
            for config, v2ray_uuid in keys_to_update:
                cursor.execute("UPDATE v2ray_keys SET client_config = ? WHERE v2ray_uuid = ?", (config, v2ray_uuid))

    if not all_keys:
        await message.answer("У вас нет активных ключей.", reply_markup=main_menu)
        return

    msg = "*Ваши активные ключи:*\n\n"
    for key in all_keys:
        remaining_seconds = key['expiry'] - now
        time_str = format_duration(remaining_seconds)
        
        protocol_info = PROTOCOLS[key['protocol']]
        
        # Получаем ссылки на приложения в зависимости от протокола
        if key['protocol'] == 'outline':
            app_links = "📱 [App Store](https://apps.apple.com/app/outline-app/id1356177741) | [Google Play](https://play.google.com/store/apps/details?id=org.outline.android.client)"
        else:  # v2ray
            app_links = "📱 [App Store](https://apps.apple.com/ru/app/v2raytun/id6476628951) | [Google Play](https://play.google.com/store/apps/details?id=com.v2raytun.android)"
            
        msg += (
            f"{protocol_info['icon']} *{protocol_info['name']}*\n"
            f"🌍 Страна: {key['country']}\n"
            f"`{key['config']}`\n"
            f"⏳ Осталось времени: {time_str}\n"
            f"{app_links}\n\n"
        )
    
    await message.answer(msg, reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "🔙 Назад")
async def back_to_main(message: types.Message):
    # Clear any existing state
    user_id = message.from_user.id
    if user_id in user_states:
        del user_states[user_id]
    await message.answer("Главное меню:", reply_markup=main_menu)

# --- Обработчик кнопки 'Пригласить друга' (теперь выше всех универсальных) ---
@dp.message_handler(lambda m: m.text == "Получить месяц бесплатно")
async def handle_invite_friend(message: types.Message):
    logging.debug(f"handle_invite_friend called: user_id={message.from_user.id}")
    user_id = message.from_user.id
    bot_username = (await bot.get_me()).username
    invite_link = f"https://t.me/{bot_username}?start={user_id}"
    await bot.send_message(
        message.chat.id,
        f"Пригласите друга по этой ссылке:\n{invite_link}\n\nЕсли друг купит доступ, вы получите месяц бесплатно!",
        reply_markup=main_menu
    )

# Handle payment method selection (новый flow: после выбора страны)
@dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "waiting_payment_method_after_country")
async def handle_payment_method_after_country(message: types.Message):
    """Обработка выбора способа оплаты после выбора страны (новый flow)"""
    user_id = message.from_user.id
    text = message.text.strip()
    state = user_states.get(user_id, {})
    
    if text == "🔙 Назад":
        # Возвращаемся к выбору страны
        protocol = state.get("protocol", "outline")
        countries = get_countries_by_protocol(protocol) if protocol else get_countries()
        if protocol:
            protocol_info = PROTOCOLS.get(protocol, {"name": protocol})
            await message.answer(
                f"Выберите страну для {protocol_info['name']}:",
                reply_markup=get_country_menu(countries)
            )
            user_states[user_id] = {"state": "protocol_selected", "protocol": protocol}
        else:
            await message.answer("Выберите сервер:", reply_markup=get_country_menu(countries))
            user_states[user_id] = {"state": "waiting_country"}
        return
    
    if text == "💳 Карта РФ / СБП":
        # Сохраняем способ оплаты и переходим к выбору тарифа
        state["payment_method"] = "yookassa"
        state["state"] = "waiting_tariff"
        user_states[user_id] = state
        
        country = state.get("country", "")
        protocol = state.get("protocol", "outline")
        
        msg = f"💳 *Оплата картой / СБП*\n\n"
        if protocol:
            msg += f"{PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}\n"
        msg += f"🌍 Страна: *{country}*\n\n"
        msg += "📦 Выберите тариф:"
        
        # Для продления скрываем бесплатные тарифы
        paid_only = state.get("paid_only", False)
        
        await message.answer(
            msg,
            reply_markup=get_tariff_menu(payment_method="yookassa", paid_only=paid_only),
            parse_mode="Markdown"
        )
        return
    
    if text == "₿ Криптовалюта (USDT)":
        # Проверяем, есть ли тарифы с крипто-ценами
        with get_db_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM tariffs WHERE price_rub > 0 AND price_crypto_usd IS NOT NULL AND price_crypto_usd > 0")
            count = cursor.fetchone()[0]
            
            if count == 0:
                await message.answer(
                    "❌ К сожалению, в данный момент нет тарифов с оплатой криптовалютой.\n\n"
                    "Пожалуйста, выберите другой способ оплаты или обратитесь в поддержку.",
                    reply_markup=get_payment_method_keyboard()
                )
                return
        
        # Сохраняем способ оплаты и переходим к выбору тарифа
        state["payment_method"] = "cryptobot"
        state["state"] = "waiting_tariff"
        user_states[user_id] = state
        
        country = state.get("country", "")
        protocol = state.get("protocol", "outline")
        
        msg = f"₿ *Оплата криптовалютой (USDT)*\n\n"
        if protocol:
            msg += f"{PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}\n"
        msg += f"🌍 Страна: *{country}*\n\n"
        msg += "📦 Выберите тариф:"
        
        # Для продления скрываем бесплатные тарифы
        paid_only = state.get("paid_only", False)
        tariff_menu = get_tariff_menu(payment_method="cryptobot", paid_only=paid_only)
        
        # Проверяем, есть ли тарифы в меню (кроме кнопки "Назад")
        if len(tariff_menu.keyboard) <= 1:  # Только кнопка "Назад"
            await message.answer(
                "❌ К сожалению, в данный момент нет доступных тарифов с оплатой криптовалютой.\n\n"
                "Пожалуйста, выберите другой способ оплаты.",
                reply_markup=get_payment_method_keyboard()
            )
            return
        
        await message.answer(
            msg,
            reply_markup=tariff_menu,
            parse_mode="Markdown"
        )
        return
    
    await message.answer(
        "Пожалуйста, выберите способ оплаты из списка:",
        reply_markup=get_payment_method_keyboard()
    )

# Handle payment method selection (старый flow: после выбора тарифа - для обратной совместимости)
@dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "waiting_payment_method")
async def handle_payment_method_input(message: types.Message):
    """Обработка выбора способа оплаты после выбора тарифа (старый flow для обратной совместимости)"""
    user_id = message.from_user.id
    text = message.text.strip()
    state = user_states.get(user_id, {})
    
    if text == "🔙 Назад":
        # Возвращаемся к выбору тарифа
        payment_method = state.get("payment_method")
        await message.answer("Выберите тариф:", reply_markup=get_tariff_menu(payment_method=payment_method))
        return
    
    if text == "💳 Карта РФ / СБП":
        # Сохраняем способ оплаты и переходим к запросу email
        state["payment_method"] = "yookassa"
        user_states[user_id] = state
        user_states[user_id]["state"] = "waiting_email"
        
        tariff = state.get("tariff", {})
        protocol = state.get("protocol", "outline")
        
        await message.answer(
            f"💳 *Оплата картой / СБП*\n\n"
            f"📦 Тариф: *{tariff.get('name', 'Неизвестно')}*\n"
            f"💰 Сумма: *{tariff.get('price_rub', 0)}₽*\n\n"
            "📧 Пожалуйста, введите ваш email адрес для получения чека:",
            reply_markup=cancel_keyboard,
            parse_mode="Markdown"
        )
        return
    
    if text == "₿ Криптовалюта (USDT)":
        tariff = state.get("tariff", {})
        if not tariff.get('price_crypto_usd'):
            await message.answer(
                "❌ Крипто-оплата недоступна для этого тарифа. Выберите другой способ оплаты.",
                reply_markup=get_payment_method_keyboard()
            )
            return
        
        # Сохраняем способ оплаты и переходим к запросу email
        state["payment_method"] = "cryptobot"
        user_states[user_id] = state
        user_states[user_id]["state"] = "waiting_email"
        
        protocol = state.get("protocol", "outline")
        
        await message.answer(
            f"₿ *Оплата криптовалютой (USDT)*\n\n"
            f"📦 Тариф: *{tariff.get('name', 'Неизвестно')}*\n"
            f"💰 Сумма: *${tariff.get('price_crypto_usd', 0):.2f} USDT*\n\n"
            "📧 Пожалуйста, введите ваш email адрес для получения чека:",
            reply_markup=cancel_keyboard,
            parse_mode="Markdown"
        )
        return
    
    await message.answer(
        "Пожалуйста, выберите способ оплаты из списка:",
        reply_markup=get_payment_method_keyboard()
    )

# Handle email input
@dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "waiting_email")
async def handle_email_input(message: types.Message):
    if message.text == "Получить месяц бесплатно":
        user_id = message.from_user.id
        user_states.pop(user_id, None)
        await handle_invite_friend(message)
        return
    
    user_id = message.from_user.id
    email = message.text.strip()
    
    # Валидация и очистка email
    try:
        # Проверяем на SQL инъекции
        if not input_validator.validate_sql_injection(email):
            await message.answer("❌ Email содержит недопустимые символы.", reply_markup=cancel_keyboard)
            return
        
        # Очищаем email от потенциально опасных символов
        email = input_validator.sanitize_string(email, max_length=100)
        
        # Валидируем формат email
        if not is_valid_email(email):
            await message.answer("❌ Неверный формат email. Пожалуйста, введите корректный email адрес:", reply_markup=cancel_keyboard)
            return
        
        logging.debug(f"handle_email_input called: user_id={user_id}, email={email}, state={user_states.get(user_id)}")
        
        state = user_states.get(user_id, {})
        tariff = state.get("tariff")
        country = state.get("country")
        protocol = state.get("protocol", "outline")
        payment_method = state.get("payment_method", "yookassa")  # По умолчанию YooKassa
        del user_states[user_id]
        
        # Создаем платеж с указанным email
        await create_payment_with_email_and_protocol(message, user_id, tariff, email, country, protocol, payment_method=payment_method)
        
    except ValidationError as e:
        await message.answer(f"❌ Ошибка валидации: {str(e)}", reply_markup=cancel_keyboard)
    except Exception as e:
        await BotErrorHandler.handle_error(message, e, "handle_email_input", bot, ADMIN_ID)

@dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "reactivation_country_selection")
async def handle_reactivation_country_selection(message: types.Message):
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

@dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "country_change_selection")
async def handle_country_change_selection(message: types.Message):
    """Обработчик выбора страны для смены"""
    user_id = message.from_user.id
    text = message.text or ""
    
    # Проверяем, что это кнопка "Назад"
    if text == "🔙 Назад":
        user_states.pop(user_id, None)
        await message.answer("Главное меню:", reply_markup=main_menu)
        return
    
    # Извлекаем название страны из текста (убираем эмодзи)
    if text.startswith("🌍 "):
        selected_country = text[2:]  # Убираем "🌍 "
    else:
        selected_country = text
    
    # Получаем данные ключа из состояния
    state = user_states.get(user_id, {})
    key_data = state.get("key_data")
    
    if not key_data:
        await message.answer("Ошибка: данные ключа не найдены. Попробуйте еще раз.", reply_markup=main_menu)
        return
    
    # Очищаем состояние
    user_states.pop(user_id, None)
    
    # Выполняем смену страны
    await change_country_for_key(message, user_id, key_data, selected_country)

@dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "waiting_country")
async def handle_country_selection(message: types.Message):
    if message.text == "Получить месяц бесплатно":
        user_id = message.from_user.id
        user_states.pop(user_id, None)
        await handle_invite_friend(message)
        return
    
    user_id = message.from_user.id
    country = message.text.strip()
    
    # Валидация и очистка названия страны
    try:
        # Проверяем на SQL инъекции
        if not input_validator.validate_sql_injection(country):
            await message.answer("❌ Название страны содержит недопустимые символы.", reply_markup=cancel_keyboard)
            return
        
        # Очищаем название страны
        country = input_validator.sanitize_string(country, max_length=50)
        
        # Валидируем формат названия страны
        if not input_validator.validate_country(country):
            await message.answer("❌ Неверное название страны.", reply_markup=cancel_keyboard)
            return
        
        countries = get_countries()
        if country not in countries:
            await message.answer("Пожалуйста, выберите сервер из списка:", reply_markup=get_country_menu(countries))
            return
        
        # Сохраняем страну и переходим к выбору способа оплаты
        user_states[user_id] = {"state": "waiting_payment_method_after_country", "country": country}
        
        msg = f"💳 *Выберите способ оплаты*\n\n"
        msg += f"🌍 Страна: *{country}*\n"
        
        await message.answer(
            msg,
            reply_markup=get_payment_method_keyboard(),
            parse_mode="Markdown"
        )
        
    except ValidationError as e:
        await message.answer(f"❌ Ошибка валидации: {str(e)}", reply_markup=cancel_keyboard)
    except Exception as e:
        await BotErrorHandler.handle_error(message, e, "handle_country_selection", bot, ADMIN_ID)

@dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "protocol_selected")
async def handle_protocol_country_selection(message: types.Message):
    """Обработка выбора страны после выбора протокола"""
    try:
        if message.text == "🔙 Назад":
            user_id = message.from_user.id
            user_states.pop(user_id, None)
            await message.answer("Выберите протокол:", reply_markup=get_protocol_selection_menu())
            return
        
        if message.text == "Получить месяц бесплатно":
            user_id = message.from_user.id
            user_states.pop(user_id, None)
            await handle_invite_friend(message)
            return
        
        user_id = message.from_user.id
        user_state = user_states.get(user_id, {})
        country = (message.text or "").strip()
        protocol = user_state.get("protocol", "outline")
        
        # Получаем страны только для выбранного протокола
        countries = get_countries_by_protocol(protocol)
        
        # Если доступна только одна страна и она не выбрана явно - автоматически выбираем её
        if len(countries) == 1 and country not in countries:
            country = countries[0]
            logging.info(f"Auto-selecting single available country: {country} for protocol {protocol}")
        
        if country not in countries:
            protocol_info = PROTOCOLS.get(protocol, {"name": protocol})
            await message.answer(
                f"Пожалуйста, выберите страну из списка для {protocol_info['name']}:", 
                reply_markup=get_country_menu(countries)
            )
            return
        
        # Сохраняем страну и переходим к выбору способа оплаты
        user_states[user_id] = {
            "state": "waiting_payment_method_after_country", 
            "country": country,
            "protocol": protocol
        }
        
        msg = f"💳 *Выберите способ оплаты*\n\n"
        msg += f"{PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}\n"
        msg += f"🌍 Страна: *{country}*\n"
        
        await message.answer(
            msg,
            reply_markup=get_payment_method_keyboard(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Error in handle_protocol_country_selection: {e}")
        await message.answer("❌ Произошла ошибка. Попробуйте ещё раз или выберите протокол заново.", reply_markup=get_protocol_selection_menu())

@dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "waiting_tariff" and "—" in m.text and any(w in m.text for w in ["₽", "$", "бесплатно"]))
async def handle_tariff_selection_with_country(message: types.Message):
    if message.text == "Получить месяц бесплатно":
        user_id = message.from_user.id
        user_states.pop(user_id, None)
        await handle_invite_friend(message)
        return
    
    if message.text == "🔙 Назад":
        # Возвращаемся к выбору способа оплаты
        user_id = message.from_user.id
        state = user_states.get(user_id, {})
        country = state.get("country")
        protocol = state.get("protocol", "outline")
        
        state["state"] = "waiting_payment_method_after_country"
        user_states[user_id] = state
        
        msg = f"💳 *Выберите способ оплаты*\n\n"
        if protocol:
            msg += f"{PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}\n"
        msg += f"🌍 Страна: *{country}*\n"
        
        await message.answer(
            msg,
            reply_markup=get_payment_method_keyboard(),
            parse_mode="Markdown"
        )
        return
    
    user_id = message.from_user.id
    label = message.text.strip()
    state = user_states.get(user_id, {})
    country = state.get("country")
    protocol = state.get("protocol", "outline")
    payment_method = state.get("payment_method")  # Получаем выбранный способ оплаты
    
    # Parse tariff name and price from the label
    parts = label.split("—")
    if len(parts) != 2:
        await message.answer("Неверный формат тарифа.", reply_markup=main_menu)
        return
    tariff_name = parts[0].strip()
    price_part = parts[1].strip()
    
    if "бесплатно" in price_part:
        # Если пользователь в сценарии продления, не разрешаем бесплатные тарифы
        if user_states.get(user_id, {}).get("paid_only"):
            await message.answer("Для продления доступны только платные тарифы. Выберите платный тариф.", reply_markup=get_tariff_menu(paid_only=True, payment_method=payment_method))
            return
        price = 0
        price_crypto = None
    else:
        # Парсим цену в зависимости от способа оплаты
        if payment_method == "cryptobot":
            # Для криптовалюты парсим цену в USD
            try:
                price_crypto = float(price_part.replace("$", "").strip())
                # Для поиска тарифа используем price_crypto, но нужно найти по имени и крипто-цене
                price = None
            except ValueError:
                await message.answer("Неверный формат цены.", reply_markup=main_menu)
                return
        else:
            # Для карты/СБП парсим рублевую цену
            try:
                price = int(price_part.replace("₽", "").strip())
                price_crypto = None
            except ValueError:
                await message.answer("Неверный формат цены.", reply_markup=main_menu)
                return
    
    with get_db_cursor(commit=True) as cursor:
        # Ищем тариф в зависимости от способа оплаты
        if payment_method == "cryptobot" and price_crypto is not None:
            # Ищем по имени и крипто-цене
            cursor.execute("SELECT id, name, price_rub, duration_sec, price_crypto_usd FROM tariffs WHERE name = ? AND ABS(price_crypto_usd - ?) < 0.01", (tariff_name, price_crypto))
            row = cursor.fetchone()
            if row:
                tariff = {
                    "id": row[0],
                    "name": row[1],
                    "price_rub": row[2],
                    "duration_sec": row[3],
                    "price_crypto_usd": row[4] if len(row) > 4 else None
                }
            else:
                await message.answer("Не удалось найти тариф с указанной ценой.", reply_markup=main_menu)
                return
        else:
            tariff = get_tariff_by_name_and_price(cursor, tariff_name, price or 0)
        if not tariff:
            await message.answer("Не удалось найти тариф.", reply_markup=main_menu)
            return
        if tariff['price_rub'] == 0 and not user_states.get(user_id, {}).get("paid_only"):
            await handle_free_tariff_with_protocol(cursor, message, user_id, tariff, country, protocol)
        else:
            # Если это сценарий продления, не запрашиваем email — используем email из БД
            if user_states.get(user_id, {}).get("paid_only"):
                email_db = None
                try:
                    now_ts = int(time.time())
                    
                    # Сначала пытаемся получить email из таблицы payments (приоритет)
                    cursor.execute("SELECT email FROM payments WHERE user_id = ? AND email IS NOT NULL AND email != '' AND email NOT LIKE 'user_%@veilbot.com' ORDER BY created_at DESC LIMIT 1", (user_id,))
                    row = cursor.fetchone()
                    email_db = row[0] if row and row[0] else None
                    
                    # Если не нашли в payments, пытаемся получить из текущего протокола, если outline
                    # Исключаем автоматически сгенерированные email-ы вида user_123@veilbot.com
                    if not email_db and (protocol or 'outline') == 'outline':
                        cursor.execute("SELECT email FROM keys WHERE user_id = ? AND email IS NOT NULL AND email != '' AND email NOT LIKE 'user_%@veilbot.com' ORDER BY expiry_at DESC LIMIT 1", (user_id,))
                        row = cursor.fetchone()
                        email_db = row[0] if row and row[0] else None
                    
                    # Фолбэк: пробуем взять из v2ray_keys, также исключаем автоматически сгенерированные
                    if not email_db:
                        cursor.execute("SELECT email FROM v2ray_keys WHERE user_id = ? AND email IS NOT NULL AND email != '' AND email NOT LIKE 'user_%@veilbot.com' ORDER BY expiry_at DESC LIMIT 1", (user_id,))
                        row2 = cursor.fetchone()
                        email_db = row2[0] if row2 and row2[0] else None
                except Exception:
                    email_db = None

                if not email_db:
                    email_db = f"user_{user_id}@veilbot.com"

                # Для продления сразу создаем платеж с выбранным способом оплаты
                # Используем выбранный способ оплаты из состояния
                renewal_payment_method = payment_method or "yookassa"  # По умолчанию YooKassa, если не указан
                # Сбрасываем временное состояние выбора тарифа
                user_states[user_id] = {}
                await create_payment_with_email_and_protocol(message, user_id, tariff, email_db, country, protocol, payment_method=renewal_payment_method, for_renewal=True)
            else:
                # Для новой покупки сразу переходим к запросу email (способ оплаты уже выбран)
                user_states[user_id]["tariff"] = tariff
                user_states[user_id]["state"] = "waiting_email"
                
                if payment_method == "cryptobot":
                    if not tariff.get('price_crypto_usd'):
                        await message.answer(
                            "❌ Крипто-оплата недоступна для этого тарифа. Выберите другой тариф.",
                            reply_markup=get_tariff_menu(payment_method="cryptobot")
                        )
                        return
                    
                    await message.answer(
                        f"₿ *Оплата криптовалютой (USDT)*\n\n"
                        f"📦 Тариф: *{tariff.get('name', 'Неизвестно')}*\n"
                        f"💰 Сумма: *${tariff.get('price_crypto_usd', 0):.2f} USDT*\n\n"
                        "📧 Пожалуйста, введите ваш email адрес для получения чека:",
                        reply_markup=cancel_keyboard,
                        parse_mode="Markdown"
                    )
                else:
                    await message.answer(
                        f"💳 *Оплата картой / СБП*\n\n"
                        f"📦 Тариф: *{tariff.get('name', 'Неизвестно')}*\n"
                        f"💰 Сумма: *{tariff.get('price_rub', 0)}₽*\n\n"
                        "📧 Пожалуйста, введите ваш email адрес для получения чека:",
                        reply_markup=cancel_keyboard,
                        parse_mode="Markdown"
                    )

def get_tariff_by_name_and_price(cursor, tariff_name, price):
    cursor.execute("SELECT id, name, price_rub, duration_sec, price_crypto_usd FROM tariffs WHERE name = ? AND price_rub = ?", (tariff_name, price))
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "price_rub": row[2],
        "duration_sec": row[3],
        "price_crypto_usd": row[4] if len(row) > 4 else None
    }

async def handle_free_tariff(cursor, message, user_id, tariff, country=None):
    if check_free_tariff_limit(cursor, user_id):
        await message.answer("Вы уже получали бесплатный тариф ранее. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
        return
    now = int(time.time())
    # Проверяем наличие активного ключа и его тип
    cursor.execute("""
        SELECT k.id, k.expiry_at, t.price_rub
        FROM keys k
        JOIN tariffs t ON k.tariff_id = t.id
        WHERE k.user_id = ? AND k.expiry_at > ?
        ORDER BY k.expiry_at DESC LIMIT 1
    """, (user_id, now))
    existing_key = cursor.fetchone()
    if existing_key:
        if existing_key[2] > 0:
            await message.answer("У вас уже есть активный платный ключ. Бесплатный ключ можно получить только если нет активных платных ключей.", reply_markup=main_menu)
            return
        else:
            await message.answer("У вас уже есть активный бесплатный ключ. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
            return
    else:
        await create_new_key_flow(cursor, message, user_id, tariff, None, country)

def check_free_tariff_limit(cursor, user_id):
    """Проверка лимита бесплатных ключей - один раз навсегда (для обратной совместимости)"""
    return check_free_tariff_limit_by_protocol_and_country(cursor, user_id, "outline")

def check_free_tariff_limit_by_protocol_and_country(cursor, user_id, protocol="outline", country=None):
    """Проверка лимита бесплатных ключей для конкретного протокола и страны - один раз навсегда"""
    
    # Проверяем в таблице free_key_usage - это основная проверка
    # Сначала проверяем, получал ли пользователь бесплатный ключ для этого протокола вообще
    cursor.execute("""
        SELECT created_at FROM free_key_usage 
        WHERE user_id = ? AND protocol = ?
    """, (user_id, protocol))
    
    row = cursor.fetchone()
    if row:
        return True  # Пользователь уже получал бесплатный ключ для этого протокола
    
    # Если указана конкретная страна, дополнительно проверяем для неё
    if country:
        cursor.execute("""
            SELECT created_at FROM free_key_usage 
            WHERE user_id = ? AND protocol = ? AND country = ?
        """, (user_id, protocol, country))
        
        row = cursor.fetchone()
        if row:
            return True  # Пользователь уже получал бесплатный ключ для этого протокола и страны
    
    # Дополнительная проверка в таблицах ключей (для обратной совместимости)
    if protocol == "outline":
        if country:
            cursor.execute("""
                SELECT k.created_at FROM keys k
                JOIN tariffs t ON k.tariff_id = t.id
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND t.price_rub = 0 AND s.country = ?
                ORDER BY k.created_at DESC LIMIT 1
            """, (user_id, country))
        else:
            cursor.execute("""
                SELECT k.created_at FROM keys k
                JOIN tariffs t ON k.tariff_id = t.id
                WHERE k.user_id = ? AND t.price_rub = 0
                ORDER BY k.created_at DESC LIMIT 1
            """, (user_id,))
    else:  # v2ray
        if country:
            cursor.execute("""
                SELECT k.created_at FROM v2ray_keys k
                JOIN tariffs t ON k.tariff_id = t.id
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND t.price_rub = 0 AND s.country = ?
                ORDER BY k.created_at DESC LIMIT 1
            """, (user_id, country))
        else:
            cursor.execute("""
                SELECT k.created_at FROM v2ray_keys k
                JOIN tariffs t ON k.tariff_id = t.id
                WHERE k.user_id = ? AND t.price_rub = 0
                ORDER BY k.created_at DESC LIMIT 1
            """, (user_id,))
    
    row = cursor.fetchone()
    # Если найден любой бесплатный ключ — нельзя (только один раз навсегда)
    if row:
        return True
    # Иначе можно
    return False

def check_free_tariff_limit_by_protocol(cursor, user_id, protocol="outline"):
    """Проверка лимита бесплатных ключей для конкретного протокола - один раз навсегда (для обратной совместимости)"""
    return check_free_tariff_limit_by_protocol_and_country(cursor, user_id, protocol)

def record_free_key_usage(cursor, user_id, protocol="outline", country=None):
    """Записывает использование бесплатного ключа пользователем"""
    now = int(time.time())
    try:
        cursor.execute("""
            INSERT INTO free_key_usage (user_id, protocol, country, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, protocol, country, now))
        return True
    except sqlite3.IntegrityError:
        # Запись уже существует (UNIQUE constraint)
        return False
    except Exception as e:
        logging.error(f"Failed to record free key usage: {e}")
        return False

def check_server_availability(api_url, cert_sha256, protocol='outline'):
    """Проверяет доступность сервера"""
    try:
        if protocol == 'outline':
            # Для Outline проверяем доступность API
            import requests
            response = requests.get(f"{api_url}/access-keys", verify=False, timeout=10)
            return response.status_code == 200
        elif protocol == 'v2ray':
            # Для V2Ray проверяем доступность API
            import requests
            response = requests.get(f"{api_url}/", verify=False, timeout=10)
            return response.status_code == 200
        return False
    except Exception as e:
        logging.warning(f"Server availability check failed: {e}")
        return False

def find_alternative_server(cursor, country, protocol, exclude_server_id=None):
    """Находит альтернативный сервер той же страны и протокола"""
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

async def extend_existing_key_with_fallback(cursor, existing_key, duration, email=None, tariff_id=None, protocol='outline'):
    """Продлевает существующий ключ с fallback на альтернативный сервер"""
    now = int(time.time())
    # Если ключ истёк, продляем от текущего времени, иначе от старого expiry_at
    if existing_key[1] <= now:
        new_expiry = now + duration
        logging.info(f"Extending expired key {existing_key[0]} ({protocol}): was expired at {existing_key[1]}, new expiry: {new_expiry}")
    else:
        new_expiry = existing_key[1] + duration
    
    # Получаем информацию о текущем сервере в зависимости от протокола
    # При продлении проверяем только active, не проверяем available_for_purchase
    if protocol == 'outline':
        cursor.execute("SELECT server_id FROM keys WHERE id = ?", (existing_key[0],))
        server_id = cursor.fetchone()[0]
        
        cursor.execute("SELECT api_url, cert_sha256, country, active FROM servers WHERE id = ?", (server_id,))
        server_data = cursor.fetchone()
        
        if not server_data:
            logging.error(f"Server {server_id} not found for key {existing_key[0]}")
            return False
        
        api_url, cert_sha256, country, is_active = server_data
        
        # Если сервер неактивен, пытаемся найти альтернативный
        if not is_active:
            logging.warning(f"Server {server_id} is not active, looking for alternative for renewal")
            alternative_server = find_alternative_server(cursor, country, protocol, server_id)
            
            if not alternative_server:
                logging.error(f"No alternative active server found for country={country}, protocol={protocol}")
                return False
            
            alt_server_id, alt_name, alt_api_url, alt_cert_sha256, alt_domain, alt_api_key, alt_v2ray_path = alternative_server
            
            # Проверяем доступность альтернативного сервера
            if not check_server_availability(alt_api_url, alt_cert_sha256, protocol):
                logging.error(f"Alternative server {alt_server_id} is not available")
                return False
            
            # Переходим к логике создания ключа на альтернативном сервере
            try:
                key = await asyncio.get_event_loop().run_in_executor(None, create_key, alt_api_url, alt_cert_sha256)
                if not key:
                    logging.error(f"Failed to create key on alternative server {alt_server_id}")
                    return False
                
                # Обновляем ключ в базе данных
                cursor.execute("""
                    UPDATE keys 
                    SET server_id = ?, access_url = ?, key_id = ?, expiry_at = ?, email = ?, tariff_id = ?
                    WHERE id = ?
                """, (alt_server_id, key['accessUrl'], key['id'], new_expiry, email or '', tariff_id or 0, existing_key[0]))
                
                logging.info(f"Key {existing_key[0]} moved to alternative active server {alt_server_id} ({alt_name}) for renewal")
                return True
            except Exception as e:
                logging.error(f"Error creating key on alternative server: {e}")
                return False
    else:  # v2ray
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
            
            # Проверяем доступность альтернативного сервера
            if not check_server_availability(alt_api_url, None, protocol):
                logging.error(f"Alternative server {alt_server_id} is not available")
                return False
            
            # Переходим к логике создания ключа на альтернативном сервере
            try:
                from vpn_protocols import V2RayProtocol
                v2ray_client = V2RayProtocol(alt_api_url, alt_api_key)
                user_data = await v2ray_client.create_user(email or f"user_{existing_key[0]}@veilbot.com")
                
                if not user_data or not user_data.get('uuid'):
                    logging.error(f"Failed to create V2Ray key on alternative server {alt_server_id}")
                    return False
                
                # Обновляем ключ в базе данных
                cursor.execute("""
                    UPDATE v2ray_keys 
                    SET server_id = ?, v2ray_uuid = ?, expiry_at = ?, email = ?, tariff_id = ?
                    WHERE id = ?
                """, (alt_server_id, user_data['uuid'], new_expiry, email or '', tariff_id or 0, existing_key[0]))
                
                logging.info(f"V2Ray key {existing_key[0]} moved to alternative active server {alt_server_id} ({alt_name}) for renewal")
                return True
            except Exception as e:
                logging.error(f"Error creating V2Ray key on alternative server: {e}")
                return False
    
    # Если сервер активен, проверяем его доступность через API
    if check_server_availability(api_url, cert_sha256, protocol):
        # Сервер доступен, просто продлеваем
        if protocol == 'outline':
            if email and tariff_id:
                cursor.execute("UPDATE keys SET expiry_at = ?, email = ?, tariff_id = ? WHERE id = ?", (new_expiry, email, tariff_id, existing_key[0]))
            elif email:
                cursor.execute("UPDATE keys SET expiry_at = ?, email = ? WHERE id = ?", (new_expiry, email, existing_key[0]))
            elif tariff_id:
                cursor.execute("UPDATE keys SET expiry_at = ?, tariff_id = ? WHERE id = ?", (new_expiry, tariff_id, existing_key[0]))
            else:
                cursor.execute("UPDATE keys SET expiry_at = ? WHERE id = ?", (new_expiry, existing_key[0]))
        else:  # v2ray
            if email and tariff_id:
                cursor.execute("UPDATE v2ray_keys SET expiry_at = ?, email = ?, tariff_id = ? WHERE id = ?", (new_expiry, email, tariff_id, existing_key[0]))
            elif email:
                cursor.execute("UPDATE v2ray_keys SET expiry_at = ?, email = ? WHERE id = ?", (new_expiry, email, existing_key[0]))
            elif tariff_id:
                cursor.execute("UPDATE v2ray_keys SET expiry_at = ?, tariff_id = ? WHERE id = ?", (new_expiry, tariff_id, existing_key[0]))
            else:
                cursor.execute("UPDATE v2ray_keys SET expiry_at = ? WHERE id = ?", (new_expiry, existing_key[0]))
        return True
    else:
        # Сервер недоступен, ищем альтернативный
        logging.warning(f"Server {server_id} is not available, looking for alternative")
        alternative_server = find_alternative_server(cursor, country, protocol, server_id)
        
        if not alternative_server:
            logging.error(f"No alternative server found for country={country}, protocol={protocol}")
            return False
        
        alt_server_id, alt_name, alt_api_url, alt_cert_sha256, alt_domain, alt_api_key, alt_v2ray_path = alternative_server
        
        # Проверяем доступность альтернативного сервера
        if not check_server_availability(alt_api_url, alt_cert_sha256, protocol):
            logging.error(f"Alternative server {alt_server_id} is also not available")
            return False
        
        try:
            # Создаем новый ключ на альтернативном сервере
            if protocol == 'outline':
                key = await asyncio.get_event_loop().run_in_executor(None, create_key, alt_api_url, alt_cert_sha256)
                if not key:
                    logging.error(f"Failed to create key on alternative server {alt_server_id}")
                    return False
                
                # Обновляем ключ в базе данных
                cursor.execute("""
                    UPDATE keys 
                    SET server_id = ?, access_url = ?, key_id = ?, expiry_at = ?, email = ?, tariff_id = ?
                    WHERE id = ?
                """, (alt_server_id, key['accessUrl'], key['id'], new_expiry, email or '', tariff_id or 0, existing_key[0]))
                
                logging.info(f"Key {existing_key[0]} moved to alternative server {alt_server_id} ({alt_name})")
                return True
                
            elif protocol == 'v2ray':
                # Для V2Ray создаем новый ключ
                from vpn_protocols import V2RayProtocol
                v2ray_client = V2RayProtocol(alt_api_url, alt_api_key)
                user_data = await v2ray_client.create_user(email or f"user_{existing_key[0]}@veilbot.com")
                
                if not user_data or not user_data.get('uuid'):
                    logging.error(f"Failed to create V2Ray key on alternative server {alt_server_id}")
                    return False
                
                # Обновляем ключ в базе данных
                cursor.execute("""
                    UPDATE v2ray_keys 
                    SET server_id = ?, v2ray_uuid = ?, expiry_at = ?, email = ?, tariff_id = ?
                    WHERE id = ?
                """, (alt_server_id, user_data['uuid'], new_expiry, email or '', tariff_id or 0, existing_key[0]))
                
                logging.info(f"V2Ray key {existing_key[0]} moved to alternative server {alt_server_id} ({alt_name})")
                return True
                
        except Exception as e:
            logging.error(f"Error creating key on alternative server: {e}")
            return False

def extend_existing_key(cursor, existing_key, duration, email=None, tariff_id=None):
    """Продлевает ключ. Если истёк - продляет от текущего времени."""
    now = int(time.time())
    # Если ключ истёк, продляем от текущего времени, иначе от старого expiry_at
    if existing_key[1] <= now:
        new_expiry = now + duration
        logging.info(f"Extending expired key {existing_key[0]}: was expired at {existing_key[1]}, new expiry: {new_expiry}")
    else:
        new_expiry = existing_key[1] + duration
    
    if email and tariff_id:
        cursor.execute("UPDATE keys SET expiry_at = ?, email = ?, tariff_id = ? WHERE id = ?", (new_expiry, email, tariff_id, existing_key[0]))
    elif email:
        cursor.execute("UPDATE keys SET expiry_at = ?, email = ? WHERE id = ?", (new_expiry, email, existing_key[0]))
    elif tariff_id:
        cursor.execute("UPDATE keys SET expiry_at = ?, tariff_id = ? WHERE id = ?", (new_expiry, tariff_id, existing_key[0]))
    else:
        cursor.execute("UPDATE keys SET expiry_at = ? WHERE id = ?", (new_expiry, existing_key[0]))

async def create_new_key_flow(cursor, message, user_id, tariff, email=None, country=None):
    now = int(time.time())
    GRACE_PERIOD = 86400  # 24 часа в секундах
    grace_threshold = now - GRACE_PERIOD
    
    # Проверяем наличие активного или недавно истекшего ключа (в пределах grace period)
    cursor.execute("SELECT id, expiry_at, access_url FROM keys WHERE user_id = ? AND expiry_at > ? ORDER BY expiry_at DESC LIMIT 1", (user_id, grace_threshold))
    existing_key = cursor.fetchone()
    if existing_key:
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

async def switch_protocol_and_extend(cursor, message, user_id, old_key_data: dict, new_protocol: str, new_country: str, additional_duration: int, email: str, tariff: dict):
    """Меняет протокол (и возможно страну) с продлением времени"""
    now = int(time.time())
    old_protocol = old_key_data['protocol']
    old_country = old_key_data['country']
    
    # Если страна не указана, используем текущую страну старого ключа
    target_country = new_country or old_country
    
    # Считаем оставшееся время старого ключа
    remaining = max(0, old_key_data['expiry_at'] - now)
    
    # Общее время = оставшееся + новое купленное
    total_duration = remaining + additional_duration
    new_expiry = now + total_duration
    
    old_server_id = old_key_data['server_id']
    old_email = old_key_data.get('email') or email
    
    logging.info(f"User {user_id}: switching protocol {old_protocol}→{new_protocol}, country {old_country}→{target_country}, remaining={remaining}s, adding={additional_duration}s")
    
    # Ищем сервер в целевой стране с новым протоколом (только доступные к покупке)
    cursor.execute("""
        SELECT id, api_url, cert_sha256, domain, v2ray_path, api_key FROM servers 
        WHERE active = 1 AND available_for_purchase = 1 AND country = ? AND protocol = ?
    """, (target_country, new_protocol))
    servers = cursor.fetchall()
    
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
        # Создаём новый ключ на новом протоколе
        if new_protocol == "outline":
            key = await asyncio.get_event_loop().run_in_executor(None, create_key, api_url, cert_sha256)
            if not key:
                logging.error(f"Failed to create Outline key on server {new_server_id}")
                await message.answer("❌ Ошибка при создании ключа на новом сервере.", reply_markup=main_menu)
                return False
            
            # Добавляем новый ключ
            cursor.execute(
                "INSERT INTO keys (server_id, user_id, access_url, expiry_at, key_id, created_at, email, tariff_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_server_id, user_id, key["accessUrl"], new_expiry, key["id"], now, old_email, tariff['id'])
            )
            
            access_url = key["accessUrl"]
            
            # Удаляем старый ключ
            await delete_old_key_after_success(cursor, old_key_for_deletion)
            
            # Уведомление пользователю
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
            
        elif new_protocol == "v2ray":
            from vpn_protocols import V2RayProtocol
            v2ray_client = V2RayProtocol(api_url, api_key)
            user_data = await v2ray_client.create_user(old_email or f"user_{user_id}@veilbot.com")
            
            if not user_data or not user_data.get('uuid'):
                logging.error(f"Failed to create V2Ray key on server {new_server_id}")
                await message.answer("❌ Ошибка при создании V2Ray ключа на новом сервере.", reply_markup=main_menu)
                return False
            
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
                access_url = config
                logging.info(f"Using client_config from create_user response for protocol switch")
            else:
                # Если client_config нет, используем get_user_config или fallback
                try:
                    config = await v2ray_client.get_user_config(user_data['uuid'], {
                        'domain': domain,
                        'port': 443,
                        'path': v2ray_path or '/v2ray',
                        'email': old_email or f"user_{user_id}@veilbot.com"
                    })
                    # Извлекаем VLESS URL, если конфигурация многострочная
                    if 'vless://' in config:
                        lines = config.split('\n')
                        for line in lines:
                            if line.strip().startswith('vless://'):
                                config = line.strip()
                                break
                    access_url = config
                except Exception as e:
                    logging.warning(f"Failed to get user config, using fallback: {e}")
                    # Fallback к хардкодированному формату
                    config = f"vless://{user_data['uuid']}@{domain}:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{old_email or 'VeilBot-V2Ray'}"
                    access_url = config
            
            # Добавляем новый ключ с конфигурацией
            cursor.execute(
                "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, expiry_at, created_at, email, tariff_id, client_config) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_server_id, user_id, user_data['uuid'], new_expiry, now, old_email, tariff['id'], config)
            )
            
            # Удаляем старый ключ
            await delete_old_key_after_success(cursor, old_key_for_deletion)
            
            # Уведомление пользователю
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
        
        # Уведомление админу
        admin_msg = (
            f"🔄🌍 *Автоматическая смена протокола*\n"
            f"Пользователь: `{user_id}`\n"
            f"Старый: *{PROTOCOLS[old_protocol]['name']}*, {old_country}\n"
            f"Новый: *{PROTOCOLS[new_protocol]['name']}*, {target_country}\n"
            f"Тариф: *{tariff.get('name', 'Неизвестно')}*\n"
            f"Старый сервер: `{old_server_id}`\n"
            f"Новый сервер: `{new_server_id}`\n"
            f"Оставшееся время: {format_duration(remaining)}\n"
            f"Добавлено: {format_duration(additional_duration)}\n"
            f"Срок действия: до <code>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(new_expiry))}</code>\n"
        )
        if old_email:
            admin_msg += f"Email: `{old_email}`\n"
        
        try:
            await bot.send_message(ADMIN_ID, admin_msg, disable_web_page_preview=True, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Failed to send admin notification (protocol switch): {e}")
        
        # Commit транзакции
        cursor.connection.commit()
        
        logging.info(f"Successfully switched protocol for user {user_id}: {old_protocol}→{new_protocol}, {old_country}→{target_country}, total={total_duration}s")
        return True
        
    except Exception as e:
        logging.error(f"Error in switch_protocol_and_extend: {e}")
        await message.answer("❌ Произошла ошибка при смене протокола. Попробуйте позже.", reply_markup=main_menu)
        return False

async def change_country_and_extend(cursor, message, user_id, key_data: dict, new_country: str, additional_duration: int, email: str, tariff: dict):
    """Меняет страну для ключа и добавляет новое время (при покупке нового тарифа)"""
    now = int(time.time())
    protocol = key_data['protocol']
    
    # Считаем оставшееся время старого ключа
    remaining = max(0, key_data['expiry_at'] - now)
    
    # Общее время = оставшееся + новое купленное
    total_duration = remaining + additional_duration
    new_expiry = now + total_duration
    
    old_server_id = key_data['server_id']
    old_country = key_data['country']
    old_email = key_data['email'] or email
    
    # Ищем сервер в новой стране с тем же протоколом (только доступные к покупке)
    cursor.execute("""
        SELECT id, api_url, cert_sha256, domain, v2ray_path, api_key FROM servers 
        WHERE active = 1 AND available_for_purchase = 1 AND country = ? AND protocol = ?
    """, (new_country, protocol))
    servers = cursor.fetchall()
    
    if not servers:
        logging.error(f"No servers found for country={new_country}, protocol={protocol}")
        await message.answer(f"❌ Нет доступных серверов {PROTOCOLS[protocol]['name']} в стране {new_country}.", reply_markup=main_menu)
        return False
    
    # Берём первый подходящий сервер
    new_server_id, api_url, cert_sha256, domain, v2ray_path, api_key = servers[0]
    
    # Сохраняем данные старого ключа для удаления
    old_key_data = {
        'type': key_data['type'],
        'server_id': old_server_id,
        'key_id': key_data.get('key_id'),
        'v2ray_uuid': key_data.get('v2ray_uuid'),
        'db_id': key_data['id']
    }
    
    try:
        # Создаём новый ключ в новой стране
        if protocol == "outline":
            key = await asyncio.get_event_loop().run_in_executor(None, create_key, api_url, cert_sha256)
            if not key:
                logging.error(f"Failed to create Outline key on server {new_server_id}")
                await message.answer("❌ Ошибка при создании ключа на новом сервере.", reply_markup=main_menu)
                return False
            
            # Добавляем новый ключ
            cursor.execute(
                "INSERT INTO keys (server_id, user_id, access_url, expiry_at, key_id, created_at, email, tariff_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_server_id, user_id, key["accessUrl"], new_expiry, key["id"], now, old_email, tariff['id'])
            )
            
            access_url = key["accessUrl"]
            
            # Удаляем старый ключ
            await delete_old_key_after_success(cursor, old_key_data)
            
            # Уведомление пользователю
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
            
        elif protocol == "v2ray":
            from vpn_protocols import V2RayProtocol
            v2ray_client = V2RayProtocol(api_url, api_key)
            user_data = await v2ray_client.create_user(old_email or f"user_{user_id}@veilbot.com")
            
            if not user_data or not user_data.get('uuid'):
                logging.error(f"Failed to create V2Ray key on server {new_server_id}")
                await message.answer("❌ Ошибка при создании V2Ray ключа на новом сервере.", reply_markup=main_menu)
                return False
            
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
                access_url = config
                logging.info(f"Using client_config from create_user response for country change")
            else:
                # Если client_config нет, используем get_user_config или fallback
                try:
                    config = await v2ray_client.get_user_config(user_data['uuid'], {
                        'domain': domain,
                        'port': 443,
                        'path': v2ray_path or '/v2ray',
                        'email': old_email or f"user_{user_id}@veilbot.com"
                    })
                    # Извлекаем VLESS URL, если конфигурация многострочная
                    if 'vless://' in config:
                        lines = config.split('\n')
                        for line in lines:
                            if line.strip().startswith('vless://'):
                                config = line.strip()
                                break
                    access_url = config
                except Exception as e:
                    logging.warning(f"Failed to get user config, using fallback: {e}")
                    # Fallback к хардкодированному формату
                    config = f"vless://{user_data['uuid']}@{domain}:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{old_email or 'VeilBot-V2Ray'}"
                    access_url = config
            
            # Добавляем новый ключ с конфигурацией
            cursor.execute(
                "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, expiry_at, created_at, email, tariff_id, client_config) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_server_id, user_id, user_data['uuid'], new_expiry, now, old_email, tariff['id'], config)
            )
            
            # Удаляем старый ключ
            await delete_old_key_after_success(cursor, old_key_data)
            
            # Уведомление пользователю
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
        
        # Уведомление админу
        admin_msg = (
            f"🌍 *Смена страны при покупке*\n"
            f"Пользователь: `{user_id}`\n"
            f"Протокол: *{PROTOCOLS[protocol]['name']}*\n"
            f"Старая страна: *{old_country}*\n"
            f"Новая страна: *{new_country}*\n"
            f"Тариф: *{tariff.get('name', 'Неизвестно')}*\n"
            f"Старый сервер: `{old_server_id}`\n"
            f"Новый сервер: `{new_server_id}`\n"
            f"Оставшееся время: {format_duration(remaining)}\n"
            f"Добавлено: {format_duration(additional_duration)}\n"
            f"Срок действия: до <code>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(new_expiry))}</code>\n"
        )
        if old_email:
            admin_msg += f"Email: `{old_email}`\n"
        
        try:
            await bot.send_message(ADMIN_ID, admin_msg, disable_web_page_preview=True, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Failed to send admin notification (country change with extend): {e}")
        
        # Commit транзакции
        cursor.connection.commit()
        
        logging.info(f"Successfully changed country and extended for user {user_id}: {old_country} -> {new_country}, +{additional_duration}s")
        return True
        
    except Exception as e:
        logging.error(f"Error in change_country_and_extend: {e}")
        await message.answer("❌ Произошла ошибка при смене страны. Попробуйте позже.", reply_markup=main_menu)
        return False

async def create_new_key_flow_with_protocol(cursor, message, user_id, tariff, email=None, country=None, protocol="outline", for_renewal=False):
    """Создание нового ключа с поддержкой протоколов
    
    Args:
        for_renewal: Если True, при выборе сервера не проверяется available_for_purchase (только active)
    """
    now = int(time.time())
    GRACE_PERIOD = 86400  # 24 часа в секундах
    grace_threshold = now - GRACE_PERIOD
    
    # Проверяем наличие активного или недавно истекшего ключа (в пределах grace period)
    if protocol == "outline":
        cursor.execute("SELECT k.id, k.expiry_at, k.access_url, s.country, k.server_id, k.key_id, k.tariff_id, k.email FROM keys k JOIN servers s ON k.server_id = s.id WHERE k.user_id = ? AND k.expiry_at > ? ORDER BY k.expiry_at DESC LIMIT 1", (user_id, grace_threshold))
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
                    'type': 'outline'
                }
                
                # Вызываем функцию смены страны с продлением
                success = await change_country_and_extend(cursor, message, user_id, key_data, country, tariff['duration_sec'], email, tariff)
                
                if success:
                    user_states.pop(user_id, None)
                    return
                else:
                    # Если не удалось сменить страну, создаем новый ключ
                    logging.warning(f"Failed to change country for key {existing_key[0]}, creating new key for user {user_id}")
                    # Продолжаем выполнение для создания нового ключа
            else:
                # Та же страна или страна не указана - продлеваем как обычно
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
                    if message:
                        await message.answer(msg_text, reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                    else:
                        # Если message=None (например, из webhook), отправляем напрямую через bot
                        await bot.send_message(user_id, msg_text, reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                    return
                else:
                    # Если не удалось продлить, создаем новый ключ
                    logging.warning(f"Failed to extend key {existing_key[0]}, creating new key for user {user_id}")
                    # Продолжаем выполнение для создания нового ключа
    else:  # v2ray
        cursor.execute("SELECT k.id, k.expiry_at, k.v2ray_uuid, s.domain, s.v2ray_path, k.server_id, s.country, k.tariff_id, k.email FROM v2ray_keys k JOIN servers s ON k.server_id = s.id WHERE k.user_id = ? AND k.expiry_at > ? ORDER BY k.expiry_at DESC LIMIT 1", (user_id, grace_threshold))
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
                    'type': 'v2ray'
                }
                
                # Вызываем функцию смены страны с продлением
                success = await change_country_and_extend(cursor, message, user_id, key_data, country, tariff['duration_sec'], email, tariff)
                
                if success:
                    user_states.pop(user_id, None)
                    return
                else:
                    # Если не удалось сменить страну, создаем новый ключ
                    logging.warning(f"Failed to change country for V2Ray key {existing_key[0]}, creating new key for user {user_id}")
                    # Продолжаем выполнение для создания нового ключа
            else:
                # Та же страна или страна не указана - продлеваем как обычно
                success = await extend_existing_key_with_fallback(cursor, existing_key, tariff['duration_sec'], email, tariff['id'], protocol)
            
                if success:
                    # Получаем обновленную информацию о ключе
                    cursor.execute("SELECT k.v2ray_uuid, s.domain, s.v2ray_path, s.api_url, s.api_key, k.email FROM v2ray_keys k JOIN servers s ON k.server_id = s.id WHERE k.id = ?", (existing_key[0],))
                    updated_key = cursor.fetchone()
                    
                    if updated_key:
                        v2ray_uuid, domain, path, api_url, api_key, key_email = updated_key
                        # Получаем реальную конфигурацию с сервера (как в "мои ключи")
                        try:
                            if api_url and api_key:
                                from vpn_protocols import ProtocolFactory
                                server_config = {'api_url': api_url, 'api_key': api_key}
                                protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                                config = await protocol_client.get_user_config(v2ray_uuid, {
                                    'domain': domain,
                                    'port': 443,
                                    'path': path or '/v2ray',
                                    'email': key_email or email or f"user_{user_id}@veilbot.com"
                                })
                            else:
                                # Fallback к хардкодной конфигурации если нет данных сервера
                                config = f"vless://{v2ray_uuid}@{domain}:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email or 'VeilBot-V2Ray'}"
                        except Exception as e:
                            logging.error(f"Error getting V2Ray config for {v2ray_uuid} during extension: {e}")
                            # Fallback к хардкодной конфигурации при ошибке
                            config = f"vless://{v2ray_uuid}@{domain}:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email or 'VeilBot-V2Ray'}"
                    else:
                        # Fallback к старой конфигурации
                        v2ray_uuid = existing_key[2]
                        domain = existing_key[3]
                        path = existing_key[4] or '/v2ray'
                        config = f"vless://{v2ray_uuid}@{domain}:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email or 'VeilBot-V2Ray'}"
                    
                    # Очищаем состояние пользователя
                    user_states.pop(user_id, None)
                    
                    # Проверяем, был ли ключ истекшим
                    was_expired = existing_key[1] <= now
                    if was_expired:
                        msg_text = f"✅ Ваш истекший ключ восстановлен и продлён на {format_duration(tariff['duration_sec'])}!\n\n{format_key_message_unified(config, protocol, tariff)}"
                    else:
                        msg_text = f"Ваш ключ продлён на {format_duration(tariff['duration_sec'])}!\n\n{format_key_message_unified(config, protocol, tariff)}"
                    
                    # Отправляем сообщение пользователю
                    if message:
                        await message.answer(msg_text, reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                    else:
                        # Если message=None (например, из webhook), отправляем напрямую через bot
                        await bot.send_message(user_id, msg_text, reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                    return
                else:
                    # Если не удалось продлить, создаем новый ключ
                    logging.warning(f"Failed to extend V2Ray key {existing_key[0]}, creating new key for user {user_id}")
                    # Продолжаем выполнение для создания нового ключа
    
    # НОВАЯ ЛОГИКА: Если нет ключа запрошенного протокола, проверяем противоположный протокол
    # Это важно: проверяем только если НЕ нашли ключ запрошенного протокола выше
    if protocol == "outline":
        # Проверяем, есть ли V2Ray ключ
        cursor.execute("SELECT k.id, k.expiry_at, k.v2ray_uuid, s.domain, s.v2ray_path, k.server_id, s.country, k.tariff_id, k.email FROM v2ray_keys k JOIN servers s ON k.server_id = s.id WHERE k.user_id = ? AND k.expiry_at > ? ORDER BY k.expiry_at DESC LIMIT 1", (user_id, grace_threshold))
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
                'type': 'v2ray'
            }
            
            # Вызываем функцию смены протокола
            success = await switch_protocol_and_extend(cursor, message, user_id, old_key_data, protocol, country, tariff['duration_sec'], email, tariff)
            
            if success:
                user_states.pop(user_id, None)
                return
            else:
                logging.warning(f"Failed to switch protocol from v2ray to outline for user {user_id}, creating new key")
                # Продолжаем создание нового ключа
    
    else:  # protocol == "v2ray"
        # Проверяем, есть ли Outline ключ
        cursor.execute("SELECT k.id, k.expiry_at, k.access_url, s.country, k.server_id, k.key_id, k.tariff_id, k.email FROM keys k JOIN servers s ON k.server_id = s.id WHERE k.user_id = ? AND k.expiry_at > ? ORDER BY k.expiry_at DESC LIMIT 1", (user_id, grace_threshold))
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
            
            await message.answer(
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
        await message.answer(f"Нет доступных серверов {PROTOCOLS[protocol]['name']} в выбранной стране.", reply_markup=main_menu)
        return
    
    try:
        # Отправляем сообщение о начале создания ключа
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
            from vpn_protocols import ProtocolFactory
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
            cursor.execute("""
                INSERT INTO keys (server_id, user_id, access_url, expiry_at, key_id, created_at, email, tariff_id, protocol)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (server[0], user_id, user_data['accessUrl'], expiry, user_data['id'], now, email, tariff['id'], protocol))
            
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
                        ip_address=getattr(message, 'from_user', {}).get('id', None),
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
            
            # Сохраняем ключ с конфигурацией в БД
            cursor.execute("""
                INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id, client_config)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (server[0], user_id, user_data['uuid'], email or f"user_{user_id}@veilbot.com", now, expiry, tariff['id'], config))
            
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
                        ip_address=getattr(message, 'from_user', {}).get('id', None),
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
        if message:
            await message.answer(
                format_key_message_unified(config, protocol, tariff),
                reply_markup=main_menu,
                disable_web_page_preview=True,
                parse_mode="Markdown"
            )
        else:
            # Если message=None (например, из webhook), отправляем напрямую через bot
            await bot.send_message(
                user_id,
                format_key_message_unified(config, protocol, tariff),
                reply_markup=main_menu,
                disable_web_page_preview=True,
                parse_mode="Markdown"
            )
        
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
        try:
            await bot.send_message(ADMIN_ID, admin_msg, disable_web_page_preview=True, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Failed to send admin notification: {e}")
            
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
                    ip_address=getattr(message, 'from_user', {}).get('id', None),
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
        await message.answer(
            f"❌ Ошибка при создании ключа {PROTOCOLS[protocol]['icon']}.\n"
            f"Попробуйте позже или обратитесь к администратору.",
            reply_markup=main_menu
        )
        return

def select_available_server_by_protocol(cursor, country=None, protocol='outline', for_renewal=False):
    """
    Выбор сервера с учетом протокола
    
    Args:
        country: Страна сервера
        protocol: Протокол (outline или v2ray)
        for_renewal: Если True, проверяет только active, не проверяет available_for_purchase (для продления)
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


async def handle_free_tariff_with_protocol(cursor, message, user_id, tariff, country=None, protocol="outline"):
    """Обработка бесплатного тарифа с поддержкой протоколов"""
    
    # Проверяем лимит бесплатных ключей для выбранного протокола и страны
    if check_free_tariff_limit_by_protocol_and_country(cursor, user_id, protocol, country):
        if country:
            await message.answer(f"Вы уже получали бесплатный тариф {PROTOCOLS[protocol]['name']} для страны {country} ранее. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
        else:
            await message.answer(f"Вы уже получали бесплатный тариф {PROTOCOLS[protocol]['name']} ранее. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
        return
    
    now = int(time.time())
    
    # Проверяем наличие активного ключа для конкретной страны и протокола
    if protocol == "outline":
        if country:
            cursor.execute("""
                SELECT k.id, k.expiry_at, t.price_rub
                FROM keys k
                JOIN tariffs t ON k.tariff_id = t.id
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND k.expiry_at > ? AND s.country = ?
                ORDER BY k.expiry_at DESC LIMIT 1
            """, (user_id, now, country))
        else:
            cursor.execute("""
                SELECT k.id, k.expiry_at, t.price_rub
                FROM keys k
                JOIN tariffs t ON k.tariff_id = t.id
                WHERE k.user_id = ? AND k.expiry_at > ?
                ORDER BY k.expiry_at DESC LIMIT 1
            """, (user_id, now))
    else:  # v2ray
        if country:
            cursor.execute("""
                SELECT k.id, k.expiry_at, t.price_rub
                FROM v2ray_keys k
                JOIN tariffs t ON k.tariff_id = t.id
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND k.expiry_at > ? AND s.country = ?
                ORDER BY k.expiry_at DESC LIMIT 1
            """, (user_id, now, country))
        else:
            cursor.execute("""
                SELECT k.id, k.expiry_at, t.price_rub
                FROM v2ray_keys k
                JOIN tariffs t ON k.tariff_id = t.id
                WHERE k.user_id = ? AND k.expiry_at > ?
                ORDER BY k.expiry_at DESC LIMIT 1
            """, (user_id, now))
    
    existing_key = cursor.fetchone()
    if existing_key:
        if existing_key[2] > 0:
            if country:
                await message.answer(f"У вас уже есть активный платный ключ {PROTOCOLS[protocol]['name']} для страны {country}. Бесплатный ключ можно получить только если нет активных платных ключей.", reply_markup=main_menu)
            else:
                await message.answer(f"У вас уже есть активный платный ключ {PROTOCOLS[protocol]['name']}. Бесплатный ключ можно получить только если нет активных платных ключей.", reply_markup=main_menu)
            return
        else:
            if country:
                await message.answer(f"У вас уже есть активный бесплатный ключ {PROTOCOLS[protocol]['name']} для страны {country}. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
            else:
                await message.answer(f"У вас уже есть активный бесплатный ключ {PROTOCOLS[protocol]['name']}. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
            return
    else:
        # Для бесплатного тарифа создаем ключ сразу без запроса email
        await create_new_key_flow_with_protocol(cursor, message, user_id, tariff, None, country, protocol)

async def handle_payment_method_selection(cursor, message, user_id, tariff, country=None, protocol="outline"):
    """Обработка выбора способа оплаты для платного тарифа"""
    # Сохраняем состояние и показываем выбор способа оплаты
    user_states[user_id] = {
        "state": "waiting_payment_method",
        "tariff": tariff,
        "country": country,
        "protocol": protocol
    }
    
    msg = f"💳 *Выберите способ оплаты*\n\n"
    msg += f"📦 Тариф: *{tariff['name']}*\n"
    msg += f"💰 Сумма: *{tariff['price_rub']}₽*"
    
    if tariff.get('price_crypto_usd'):
        msg += f" / *${tariff['price_crypto_usd']:.2f}* (криптовалюта)\n"
    else:
        msg += "\n"
    
    msg += f"\n{PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}\n"
    
    await message.answer(
        msg,
        reply_markup=get_payment_method_keyboard(),
        parse_mode="Markdown"
    )

async def handle_paid_tariff_with_protocol(cursor, message, user_id, tariff, country=None, protocol="outline"):
    """Обработка платного тарифа с поддержкой протоколов (старая функция для совместимости)"""
    # Перенаправляем на выбор способа оплаты
    await handle_payment_method_selection(cursor, message, user_id, tariff, country, protocol)

async def create_payment_with_email_and_protocol(message, user_id, tariff, email=None, country=None, protocol="outline", payment_method="yookassa", for_renewal=False):
    """Создание платежа с поддержкой протоколов и способов оплаты
    
    Args:
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
                logging.info("Платежный сервис инициализирован (lazy loading)")
            else:
                logging.warning("Платежный сервис недоступен")
        except Exception as e:
            PAYMENT_MODULE_AVAILABLE = False
            logging.warning(f"Ошибка инициализации платежного сервиса: {e}")
    
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

def select_available_server(cursor, country=None):
    """Выбор сервера для покупки (только доступные к покупке)"""
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



async def wait_for_payment_with_protocol(message, payment_id, server, user_id, tariff, country=None, protocol="outline", for_renewal=False):
    """Ожидание платежа с поддержкой протоколов
    
    Args:
        for_renewal: Если True, при выборе сервера не проверяется available_for_purchase (только active)
    """
    
    # Используем новый платежный модуль
    if PAYMENT_MODULE_AVAILABLE:
        try:
            from payments.adapters.legacy_adapter import wait_for_payment_with_protocol_legacy
            success = await wait_for_payment_with_protocol_legacy(message, payment_id, protocol)
            
            if success:
                logging.debug(f"New payment module confirmed payment success: {payment_id}")
                # Создаем ключ после успешного платежа
                with get_db_cursor(commit=True) as cursor:
                    cursor.execute("UPDATE payments SET status = 'paid' WHERE payment_id = ?", (payment_id,))
                    cursor.execute("SELECT email FROM payments WHERE payment_id = ?", (payment_id,))
                    payment_data = cursor.fetchone()
                    email = payment_data[0] if payment_data else None
                    await create_new_key_flow_with_protocol(cursor, message, user_id, tariff, email, country, protocol, for_renewal=for_renewal)
                    # --- Реферальный бонус ---
                    cursor.execute("SELECT referrer_id, bonus_issued FROM referrals WHERE referred_id = ?", (user_id,))
                    ref_row = cursor.fetchone()
                    if ref_row and ref_row[0] and not ref_row[1]:
                        referrer_id = ref_row[0]
                        # Проверяем, есть ли у пригласившего активный ключ
                        now = int(time.time())
                        cursor.execute("SELECT id, expiry_at FROM keys WHERE user_id = ? AND expiry_at > ? ORDER BY expiry_at DESC LIMIT 1", (referrer_id, now))
                        key = cursor.fetchone()
                        bonus_duration = 30 * 24 * 3600  # 1 месяц
                        if key:
                            extend_existing_key(cursor, key, bonus_duration)
                            await bot.send_message(referrer_id, "🎉 Ваш ключ продлён на месяц за приглашённого друга!")
                        else:
                            # Выдаём новый ключ на месяц
                            cursor.execute("SELECT * FROM tariffs WHERE duration_sec >= ? ORDER BY duration_sec ASC LIMIT 1", (bonus_duration,))
                            bonus_tariff = cursor.fetchone()
                            if bonus_tariff:
                                bonus_tariff_dict = {"id": bonus_tariff[0], "name": bonus_tariff[1], "price_rub": bonus_tariff[4], "duration_sec": bonus_tariff[2]}
                                await create_new_key_flow_with_protocol(cursor, message, referrer_id, bonus_tariff_dict, None, None, protocol)
                                await bot.send_message(referrer_id, "🎉 Вам выдан бесплатный месяц за приглашённого друга!")
                        cursor.execute("UPDATE referrals SET bonus_issued = 1 WHERE referred_id = ?", (user_id,))
                return
            else:
                logging.debug(f"New payment module timeout or failed: {payment_id}")
                await message.answer("Время ожидания платежа истекло. Попробуйте создать платеж заново.", reply_markup=main_menu)
                return
                
        except Exception as e:
            logging.warning(f"Ошибка в новом платежном модуле: {e}")
            await message.answer("Ошибка при проверке платежа. Обратитесь в поддержку.", reply_markup=main_menu)
            return
    else:
        # Если новый модуль недоступен
        logging.warning("Новый платежный модуль недоступен")
        await message.answer("Платежная система временно недоступна.", reply_markup=main_menu)
        return

async def wait_for_crypto_payment(message, invoice_id, server, user_id, tariff, country=None, protocol="outline", for_renewal=False):
    """Ожидание криптоплатежа через CryptoBot"""
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
                
                # Обновляем статус платежа в БД
                with get_db_cursor(commit=True) as cursor:
                    cursor.execute("UPDATE payments SET status = 'paid' WHERE payment_id = ?", (str(invoice_id),))
                    cursor.execute("SELECT email FROM payments WHERE payment_id = ?", (str(invoice_id),))
                    payment_data = cursor.fetchone()
                    email = payment_data[0] if payment_data else None
                    
                    # Создаем ключ
                    await create_new_key_flow_with_protocol(cursor, message, user_id, tariff, email, country, protocol, for_renewal=for_renewal)
                    
                    # Реферальный бонус (та же логика что и для обычных платежей)
                    cursor.execute("SELECT referrer_id, bonus_issued FROM referrals WHERE referred_id = ?", (user_id,))
                    ref_row = cursor.fetchone()
                    if ref_row and ref_row[0] and not ref_row[1]:
                        referrer_id = ref_row[0]
                        now = int(time.time())
                        cursor.execute("SELECT id, expiry_at FROM keys WHERE user_id = ? AND expiry_at > ? ORDER BY expiry_at DESC LIMIT 1", (referrer_id, now))
                        key = cursor.fetchone()
                        bonus_duration = 30 * 24 * 3600  # 1 месяц
                        if key:
                            extend_existing_key(cursor, key, bonus_duration)
                            await bot.send_message(referrer_id, "🎉 Ваш ключ продлён на месяц за приглашённого друга!")
                        else:
                            cursor.execute("SELECT * FROM tariffs WHERE duration_sec >= ? ORDER BY duration_sec ASC LIMIT 1", (bonus_duration,))
                            bonus_tariff = cursor.fetchone()
                            if bonus_tariff:
                                bonus_tariff_dict = {"id": bonus_tariff[0], "name": bonus_tariff[1], "price_rub": bonus_tariff[4], "duration_sec": bonus_tariff[2]}
                                await create_new_key_flow_with_protocol(cursor, message, referrer_id, bonus_tariff_dict, None, None, protocol)
                                await bot.send_message(referrer_id, "🎉 Вам выдан бесплатный месяц за приглашённого друга!")
                        cursor.execute("UPDATE referrals SET bonus_issued = 1 WHERE referred_id = ?", (user_id,))
                
                return
            
            # Проверяем, не истек ли инвойс
            invoice_info = await payment_service.cryptobot_service.get_invoice(int(invoice_id))
            if invoice_info and invoice_info.get("status") == "expired":
                await message.answer("⏰ Время оплаты истекло. Создайте новый платеж.", reply_markup=main_menu)
                return
            
            await asyncio.sleep(check_interval)
        
        # Если цикл завершился, значит таймаут
        await message.answer("⏰ Время ожидания платежа истекло. Попробуйте создать платеж заново.", reply_markup=main_menu)
        
    except Exception as e:
        logging.error(f"Error waiting for crypto payment: {e}")
        await message.answer("Ошибка при проверке платежа. Обратитесь в поддержку.", reply_markup=main_menu)

async def auto_delete_expired_keys():
    """Автоматическое удаление истекших ключей с grace period 24 часа"""
    GRACE_PERIOD = 86400  # 24 часа в секундах
    
    while True:
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
                cursor.connection.execute("PRAGMA foreign_keys=OFF")
                cursor.execute("DELETE FROM v2ray_keys WHERE expiry_at <= ?", (grace_threshold,))
                v2ray_deleted = cursor.rowcount
                cursor.connection.execute("PRAGMA foreign_keys=ON")
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
        
        await asyncio.sleep(600)

async def notify_expiring_keys():
    while True:
        updates = []  # Список для батчинга обновлений
        notifications_to_send = []  # Список уведомлений для отправки
        
        with get_db_cursor() as cursor:
            now = int(time.time())
            cursor.execute("""
                SELECT k.id, k.user_id, k.access_url, k.expiry_at, 
                       k.created_at, k.notified
                FROM keys k 
                WHERE k.expiry_at > ?
            """, (now,))
            rows = cursor.fetchall()
            
            for row in rows:
                key_id_db, user_id, access_url, expiry, created_at, notified = row
                remaining_time = expiry - now
                original_duration = expiry - created_at
                ten_percent_threshold = int(original_duration * 0.1)
                one_day = 86400
                one_hour = 3600
                message = None
                new_notified = notified

                # 1 day notification
                if original_duration > one_day and remaining_time <= one_day and notified < 3:
                    time_str = format_duration(remaining_time)
                    message = f"⏳ Ваш ключ истечет через {time_str}:\n`{access_url}`\nПродлите доступ:"
                    new_notified = 3
                # 1 hour notification
                elif original_duration > one_hour and remaining_time <= one_hour and notified < 2:
                    time_str = format_duration(remaining_time)
                    message = f"⏳ Ваш ключ истечет через {time_str}:\n`{access_url}`\nПродлите доступ:"
                    new_notified = 2
                # 10 minutes notification (универсально для всех ключей)
                elif remaining_time > 0 and remaining_time <= 600 and notified < 4:
                    time_str = format_duration(remaining_time)
                    message = f"⏳ Ваш ключ истечет через {time_str}:\n`{access_url}`\nПродлите доступ:"
                    new_notified = 4
                # 10% notification
                elif remaining_time > 0 and remaining_time <= ten_percent_threshold and notified < 1:
                    time_str = format_duration(remaining_time)
                    message = f"⏳ Ваш ключ истечет через {time_str}:\n`{access_url}`\nПродлите доступ:"
                    new_notified = 1

                if message:
                    # Сохраняем уведомление для отправки после коммита
                    keyboard = InlineKeyboardMarkup()
                    keyboard.add(InlineKeyboardButton("🔁 Продлить", callback_data="buy"))
                    notifications_to_send.append((user_id, message, keyboard))
                    # Добавляем обновление в батч
                    updates.append((new_notified, key_id_db))
        
        # Отправляем все уведомления
        for user_id, message, keyboard in notifications_to_send:
            try:
                await bot.send_message(user_id, message, reply_markup=keyboard, disable_web_page_preview=True, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Error sending expiry notification to user {user_id}: {e}")
        
        # Батчинг обновлений в БД
        if updates:
            with get_db_cursor(commit=True) as cursor:
                cursor.executemany("UPDATE keys SET notified = ? WHERE id = ?", updates)
                logging.debug(f"Updated {len(updates)} keys with expiry notifications")
        
        await asyncio.sleep(60)

async def check_key_availability():
    """Checks for the number of available keys and notifies the admin if it's low."""
    global low_key_notified
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
                            f"⚠️ **Внимание:** Осталось мало свободных ключей: *{free_keys}*."
                        )
                        low_key_notified = True
                else:
                    if low_key_notified:
                        await bot.send_message(
                            ADMIN_ID,
                            f"✅ **Статус:** Количество свободных ключей восстановлено: *{free_keys}*."
                        )
                    low_key_notified = False
        except Exception as e:
            logging.error(f"Error in check_key_availability: {e}")

        await asyncio.sleep(300) # Check every 5 minutes

@dp.callback_query_handler(lambda c: c.data == "buy")
@rate_limit("renew")
async def callback_buy_button(callback_query: types.CallbackQuery):
    """Обработчик кнопки 'Продлить' - показывает выбор способа платежа (как при покупке)"""
    user_id = callback_query.from_user.id
    now = int(time.time())
    
    # Находим активный ключ пользователя (самый новый по сроку действия)
    with get_db_cursor() as cursor:
        # Проверяем Outline ключи
        cursor.execute("""
            SELECT k.id, k.expiry_at, s.protocol, s.country
            FROM keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.expiry_at > ?
            ORDER BY k.expiry_at DESC LIMIT 1
        """, (user_id, now))
        outline_key = cursor.fetchone()
        
        # Проверяем V2Ray ключи
        cursor.execute("""
            SELECT k.id, k.expiry_at, s.protocol, s.country
            FROM v2ray_keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.expiry_at > ?
            ORDER BY k.expiry_at DESC LIMIT 1
        """, (user_id, now))
        v2ray_key = cursor.fetchone()
        
        # Выбираем самый новый ключ
        current_key = None
        if outline_key and v2ray_key:
            # Сравниваем по expiry_at
            current_key = outline_key if outline_key[1] > v2ray_key[1] else v2ray_key
        elif outline_key:
            current_key = outline_key
        elif v2ray_key:
            current_key = v2ray_key
    
    if not current_key:
        await callback_query.answer("У вас нет активных ключей для продления", show_alert=True)
        return
    
    # Получаем протокол и страну из найденного ключа
    key_id, expiry_at, protocol, country = current_key
    
    # Устанавливаем состояние для выбора способа платежа (как при покупке)
    user_states[user_id] = {
        "state": "waiting_payment_method_after_country",
        "country": country,
        "protocol": protocol,
        "is_renewal": True,  # Флаг, что это продление
        "paid_only": True
    }
    
    # Показываем выбор способа платежа
    msg = f"💳 *Выберите способ оплаты*\n\n"
    msg += f"{PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}\n"
    msg += f"🌍 Страна: *{country}*\n"
    
    await bot.send_message(
        user_id,
        msg,
        reply_markup=get_payment_method_keyboard(),
        parse_mode="Markdown"
    )
    
    try:
        await callback_query.answer()
    except Exception:
        pass

# --- Country selection helpers ---

async def process_pending_paid_payments():
    # Используем новый платежный модуль если доступен
    if PAYMENT_MODULE_AVAILABLE:
        try:
            from payments.adapters.legacy_adapter import process_pending_paid_payments_legacy
            return await process_pending_paid_payments_legacy()
        except Exception as e:
            logging.warning(f"Ошибка в новом платежном модуле, используем старый: {e}")
    
    # Fallback на старый код
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
                    
                    # Создаём ключ в зависимости от протокола
                    if protocol == "outline":
                        try:
                            key = await asyncio.get_event_loop().run_in_executor(None, create_key, server['api_url'], server['cert_sha256'])
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
                            (server['id'], user_id, key["accessUrl"], expiry, key["id"], now, email, tariff_id)
                        )
                        
                        # Если это бесплатный тариф, записываем использование
                        if tariff['price_rub'] == 0:
                            record_free_key_usage(cursor, user_id, protocol, country)
                        
                        # Уведомляем пользователя
                        try:
                            await bot.send_message(user_id, format_key_message(key["accessUrl"]), reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                        except Exception as e:
                            logging.error(f"[AUTO-ISSUE] Не удалось отправить Outline ключ user_id={user_id}: {e}")
                    
                    elif protocol == "v2ray":
                        try:
                            server_config = {'api_url': server['api_url'], 'api_key': server.get('api_key')}
                            protocol_client = ProtocolFactory.create_protocol(protocol, server_config)
                            
                            # Создаем пользователя на сервере (ВАЖНО: делаем это до сохранения в БД)
                            user_data = await protocol_client.create_user(email or f"user_{user_id}@veilbot.com")
                            
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
                                    'domain': server.get('domain', 'veil-bot.ru'),
                                    'port': 443,
                                    'path': server.get('v2ray_path', '/v2ray'),
                                    'email': email or f"user_{user_id}@veilbot.com"
                                })
                                # Извлекаем VLESS URL, если конфигурация многострочная
                                if 'vless://' in config:
                                    lines = config.split('\n')
                                    for line in lines:
                                        if line.strip().startswith('vless://'):
                                            config = line.strip()
                                            break
                            
                            now = int(time.time())
                            expiry = now + tariff['duration_sec']
                            cursor.execute(
                                "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id, client_config) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (server['id'], user_id, user_data['uuid'], email or f"user_{user_id}@veilbot.com", now, expiry, tariff_id, config)
                            )
                            
                            # Если это бесплатный тариф, записываем использование
                            if tariff['price_rub'] == 0:
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
                                    await protocol_client.delete_user(user_data['uuid'])
                                    logging.info(f"[AUTO-ISSUE] Deleted V2Ray user {user_data['uuid']} from server due to error")
                            except Exception as cleanup_error:
                                logging.error(f"[AUTO-ISSUE] Failed to cleanup V2Ray user after error: {cleanup_error}")
                            
                            continue
                    
                    logging.info(f"[AUTO-ISSUE] Успешно создан ключ {protocol} для user_id={user_id}, payment_id={payment_id}")
                    
        except Exception as e:
            logging.error(f"[AUTO-ISSUE] Общая ошибка фоновой задачи: {e}")
        await asyncio.sleep(300)

@dp.message_handler(lambda m: m.text == "Помощь")
async def handle_help(message: types.Message):
    help_text = (
        "Если VPN не работает:\n"
        "- возможно был заблокирован сервер, поможет перевыпуск ключа;\n"
        "- сломалось приложение, поможет его смена.\n\n"
        "Оплаченный срок действия ключа сохранится!\n\n"
        "Выберите вариант ниже:"
    )
    await message.answer(help_text, reply_markup=help_keyboard)

@dp.message_handler(lambda m: m.text == "💬 Связаться с поддержкой")
async def handle_support(message: types.Message):
    """Обработчик кнопки связи с поддержкой"""
    from config import SUPPORT_USERNAME
    
    if SUPPORT_USERNAME:
        # Убираем @ если пользователь добавил его
        username = SUPPORT_USERNAME.lstrip('@')
        support_text = (
            f"💬 Напишите нашему специалисту поддержки:\n"
            f"@{username}\n\n"
            f"Мы поможем решить любую проблему!"
        )
        support_button = InlineKeyboardMarkup()
        support_button.add(InlineKeyboardButton(
            "💬 Написать в поддержку",
            url=f"https://t.me/{username}?start"
        ))
        await message.answer(support_text, reply_markup=support_button)
    else:
        await message.answer(
            "❌ Контакт поддержки не настроен.\n"
            "Обратитесь к администратору бота.",
            reply_markup=help_keyboard
        )

@dp.message_handler(lambda m: m.text == "🔙 Назад" and m.reply_markup == help_keyboard)
async def handle_help_back(message: types.Message):
    await message.answer("Главное меню:", reply_markup=main_menu)

@dp.message_handler(lambda m: m.text == "Сменить страну")
async def handle_change_country(message: types.Message):
    """Обработчик смены страны"""
    user_id = message.from_user.id
    logging.debug(f"handle_change_country called for user {user_id}")
    
    try:
        with get_db_cursor() as cursor:
            # Получаем активные ключи пользователя
            now = int(time.time())
            
            # Получаем Outline ключи
            cursor.execute("""
                SELECT k.id, k.expiry_at, k.server_id, k.key_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol
                FROM keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND k.expiry_at > ?
            """, (user_id, now))
            outline_keys = cursor.fetchall()
            
            # Получаем V2Ray ключи
            cursor.execute("""
                SELECT k.id, k.expiry_at, k.server_id, k.v2ray_uuid, s.country, k.tariff_id, k.email, s.protocol, s.domain, s.v2ray_path
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND k.expiry_at > ?
            """, (user_id, now))
            v2ray_keys = cursor.fetchall()
            
            # Объединяем все ключи
            all_keys = []
            for key in outline_keys:
                all_keys.append({
                    'id': key[0],
                    'expiry_at': key[1],
                    'server_id': key[2],
                    'key_id': key[3],
                    'access_url': key[4],
                    'country': key[5],
                    'tariff_id': key[6],
                    'email': key[7],
                    'protocol': key[8],
                    'type': 'outline'
                })
            
            for key in v2ray_keys:
                all_keys.append({
                    'id': key[0],
                    'expiry_at': key[1],
                    'server_id': key[2],
                    'v2ray_uuid': key[3],
                    'country': key[4],
                    'tariff_id': key[5],
                    'email': key[6],
                    'protocol': key[7],
                    'type': 'v2ray',
                    'domain': key[8],
                    'v2ray_path': key[9]
                })
            
            logging.debug(f"Всего активных ключей для смены страны: {len(all_keys)}")
            
            if not all_keys:
                await message.answer("У вас нет активных ключей для смены страны.", reply_markup=main_menu)
                return
            
            if len(all_keys) == 1:
                # Если только один ключ, показываем выбор страны сразу
                logging.debug(f"Меняем страну для одного ключа: {all_keys[0]['type']}")
                await show_country_change_menu(message, user_id, all_keys[0])
            else:
                # Если несколько ключей, показываем список для выбора
                logging.debug("Показываем меню выбора ключа для смены страны")
                await show_key_selection_for_country_change(message, user_id, all_keys)
    
    except Exception as e:
        logging.error(f"Error in handle_change_country: {e}")
        await message.answer("❌ Произошла ошибка. Попробуйте еще раз.", reply_markup=main_menu)

@dp.message_handler(lambda m: m.text == "Сменить приложение")
async def handle_change_app(message: types.Message):
    logging.debug(f"Обработчик 'Сменить приложение' вызван для пользователя {message.from_user.id}")
    user_id = message.from_user.id
    now = int(time.time())
    
    try:
        with get_db_cursor() as cursor:
            # Получаем все активные ключи пользователя
            cursor.execute("""
                SELECT k.id, k.expiry_at, k.server_id, k.key_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol, 'outline' as key_type
                FROM keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND k.expiry_at > ?
                ORDER BY k.expiry_at DESC
            """, (user_id, now))
            outline_keys = cursor.fetchall()
            logging.debug(f"Найдено {len(outline_keys)} Outline ключей")
            
            cursor.execute("""
                SELECT k.id, k.expiry_at, k.server_id, k.v2ray_uuid, s.country, k.tariff_id, k.email, s.protocol, 'v2ray' as key_type, s.domain, s.v2ray_path
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND k.expiry_at > ?
                ORDER BY k.expiry_at DESC
            """, (user_id, now))
            v2ray_keys = cursor.fetchall()
            logging.debug(f"Найдено {len(v2ray_keys)} V2Ray ключей")
            
            # Объединяем все ключи
            all_keys = []
            for key in outline_keys:
                all_keys.append({
                    'id': key[0],
                    'expiry_at': key[1],
                    'server_id': key[2],
                    'key_id': key[3],
                    'access_url': key[4],
                    'country': key[5],
                    'tariff_id': key[6],
                    'email': key[7],
                    'protocol': key[8],
                    'type': 'outline'
                })
            
            for key in v2ray_keys:
                all_keys.append({
                    'id': key[0],
                    'expiry_at': key[1],
                    'server_id': key[2],
                    'v2ray_uuid': key[3],
                    'country': key[4],
                    'tariff_id': key[5],
                    'email': key[6],
                    'protocol': key[7],
                    'type': key[8],
                    'domain': key[9],
                    'v2ray_path': key[10]
                })
            
            logging.debug(f"Всего активных ключей: {len(all_keys)}")
            
            if not all_keys:
                await message.answer("У вас нет активных ключей для смены протокола.", reply_markup=main_menu)
                return
            
            if len(all_keys) == 1:
                # Если только один ключ, меняем его протокол сразу
                logging.debug(f"Меняем протокол для одного ключа: {all_keys[0]['type']}")
                await change_protocol_for_key(message, user_id, all_keys[0])
            else:
                # Если несколько ключей, показываем список для выбора
                logging.debug("Показываем меню выбора ключа для смены протокола")
                await show_protocol_change_menu(message, user_id, all_keys)
    
    except Exception as e:
        logging.error(f"Ошибка в handle_change_app: {e}")
        await message.answer("Произошла ошибка при обработке запроса. Попробуйте позже.", reply_markup=main_menu)

@dp.message_handler(lambda m: m.text == "Перевыпустить ключ")
async def handle_reissue_key(message: types.Message):
    user_id = message.from_user.id
    now = int(time.time())
    
    with get_db_cursor() as cursor:
        # Получаем все активные ключи пользователя
        cursor.execute("""
            SELECT k.id, k.expiry_at, k.server_id, k.key_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol, 'outline' as key_type
            FROM keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.expiry_at > ?
            ORDER BY k.expiry_at DESC
        """, (user_id, now))
        outline_keys = cursor.fetchall()
        
        cursor.execute("""
            SELECT k.id, k.expiry_at, k.server_id, k.v2ray_uuid, s.country, k.tariff_id, k.email, s.protocol, 'v2ray' as key_type, s.domain, s.v2ray_path
            FROM v2ray_keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.expiry_at > ?
            ORDER BY k.expiry_at DESC
        """, (user_id, now))
        v2ray_keys = cursor.fetchall()
        
        # Объединяем все ключи
        all_keys = []
        for key in outline_keys:
            all_keys.append({
                'id': key[0],
                'expiry_at': key[1],
                'server_id': key[2],
                'key_id': key[3],
                'access_url': key[4],
                'country': key[5],
                'tariff_id': key[6],
                'email': key[7],
                'protocol': key[8],
                'type': 'outline'
            })
        
        for key in v2ray_keys:
            all_keys.append({
                'id': key[0],
                'expiry_at': key[1],
                'server_id': key[2],
                'v2ray_uuid': key[3],
                'country': key[4],
                'tariff_id': key[5],
                'email': key[6],
                'protocol': key[7],
                'type': key[8],
                'domain': key[9],
                'v2ray_path': key[10]
            })
        
        if not all_keys:
            await message.answer("У вас нет активных ключей для перевыпуска.", reply_markup=main_menu)
            return
        
        if len(all_keys) == 1:
            # Если только один ключ, перевыпускаем его сразу
            await reissue_specific_key(message, user_id, all_keys[0])
        else:
            # Если несколько ключей, показываем список для выбора
            await show_key_selection_menu(message, user_id, all_keys)

async def show_key_selection_menu(message: types.Message, user_id: int, keys: list):
    """Показывает меню выбора ключа для перевыпуска"""
    
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

async def show_protocol_change_menu(message: types.Message, user_id: int, keys: list):
    """Показывает меню выбора ключа для смены протокола"""
    
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

async def delete_old_key_after_success(cursor, old_key_data: dict):
    """Удаляет старый ключ после успешного создания нового"""
    try:
        if old_key_data['type'] == "outline":
            # Удаляем старый ключ из Outline сервера
            cursor.execute("SELECT api_url, cert_sha256 FROM servers WHERE id = ?", (old_key_data['server_id'],))
            old_server_data = cursor.fetchone()
            if old_server_data and old_key_data.get('key_id'):
                old_api_url, old_cert_sha256 = old_server_data
                try:
                    await asyncio.get_event_loop().run_in_executor(None, delete_key, old_api_url, old_cert_sha256, old_key_data['key_id'])
                    logging.info(f"Удален старый Outline ключ {old_key_data['key_id']} с сервера")
                except Exception as e:
                    logging.warning(f"Не удалось удалить старый Outline ключ с сервера: {e}")
            
            # Удаляем старый ключ из базы
            cursor.execute("DELETE FROM keys WHERE id = ?", (old_key_data['db_id'],))
            logging.info(f"Удален старый Outline ключ {old_key_data['db_id']} из базы")
            
        else:  # v2ray
            # Удаляем старый ключ из V2Ray сервера
            cursor.execute("SELECT api_url, api_key FROM servers WHERE id = ?", (old_key_data['server_id'],))
            old_server_data = cursor.fetchone()
            if old_server_data and old_key_data.get('v2ray_uuid'):
                old_api_url, old_api_key = old_server_data
                server_config = {'api_url': old_api_url, 'api_key': old_api_key}
                protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                try:
                    await protocol_client.delete_user(old_key_data['v2ray_uuid'])
                    logging.info(f"Удален старый V2Ray ключ {old_key_data['v2ray_uuid']} с сервера")
                except Exception as e:
                    logging.warning(f"Не удалось удалить старый V2Ray ключ с сервера: {e}")
            
            # Удаляем старый ключ из базы
            cursor.execute("DELETE FROM v2ray_keys WHERE id = ?", (old_key_data['db_id'],))
            logging.info(f"Удален старый V2Ray ключ {old_key_data['db_id']} из базы")
            
    except Exception as e:
        logging.error(f"Ошибка при удалении старого ключа: {e}")

async def change_protocol_for_key(message: types.Message, user_id: int, key_data: dict):
    """Меняет протокол для конкретного ключа"""
    now = int(time.time())
    
    with get_db_cursor(commit=False) as cursor:
        # Получаем тариф
        cursor.execute("SELECT name, duration_sec, price_rub FROM tariffs WHERE id = ?", (key_data['tariff_id'],))
        tariff_row = cursor.fetchone()
        if not tariff_row:
            await message.answer("Ошибка: тариф не найден.", reply_markup=main_menu)
            return
        tariff = {'id': key_data['tariff_id'], 'name': tariff_row[0], 'duration_sec': tariff_row[1], 'price_rub': tariff_row[2]}
        
        # Считаем оставшееся время
        remaining = key_data['expiry_at'] - now
        if remaining <= 0:
            await message.answer("Срок действия ключа истёк.", reply_markup=main_menu)
            return
        
        old_server_id = key_data['server_id']
        country = key_data['country']
        old_email = key_data['email']
        old_protocol = key_data['protocol']
        
        # Определяем новый протокол (противоположный текущему)
        new_protocol = "v2ray" if old_protocol == "outline" else "outline"
        
        # Ищем сервер той же страны с новым протоколом (только доступные к покупке)
        cursor.execute("""
            SELECT id, api_url, cert_sha256, domain, v2ray_path, api_key FROM servers 
            WHERE active = 1 AND available_for_purchase = 1 AND country = ? AND protocol = ?
        """, (country, new_protocol))
        servers = cursor.fetchall()
        
        if not servers:
            await message.answer(f"Нет серверов {PROTOCOLS[new_protocol]['name']} в вашей стране для смены протокола.", reply_markup=main_menu)
            return
        
        # Берём первый подходящий сервер
        new_server_id, api_url, cert_sha256, domain, v2ray_path, api_key = servers[0]
        
        # Сохраняем данные старого ключа для удаления после успешного создания нового
        old_key_data = {
            'type': key_data['type'],
            'server_id': old_server_id,
            'key_id': key_data.get('key_id'),
            'v2ray_uuid': key_data.get('v2ray_uuid'),
            'db_id': key_data['id']
        }
        
        # Создаём новый ключ на другом протоколе
        if new_protocol == "outline":
            # Создаём новый Outline ключ
            try:
                key = await asyncio.get_event_loop().run_in_executor(None, create_key, api_url, cert_sha256)
                if not key:
                    await message.answer("Ошибка при создании нового Outline ключа.", reply_markup=main_menu)
                    return
            except Exception as e:
                logging.error(f"Ошибка при создании Outline ключа: {e}")
                await message.answer("Ошибка при создании нового ключа.", reply_markup=main_menu)
                return
            
            # Добавляем новый ключ с тем же сроком действия и email
            cursor.execute(
                "INSERT INTO keys (server_id, user_id, access_url, expiry_at, key_id, created_at, email, tariff_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_server_id, user_id, key["accessUrl"], now + remaining, key["id"], now, old_email, key_data['tariff_id'])
            )
            
            await message.answer(format_key_message_unified(key["accessUrl"], new_protocol, tariff, remaining), reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
            
            # Удаляем старый ключ после успешного создания нового
            await delete_old_key_after_success(cursor, old_key_data)
            
        else:  # v2ray
            # Создаём новый V2Ray ключ
            server_config = {'api_url': api_url, 'api_key': api_key}
            protocol_client = ProtocolFactory.create_protocol(new_protocol, server_config)
            try:
                user_data = await protocol_client.create_user(old_email or f"user_{user_id}@veilbot.com")
                
                # Проверяем, что пользователь действительно создан
                if not user_data or not user_data.get('uuid'):
                    raise Exception("Failed to create V2Ray user - API returned None or invalid data")
                
                # ИСПРАВЛЕНИЕ: Используем client_config из ответа create_user, если он есть
                config = None
                if user_data.get('client_config'):
                    config = user_data['client_config']
                    logging.info(f"Using client_config from create_user response for protocol change")
                else:
                    # Если client_config нет в ответе, запрашиваем через get_user_config
                    logging.debug(f"client_config not in create_user response, fetching via get_user_config")
                    config = await protocol_client.get_user_config(user_data['uuid'], {
                        'domain': domain,
                        'port': 443,
                        'path': v2ray_path or '/v2ray',
                        'email': old_email or f"user_{user_id}@veilbot.com"
                    })
                
                # Добавляем новый ключ с тем же сроком действия и email
                cursor.execute(
                    "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_server_id, user_id, user_data['uuid'], old_email or f"user_{user_id}@veilbot.com", now, now + remaining, key_data['tariff_id'])
                )
                
                await message.answer(format_key_message_unified(config, new_protocol, tariff, remaining), reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                
                # Удаляем старый ключ после успешного создания нового
                await delete_old_key_after_success(cursor, old_key_data)
                
            except Exception as e:
                logging.error(f"Ошибка при создании нового V2Ray ключа: {e}")
                error_msg = "Ошибка при создании нового ключа."
                if "401" in str(e):
                    error_msg = "Ошибка авторизации на сервере V2Ray. Обратитесь к администратору."
                elif "404" in str(e):
                    error_msg = "Сервер V2Ray недоступен. Попробуйте позже."
                await message.answer(error_msg, reply_markup=main_menu)
                return
        
        # Уведомление админу о смене протокола
        admin_msg = (
            f"🔄 *Смена протокола*\n"
            f"Пользователь: `{user_id}`\n"
            f"Старый протокол: *{PROTOCOLS[old_protocol]['name']}*\n"
            f"Новый протокол: *{PROTOCOLS[new_protocol]['name']}*\n"
            f"Тариф: *{tariff.get('name', 'Неизвестно')}*\n"
            f"Старый сервер: `{old_server_id}`\n"
            f"Новый сервер: `{new_server_id}`\n"
            f"Срок действия: до <code>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now + remaining))}</code>\n"
        )
        if old_email:
            admin_msg += f"Email: `{old_email}`\n"
        try:
            await bot.send_message(ADMIN_ID, admin_msg, disable_web_page_preview=True, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Failed to send admin notification (protocol change): {e}")
        
        # Ручной commit транзакции
        cursor.connection.commit()

async def show_key_selection_for_country_change(message: types.Message, user_id: int, all_keys: list):
    """Показать меню выбора ключа для смены страны"""
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

async def show_country_change_menu(message: types.Message, user_id: int, key_data: dict):
    """Показать меню выбора страны для смены"""
    protocol = key_data['protocol']
    current_country = key_data['country']
    
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
    user_states[user_id] = {
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

async def change_country_for_key(message: types.Message, user_id: int, key_data: dict, new_country: str):
    """Меняет страну для конкретного ключа"""
    now = int(time.time())
    
    with get_db_cursor(commit=False) as cursor:
        # Получаем тариф
        cursor.execute("SELECT name, duration_sec, price_rub FROM tariffs WHERE id = ?", (key_data['tariff_id'],))
        tariff_row = cursor.fetchone()
        if not tariff_row:
            await message.answer("Ошибка: тариф не найден.", reply_markup=main_menu)
            return
        tariff = {'id': key_data['tariff_id'], 'name': tariff_row[0], 'duration_sec': tariff_row[1], 'price_rub': tariff_row[2]}
        
        # Считаем оставшееся время
        remaining = key_data['expiry_at'] - now
        if remaining <= 0:
            await message.answer("Срок действия ключа истёк.", reply_markup=main_menu)
            return
        
        old_server_id = key_data['server_id']
        old_country = key_data['country']
        old_email = key_data['email']
        protocol = key_data['protocol']
        
        # Ищем сервер той же страны с тем же протоколом (только доступные к покупке)
        cursor.execute("""
            SELECT id, api_url, cert_sha256, domain, v2ray_path, api_key FROM servers 
            WHERE active = 1 AND available_for_purchase = 1 AND country = ? AND protocol = ?
        """, (new_country, protocol))
        servers = cursor.fetchall()
        
        if not servers:
            await message.answer(f"Нет серверов {PROTOCOLS[protocol]['name']} в стране {new_country}.", reply_markup=main_menu)
            return
        
        # Берём первый подходящий сервер
        new_server_id, api_url, cert_sha256, domain, v2ray_path, api_key = servers[0]
        
        # Сохраняем данные старого ключа для удаления после успешного создания нового
        old_key_data = {
            'type': key_data['type'],
            'server_id': old_server_id,
            'key_id': key_data.get('key_id'),
            'v2ray_uuid': key_data.get('v2ray_uuid'),
            'db_id': key_data['id']
        }
        
        # Создаём новый ключ в новой стране
        if protocol == "outline":
            # Создаём новый Outline ключ
            try:
                key = await asyncio.get_event_loop().run_in_executor(None, create_key, api_url, cert_sha256)
                if not key:
                    await message.answer("Ошибка при создании нового Outline ключа.", reply_markup=main_menu)
                    return
            except Exception as e:
                logging.error(f"Ошибка при создании Outline ключа: {e}")
                await message.answer("Ошибка при создании нового ключа.", reply_markup=main_menu)
                return
            
            # Добавляем новый ключ с тем же сроком действия и email
            cursor.execute(
                "INSERT INTO keys (server_id, user_id, access_url, expiry_at, key_id, created_at, email, tariff_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_server_id, user_id, key["accessUrl"], now + remaining, key["id"], now, old_email, key_data['tariff_id'])
            )
            
            await message.answer(format_key_message_unified(key["accessUrl"], protocol, tariff, remaining), reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
            
            # Удаляем старый ключ после успешного создания нового
            await delete_old_key_after_success(cursor, old_key_data)
            
        elif protocol == "v2ray":
            # Создаём новый V2Ray ключ
            try:
                from vpn_protocols import V2RayProtocol
                server_config = {'api_url': api_url, 'api_key': api_key}
                protocol_client = ProtocolFactory.create_protocol(protocol, server_config)
                
                # Создаем пользователя на новом сервере
                user_data = await protocol_client.create_user(old_email or f"user_{user_id}@veilbot.com")
                
                if not user_data or not user_data.get('uuid'):
                    raise Exception(f"Failed to create V2Ray user - invalid response from server")
                
                # Получаем client_config из ответа create_user или через get_user_config
                config = None
                old_email_val = old_email or f"user_{user_id}@veilbot.com"
                logging.info(f"[REISSUE] Processing reissue for email: {old_email_val}, new UUID: {user_data.get('uuid')}")
                
                if user_data.get('client_config'):
                    config = user_data['client_config']
                    logging.info(f"[REISSUE] Using client_config from create_user response for email {old_email_val}")
                else:
                    # Если client_config нет в ответе, запрашиваем через get_user_config
                    logging.warning(f"[REISSUE] client_config not in create_user response for email {old_email_val}, fetching via get_user_config")
                    await asyncio.sleep(1.0)  # Даем API время сгенерировать конфигурацию
                    config = await protocol_client.get_user_config(user_data['uuid'], {
                        'domain': domain,
                        'port': 443,
                        'path': v2ray_path or '/v2ray',
                        'email': old_email_val
                    }, max_retries=5, retry_delay=1.5)
                
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
                
                # Добавляем новый ключ с client_config в базу данных
                cursor.execute(
                    "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, expiry_at, created_at, email, tariff_id, client_config) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_server_id, user_id, user_data['uuid'], now + remaining, now, old_email, key_data['tariff_id'], config)
                )
                
                # Используем сохраненный config для отправки пользователю
                await message.answer(format_key_message_unified(config, protocol, tariff, remaining), reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                
                # Удаляем старый ключ из базы после успешного создания нового
                cursor.execute("DELETE FROM v2ray_keys WHERE id = ?", (key_data['id'],))
                
            except Exception as e:
                logging.error(f"Ошибка при создании нового V2Ray ключа: {e}")
                error_msg = "Ошибка при создании нового ключа."
                if "401" in str(e):
                    error_msg = "Ошибка авторизации на сервере V2Ray. Обратитесь к администратору."
                elif "404" in str(e):
                    error_msg = "Сервер V2Ray недоступен. Попробуйте позже."
                await message.answer(error_msg, reply_markup=main_menu)
                return
        
        # Уведомление админу о смене страны
        admin_msg = (
            f"🌍 *Смена страны*\n"
            f"Пользователь: `{user_id}`\n"
            f"Протокол: *{PROTOCOLS[protocol]['name']}*\n"
            f"Старая страна: *{old_country}*\n"
            f"Новая страна: *{new_country}*\n"
            f"Тариф: *{tariff.get('name', 'Неизвестно')}*\n"
            f"Старый сервер: `{old_server_id}`\n"
            f"Новый сервер: `{new_server_id}`\n"
            f"Срок действия: до <code>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now + remaining))}</code>\n"
        )
        if old_email:
            admin_msg += f"Email: `{old_email}`\n"
        try:
            await bot.send_message(ADMIN_ID, admin_msg, disable_web_page_preview=True, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Failed to send admin notification (country change): {e}")
        
        # Ручной commit транзакции
        cursor.connection.commit()

async def reissue_specific_key(message: types.Message, user_id: int, key_data: dict):
    """Улучшенная функция перевыпуска конкретного ключа"""
    now = int(time.time())
    
    with get_db_cursor(commit=True) as cursor:
        # Получаем тариф
        cursor.execute("SELECT name, duration_sec, price_rub FROM tariffs WHERE id = ?", (key_data['tariff_id'],))
        tariff_row = cursor.fetchone()
        if not tariff_row:
            await message.answer("Ошибка: тариф не найден.", reply_markup=main_menu)
            return
        tariff = {'id': key_data['tariff_id'], 'name': tariff_row[0], 'duration_sec': tariff_row[1], 'price_rub': tariff_row[2]}
        
        # Считаем оставшееся время
        remaining = key_data['expiry_at'] - now
        if remaining <= 0:
            await message.answer("Срок действия ключа истёк.", reply_markup=main_menu)
            return
        
        old_server_id = key_data['server_id']
        country = key_data['country']
        old_email = key_data['email']
        protocol = key_data['protocol']
        
        # Ищем другие серверы той же страны и протокола (только доступные к покупке)
        cursor.execute("""
            SELECT id, api_url, cert_sha256, domain, v2ray_path, api_key, max_keys FROM servers 
            WHERE active = 1 AND available_for_purchase = 1 AND country = ? AND protocol = ? AND id != ?
        """, (country, protocol, old_server_id))
        other_servers = cursor.fetchall()
        
        # Проверяем доступность других серверов
        available_servers = []
        for server in other_servers:
            server_id, api_url, cert_sha256, domain, v2ray_path, api_key, max_keys = server
            
            # Дополнительная проверка: убеждаемся, что сервер действительно доступен к покупке
            cursor.execute("SELECT available_for_purchase FROM servers WHERE id = ?", (server_id,))
            purchase_status = cursor.fetchone()
            if not purchase_status or not purchase_status[0]:
                logging.debug(f"Server {server_id} is not available for purchase, skipping")
                continue
            
            # Проверяем емкость сервера
            if protocol == 'outline':
                cursor.execute("SELECT COUNT(*) FROM keys WHERE server_id = ? AND expiry_at > ?", (server_id, now))
            elif protocol == 'v2ray':
                cursor.execute("SELECT COUNT(*) FROM v2ray_keys WHERE server_id = ? AND expiry_at > ?", (server_id, now))
            
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
            # НО только если текущий сервер доступен к покупке
            cursor.execute("SELECT max_keys, available_for_purchase FROM servers WHERE id = ?", (old_server_id,))
            server_info = cursor.fetchone()
            if not server_info:
                await message.answer(f"Сервер не найден. Обратитесь к администратору.", reply_markup=main_menu)
                return
            
            max_keys, available_for_purchase = server_info
            
            # Проверяем, доступен ли текущий сервер к покупке
            if not available_for_purchase:
                await message.answer(
                    f"Нет доступных серверов {PROTOCOLS[protocol]['name']} в вашей стране для перевыпуска ключа. "
                    f"Текущий сервер недоступен для перевыпуска.",
                    reply_markup=main_menu
                )
                return
            
            if protocol == 'outline':
                cursor.execute("SELECT COUNT(*) FROM keys WHERE server_id = ? AND expiry_at > ?", (old_server_id, now))
            elif protocol == 'v2ray':
                cursor.execute("SELECT COUNT(*) FROM v2ray_keys WHERE server_id = ? AND expiry_at > ?", (old_server_id, now))
            
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
        
        if key_data['type'] == "outline":
            # Создаём новый Outline ключ
            key = await asyncio.get_event_loop().run_in_executor(None, create_key, api_url, cert_sha256)
            if not key:
                await message.answer("Ошибка при создании нового ключа.", reply_markup=main_menu)
                return
            
            # Удаляем старый ключ из Outline сервера
            cursor.execute("SELECT api_url, cert_sha256 FROM servers WHERE id = ?", (old_server_id,))
            old_server_data = cursor.fetchone()
            if old_server_data:
                old_api_url, old_cert_sha256 = old_server_data
                try:
                    # Проверяем, что это Outline ключ и у него есть key_id
                    logging.debug(f"key_data type: {key_data.get('type')}, key_id present: {'key_id' in key_data}")
                    if key_data['type'] == "outline" and 'key_id' in key_data:
                        logging.debug(f"Удаляем Outline ключ с ID: {key_data['key_id']} с сервера {old_server_id}")
                        await asyncio.get_event_loop().run_in_executor(None, delete_key, old_api_url, old_cert_sha256, key_data['key_id'])
                    else:
                        logging.warning(f"Пропускаем удаление Outline ключа - неверный тип или отсутствует key_id")
                        logging.debug(f"key_data keys: {list(key_data.keys())}")
                except Exception as e:
                    logging.error(f"Не удалось удалить старый Outline ключ: {e}")
            
            # Удаляем старый ключ из базы
            cursor.execute("DELETE FROM keys WHERE id = ?", (key_data['id'],))
            
            # Добавляем новый ключ с тем же сроком действия и email
            cursor.execute(
                "INSERT INTO keys (server_id, user_id, access_url, expiry_at, key_id, created_at, email, tariff_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_server_id, user_id, key["accessUrl"], now + remaining, key["id"], now, old_email, key_data['tariff_id'])
            )
            
            await message.answer(format_key_message_unified(key["accessUrl"], protocol, tariff, remaining), reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
            
        elif key_data['type'] == "v2ray":  # v2ray
            # Создаём новый V2Ray ключ
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
                
                # Добавляем новый ключ с client_config в базу данных (до удаления старого)
                cursor.execute(
                    "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id, client_config) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_server_id, user_id, user_data['uuid'], old_email or f"user_{user_id}@veilbot.com", now, now + remaining, key_data['tariff_id'], config)
                )
                
                # Удаляем старый ключ из базы после успешного создания нового
                cursor.execute("DELETE FROM v2ray_keys WHERE id = ?", (key_data['id'],))
                
                # Используем сохраненный config для отправки пользователю
                await message.answer(format_key_message_unified(config, protocol, tariff, remaining), reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                
            except Exception as e:
                logging.error(f"Ошибка при перевыпуске V2Ray ключа: {e}")
                
                # При ошибке пытаемся удалить созданного пользователя с сервера
                try:
                    if 'user_data' in locals() and user_data and user_data.get('uuid'):
                        await protocol_client.delete_user(user_data['uuid'])
                        logging.info(f"Deleted V2Ray user {user_data['uuid']} from server due to error")
                except Exception as cleanup_error:
                    logging.error(f"Failed to cleanup V2Ray user after error: {cleanup_error}")
                
                await message.answer("Ошибка при создании нового V2Ray ключа.", reply_markup=main_menu)
                return
        
        # Уведомление админу о перевыпуске
        server_change_msg = "на другом сервере" if new_server_id != old_server_id else "на том же сервере"
        admin_msg = (
            f"🔄 *Перевыпуск ключа*\n"
            f"Пользователь: `{user_id}`\n"
            f"Протокол: *{PROTOCOLS[protocol]['name']}*\n"
            f"Тариф: *{tariff.get('name', 'Неизвестно')}*\n"
            f"Сервер: `{new_server_id}` ({server_change_msg})\n"
            f"Срок действия: до <code>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now + remaining))}</code>\n"
        )
        if old_email:
            admin_msg += f"Email: `{old_email}`\n"
        try:
            await bot.send_message(ADMIN_ID, admin_msg, disable_web_page_preview=True, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Failed to send admin notification (reissue): {e}")

@dp.callback_query_handler(lambda c: c.data.startswith("reissue_key_"))
async def handle_reissue_key_callback(callback_query: types.CallbackQuery):
    """Обработчик выбора ключа для перевыпуска"""
    user_id = callback_query.from_user.id
    
    # Парсим callback_data: reissue_key_{type}_{id}
    parts = callback_query.data.split("_")
    if len(parts) != 4:
        await callback_query.answer("Ошибка: неверный формат данных")
        return
    
    key_type = parts[2]
    key_id = int(parts[3])
    
    # Получаем данные ключа
    with get_db_cursor() as cursor:
        if key_type == "outline":
            cursor.execute("""
                SELECT k.id, k.expiry_at, k.server_id, k.key_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol
                FROM keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.id = ? AND k.user_id = ?
            """, (key_id, user_id))
        else:  # v2ray
            cursor.execute("""
                SELECT k.id, k.expiry_at, k.server_id, k.v2ray_uuid, s.country, k.tariff_id, k.email, s.protocol, s.domain, s.v2ray_path
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.id = ? AND k.user_id = ?
            """, (key_id, user_id))
        
        key_data = cursor.fetchone()
        if not key_data:
            await callback_query.answer("Ключ не найден")
            return
        
        # Формируем словарь с данными ключа
        if key_type == "outline":
            key_dict = {
                'id': key_data[0],
                'expiry_at': key_data[1],
                'server_id': key_data[2],
                'key_id': key_data[3],
                'access_url': key_data[4],
                'country': key_data[5],
                'tariff_id': key_data[6],
                'email': key_data[7],
                'protocol': key_data[8],
                'type': 'outline'
            }
        else:
            key_dict = {
                'id': key_data[0],
                'expiry_at': key_data[1],
                'server_id': key_data[2],
                'v2ray_uuid': key_data[3],
                'country': key_data[4],
                'tariff_id': key_data[5],
                'email': key_data[6],
                'protocol': key_data[7],
                'domain': key_data[8],
                'v2ray_path': key_data[9],
                'type': 'v2ray'
            }
    
    # Перевыпускаем ключ
    logging.debug(f"Передаем key_dict в reissue_specific_key: {list(key_dict.keys())}")
    await reissue_specific_key(callback_query.message, user_id, key_dict)
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "cancel_reissue")
async def handle_cancel_reissue(callback_query: types.CallbackQuery):
    """Обработчик отмены перевыпуска ключа"""
    await callback_query.message.edit_text("Перевыпуск ключа отменен.")
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("change_country_"))
async def handle_change_country_callback(callback_query: types.CallbackQuery):
    """Обработчик выбора ключа для смены страны"""
    user_id = callback_query.from_user.id
    
    # Парсим callback_data: change_country_{type}_{id}
    parts = callback_query.data.split("_")
    if len(parts) != 4:
        await callback_query.answer("Ошибка: неверный формат данных")
        return
    
    key_type = parts[2]
    key_id = int(parts[3])
    
    # Получаем данные ключа
    with get_db_cursor() as cursor:
        if key_type == "outline":
            cursor.execute("""
                SELECT k.id, k.expiry_at, k.server_id, k.key_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol
                FROM keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.id = ? AND k.user_id = ?
            """, (key_id, user_id))
        else:  # v2ray
            cursor.execute("""
                SELECT k.id, k.expiry_at, k.server_id, k.v2ray_uuid, s.country, k.tariff_id, k.email, s.protocol, s.domain, s.v2ray_path
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.id = ? AND k.user_id = ?
            """, (key_id, user_id))
        
        key_data = cursor.fetchone()
        if not key_data:
            await callback_query.answer("Ключ не найден")
            return
        
        # Формируем словарь с данными ключа
        if key_type == "outline":
            key_dict = {
                'id': key_data[0],
                'expiry_at': key_data[1],
                'server_id': key_data[2],
                'key_id': key_data[3],
                'access_url': key_data[4],
                'country': key_data[5],
                'tariff_id': key_data[6],
                'email': key_data[7],
                'protocol': key_data[8],
                'type': 'outline'
            }
        else:  # v2ray
            key_dict = {
                'id': key_data[0],
                'expiry_at': key_data[1],
                'server_id': key_data[2],
                'v2ray_uuid': key_data[3],
                'country': key_data[4],
                'tariff_id': key_data[5],
                'email': key_data[6],
                'protocol': key_data[7],
                'type': 'v2ray',
                'domain': key_data[8],
                'v2ray_path': key_data[9]
            }
    
    await callback_query.answer()
    await show_country_change_menu(callback_query.message, user_id, key_dict)

@dp.callback_query_handler(lambda c: c.data == "cancel_country_change")
async def handle_cancel_country_change(callback_query: types.CallbackQuery):
    """Обработчик отмены смены страны"""
    await callback_query.answer()
    await callback_query.message.answer("Смена страны отменена.", reply_markup=main_menu)

@dp.callback_query_handler(lambda c: c.data.startswith("change_protocol_"))
async def handle_change_protocol_callback(callback_query: types.CallbackQuery):
    """Обработчик выбора ключа для смены протокола"""
    user_id = callback_query.from_user.id
    
    # Парсим callback_data: change_protocol_{type}_{id}
    parts = callback_query.data.split("_")
    if len(parts) != 4:
        await callback_query.answer("Ошибка: неверный формат данных")
        return
    
    key_type = parts[2]
    key_id = int(parts[3])
    
    # Получаем данные ключа
    with get_db_cursor() as cursor:
        if key_type == "outline":
            cursor.execute("""
                SELECT k.id, k.expiry_at, k.server_id, k.key_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol
                FROM keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.id = ? AND k.user_id = ?
            """, (key_id, user_id))
        else:  # v2ray
            cursor.execute("""
                SELECT k.id, k.expiry_at, k.server_id, k.v2ray_uuid, s.country, k.tariff_id, k.email, s.protocol, s.domain, s.v2ray_path
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.id = ? AND k.user_id = ?
            """, (key_id, user_id))
        
        key_data = cursor.fetchone()
        if not key_data:
            await callback_query.answer("Ключ не найден")
            return
        
        # Формируем словарь с данными ключа
        if key_type == "outline":
            key_dict = {
                'id': key_data[0],
                'expiry_at': key_data[1],
                'server_id': key_data[2],
                'key_id': key_data[3],
                'access_url': key_data[4],
                'country': key_data[5],
                'tariff_id': key_data[6],
                'email': key_data[7],
                'protocol': key_data[8],
                'type': 'outline'
            }
        else:
            key_dict = {
                'id': key_data[0],
                'expiry_at': key_data[1],
                'server_id': key_data[2],
                'v2ray_uuid': key_data[3],
                'country': key_data[4],
                'tariff_id': key_data[5],
                'email': key_data[6],
                'protocol': key_data[7],
                'domain': key_data[8],
                'v2ray_path': key_data[9],
                'type': 'v2ray'
            }
    
    # Меняем протокол для ключа
    await change_protocol_for_key(callback_query.message, user_id, key_dict)
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "cancel_protocol_change")
async def handle_cancel_protocol_change(callback_query: types.CallbackQuery):
    """Обработчик отмены смены протокола"""
    await callback_query.message.edit_text("Смена протокола отменена.")
    await callback_query.answer()

async def broadcast_message(message_text: str, admin_id: int = None):
    """
    Функция для рассылки сообщений всем пользователям бота
    
    Args:
        message_text (str): Текст сообщения для рассылки
        admin_id (int): ID администратора для уведомлений о результатах рассылки
    """
    success_count = 0
    failed_count = 0
    total_users = 0
    
    try:
        # Получаем всех пользователей из таблицы users
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT user_id FROM users 
                WHERE blocked = 0
                ORDER BY user_id
            """)
            user_ids = [row[0] for row in cursor.fetchall()]
            total_users = len(user_ids)
        
        if total_users == 0:
            if admin_id:
                await bot.send_message(admin_id, "❌ Нет пользователей для рассылки")
            return
        
        # Отправляем сообщение каждому пользователю
        for user_id in user_ids:
            try:
                await bot.send_message(user_id, message_text, parse_mode='Markdown')
                success_count += 1
                # Небольшая задержка, чтобы не превысить лимиты Telegram
                await asyncio.sleep(0.05)
            except Exception as e:
                failed_count += 1
                logging.error(f"Ошибка отправки сообщения пользователю {user_id}: {e}")
                continue
        
        # Отправляем отчет администратору
        if admin_id:
            report = (
                f"📊 *Отчет о рассылке*\n\n"
                f"✅ Успешно отправлено: {success_count}\n"
                f"❌ Ошибок: {failed_count}\n"
                f"📈 Всего пользователей: {total_users}\n"
                f"📊 Процент успеха: {(success_count/total_users*100):.1f}%"
            )
            await bot.send_message(admin_id, report, parse_mode='Markdown')
            
    except Exception as e:
        error_msg = f"❌ Ошибка при рассылке: {e}"
        logging.error(error_msg)
        if admin_id:
            await bot.send_message(admin_id, error_msg)

@dp.message_handler(commands=["broadcast"])
async def handle_broadcast_command(message: types.Message):
    """
    Обработчик команды /broadcast для администратора
    Использование: /broadcast <текст сообщения>
    """
    # Проверяем, что команда отправлена администратором
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет прав для выполнения этой команды")
        return
    
    # Получаем текст сообщения (убираем команду /broadcast)
    command_parts = message.text.split(' ', 1)
    if len(command_parts) < 2:
        await message.answer(
            "❌ Неверный формат команды\n"
            "Использование: /broadcast <текст сообщения>\n\n"
            "Пример:\n"
            "/broadcast 🔔 Важное обновление! Добавлены новые серверы."
        )
        return
    
    broadcast_text = command_parts[1]
    
    # Сохраняем текст в временное хранилище
    text_hash = hash(broadcast_text)
    broadcast_texts[text_hash] = broadcast_text
    
    # Подтверждение рассылки
    confirm_keyboard = InlineKeyboardMarkup()
    confirm_keyboard.add(
        InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_broadcast:{text_hash}"),
        InlineKeyboardButton("❌ Отменить", callback_data="cancel_broadcast")
    )
    
    await message.answer(
        f"📢 *Предварительный просмотр рассылки:*\n\n"
        f"{broadcast_text}\n\n"
        f"⚠️ Это сообщение будет отправлено всем пользователям бота!",
        reply_markup=confirm_keyboard,
        parse_mode='Markdown'
    )

# Временное хранилище для текстов рассылки
broadcast_texts = {}

@dp.callback_query_handler(lambda c: c.data.startswith("confirm_broadcast:"))
async def handle_confirm_broadcast(callback_query: types.CallbackQuery):
    """Обработчик подтверждения рассылки"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("❌ У вас нет прав для выполнения этой команды")
        return
    
    # Получаем хеш сообщения из callback_data
    message_hash = int(callback_query.data.split(":")[1])
    
    # Получаем оригинальный текст из временного хранилища
    original_text = broadcast_texts.get(message_hash)
    if not original_text:
        await callback_query.answer("❌ Ошибка: текст рассылки не найден")
        return
    
    await callback_query.message.edit_text(
        "📤 *Рассылка запущена...*\n\n"
        "⏳ Пожалуйста, подождите. Отчет будет отправлен по завершении.",
        parse_mode='Markdown'
    )
    
    # Запускаем рассылку с оригинальным текстом
    await broadcast_message(original_text, ADMIN_ID)
    
    # Очищаем временное хранилище
    del broadcast_texts[message_hash]

@dp.callback_query_handler(lambda c: c.data == "cancel_broadcast")
async def handle_cancel_broadcast(callback_query: types.CallbackQuery):
    """Обработчик отмены рассылки"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("❌ У вас нет прав для выполнения этой команды")
        return
    
    await callback_query.message.edit_text("❌ Рассылка отменена")
    await callback_query.answer()

if __name__ == "__main__":
    import sys
    import traceback
    
    # Настройка логирования с маскированием секретов
    setup_logging(level="INFO")
    try:
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler('bot.log', maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        logging.getLogger().addHandler(file_handler)
    except Exception:
        pass
    
    logger = logging.getLogger(__name__)
    
    try:
        # Инициализация базы данных
        logger.info("Инициализация базы данных...")
        init_db()
        logger.info("База данных инициализирована успешно")
        
        # Создание event loop
        loop = asyncio.get_event_loop()
        
        # Запуск фоновых задач
        logger.info("Запуск фоновых задач...")
        background_tasks = [
            process_pending_paid_payments(),
            auto_delete_expired_keys(),
            notify_expiring_keys(),
            check_key_availability()
        ]
        
        for task in background_tasks:
            try:
                loop.create_task(task)
                logger.info(f"Фоновая задача {task.__name__} запущена")
            except Exception as e:
                logger.error(f"Ошибка при запуске фоновой задачи {task.__name__}: {e}")
        
        logger.info("Запуск бота...")
        logging.info("🚀 VeilBot запущен с оптимизацией памяти")
        logging.info("Updates were skipped successfully.")
        
        # Настройка централизованной обработки ошибок
        logger.info("Настройка обработчика ошибок...")
        error_handler = setup_error_handler(bot, ADMIN_ID)
        logger.info("Обработчик ошибок настроен")
        
        # Запуск бота с обработкой ошибок
        executor.start_polling(dp, skip_updates=True, loop=loop)
        
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
        logging.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        logging.critical(f"Критическая ошибка: {e}")
        logging.error("Проверьте логи в файле bot.log")
        sys.exit(1)
