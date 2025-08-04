import asyncio
import time
import sqlite3
import re
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from config import TELEGRAM_BOT_TOKEN, PROTOCOLS
from db import init_db
from outline import create_key, delete_key
from payment import create_payment, check_payment
from utils import get_db_cursor
from vpn_protocols import ProtocolFactory, get_protocol_instructions, format_duration

# Security configuration
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin"
}

ADMIN_ID = 46701395

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is not set in config.py")

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot)

# Simple state management for email collection
user_states = {}  # user_id -> {"state": ..., ...}

# Notification state for key availability
low_key_notified = False

# Главное меню
main_menu = ReplyKeyboardMarkup(resize_keyboard=True)
main_menu.add(KeyboardButton("Купить доступ"))
main_menu.add(KeyboardButton("Мои ключи"))
main_menu.add(KeyboardButton("Получить месяц бесплатно"))
main_menu.add(KeyboardButton("Помощь"))

# Меню выбора протокола
def get_protocol_selection_menu() -> ReplyKeyboardMarkup:
    menu = ReplyKeyboardMarkup(resize_keyboard=True)
    menu.add(KeyboardButton(f"{PROTOCOLS['outline']['icon']} {PROTOCOLS['outline']['name']}"))
    menu.add(KeyboardButton(f"{PROTOCOLS['v2ray']['icon']} {PROTOCOLS['v2ray']['name']}"))
    menu.add(KeyboardButton("ℹ️ Сравнить протоколы"))
    menu.add(KeyboardButton("🔙 Назад"))
    return menu

# Клавиатура для помощи
help_keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
help_keyboard.add(KeyboardButton("Перевыпустить ключ"))
help_keyboard.add(KeyboardButton("Сменить приложение"))
help_keyboard.add(KeyboardButton("🔙 Назад"))

cancel_keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
cancel_keyboard.add(KeyboardButton("🔙 Отмена"))

def get_tariff_menu() -> ReplyKeyboardMarkup:
    with get_db_cursor() as cursor:
        cursor.execute("SELECT id, name, price_rub, duration_sec FROM tariffs ORDER BY price_rub ASC")
        tariffs = cursor.fetchall()

    menu = ReplyKeyboardMarkup(resize_keyboard=True)
    for _, name, price, duration in tariffs:
        label = f"{name} — {price}₽" if price > 0 else f"{name} — бесплатно"
        menu.add(KeyboardButton(label))
    menu.add(KeyboardButton("🔙 Назад"))
    return menu

def format_key_message(access_url: str) -> str:
    return (
        f"*Ваш ключ* (коснитесь, чтобы скопировать):\n"
        f"`{access_url}`\n\n"
        "🔧 *Как подключиться:*\n"
        "1. Установите Outline:\n"
        "   • [App Store](https://apps.apple.com/app/outline-app/id1356177741)\n"
        "   • [Google Play](https://play.google.com/store/apps/details?id=org.outline.android.client)\n"
        "2. Откройте приложение и нажмите «Добавить сервер» или «+»\n"
        "3. Вставьте ключ выше"
    )

def format_key_message_unified(config: str, protocol: str, tariff: dict = None, remaining_time: int = None) -> str:
    """Унифицированное форматирование сообщения для обоих протоколов"""
    protocol_info = PROTOCOLS[protocol]
    
    # Базовая структура сообщения
    message = (
        f"*Ваш ключ {protocol_info['icon']} {protocol_info['name']}* (коснитесь, чтобы скопировать):\n"
        f"`{config}`\n\n"
    )
    
    # Добавляем информацию о времени
    if remaining_time is not None:
        # Если передано оставшееся время, используем его
        message += (
            f"⏱ Осталось времени: *{format_duration(remaining_time)}*\n\n"
        )
    elif tariff:
        # Иначе используем длительность тарифа (для новых ключей)
        message += (
            f"⏱ Осталось времени: *{format_duration(tariff['duration_sec'])}*\n\n"
        )
    
    # Добавляем инструкции по подключению
    message += (
        f"🔧 *Как подключиться:*\n"
        f"{get_protocol_instructions(protocol)}"
    )
    
    return message

def is_valid_email(email: str) -> bool:
    """Simple email validation"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

@dp.message_handler(commands=["start"])
async def handle_start(message: types.Message):
    args = message.get_args()
    user_id = message.from_user.id
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
async def handle_buy_menu(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_states:
        del user_states[user_id]
    
    # Показываем выбор протокола
    await message.answer(
        "Выберите VPN протокол:",
        reply_markup=get_protocol_selection_menu()
    )

@dp.message_handler(lambda m: m.text in [f"{PROTOCOLS['outline']['icon']} {PROTOCOLS['outline']['name']}", 
                                        f"{PROTOCOLS['v2ray']['icon']} {PROTOCOLS['v2ray']['name']}"])
async def handle_protocol_selection(message: types.Message):
    """Обработка выбора протокола"""
    user_id = message.from_user.id
    protocol = 'outline' if 'Outline' in message.text else 'v2ray'
    
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
    
    # Показываем список стран
    await message.answer(
        "Доступные страны:",
        reply_markup=get_country_menu(countries)
    )

@dp.message_handler(lambda m: m.text == "ℹ️ Сравнить протоколы")
async def handle_protocol_comparison(message: types.Message):
    """Сравнение протоколов"""
    comparison_text = """
🔒 **Outline VPN**
• Высокая скорость соединения
• Простая настройка
• Стабильная работа
• Подходит для большинства задач

🛡️ **V2Ray VLESS**
• Продвинутая обфускация трафика
• Лучшая защита от блокировок
• Больше настроек
• Рекомендуется для сложных случаев

