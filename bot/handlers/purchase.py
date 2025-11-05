"""
Обработчики покупки ключей
"""
import time
import logging
from aiogram import Dispatcher, types
from config import PROTOCOLS, ADMIN_ID
from utils import get_db_cursor
from validators import input_validator, ValidationError
from bot.keyboards import (
    get_main_menu, get_cancel_keyboard, get_protocol_selection_menu,
    get_tariff_menu, get_payment_method_keyboard, get_country_menu,
    get_countries, get_countries_by_protocol
)
from bot_rate_limiter import rate_limit
from bot_error_handler import BotErrorHandler

# Эти функции будут импортированы из bot.py
# Они будут переданы через register_purchase_handlers
# create_payment_with_email_and_protocol, create_new_key_flow_with_protocol, 
# handle_free_tariff_with_protocol, handle_invite_friend, get_tariff_by_name_and_price

def register_purchase_handlers(
    dp: Dispatcher,
    user_states: dict,
    bot,
    main_menu,
    cancel_keyboard,
    is_valid_email,
    create_payment_with_email_and_protocol,
    create_new_key_flow_with_protocol,
    handle_free_tariff_with_protocol,
    handle_invite_friend,
    get_tariff_by_name_and_price
):
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
    
    # Остальные handlers будут в следующей части из-за размера файла

