"""
Обработчики для работы с подписками V2Ray
"""
import time
import logging
from typing import Dict, Any
from aiogram import Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import get_db_cursor
from bot.keyboards import get_main_menu, get_cancel_keyboard, get_payment_method_keyboard
from bot_rate_limiter import rate_limit
from bot.services.subscription_service import SubscriptionService
from app.repositories.subscription_repository import SubscriptionRepository
from vpn_protocols import format_duration
from validators import input_validator, ValidationError, is_valid_email

logger = logging.getLogger(__name__)

# Глобальная переменная для user_states (будет установлена при регистрации)
_user_states: Dict[int, Dict[str, Any]] = {}


async def format_subscription_info(subscription_data: tuple, server_count: int = 0) -> str:
    """
    Форматировать информацию о подписке для отображения пользователю
    
    Args:
        subscription_data: Кортеж с данными подписки (id, user_id, token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified)
        server_count: Количество серверов в подписке
    """
    subscription_id, user_id, token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified = subscription_data
    
    now = int(time.time())
    remaining_time = expires_at - now
    
    # Получаем информацию о тарифе
    tariff_name = "V2Ray"
    traffic_limit = "без ограничений"
    if tariff_id:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT name, traffic_limit_mb FROM tariffs WHERE id = ?", (tariff_id,))
            tariff_row = cursor.fetchone()
            if tariff_row:
                tariff_name = tariff_row[0]
                if tariff_row[1] and tariff_row[1] > 0:
                    traffic_limit = f"{tariff_row[1]} ГБ"
    
    # Форматируем дату истечения
    from datetime import datetime
    expiry_date = datetime.fromtimestamp(expires_at).strftime("%d.%m.%Y")
    remaining_str = format_duration(remaining_time)
    
    subscription_url = f"https://veil-bot.ru/api/subscription/{token}"
    
    msg = (
        f"📋 *Подписка V2Ray*\n\n"
        f"🔗 *Ссылка подписки:*\n"
        f"`{subscription_url}`\n\n"
        f"⏳ *Срок действия:*\n"
        f"{remaining_str} (до {expiry_date})\n\n"
        f"🌐 *Серверов в подписке:* {server_count}\n\n"
        f"📊 *Трафик:* {traffic_limit}\n\n"
        f"💡 *Как использовать:*\n"
        f"1. Откройте приложение V2Ray\n"
        f"2. Нажмите \"+\" → \"Импорт подписки\"\n"
        f"3. Вставьте ссылку выше\n"
        f"4. Все серверы будут добавлены автоматически\n\n"
        f"🔄 Подписка обновляется автоматически при добавлении новых серверов"
    )
    
    return msg


def format_bytes(bytes_value: int | None) -> str:
    """Форматировать байты в читаемый формат"""
    if bytes_value is None or bytes_value == 0:
        return "0 Б"
    
    value = float(bytes_value)
    for unit in ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']:
        if value < 1024.0:
            if unit == 'Б':
                return f"{int(value)} {unit}"
            else:
                return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} ПБ"


async def format_subscription_short_info(subscription_data: tuple) -> str:
    """
    Форматировать краткую информацию о подписке (срок действия, остаток трафика)
    
    Args:
        subscription_data: Кортеж с данными подписки (id, user_id, token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified)
    """
    subscription_id, user_id, token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified = subscription_data
    
    now = int(time.time())
    remaining_time = expires_at - now
    
    # Форматируем дату истечения
    from datetime import datetime
    expiry_date = datetime.fromtimestamp(expires_at).strftime("%d.%m.%Y")
    remaining_str = format_duration(remaining_time)
    
    # Получаем информацию о трафике
    repo = SubscriptionRepository()
    traffic_usage_bytes = repo.get_subscription_traffic_sum(subscription_id)
    traffic_limit_bytes = repo.get_subscription_traffic_limit(subscription_id)
    
    # Форматируем информацию о трафике
    if traffic_limit_bytes and traffic_limit_bytes > 0:
        traffic_usage_formatted = format_bytes(traffic_usage_bytes)
        traffic_limit_formatted = format_bytes(traffic_limit_bytes)
        remaining_bytes = max(0, traffic_limit_bytes - (traffic_usage_bytes or 0))
        remaining_traffic_formatted = format_bytes(remaining_bytes)
        
        traffic_info = f"{traffic_usage_formatted} / {traffic_limit_formatted}\n📊 Остаток: {remaining_traffic_formatted}"
    else:
        traffic_info = "без ограничений"
    
    msg = (
        f"📋 *Ваша подписка*\n\n"
        f"⏳ *Срок действия:*\n"
        f"{remaining_str} (до {expiry_date})\n\n"
        f"📊 *Трафик:* {traffic_info}"
    )
    
    return msg