Выберите протокол, который подходит именно вам!
    """
    await message.answer(comparison_text, reply_markup=get_protocol_selection_menu(), parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "🔙 Отмена")
async def handle_cancel(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_states:
        del user_states[user_id]
    await message.answer("Операция отменена. Выберите протокол:", reply_markup=get_protocol_selection_menu())

@dp.message_handler(lambda m: m.text == "Мои ключи")
async def handle_my_keys_btn(message: types.Message):
    user_id = message.from_user.id
    now = int(time.time())
    
    with get_db_cursor() as cursor:
        # Получаем Outline ключи с информацией о стране
        cursor.execute("""
            SELECT k.access_url, k.expiry_at, k.protocol, s.country
            FROM keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.expiry_at > ?
        """, (user_id, now))
        outline_keys = cursor.fetchall()
        
        # Получаем V2Ray ключи с информацией о стране
        cursor.execute("""
            SELECT k.v2ray_uuid, k.expiry_at, s.domain, s.v2ray_path, s.country, k.email
            FROM v2ray_keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.expiry_at > ?
        """, (user_id, now))
        v2ray_keys = cursor.fetchall()

    all_keys = []
    
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
    for v2ray_uuid, exp, domain, path, country, email in v2ray_keys:
        # Используем новый формат VLESS с Reality протоколом
        config = f"vless://{v2ray_uuid}@{domain}:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email or 'VeilBot-V2Ray'}"
        all_keys.append({
            'type': 'v2ray',
            'config': config,
            'expiry': exp,
            'protocol': 'v2ray',
            'country': country
        })

    if not all_keys:
        await message.answer("У вас нет активных ключей.", reply_markup=main_menu)
        return

    msg = "*Ваши активные ключи:*\n\n"
    for key in all_keys:
        minutes = int((key['expiry'] - now) / 60)
        hours = minutes // 60
        days = hours // 24
        
        if days > 0:
            time_str = f"{days}д {hours % 24}ч"
        elif hours > 0:
            time_str = f"{hours}ч {minutes % 60}мин"
        else:
            time_str = f"{minutes}мин"
        
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
    print(f"[DEBUG] handle_invite_friend called: user_id={message.from_user.id}")
    user_id = message.from_user.id
    bot_username = (await bot.get_me()).username
    invite_link = f"https://t.me/{bot_username}?start={user_id}"
    await bot.send_message(
        message.chat.id,
        f"Пригласите друга по этой ссылке:\n{invite_link}\n\nЕсли друг купит доступ, вы получите месяц бесплатно!",
        reply_markup=main_menu
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
    print(f"[DEBUG] handle_email_input called: user_id={user_id}, email={email}, state={user_states.get(user_id)}")
    if not is_valid_email(email):
        await message.answer("❌ Неверный формат email. Пожалуйста, введите корректный email адрес:", reply_markup=cancel_keyboard)
        return
    state = user_states.get(user_id, {})
    tariff = state.get("tariff")
    country = state.get("country")
    protocol = state.get("protocol", "outline")  # Получаем выбранный протокол
    del user_states[user_id]
    await create_payment_with_email_and_protocol(message, user_id, tariff, email, country, protocol)

@dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "waiting_country")
async def handle_country_selection(message: types.Message):
    if message.text == "Получить месяц бесплатно":
        user_id = message.from_user.id
        user_states.pop(user_id, None)
        await handle_invite_friend(message)
        return
    user_id = message.from_user.id
    country = message.text.strip()
    countries = get_countries()
    if country not in countries:
        await message.answer("Пожалуйста, выберите сервер из списка:", reply_markup=get_country_menu(countries))
        return
    
    # Сохраняем страну и переходим к выбору тарифа
    user_states[user_id] = {"state": "waiting_tariff", "country": country}
    await message.answer("Выберите тариф:", reply_markup=get_tariff_menu())

@dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "protocol_selected")
async def handle_protocol_country_selection(message: types.Message):
    """Обработка выбора страны после выбора протокола"""
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
    country = message.text.strip()
    protocol = user_state.get("protocol", "outline")
    
    # Получаем страны только для выбранного протокола
    countries = get_countries_by_protocol(protocol)
    
    if country not in countries:
        protocol_info = PROTOCOLS[protocol]
        await message.answer(
            f"Пожалуйста, выберите страну из списка для {protocol_info['name']}:", 
            reply_markup=get_country_menu(countries)
        )
        return
    
    # Сохраняем страну и переходим к выбору тарифа
    user_states[user_id] = {
        "state": "waiting_tariff", 
        "country": country,
        "protocol": protocol
    }
    await message.answer("Выберите тариф:", reply_markup=get_tariff_menu())

@dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "waiting_tariff" and "—" in m.text and any(w in m.text for w in ["₽", "бесплатно"]))
async def handle_tariff_selection_with_country(message: types.Message):
    if message.text == "Получить месяц бесплатно":
        user_id = message.from_user.id
        user_states.pop(user_id, None)
        await handle_invite_friend(message)
        return
    user_id = message.from_user.id
    label = message.text.strip()
    state = user_states.get(user_id, {})
    country = state.get("country")
    protocol = state.get("protocol", "outline")  # Получаем выбранный протокол
    
    # Parse tariff name and price from the label
    parts = label.split("—")
    if len(parts) != 2:
        await message.answer("Неверный формат тарифа.", reply_markup=main_menu)
        return
    tariff_name = parts[0].strip()
    price_part = parts[1].strip()
    if "бесплатно" in price_part:
        price = 0
    else:
        try:
            price = int(price_part.replace("₽", "").strip())
        except ValueError:
            await message.answer("Неверный формат цены.", reply_markup=main_menu)
            return
    with get_db_cursor(commit=True) as cursor:
        tariff = get_tariff_by_name_and_price(cursor, tariff_name, price)
        if not tariff:
            await message.answer("Не удалось найти тариф.", reply_markup=main_menu)
            return
        if tariff['price_rub'] == 0:
            await handle_free_tariff_with_protocol(cursor, message, user_id, tariff, country, protocol)
        else:
            await handle_paid_tariff_with_protocol(cursor, message, user_id, tariff, country, protocol)

def get_tariff_by_name_and_price(cursor, tariff_name, price):
    cursor.execute("SELECT id, name, price_rub, duration_sec FROM tariffs WHERE name = ? AND price_rub = ?", (tariff_name, price))
    row = cursor.fetchone()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "price_rub": row[2], "duration_sec": row[3]}

async def handle_free_tariff(cursor, message, user_id, tariff, country=None):
    if check_free_tariff_limit(cursor, user_id):
        await message.answer("Вы уже активировали бесплатный тариф за последние 24 часа.", reply_markup=main_menu)
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
            await message.answer("У вас уже есть активный бесплатный ключ. Получить новый можно через 24 часа после последней активации.", reply_markup=main_menu)
            return
    else:
        await create_new_key_flow(cursor, message, user_id, tariff, None, country)

def check_free_tariff_limit(cursor, user_id):
    # Найти последний бесплатный ключ пользователя
    cursor.execute("""
        SELECT expiry_at, created_at FROM keys k
        JOIN tariffs t ON k.tariff_id = t.id
        WHERE k.user_id = ? AND t.price_rub = 0
        ORDER BY k.expiry_at DESC LIMIT 1
    """, (user_id,))
    row = cursor.fetchone()
    if not row:
        return False
    expiry_at, created_at = row
    now = int(time.time())
    # Если ключ ещё активен — нельзя
    if expiry_at > now:
        return True
    # Если с момента истечения прошло менее 24 часов — нельзя
    if now - expiry_at < 86400:
        return True
    # Иначе можно
    return False

def check_free_tariff_limit_by_protocol_and_country(cursor, user_id, protocol="outline", country=None):
    """Проверка лимита бесплатных ключей для конкретного протокола и страны"""
    if protocol == "outline":
        if country:
            cursor.execute("""
                SELECT k.expiry_at, k.created_at FROM keys k
                JOIN tariffs t ON k.tariff_id = t.id
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND t.price_rub = 0 AND s.country = ?
                ORDER BY k.expiry_at DESC LIMIT 1
            """, (user_id, country))
        else:
            cursor.execute("""
                SELECT expiry_at, created_at FROM keys k
                JOIN tariffs t ON k.tariff_id = t.id
                WHERE k.user_id = ? AND t.price_rub = 0
                ORDER BY k.expiry_at DESC LIMIT 1
            """, (user_id,))
    else:  # v2ray
        if country:
            cursor.execute("""
                SELECT k.expiry_at, k.created_at FROM v2ray_keys k
                JOIN tariffs t ON k.tariff_id = t.id
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND t.price_rub = 0 AND s.country = ?
                ORDER BY k.expiry_at DESC LIMIT 1
            """, (user_id, country))
        else:
            cursor.execute("""
                SELECT expiry_at, created_at FROM v2ray_keys k
                JOIN tariffs t ON k.tariff_id = t.id
                WHERE k.user_id = ? AND t.price_rub = 0
                ORDER BY k.expiry_at DESC LIMIT 1
            """, (user_id,))
    
    row = cursor.fetchone()
    if not row:
        return False
    expiry_at, created_at = row
    now = int(time.time())
    # Если ключ ещё активен — нельзя
    if expiry_at > now:
        return True
    # Если с момента истечения прошло менее 24 часов — нельзя
    if now - expiry_at < 86400:
        return True
    # Иначе можно
    return False

def check_free_tariff_limit_by_protocol(cursor, user_id, protocol="outline"):
    """Проверка лимита бесплатных ключей для конкретного протокола (для обратной совместимости)"""
    return check_free_tariff_limit_by_protocol_and_country(cursor, user_id, protocol)

def extend_existing_key(cursor, existing_key, duration, email=None, tariff_id=None):
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
    # Проверяем наличие активного ключа
    cursor.execute("SELECT id, expiry_at, access_url FROM keys WHERE user_id = ? AND expiry_at > ? ORDER BY expiry_at DESC LIMIT 1", (user_id, now))
    existing_key = cursor.fetchone()
    if existing_key:
        extend_existing_key(cursor, existing_key, tariff['duration_sec'], email, tariff['id'])
        await message.answer(f"Ваш ключ продлён на {tariff['duration_sec']//86400} дней!\n\n{format_key_message(existing_key[2])}", reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
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
            print(f"[ERROR] Failed to send admin notification: {e}")
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
        print(f"[ERROR] Failed to send admin notification: {e}")

async def create_new_key_flow_with_protocol(cursor, message, user_id, tariff, email=None, country=None, protocol="outline"):
    """Создание нового ключа с поддержкой протоколов"""
    now = int(time.time())
    
    # Проверяем наличие активного ключа (для обоих протоколов)
    if protocol == "outline":
        cursor.execute("SELECT id, expiry_at, access_url FROM keys WHERE user_id = ? AND expiry_at > ? ORDER BY expiry_at DESC LIMIT 1", (user_id, now))
        existing_key = cursor.fetchone()
        if existing_key:
            extend_existing_key(cursor, existing_key, tariff['duration_sec'], email, tariff['id'])
            await message.answer(f"Ваш ключ продлён на {format_duration(tariff['duration_sec'])}!\n\n{format_key_message_unified(existing_key[2], protocol, tariff)}", reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
            return
    else:  # v2ray
        cursor.execute("SELECT k.id, k.expiry_at, k.v2ray_uuid, s.domain, s.v2ray_path FROM v2ray_keys k JOIN servers s ON k.server_id = s.id WHERE k.user_id = ? AND k.expiry_at > ? ORDER BY k.expiry_at DESC LIMIT 1", (user_id, now))
        existing_key = cursor.fetchone()
        if existing_key:
            # Продление V2Ray ключа
            new_expiry = existing_key[1] + tariff['duration_sec']
            if email:
                cursor.execute("UPDATE v2ray_keys SET expiry_at = ?, tariff_id = ?, email = ? WHERE id = ?", (new_expiry, tariff['id'], email, existing_key[0]))
            else:
                cursor.execute("UPDATE v2ray_keys SET expiry_at = ?, tariff_id = ? WHERE id = ?", (new_expiry, tariff['id'], existing_key[0]))
            
            # Формируем конфигурацию V2Ray
            v2ray_uuid = existing_key[2]
            domain = existing_key[3]
            path = existing_key[4] or '/v2ray'
            # Используем новый формат Reality протокола
            config = f"vless://{v2ray_uuid}@{domain}:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email or 'VeilBot-V2Ray'}"
            
            await message.answer(f"Ваш ключ продлён на {format_duration(tariff['duration_sec'])}!\n\n{format_key_message_unified(config, protocol, tariff)}", reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
            return
    
    # Если нет активного ключа — создаём новый
    server = select_available_server_by_protocol(cursor, country, protocol)
    if not server:
        await message.answer(f"Нет доступных серверов {PROTOCOLS[protocol]['name']} в выбранной стране.", reply_markup=main_menu)
        return
    
    try:
        # Создаем протокол-клиент
        server_config = {
            'api_url': server[2],
            'cert_sha256': server[3],
            'api_key': server[5],
            'domain': server[4],
            'path': server[6]
        }
        
        protocol_client = ProtocolFactory.create_protocol(protocol, server_config)
        
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
        else:  # v2ray
            cursor.execute("""
                INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (server[0], user_id, user_data['uuid'], email or f"user_{user_id}@veilbot.com", now, expiry, tariff['id']))
            
            # Получаем конфигурацию
            config = await protocol_client.get_user_config(user_data['uuid'], {
                'domain': server[4],
                'port': 443,
                'path': server[6] or '/v2ray',
                'email': email or f"user_{user_id}@veilbot.com"
            })
        
        # Отправляем пользователю
        await message.answer(
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
            print(f"[ERROR] Failed to send admin notification: {e}")
            
    except Exception as e:
        # При ошибке пытаемся удалить созданного пользователя с сервера
        print(f"[ERROR] Failed to create {protocol} key: {e}")
        try:
            if 'user_data' in locals() and user_data:
                if protocol == 'v2ray' and user_data.get('uuid'):
                    await protocol_client.delete_user(user_data['uuid'])
                    print(f"[CLEANUP] Deleted V2Ray user {user_data['uuid']} from server due to error")
                elif protocol == 'outline' and user_data.get('id'):
                    # Для Outline используем существующую функцию
                    from outline import delete_key
                    delete_key(server[2], server[3], user_data['id'])
                    print(f"[CLEANUP] Deleted Outline key {user_data['id']} from server due to error")
        except Exception as cleanup_error:
            print(f"[ERROR] Failed to cleanup {protocol} user after error: {cleanup_error}")
        
        # Отправляем сообщение об ошибке пользователю
        await message.answer(
            f"❌ Ошибка при создании ключа {PROTOCOLS[protocol]['icon']}.\n"
            f"Попробуйте позже или обратитесь к администратору.",
            reply_markup=main_menu
        )
        return

def select_available_server_by_protocol(cursor, country=None, protocol='outline'):
    """Выбор сервера с учетом протокола"""
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
    
    row = cursor.fetchone()
    if not row:
        return None
    
    return row

def format_key_message_with_protocol(config: str, protocol: str, tariff: dict) -> str:
    """Форматирование сообщения с учетом протокола"""
    protocol_info = PROTOCOLS[protocol]
    
    return (
        f"*Ваш ключ {protocol_info['icon']} {protocol_info['name']}* (коснитесь, чтобы скопировать):\n"
        f"`{config}`\n\n"
        f"📦 Тариф: *{tariff['name']}*\n"
        f"⏱ Срок действия: *{format_duration(tariff['duration_sec'])}*\n\n"
        f"🔧 *Как подключиться:*\n"
        f"{get_protocol_instructions(protocol)}"
    )

async def handle_free_tariff_with_protocol(cursor, message, user_id, tariff, country=None, protocol="outline"):
    """Обработка бесплатного тарифа с поддержкой протоколов"""
    
    # Проверяем лимит бесплатных ключей для выбранного протокола и страны
    if check_free_tariff_limit_by_protocol_and_country(cursor, user_id, protocol, country):
        if country:
            await message.answer(f"Вы уже активировали бесплатный тариф {PROTOCOLS[protocol]['name']} для страны {country} за последние 24 часа.", reply_markup=main_menu)
        else:
            await message.answer(f"Вы уже активировали бесплатный тариф {PROTOCOLS[protocol]['name']} за последние 24 часа.", reply_markup=main_menu)
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
                await message.answer(f"У вас уже есть активный бесплатный ключ {PROTOCOLS[protocol]['name']} для страны {country}. Получить новый можно через 24 часа после последней активации.", reply_markup=main_menu)
            else:
                await message.answer(f"У вас уже есть активный бесплатный ключ {PROTOCOLS[protocol]['name']}. Получить новый можно через 24 часа после последней активации.", reply_markup=main_menu)
            return
    else:
        await create_new_key_flow_with_protocol(cursor, message, user_id, tariff, None, country, protocol)

async def handle_paid_tariff_with_protocol(cursor, message, user_id, tariff, country=None, protocol="outline"):
    """Обработка платного тарифа с поддержкой протоколов"""
    await create_payment_with_email_and_protocol(message, user_id, tariff, None, country, protocol)

async def create_payment_with_email_and_protocol(message, user_id, tariff, email=None, country=None, protocol="outline"):
    """Создание платежа с поддержкой протоколов"""
    print(f"[DEBUG] create_payment_with_email_and_protocol: user_id={user_id}, email={email}, tariff={tariff}, country={country}, protocol={protocol}")
    if email:
        # Если email уже есть, создаем платеж сразу
        print(f"[DEBUG] Creating payment: amount={tariff['price_rub']}, description='Покупка тарифа {tariff['name']}', email={email}")
        payment_id, payment_url = await asyncio.get_event_loop().run_in_executor(
            None, create_payment, tariff['price_rub'], f"Покупка тарифа '{tariff['name']}'", email
        )
        if not payment_id:
            await message.answer("Ошибка при создании платежа.", reply_markup=main_menu)
            return
        
        # Сохраняем информацию о платеже
        with get_db_cursor(commit=True) as cursor:
            cursor.execute("""
                INSERT INTO payments (payment_id, user_id, tariff_id, amount, email, status, created_at, country, protocol)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (payment_id, user_id, tariff['id'], tariff['price_rub'], email, 'pending', int(time.time()), country, protocol))
        
        # Выбираем сервер с учетом протокола
        with get_db_cursor() as cursor:
            server = select_available_server_by_protocol(cursor, country, protocol)
            if not server:
                await message.answer(f"Нет доступных серверов {PROTOCOLS[protocol]['name']} в выбранной стране.", reply_markup=main_menu)
                return
        
        # Используем URL, возвращаемый функцией create_payment
        if not payment_url:
            payment_url = f"https://yoomoney.ru/checkout/payments/v2/contract?orderId={payment_id}"
        
        # Создаем inline клавиатуру для оплаты
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("💳 Оплатить", url=payment_url))
        keyboard.add(InlineKeyboardButton("🔙 Отмена", callback_data="cancel_payment"))
        
        await message.answer(
            f"💳 *Оплата {PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}*\n\n"
            f"📦 Тариф: *{tariff['name']}*\n"
            f"💰 Сумма: *{tariff['price_rub']}₽*\n"
            f"📧 Email: `{email}`\n\n"
            "Нажмите кнопку ниже для оплаты:",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        
        # Запускаем ожидание платежа
        asyncio.create_task(wait_for_payment_with_protocol(message, payment_id, server, user_id, tariff, country, protocol))
    else:
        # Если email нет, запрашиваем его
        user_states[user_id] = {
            "state": "waiting_email",
            "tariff": tariff,
            "country": country,
            "protocol": protocol
        }
        await message.answer("Введите ваш email для получения ключа:", reply_markup=cancel_keyboard)

def select_available_server(cursor, country=None):
    now = int(time.time())
    if country:
        servers = cursor.execute("SELECT id, api_url, cert_sha256, max_keys FROM servers WHERE active = 1 AND country = ?", (country,)).fetchall()
    else:
        servers = cursor.execute("SELECT id, api_url, cert_sha256, max_keys FROM servers WHERE active = 1").fetchall()
    for s_id, api_url, cert_sha256, max_keys in servers:
        cursor.execute("SELECT COUNT(*) FROM keys WHERE server_id = ? AND expiry_at > ?", (s_id, now))
        active_keys = cursor.fetchone()[0]
        if active_keys < max_keys:
            return {"id": s_id, "api_url": api_url, "cert_sha256": cert_sha256}
    return None

async def handle_paid_tariff(cursor, message, user_id, tariff, country=None):
    user_states[user_id] = {
        "state": "waiting_email",
        "tariff": tariff,
        "country": country
    }
    print(f"[DEBUG] handle_paid_tariff: user_id={user_id}, set state to waiting_email, tariff={tariff}, country={country}")
    await message.answer(
        f"💳 Для создания платежа необходимо указать email для чека.\n\n"
        f"Тариф: {tariff['name']}\n"
        f"Стоимость: {tariff['price_rub']}₽\n\n"
        f"Пожалуйста, введите ваш email адрес:",
        reply_markup=cancel_keyboard
    )

async def create_payment_with_email(message, user_id, tariff, email, country=None):
    print(f"[DEBUG] create_payment_with_email called: user_id={user_id}, email={email}, tariff={tariff}, country={country}")
    with get_db_cursor() as cursor:
        server = select_available_server(cursor, country)
        if not server:
            await message.answer("На всех серверах закончились места. Попробуйте позже.", reply_markup=main_menu)
            return
    payment_id, url = await asyncio.get_event_loop().run_in_executor(
        None, create_payment, tariff['price_rub'], f"Покупка тарифа '{tariff['name']}'", email
    )
    if not payment_id:
        await message.answer("Ошибка при создании платежа.", reply_markup=main_menu)
        return
    with get_db_cursor(commit=True) as cursor:
        cursor.execute("INSERT INTO payments (user_id, tariff_id, payment_id, email) VALUES (?, ?, ?, ?)", (user_id, tariff['id'], payment_id, email))
        await message.answer(f"Перейдите по ссылке для оплаты: {url}", reply_markup=main_menu)
        asyncio.create_task(wait_for_payment(message, payment_id, server, user_id, tariff, country))

async def wait_for_payment(message, payment_id, server, user_id, tariff, country=None):
    for _ in range(60): # 5 минут
        await asyncio.sleep(5)
        if await asyncio.get_event_loop().run_in_executor(None, check_payment, payment_id):
            with get_db_cursor(commit=True) as cursor:
                cursor.execute("UPDATE payments SET status = 'paid' WHERE payment_id = ?", (payment_id,))
                cursor.execute("SELECT email FROM payments WHERE payment_id = ?", (payment_id,))
                payment_data = cursor.fetchone()
                email = payment_data[0] if payment_data else None
                await create_new_key_flow(cursor, message, user_id, tariff, email, country)
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
                            await create_new_key_flow(cursor, message, referrer_id, bonus_tariff_dict)
                            await bot.send_message(referrer_id, "🎉 Вам выдан бесплатный месяц за приглашённого друга!")
                    cursor.execute("UPDATE referrals SET bonus_issued = 1 WHERE referred_id = ?", (user_id,))
            return

async def wait_for_payment_with_protocol(message, payment_id, server, user_id, tariff, country=None, protocol="outline"):
    """Ожидание платежа с поддержкой протоколов"""
    for _ in range(60): # 5 минут
        await asyncio.sleep(5)
        if await asyncio.get_event_loop().run_in_executor(None, check_payment, payment_id):
            with get_db_cursor(commit=True) as cursor:
                cursor.execute("UPDATE payments SET status = 'paid' WHERE payment_id = ?", (payment_id,))
                cursor.execute("SELECT email FROM payments WHERE payment_id = ?", (payment_id,))
                payment_data = cursor.fetchone()
                email = payment_data[0] if payment_data else None
                await create_new_key_flow_with_protocol(cursor, message, user_id, tariff, email, country, protocol)
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

async def auto_delete_expired_keys():
    while True:
        with get_db_cursor(commit=True) as cursor:
            now = int(time.time())
            # Get expired keys with server info before deleting
            cursor.execute("""
                SELECT k.id, k.key_id, s.api_url, s.cert_sha256 
                FROM keys k 
                JOIN servers s ON k.server_id = s.id 
                WHERE k.expiry_at <= ?
            """, (now,))
            expired_keys = cursor.fetchall()
            
            # Delete keys from Outline server first
            for key_id_db, key_id_outline, api_url, cert_sha256 in expired_keys:
                if key_id_outline:
                    success = await asyncio.get_event_loop().run_in_executor(
                        None, delete_key, api_url, cert_sha256, key_id_outline
                    )
                    if not success:
                        print(f"Failed to delete key {key_id_outline} from Outline server")
            
            # Then delete from database
            cursor.execute("DELETE FROM keys WHERE expiry_at <= ?", (now,))
            deleted_count = cursor.rowcount
            if deleted_count > 0:
                print(f"Deleted {deleted_count} expired keys")
        await asyncio.sleep(600)

async def notify_expiring_keys():
    while True:
        with get_db_cursor(commit=True) as cursor:
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
                    message = f"⏳ Ваш ключ истечет через 1 день:\n`{access_url}`\nПродлите доступ:"
                    new_notified = 3
                # 1 hour notification
                elif original_duration > one_hour and remaining_time <= one_hour and notified < 2:
                    message = f"⏳ Ваш ключ истечет через 1 час:\n`{access_url}`\nПродлите доступ:"
                    new_notified = 2
                # 10 minutes notification (универсально для всех ключей)
                elif remaining_time > 0 and remaining_time <= 600 and notified < 4:
                    minutes_remaining = (remaining_time % 3600) // 60
                    message = f"⏳ Ваш ключ истечет через {minutes_remaining} мин.\n`{access_url}`\nПродлите доступ:"
                    new_notified = 4
                # 10% notification
                elif remaining_time > 0 and remaining_time <= ten_percent_threshold and notified < 1:
                    hours_remaining = remaining_time // 3600
                    minutes_remaining = (remaining_time % 3600) // 60
                    if hours_remaining > 0:
                        message = f"⏳ Ваш ключ истечет через {hours_remaining}ч. {minutes_remaining}мин.:\n`{access_url}`\nПродлите доступ:"
                    else:
                        message = f"⏳ Ваш ключ истечет через {minutes_remaining}мин.:\n`{access_url}`\nПродлите доступ:"
                    new_notified = 1

                if message:
                    keyboard = InlineKeyboardMarkup()
                    keyboard.add(InlineKeyboardButton("🔁 Продлить", callback_data="buy"))
                    await bot.send_message(user_id, message, reply_markup=keyboard, disable_web_page_preview=True, parse_mode="Markdown")
                    cursor.execute("UPDATE keys SET notified = ? WHERE id = ?", (new_notified, key_id_db))
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
            print(f"Error in check_key_availability: {e}")

        await asyncio.sleep(300) # Check every 5 minutes

@dp.callback_query_handler(lambda c: c.data == "buy")
async def callback_buy_button(callback_query: types.CallbackQuery):
    await bot.send_message(callback_query.from_user.id, "Выберите тариф:", reply_markup=get_tariff_menu())

# --- Country selection helpers ---
def get_countries():
    with get_db_cursor() as cursor:
        cursor.execute("SELECT DISTINCT country FROM servers WHERE active = 1 AND country IS NOT NULL AND country != ''")
        countries = [row[0] for row in cursor.fetchall()]
    return countries

def get_countries_by_protocol(protocol):
    """Получить страны только для указанного протокола"""
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT country 
            FROM servers 
            WHERE active = 1 
            AND country IS NOT NULL 
            AND country != '' 
            AND protocol = ?
        """, (protocol,))
        countries = [row[0] for row in cursor.fetchall()]
    return countries

def get_country_menu(countries):
    menu = ReplyKeyboardMarkup(resize_keyboard=True)
    for country in countries:
        menu.add(KeyboardButton(country))
    menu.add(KeyboardButton("🔙 Назад"))
    return menu

async def process_pending_paid_payments():
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
                    cursor.execute('SELECT name, duration_sec FROM tariffs WHERE id=?', (tariff_id,))
                    tariff_row = cursor.fetchone()
                    if not tariff_row:
                        logging.error(f"[AUTO-ISSUE] Не найден тариф id={tariff_id} для user_id={user_id}")
                        continue
                    tariff = {'id': tariff_id, 'name': tariff_row[0], 'duration_sec': tariff_row[1]}
                    
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
                            
                            now = int(time.time())
                            expiry = now + tariff['duration_sec']
                            cursor.execute(
                                "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (server['id'], user_id, user_data['uuid'], email or f"user_{user_id}@veilbot.com", now, expiry, tariff_id)
                            )
                            
                            # Получаем конфигурацию
                            config = await protocol_client.get_user_config(user_data['uuid'], {
                                'domain': server.get('domain', 'veil-bot.ru'),
                                'port': 443,
                                'path': server.get('v2ray_path', '/v2ray')
                            })
                            
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
        "Выберите вариант ниже:"
    )
    await message.answer(help_text, reply_markup=help_keyboard)

