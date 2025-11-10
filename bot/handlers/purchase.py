"""
Обработчики покупки ключей
"""
import time
import logging
from typing import Dict, Any, Callable, Optional
from aiogram import Dispatcher, types, Bot
from config import PROTOCOLS, ADMIN_ID, FREE_V2RAY_TARIFF_ID
from utils import get_db_cursor
from validators import input_validator, ValidationError
from bot.keyboards import (
    get_main_menu, get_cancel_keyboard, get_protocol_selection_menu,
    get_tariff_menu, get_payment_method_keyboard, get_country_menu,
    get_countries, get_countries_by_protocol
)
from bot_rate_limiter import rate_limit
from bot_error_handler import BotErrorHandler
import sqlite3

# Эти функции будут импортированы из bot.py
# Они будут переданы через register_purchase_handlers
# create_payment_with_email_and_protocol, create_new_key_flow_with_protocol, 
# handle_free_tariff_with_protocol, handle_invite_friend, get_tariff_by_name_and_price

def register_purchase_handlers(
    dp: Dispatcher,
    user_states: Dict[int, Dict[str, Any]],
    bot: Bot,
    main_menu: Any,
    cancel_keyboard: Any,
    is_valid_email: Callable[[str], bool],
    create_payment_with_email_and_protocol: Callable,
    create_new_key_flow_with_protocol: Callable,
    handle_free_tariff_with_protocol: Callable,
    handle_invite_friend: Callable,
    get_tariff_by_name_and_price: Callable
) -> None:
    """
    Регистрация всех обработчиков покупки
    
    Args:
        dp: Dispatcher aiogram
        user_states: Словарь состояний пользователей
        bot: Экземпляр бота
        main_menu: Главное меню
        cancel_keyboard: Клавиатура отмены
        is_valid_email: Функция валидации email
        create_payment_with_email_and_protocol: Функция создания платежа
        create_new_key_flow_with_protocol: Функция создания ключа
        handle_free_tariff_with_protocol: Функция обработки бесплатного тарифа
        handle_invite_friend: Функция обработки приглашения друга
        get_tariff_by_name_and_price: Функция получения тарифа
    """
    
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
                'protocol': protocol,
                'auto_protocol': True
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
                    "protocol": protocol,
                    "auto_protocol": True,
                    "auto_country": True
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
            await BotErrorHandler.handle_error(message, e, "handle_buy_menu", bot, ADMIN_ID)
    
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
            'protocol': protocol,
            'auto_protocol': False
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
                "protocol": protocol,
                "auto_protocol": True,
                "auto_country": True
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
    
    @dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "waiting_payment_method_after_country")
    async def handle_payment_method_after_country(message: types.Message):
        """Обработка выбора способа оплаты после выбора страны (новый flow)"""
        user_id = message.from_user.id
        text = message.text.strip()
        state = user_states.get(user_id, {})
        
        if text == "🔙 Назад":
            # Возвращаемся к выбору страны
            protocol = state.get("protocol", "outline")
            auto_protocol = state.get("auto_protocol", False)
            auto_country = state.get("auto_country", False)

            if auto_country:
                user_states.pop(user_id, None)
                await message.answer("Главное меню:", reply_markup=main_menu)
                return

            countries = get_countries_by_protocol(protocol) if protocol else get_countries()
            if protocol and not auto_protocol:
                protocol_info = PROTOCOLS.get(protocol, {"name": protocol})
                await message.answer(
                    f"Выберите страну для {protocol_info['name']}:",
                    reply_markup=get_country_menu(countries)
                )
                user_states[user_id] = {"state": "protocol_selected", "protocol": protocol, "auto_protocol": False}
            else:
                user_states.pop(user_id, None)
                await message.answer("Главное меню:", reply_markup=main_menu)
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
            user_states[user_id] = {"state": "waiting_payment_method_after_country", "country": country, "auto_country": False}
            
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
                state = user_states.pop(user_id, {})
                if state.get("auto_protocol"):
                    await message.answer("Главное меню:", reply_markup=main_menu)
                else:
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
            auto_country = len(countries) == 1 and message.text.strip() not in countries
            user_states[user_id] = {
                "state": "waiting_payment_method_after_country",
                "country": country,
                "protocol": protocol,
                "auto_protocol": user_state.get("auto_protocol", False),
                "auto_country": auto_country
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
            await BotErrorHandler.handle_error(message, e, "handle_protocol_country_selection", bot, ADMIN_ID)
    
    @dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "waiting_tariff" and m.text == "🔙 Назад")
    async def handle_tariff_back(message: types.Message):
        user_id = message.from_user.id
        state = user_states.get(user_id, {})
        protocol = state.get("protocol", "outline")
        country = state.get("country", "")

        state["state"] = "waiting_payment_method_after_country"
        state["auto_country"] = False
        user_states[user_id] = state

        msg = f"💳 *Выберите способ оплаты*\n\n"
        if protocol:
            msg += f"{PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}\n"
        if country:
            msg += f"🌍 Страна: *{country}*\n"

        await message.answer(
            msg,
            reply_markup=get_payment_method_keyboard(),
            parse_mode="Markdown"
        )

    @dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "waiting_tariff" and "—" in m.text and any(w in m.text for w in ["₽", "$", "бесплатно"]))
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
                        
                        # Оптимизированный запрос: объединяем 3 запроса в один с UNION ALL
                        # Приоритет: payments > keys (если outline) > v2ray_keys
                        # Если протокол outline, проверяем keys, иначе только payments и v2ray_keys
                        if (protocol or 'outline') == 'outline':
                            cursor.execute("""
                                SELECT email FROM (
                                    SELECT email, 1 as priority, created_at as sort_date
                                    FROM payments 
                                    WHERE user_id = ? AND email IS NOT NULL AND email != '' AND email NOT LIKE 'user_%@veilbot.com'
                                    UNION ALL
                                    SELECT email, 2 as priority, expiry_at as sort_date
                                    FROM keys 
                                    WHERE user_id = ? AND email IS NOT NULL AND email != '' AND email NOT LIKE 'user_%@veilbot.com'
                                    UNION ALL
                                    SELECT email, 3 as priority, expiry_at as sort_date
                                    FROM v2ray_keys 
                                    WHERE user_id = ? AND email IS NOT NULL AND email != '' AND email NOT LIKE 'user_%@veilbot.com'
                                ) ORDER BY priority ASC, sort_date DESC LIMIT 1
                            """, (user_id, user_id, user_id))
                        else:
                            # Для v2ray протокола не проверяем keys
                            cursor.execute("""
                                SELECT email FROM (
                                    SELECT email, 1 as priority, created_at as sort_date
                                    FROM payments 
                                    WHERE user_id = ? AND email IS NOT NULL AND email != '' AND email NOT LIKE 'user_%@veilbot.com'
                                    UNION ALL
                                    SELECT email, 2 as priority, expiry_at as sort_date
                                    FROM v2ray_keys 
                                    WHERE user_id = ? AND email IS NOT NULL AND email != '' AND email NOT LIKE 'user_%@veilbot.com'
                                ) ORDER BY priority ASC, sort_date DESC LIMIT 1
                            """, (user_id, user_id))
                        
                        row = cursor.fetchone()
                        email_db = row[0] if row and row[0] else None
                    except Exception:
                        email_db = None

                    if not email_db:
                        email_db = f"user_{user_id}@veilbot.com"

                    # Для продления сразу создаем платеж с выбранным способом оплаты
                    # Используем выбранный способ оплаты из состояния
                    renewal_payment_method = payment_method or "yookassa"  # По умолчанию YooKassa, если не указан
                    
                    # Удаляем клавиатуру с тарифами (ReplyKeyboardMarkup нельзя редактировать, поэтому отправляем новое сообщение)
                    # Отправляем сообщение о создании платежа, которое заменит предыдущее сообщение с тарифами
                    await message.answer(
                        f"💳 Создание платежа для тарифа: *{tariff.get('name', 'Неизвестно')}*\n\n"
                        f"⏳ Пожалуйста, подождите...",
                        reply_markup=main_menu,
                        parse_mode="Markdown"
                    )
                    
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

    @dp.message_handler(lambda m: m.text == "🔙 Назад" and user_states.get(m.from_user.id) is None)
    async def back_to_main_from_protocol(message: types.Message):
        await message.answer("Главное меню:", reply_markup=main_menu)