async def handle_get_access(message: types.Message):
    """Обработчик кнопки 'Получить доступ'"""
    user_id = message.from_user.id
    
    # Кнопка доступна только для определенного пользователя
    if user_id != 6358556135:
        await message.answer(
            "❌ У вас нет доступа к этой функции.",
            reply_markup=get_main_menu(user_id)
        )
        return
    
    try:
        # Проверить наличие активной подписки
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified
                FROM subscriptions
                WHERE user_id = ? AND is_active = 1 AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (user_id, int(time.time())))
            subscription = cursor.fetchone()
        
        if subscription:
            # Показать краткую информацию о подписке и кнопку продления
            msg = await format_subscription_short_info(subscription)
            
            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton("🔄 Продлить подписку", callback_data="renew_subscription"))
            
            await message.answer(
                msg,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        else:
            # Предложить создать подписку
            with get_db_cursor() as cursor:
                cursor.execute("""
                    SELECT COUNT(*) FROM servers
                    WHERE protocol = 'v2ray' AND active = 1
                """)
                v2ray_server_count = cursor.fetchone()[0] or 0
            
            if v2ray_server_count == 0:
                await message.answer(
                    "❌ К сожалению, сейчас нет доступных серверов для создания подписки.\n"
                    "Пожалуйста, попробуйте позже.",
                    reply_markup=get_main_menu(user_id)
                )
                return
            
            msg = (
                f"📋 *Получить подписку*\n\n"
                f"Подписка дает доступ ко всем серверам:\n"
            )
            
            # Получаем список стран
            with get_db_cursor() as cursor:
                cursor.execute("""
                    SELECT DISTINCT country FROM servers
                    WHERE protocol = 'v2ray' AND active = 1 AND country IS NOT NULL AND country != ''
                    ORDER BY country
                """)
                countries = [row[0] for row in cursor.fetchall()]
            
            if countries:
                for country in countries[:10]:  # Показываем первые 10 стран
                    msg += f"• {country}\n"
                if len(countries) > 10:
                    msg += f"• и другие...\n"
            
            msg += (
                f"\n🔄 Автоматическое обновление при добавлении новых серверов\n\n"
                f"Выберите способ оплаты:"
            )
            
            # Показываем меню выбора способа оплаты (как в обычном flow)
            await message.answer(
                msg,
                reply_markup=get_payment_method_keyboard(),
                parse_mode="Markdown"
            )
            
            # Сохраняем состояние для создания подписки
            _user_states[user_id] = {
                'state': 'waiting_payment_method_for_subscription',
                'protocol': 'v2ray',
                'key_type': 'subscription'
            }
    
    except Exception as e:
        logger.error(f"Error in handle_get_access for user {user_id}: {e}", exc_info=True)
        await message.answer(
            "❌ Произошла ошибка при получении информации о подписке. Попробуйте позже.",
            reply_markup=get_main_menu(user_id)
        )


async def handle_copy_subscription(callback_query: types.CallbackQuery):
    """Обработчик callback для копирования ссылки подписки"""
    token = callback_query.data.split(":")[1]
    subscription_url = f"https://veil-bot.ru/api/subscription/{token}"
    
    await callback_query.answer(f"Ссылка скопирована: {subscription_url}", show_alert=False)
    await callback_query.message.answer(
        f"📋 *Ссылка подписки:*\n\n`{subscription_url}`\n\n"
        f"Скопируйте эту ссылку и вставьте в приложение V2Ray для импорта подписки.",
        parse_mode="Markdown"
    )


async def handle_refresh_subscription(callback_query: types.CallbackQuery):
    """Обработчик callback для обновления подписки"""
    token = callback_query.data.split(":")[1]
    user_id = callback_query.from_user.id
    
    try:
        # Инвалидируем кэш подписки
        from bot.services.subscription_service import invalidate_subscription_cache
        invalidate_subscription_cache(token)
        
        # Принудительно обновляем подписку
        service = SubscriptionService()
        content = await service.generate_subscription_content(token)
        
        if content:
            await callback_query.answer("✅ Подписка обновлена!", show_alert=False)
            await callback_query.message.answer(
                "✅ Подписка успешно обновлена!\n\n"
                "Новые серверы (если они были добавлены) теперь доступны в вашей подписке.",
                reply_markup=get_main_menu(user_id)
            )
        else:
            await callback_query.answer("❌ Не удалось обновить подписку", show_alert=True)
    
    except Exception as e:
        logger.error(f"Error refreshing subscription {token} for user {user_id}: {e}", exc_info=True)
        await callback_query.answer("❌ Произошла ошибка при обновлении подписки", show_alert=True)


async def handle_tariff_selection_for_subscription(message: types.Message):
    """Обработчик выбора тарифа для создания подписки"""
    user_id = message.from_user.id
    label = message.text.strip()
    
    # Проверка на кнопку "Назад"
    if label == "🔙 Назад":
        _user_states.pop(user_id, None)
        await message.answer("Главное меню:", reply_markup=get_main_menu(user_id))
        return
    
    # Парсим тариф из текста (точно так же, как в обычном обработчике)
    if "—" not in label:
        await message.answer("Неверный формат тарифа.", reply_markup=get_main_menu(user_id))
        return
    
    parts = label.split("—")
    if len(parts) != 2:
        await message.answer("Неверный формат тарифа.", reply_markup=get_main_menu(user_id))
        return
    
    tariff_name = parts[0].strip()
    price_part = parts[1].strip()
    
    # Получаем способ оплаты из состояния (по умолчанию yookassa)
    state = _user_states.get(user_id, {})
    payment_method = state.get('payment_method', 'yookassa')
    
    # Определяем цену (точно так же, как в обычном обработчике)
    if "бесплатно" in price_part:
        price = 0
        price_crypto = None
    else:
        # Парсим цену в зависимости от способа оплаты
        if payment_method == "cryptobot":
            # Для криптовалюты парсим цену в USD
            try:
                # Если формат "100₽ / $1.50", берем часть после "/"
                if "/" in price_part:
                    price_crypto_part = price_part.split("/")[-1].strip()
                    price_crypto = float(price_crypto_part.replace("$", "").strip())
                else:
                    price_crypto = float(price_part.replace("$", "").strip())
                price = None
            except ValueError:
                await message.answer("Неверный формат цены.", reply_markup=get_main_menu(user_id))
                return
        else:
            # Для карты/СБП парсим рублевую цену
            try:
                # Если формат "100₽ / $1.50", берем часть до "/"
                if "/" in price_part:
                    price_rub_part = price_part.split("/")[0].strip()
                    price = int(price_rub_part.replace("₽", "").strip())
                else:
                    price = int(price_part.replace("₽", "").strip())
                price_crypto = None
            except ValueError:
                await message.answer("Неверный формат цены.", reply_markup=get_main_menu(user_id))
                return
    
    # Получаем тариф из БД (точно так же, как в обычном обработчике)
    import sqlite3
    from config import FREE_V2RAY_TARIFF_ID
    from bot.services.tariff_service import get_tariff_by_name_and_price
    
    with get_db_cursor(commit=True) as cursor:
        # Ищем тариф в зависимости от способа оплаты
        if payment_method == "cryptobot" and price_crypto is not None:
            # Ищем по имени и крипто-цене
            try:
                cursor.execute(
                    "SELECT id, name, price_rub, duration_sec, price_crypto_usd, traffic_limit_mb FROM tariffs WHERE name = ? AND ABS(price_crypto_usd - ?) < 0.01",
                    (tariff_name, price_crypto),
                )
                row = cursor.fetchone()
            except sqlite3.OperationalError as exc:
                if "traffic_limit_mb" in str(exc):
                    cursor.execute(
                        "SELECT id, name, price_rub, duration_sec, price_crypto_usd FROM tariffs WHERE name = ? AND ABS(price_crypto_usd - ?) < 0.01",
                        (tariff_name, price_crypto),
                    )
                    row = cursor.fetchone()
                    if row:
                        row = (*row, 0)
                else:
                    raise
            if row and row[0] == FREE_V2RAY_TARIFF_ID:
                row = None
            if row:
                tariff = {
                    "id": row[0],
                    "name": row[1],
                    "price_rub": row[2],
                    "duration_sec": row[3],
                    "price_crypto_usd": row[4] if len(row) > 4 else None,
                    "traffic_limit_mb": row[5] if len(row) > 5 else 0,
                }
            else:
                await message.answer("Не удалось найти тариф с указанной ценой.", reply_markup=get_main_menu(user_id))
                return
        else:
            tariff = get_tariff_by_name_and_price(cursor, tariff_name, price or 0)
        
        if not tariff:
            await message.answer("Не удалось найти тариф.", reply_markup=get_main_menu(user_id))
            return
    
    # Если бесплатный тариф - создаем подписку сразу
    if tariff['price_rub'] == 0:
        try:
            service = SubscriptionService()
            subscription_data = await service.create_subscription(
                user_id=user_id,
                tariff_id=tariff['id'],
                duration_sec=tariff['duration_sec']
            )
            
            if subscription_data:
                subscription_url = f"https://veil-bot.ru/api/subscription/{subscription_data['token']}"
                msg = (
                    f"✅ *Подписка V2Ray успешно создана!*\n\n"
                    f"🔗 *Ссылка подписки:*\n"
                    f"`{subscription_url}`\n\n"
                    f"⏳ *Срок действия:* {format_duration(tariff['duration_sec'])}\n\n"
                    f"💡 *Как использовать:*\n"
                    f"1. Откройте приложение V2Ray\n"
                    f"2. Нажмите \"+\" → \"Импорт подписки\"\n"
                    f"3. Вставьте ссылку выше\n"
                    f"4. Все серверы будут добавлены автоматически"
                )
                
                _user_states.pop(user_id, None)
                await message.answer(
                    msg,
                    reply_markup=get_main_menu(user_id),
                    disable_web_page_preview=True,
                    parse_mode="Markdown"
                )
            else:
                await message.answer(
                    "❌ Не удалось создать подписку. Попробуйте позже.",
                    reply_markup=get_main_menu(user_id)
                )
        except Exception as e:
            logger.error(f"Error creating subscription for user {user_id}: {e}", exc_info=True)
            await message.answer(
                "❌ Произошла ошибка при создании подписки. Попробуйте позже.",
                reply_markup=get_main_menu(user_id)
            )
    else:
        # Для платного тарифа - создаем платеж
        # Сначала запрашиваем email (сохраняем способ оплаты из состояния)
        _user_states[user_id] = {
            'state': 'waiting_email_for_subscription',
            'protocol': 'v2ray',
            'key_type': 'subscription',
            'tariff': tariff,
            'payment_method': payment_method  # Сохраняем способ оплаты
        }
        
        # Формируем сообщение о цене в зависимости от способа оплаты
        if payment_method == "cryptobot" and tariff.get('price_crypto_usd'):
            price_display = f"${tariff['price_crypto_usd']:.2f} USDT"
        else:
            price_display = f"{tariff['price_rub']}₽"
        
        await message.answer(
            f"💳 *Оплата подписки V2Ray*\n\n"
            f"Тариф: *{tariff['name']}*\n"
            f"Цена: *{price_display}*\n\n"
            f"📧 Введите ваш email адрес:",
            reply_markup=get_cancel_keyboard(),
            parse_mode="Markdown"
        )


async def handle_email_for_subscription(message: types.Message):
    """Обработчик ввода email для создания подписки"""
    user_id = message.from_user.id
    email = message.text.strip()
    
    if email == "🔙 Отмена":
        _user_states.pop(user_id, None)
        await message.answer("Главное меню:", reply_markup=get_main_menu(user_id))
        return
    
    state = _user_states.get(user_id, {})
    tariff = state.get('tariff')
    payment_method = state.get('payment_method', 'yookassa')  # Получаем способ оплаты из состояния
    
    if not tariff:
        await message.answer("Ошибка: данные тарифа не найдены.", reply_markup=get_main_menu(user_id))
        _user_states.pop(user_id, None)
        return
    
    # Валидация и очистка email (такая же, как при покупке ключа)
    try:
        logger.info(f"Validating email for subscription: original='{email}', user_id={user_id}")
        
        # Проверяем на SQL инъекции
        sql_check = input_validator.validate_sql_injection(email)
        logger.info(f"SQL injection check result: {sql_check} for email: '{email}'")
        if not sql_check:
            logger.warning(f"SQL injection check failed for email: {email}, user_id={user_id}")
            await message.answer("❌ Email содержит недопустимые символы.", reply_markup=get_cancel_keyboard())
            return
        
        # Очищаем email от потенциально опасных символов
        email = input_validator.sanitize_string(email, max_length=100)
        logger.info(f"Email after sanitize: '{email}'")
        
        # Валидируем формат email
        email_valid = is_valid_email(email)
        logger.info(f"Email validation result: {email_valid} for email: '{email}'")
        if not email_valid:
            logger.warning(f"Email validation failed for: '{email}', user_id={user_id}, original='{message.text}'")
            await message.answer(
                "❌ Неверный формат email. Пожалуйста, введите корректный email адрес:",
                reply_markup=get_cancel_keyboard()
            )
            return
        
        logger.info(f"Email validation passed: '{email}', user_id={user_id}")
    except ValidationError as e:
        logger.error(f"ValidationError in email validation: {e}, email='{email}', user_id={user_id}", exc_info=True)
        await message.answer(f"❌ Ошибка валидации: {str(e)}", reply_markup=get_cancel_keyboard())
        return
    except Exception as e:
        logger.error(f"Unexpected error in email validation: {e}, email='{email}', user_id={user_id}", exc_info=True)
        await message.answer(
            "❌ Неверный формат email. Пожалуйста, введите корректный email адрес:",
            reply_markup=get_cancel_keyboard()
        )
        return
    
    # Создаем платеж с метаданными для подписки
    try:
        from memory_optimizer import get_payment_service
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        from bot.core import get_bot_instance
        
        payment_service = get_payment_service()
        if not payment_service:
            await message.answer(
                "❌ Сервис платежей временно недоступен. Попробуйте позже.",
                reply_markup=get_main_menu(user_id)
            )
            _user_states.pop(user_id, None)
            return
        
        # Создаем платеж в зависимости от способа оплаты
        if payment_method == "cryptobot":
            # Криптоплатеж
            if not tariff.get('price_crypto_usd'):
                await message.answer(
                    "❌ Крипто-оплата недоступна для этого тарифа. Пожалуйста, выберите другой способ оплаты.",
                    reply_markup=get_main_menu(user_id)
                )
                _user_states.pop(user_id, None)
                return
            
            invoice_id, payment_url = await payment_service.create_crypto_payment(
                user_id=user_id,
                tariff_id=tariff['id'],
                amount_usd=float(tariff['price_crypto_usd']),
                email=email or f"user_{user_id}@veilbot.com",
                country=None,  # Для подписки страна не нужна
                protocol='v2ray',
                description=f"Подписка V2Ray: {tariff['name']}",
                metadata={'key_type': 'subscription'}  # Сохраняем информацию о подписке
            )
            
            if not invoice_id or not payment_url:
                logger.error(f"Failed to create crypto payment for user {user_id}, email: {email}")
                await message.answer(
                    "❌ Ошибка при создании платежа. Попробуйте позже.",
                    reply_markup=get_main_menu(user_id)
                )
                _user_states.pop(user_id, None)
                return
            
            keyboard = InlineKeyboardMarkup(row_width=1)
            keyboard.add(InlineKeyboardButton("₿ Оплатить USDT", url=payment_url))
            
            await message.answer(
                f"₿ *Оплата подписки V2Ray (USDT)*\n\n"
                f"📦 Тариф: *{tariff['name']}*\n"
                f"💰 Сумма: *${tariff['price_crypto_usd']:.2f} USDT*\n"
                f"📧 Email: `{email}`\n\n"
                f"Нажмите кнопку ниже для оплаты:\n"
                f"⚠️ Инвойс действителен 1 час",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            
            # Запускаем ожидание криптоплатежа и создание подписки (аналогично обычным ключам)
            import asyncio
            from bot.services.key_creation import wait_for_crypto_payment
            dummy_server = (None,) * 7  # Пустой server tuple для подписок
            asyncio.create_task(
                wait_for_crypto_payment(
                    message, 
                    invoice_id, 
                    dummy_server, 
                    user_id, 
                    tariff, 
                    None,  # country не нужен для подписки
                    'v2ray', 
                    for_renewal=False
                )
            )
        else:
            # Обычный платеж YooKassa
            payment_id, confirmation_url = await payment_service.create_payment(
                user_id=user_id,
                tariff_id=tariff['id'],
                amount=tariff['price_rub'] * 100,  # В копейках
                email=email,
                country=None,  # Для подписки страна не нужна
                protocol='v2ray',
                description=f"Подписка V2Ray: {tariff['name']}",
                metadata={'key_type': 'subscription'}  # Сохраняем информацию о подписке
            )
            
            if not payment_id or not confirmation_url:
                logger.error(f"Failed to create payment for user {user_id}, email: {email}")
                await message.answer(
                    "❌ Ошибка при создании платежа. Попробуйте позже.",
                    reply_markup=get_main_menu(user_id)
                )
                _user_states.pop(user_id, None)
                return
            
            keyboard = InlineKeyboardMarkup(row_width=1)
            keyboard.add(InlineKeyboardButton("💳 Оплатить", url=confirmation_url))
            
            await message.answer(
                f"💳 *Оплата подписки V2Ray*\n\n"
                f"📦 Тариф: *{tariff['name']}*\n"
                f"💰 Сумма: *{tariff['price_rub']}₽*\n"
                f"📧 Email: `{email}`\n\n"
                f"Нажмите кнопку ниже для оплаты:\n"
                f"⚠️ Ссылка действительна 1 час",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            
            # Запускаем ожидание платежа и создание подписки (аналогично обычным ключам)
            # Для подписки server не нужен, так как подписка создает ключи на всех серверах
            # Передаем пустой tuple как placeholder
            import asyncio
            from bot.services.key_creation import wait_for_payment_with_protocol
            dummy_server = (None,) * 7  # Пустой server tuple для подписок
            asyncio.create_task(
                wait_for_payment_with_protocol(
                    message, 
                    payment_id, 
                    dummy_server, 
                    user_id, 
                    tariff, 
                    None,  # country не нужен для подписки
                    'v2ray', 
                    for_renewal=False
                )
            )
        
        # Очищаем состояние - информация о подписке сохранена в метаданных платежа
        _user_states.pop(user_id, None)
    except Exception as e:
        logger.error(f"Error creating payment for subscription: {e}", exc_info=True)
        await message.answer(
            "❌ Произошла ошибка при создании платежа. Попробуйте позже.",
            reply_markup=get_main_menu(user_id)
        )
        _user_states.pop(user_id, None)


async def handle_payment_method_for_subscription(message: types.Message):
    """Обработчик выбора способа оплаты для подписки"""
    user_id = message.from_user.id
    text = message.text.strip()
    
    state = _user_states.get(user_id, {})
    
    if text == "🔙 Назад":
        _user_states.pop(user_id, None)
        await message.answer("Главное меню:", reply_markup=get_main_menu(user_id))
        return
    
    if text == "💳 Карта РФ / СБП":
        # Сохраняем способ оплаты и переходим к выбору тарифа
        state["payment_method"] = "yookassa"
        state["state"] = "waiting_tariff_for_subscription"
        _user_states[user_id] = state
        
        msg = f"💳 *Оплата картой / СБП*\n\n"
        msg += f"📋 Подписка V2Ray\n\n"
        msg += "📦 Выберите тариф:"
        
        from bot.keyboards import get_tariff_menu
        await message.answer(
            msg,
            reply_markup=get_tariff_menu(payment_method="yookassa", paid_only=False),
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
                    "Пожалуйста, выберите другой способ оплаты.",
                    reply_markup=get_payment_method_keyboard()
                )
                return
        
        # Сохраняем способ оплаты и переходим к выбору тарифа
        state["payment_method"] = "cryptobot"
        state["state"] = "waiting_tariff_for_subscription"
        _user_states[user_id] = state
        
        msg = f"₿ *Оплата криптовалютой (USDT)*\n\n"
        msg += f"📋 Подписка V2Ray\n\n"
        msg += "📦 Выберите тариф:"
        
        from bot.keyboards import get_tariff_menu
        tariff_menu = get_tariff_menu(payment_method="cryptobot", paid_only=False)
        
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


def register_subscription_handlers(dp: Dispatcher, user_states: Dict[int, Dict[str, Any]]) -> None:
    """
    Регистрация обработчиков подписок
    
    Args:
        dp: Dispatcher aiogram
        user_states: Словарь состояний пользователей
    """
    global _user_states
    _user_states = user_states
    
    @dp.message_handler(lambda m: m.text == "Получить доступ")
    @rate_limit("subscription")
    async def get_access_handler(message: types.Message):
        await handle_get_access(message)
    
    @dp.message_handler(lambda m: _user_states.get(m.from_user.id, {}).get("state") == "waiting_payment_method_for_subscription" and m.text in ["💳 Карта РФ / СБП", "₿ Криптовалюта (USDT)", "🔙 Назад"])
    async def payment_method_for_subscription_handler(message: types.Message):
        await handle_payment_method_for_subscription(message)
    
    @dp.message_handler(lambda m: _user_states.get(m.from_user.id, {}).get("state") == "waiting_tariff_for_subscription" and "—" in m.text and any(w in m.text for w in ["₽", "$", "бесплатно"]))
    async def tariff_selection_handler(message: types.Message):
        await handle_tariff_selection_for_subscription(message)
    
    @dp.message_handler(lambda m: _user_states.get(m.from_user.id, {}).get("state") == "waiting_tariff_for_subscription" and m.text == "🔙 Назад")
    async def tariff_back_handler(message: types.Message):
        user_id = message.from_user.id
        state = _user_states.get(user_id, {})
        # Возвращаемся к выбору способа оплаты
        state["state"] = "waiting_payment_method_for_subscription"
        _user_states[user_id] = state
        
        msg = (
            f"📋 *Получить подписку V2Ray*\n\n"
            f"Подписка дает доступ ко всем серверам V2Ray\n\n"
            f"🔄 Автоматическое обновление при добавлении новых серверов\n\n"
            f"Выберите способ оплаты:"
        )
        await message.answer(msg, reply_markup=get_payment_method_keyboard(), parse_mode="Markdown")
    
    @dp.message_handler(lambda m: _user_states.get(m.from_user.id, {}).get("state") == "waiting_email_for_subscription")
    async def email_for_subscription_handler(message: types.Message):
        await handle_email_for_subscription(message)
    
    @dp.callback_query_handler(lambda c: c.data.startswith("copy_subscription:"))
    async def copy_subscription_handler(callback_query: types.CallbackQuery):
        await handle_copy_subscription(callback_query)
    
    @dp.callback_query_handler(lambda c: c.data.startswith("refresh_subscription:"))
    async def refresh_subscription_handler(callback_query: types.CallbackQuery):
        await handle_refresh_subscription(callback_query)
    
    @dp.callback_query_handler(lambda c: c.data == "renew_subscription")
    @rate_limit("renew_subscription")
    async def renew_subscription_handler(callback_query: types.CallbackQuery):
        """Обработчик кнопки 'Продлить подписку' - показывает выбор способа оплаты"""
        user_id = callback_query.from_user.id
        
        # Проверяем наличие активной подписки
        with get_db_cursor() as cursor:
            now = int(time.time())
            cursor.execute("""
                SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified
                FROM subscriptions
                WHERE user_id = ? AND is_active = 1 AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (user_id, now))
            subscription = cursor.fetchone()
        
        if not subscription:
            await callback_query.answer("У вас нет активной подписки для продления", show_alert=True)
            return
        
        # Устанавливаем состояние для выбора способа оплаты подписки
        _user_states[user_id] = {
            "state": "waiting_payment_method_for_subscription",
            "is_renewal": True  # Флаг, что это продление
        }
        
        # Показываем выбор способа оплаты
        msg = (
            f"📋 *Продление подписки V2Ray*\n\n"
            f"Подписка дает доступ ко всем серверам V2Ray\n\n"
            f"🔄 Автоматическое обновление при добавлении новых серверов\n\n"
            f"Выберите способ оплаты:"
        )
        await callback_query.message.answer(msg, reply_markup=get_payment_method_keyboard(), parse_mode="Markdown")
        
        try:
            await callback_query.answer()
        except Exception:
            pass