@dp.message_handler(lambda m: m.text == "🔙 Назад" and message.reply_markup == help_keyboard)
async def handle_help_back(message: types.Message):
    await message.answer("Главное меню:", reply_markup=main_menu)

@dp.message_handler(lambda m: m.text == "Сменить приложение")
async def handle_change_app(message: types.Message):
    user_id = message.from_user.id
    now = int(time.time())
    
    with get_db_cursor() as cursor:
        # Получаем все активные ключи пользователя
        cursor.execute("""
            SELECT k.id, k.expiry_at, k.server_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol, 'outline' as key_type
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
                'access_url': key[3],
                'country': key[4],
                'tariff_id': key[5],
                'email': key[6],
                'protocol': key[7],
                'type': key[8]
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
            await message.answer("У вас нет активных ключей для смены протокола.", reply_markup=main_menu)
            return
        
        if len(all_keys) == 1:
            # Если только один ключ, меняем его протокол сразу
            await change_protocol_for_key(message, user_id, all_keys[0])
        else:
            # Если несколько ключей, показываем список для выбора
            await show_protocol_change_menu(message, user_id, all_keys)

@dp.message_handler(lambda m: m.text == "Перевыпустить ключ")
async def handle_reissue_key(message: types.Message):
    user_id = message.from_user.id
    now = int(time.time())
    
    with get_db_cursor() as cursor:
        # Получаем все активные ключи пользователя
        cursor.execute("""
            SELECT k.id, k.expiry_at, k.server_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol, 'outline' as key_type
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
                'access_url': key[3],
                'country': key[4],
                'tariff_id': key[5],
                'email': key[6],
                'protocol': key[7],
                'type': key[8]
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
        
        # Ищем сервер той же страны с новым протоколом
        cursor.execute("""
            SELECT id, api_url, cert_sha256, domain, v2ray_path, api_key FROM servers 
            WHERE active = 1 AND country = ? AND protocol = ?
        """, (country, new_protocol))
        servers = cursor.fetchall()
        
        if not servers:
            await message.answer(f"Нет серверов {PROTOCOLS[new_protocol]['name']} в вашей стране для смены протокола.", reply_markup=main_menu)
            return
        
        # Берём первый подходящий сервер
        new_server_id, api_url, cert_sha256, domain, v2ray_path, api_key = servers[0]
        
        # Удаляем старый ключ
        if key_data['type'] == "outline":
            # Удаляем старый ключ из Outline сервера
            cursor.execute("SELECT api_url, cert_sha256 FROM servers WHERE id = ?", (old_server_id,))
            old_server_data = cursor.fetchone()
            if old_server_data:
                old_api_url, old_cert_sha256 = old_server_data
                try:
                    await asyncio.get_event_loop().run_in_executor(None, delete_key, old_api_url, old_cert_sha256, key_data['key_id'])
                except Exception as e:
                    print(f"[WARNING] Не удалось удалить старый Outline ключ (возможно уже удален): {e}")
            
            # Удаляем старый ключ из базы
            cursor.execute("DELETE FROM keys WHERE id = ?", (key_data['id'],))
        else:  # v2ray
            # Удаляем старый ключ из V2Ray сервера
            # Получаем данные старого сервера для правильного API ключа
            cursor.execute("SELECT api_url, api_key FROM servers WHERE id = ?", (old_server_id,))
            old_server_data = cursor.fetchone()
            if old_server_data:
                old_api_url, old_api_key = old_server_data
                server_config = {'api_url': old_api_url, 'api_key': old_api_key}
                protocol_client = ProtocolFactory.create_protocol(old_protocol, server_config)
                try:
                    old_uuid = key_data['v2ray_uuid']
                    await protocol_client.delete_user(old_uuid)
                except Exception as e:
                    print(f"[WARNING] Не удалось удалить старый V2Ray ключ (возможно уже удален): {e}")
            
            # Удаляем старый ключ из базы
            cursor.execute("DELETE FROM v2ray_keys WHERE id = ?", (key_data['id'],))
        
        # Создаём новый ключ на другом протоколе
        if new_protocol == "outline":
            # Создаём новый Outline ключ
            try:
                key = await asyncio.get_event_loop().run_in_executor(None, create_key, api_url, cert_sha256)
                if not key:
                    await message.answer("Ошибка при создании нового Outline ключа.", reply_markup=main_menu)
                    return
            except Exception as e:
                print(f"[ERROR] Ошибка при создании Outline ключа: {e}")
                await message.answer("Ошибка при создании нового ключа.", reply_markup=main_menu)
                return
            
            # Добавляем новый ключ с тем же сроком действия и email
            cursor.execute(
                "INSERT INTO keys (server_id, user_id, access_url, expiry_at, key_id, created_at, email, tariff_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_server_id, user_id, key["accessUrl"], now + remaining, key["id"], now, old_email, key_data['tariff_id'])
            )
            
            await message.answer(format_key_message_unified(key["accessUrl"], new_protocol, tariff, remaining), reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
            
        else:  # v2ray
            # Создаём новый V2Ray ключ
            server_config = {'api_url': api_url, 'api_key': api_key}
            protocol_client = ProtocolFactory.create_protocol(new_protocol, server_config)
            try:
                user_data = await protocol_client.create_user(old_email or f"user_{user_id}@veilbot.com")
                
                # Добавляем новый ключ с тем же сроком действия и email
                cursor.execute(
                    "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_server_id, user_id, user_data['uuid'], old_email or f"user_{user_id}@veilbot.com", now, now + remaining, key_data['tariff_id'])
                )
                
                # Получаем конфигурацию
                config = await protocol_client.get_user_config(user_data['uuid'], {
                    'domain': domain,
                    'port': 443,
                    'path': v2ray_path or '/v2ray',
                    'email': old_email or f"user_{user_id}@veilbot.com"
                })
                
                await message.answer(format_key_message_unified(config, new_protocol, tariff, remaining), reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                
            except Exception as e:
                print(f"[ERROR] Ошибка при создании нового V2Ray ключа: {e}")
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
            print(f"[ERROR] Failed to send admin notification (protocol change): {e}")
        
        # Ручной commit транзакции
        cursor.connection.commit()

async def reissue_specific_key(message: types.Message, user_id: int, key_data: dict):
    """Перевыпускает конкретный ключ"""
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
        
        # Ищем другой сервер той же страны и протокола
        cursor.execute("""
            SELECT id, api_url, cert_sha256, domain, v2ray_path, api_key FROM servers 
            WHERE active = 1 AND country = ? AND protocol = ? AND id != ?
        """, (country, protocol, old_server_id))
        servers = cursor.fetchall()
        
        if not servers:
            await message.answer(f"Нет других серверов {PROTOCOLS[protocol]['name']} в вашей стране для перевыпуска ключа.", reply_markup=main_menu)
            return
        
        # Берём первый подходящий сервер
        new_server_id, api_url, cert_sha256, domain, v2ray_path, api_key = servers[0]
        
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
                    if key_data['type'] == "outline" and 'key_id' in key_data:
                        await asyncio.get_event_loop().run_in_executor(None, delete_key, old_api_url, old_cert_sha256, key_data['key_id'])
                    else:
                        print(f"[WARNING] Пропускаем удаление Outline ключа - неверный тип или отсутствует key_id")
                except Exception as e:
                    print(f"[ERROR] Не удалось удалить старый Outline ключ: {e}")
            
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
                    print(f"[WARNING] Не удалось удалить старый V2Ray ключ (возможно уже удален): {e}")
                
                # Удаляем старый ключ из базы
                cursor.execute("DELETE FROM v2ray_keys WHERE id = ?", (key_data['id'],))
                
                # Добавляем новый ключ с тем же сроком действия и email
                cursor.execute(
                    "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_server_id, user_id, user_data['uuid'], old_email or f"user_{user_id}@veilbot.com", now, now + remaining, key_data['tariff_id'])
                )
                
                # Получаем конфигурацию
                config = await protocol_client.get_user_config(user_data['uuid'], {
                    'domain': domain,
                    'port': 443,
                    'path': v2ray_path or '/v2ray',
                    'email': old_email or f"user_{user_id}@veilbot.com"
                })
                
                await message.answer(format_key_message_unified(config, protocol, tariff, remaining), reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                
            except Exception as e:
                print(f"[ERROR] Ошибка при перевыпуске V2Ray ключа: {e}")
                
                # При ошибке пытаемся удалить созданного пользователя с сервера
                try:
                    if 'user_data' in locals() and user_data and user_data.get('uuid'):
                        await protocol_client.delete_user(user_data['uuid'])
                        print(f"[CLEANUP] Deleted V2Ray user {user_data['uuid']} from server due to error")
                except Exception as cleanup_error:
                    print(f"[ERROR] Failed to cleanup V2Ray user after error: {cleanup_error}")
                
                await message.answer("Ошибка при создании нового V2Ray ключа.", reply_markup=main_menu)
                return
        
        # Уведомление админу о перевыпуске
        admin_msg = (
            f"🔄 *Перевыпуск ключа*\n"
            f"Пользователь: `{user_id}`\n"
            f"Протокол: *{PROTOCOLS[protocol]['name']}*\n"
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
            print(f"[ERROR] Failed to send admin notification (reissue): {e}")

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
                SELECT k.id, k.expiry_at, k.server_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol
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
                'access_url': key_data[3],
                'country': key_data[4],
                'tariff_id': key_data[5],
                'email': key_data[6],
                'protocol': key_data[7],
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
    await reissue_specific_key(callback_query.message, user_id, key_dict)
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "cancel_reissue")
async def handle_cancel_reissue(callback_query: types.CallbackQuery):
    """Обработчик отмены перевыпуска ключа"""
    await callback_query.message.edit_text("Перевыпуск ключа отменен.")
    await callback_query.answer()

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
                SELECT k.id, k.expiry_at, k.server_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol
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
                'access_url': key_data[3],
                'country': key_data[4],
                'tariff_id': key_data[5],
                'email': key_data[6],
                'protocol': key_data[7],
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

if __name__ == "__main__":
    from aiogram import executor
    init_db()
    loop = asyncio.get_event_loop()
    # Запуск фоновой задачи автоматической выдачи ключей
    loop.create_task(process_pending_paid_payments())
    loop.create_task(auto_delete_expired_keys())
    loop.create_task(notify_expiring_keys())
    loop.create_task(check_key_availability())
    executor.start_polling(dp, skip_updates=True)
