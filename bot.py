import asyncio
import time
import sqlite3
import re
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from config import TELEGRAM_BOT_TOKEN
from db import init_db
from outline import create_key, delete_key
from payment import create_payment, check_payment
from utils import get_db_cursor

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

main_menu = ReplyKeyboardMarkup(resize_keyboard=True)
main_menu.add(KeyboardButton("Купить доступ"))
main_menu.add(KeyboardButton("Мои ключи"))
main_menu.add(KeyboardButton("Получить месяц бесплатно"))

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
    countries = get_countries()
    if len(countries) <= 1:
        # Only one country, skip selection
        country = countries[0] if countries else None
        user_states[user_id] = {"state": "waiting_tariff", "country": country}
        await message.answer("Выберите тариф:", reply_markup=get_tariff_menu())
    else:
        user_states[user_id] = {"state": "waiting_country"}
        await message.answer("Выберите сервер:", reply_markup=get_country_menu(countries))

@dp.message_handler(lambda m: m.text == "🔙 Отмена")
async def handle_cancel(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_states:
        del user_states[user_id]
    await message.answer("Операция отменена. Выберите тариф:", reply_markup=get_tariff_menu())

@dp.message_handler(lambda m: m.text == "Мои ключи")
async def handle_my_keys_btn(message: types.Message):
    user_id = message.from_user.id
    now = int(time.time())
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT k.access_url, k.expiry_at
            FROM keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.expiry_at > ?
        """, (user_id, now))
        keys = cursor.fetchall()

    if not keys:
        await message.answer("У вас нет активных ключей.", reply_markup=main_menu)
        return

    msg = "*Ваши активные ключи:*\n\n"
    for access_url, exp in keys:
        minutes = int((exp - now) / 60)
        hours = minutes // 60
        days = hours // 24
        
        if days > 0:
            time_str = f"{days}д {hours % 24}ч"
        elif hours > 0:
            time_str = f"{hours}ч {minutes % 60}мин"
        else:
            time_str = f"{minutes}мин"
            
        msg += (
            f"`{access_url}`\n"
            f"⏳ Осталось времени: {time_str}\n\n"
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
    del user_states[user_id]
    await create_payment_with_email(message, user_id, tariff, email, country)

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
    user_states[user_id] = {"state": "waiting_tariff", "country": country}
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
    print(f"[DEBUG] handle_tariff_selection_with_country: user_id={user_id}, label={label}, state={user_states.get(user_id)}")
    state = user_states.get(user_id, {})
    country = state.get("country")
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
            await handle_free_tariff(cursor, message, user_id, tariff, country)
        else:
            await handle_paid_tariff(cursor, message, user_id, tariff, country)

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
    cursor.execute("SELECT id, expiry_at FROM keys WHERE user_id = ? AND expiry_at > ?", (user_id, now))
    existing_key = cursor.fetchone()
    if existing_key:
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

def extend_existing_key(cursor, existing_key, duration):
    new_expiry = existing_key[1] + duration
    cursor.execute("UPDATE keys SET expiry_at = ? WHERE id = ?", (new_expiry, existing_key[0]))

async def create_new_key_flow(cursor, message, user_id, tariff, email=None, country=None):
    now = int(time.time())
    # Проверяем наличие активного ключа
    cursor.execute("SELECT id, expiry_at, access_url FROM keys WHERE user_id = ? AND expiry_at > ? ORDER BY expiry_at DESC LIMIT 1", (user_id, now))
    existing_key = cursor.fetchone()
    if existing_key:
        extend_existing_key(cursor, existing_key, tariff['duration_sec'])
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
                cursor.execute('''
                    SELECT id, user_id, tariff_id, email FROM payments
                    WHERE status="paid" AND user_id NOT IN (SELECT user_id FROM keys)
                ''')
                payments = cursor.fetchall()
                for payment_id, user_id, tariff_id, email in payments:
                    # Получаем тариф
                    cursor.execute('SELECT name, duration_sec FROM tariffs WHERE id=?', (tariff_id,))
                    tariff_row = cursor.fetchone()
                    if not tariff_row:
                        logging.error(f"[AUTO-ISSUE] Не найден тариф id={tariff_id} для user_id={user_id}")
                        continue
                    tariff = {'id': tariff_id, 'name': tariff_row[0], 'duration_sec': tariff_row[1]}
                    # Выбираем сервер с местами
                    server = select_available_server(cursor)
                    if not server:
                        logging.error(f"[AUTO-ISSUE] Нет доступных серверов для user_id={user_id}, тариф={tariff}")
                        continue
                    # Создаём ключ
                    try:
                        key = await asyncio.get_event_loop().run_in_executor(None, create_key, server['api_url'], server['cert_sha256'])
                    except Exception as e:
                        logging.error(f"[AUTO-ISSUE] Ошибка при создании ключа для user_id={user_id}: {e}")
                        continue
                    if not key:
                        logging.error(f"[AUTO-ISSUE] Не удалось создать ключ для user_id={user_id}, тариф={tariff}")
                        continue
                    now = int(time.time())
                    expiry = now + tariff['duration_sec']
                    cursor.execute(
                        "INSERT INTO keys (server_id, user_id, access_url, expiry_at, key_id, created_at, email, tariff_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (server['id'], user_id, key["accessUrl"], expiry, key["id"], now, email, tariff_id)
                    )
                    # Можно уведомить пользователя через бота, если нужно
                    try:
                        await bot.send_message(user_id, format_key_message(key["accessUrl"]), reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")
                    except Exception as e:
                        logging.error(f"[AUTO-ISSUE] Не удалось отправить ключ user_id={user_id}: {e}")
        except Exception as e:
            logging.error(f"[AUTO-ISSUE] Общая ошибка фоновой задачи: {e}")
        await asyncio.sleep(300)

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
